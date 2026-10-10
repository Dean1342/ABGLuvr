import asyncio
import datetime

import httpx
import pdfplumber
import docx
import openpyxl
import csv
from io import BytesIO, StringIO

from utils.conversation.channel_context import author_of, recent_before, snap_label


async def is_expired_discord_cdn_url(url):
    # Check if a Discord CDN image URL is expired
    if not (isinstance(url, str) and url.startswith("https://cdn.discordapp.com/")):
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.head(url, timeout=5)
            return resp.status_code >= 400
    except Exception:
        return True


def own_mention_as_name(text, guild):
    # The bot's own <@id> mention (how people call it) shown as "@ABGLuvr". As a raw id the
    # model took it for a ping target ("what are people saying about this @ABGLuvr"). Other
    # people's mentions stay raw so pings can target them exactly.
    me = getattr(guild, "me", None)
    if me is None or not text:
        return text
    return text.replace(f"<@{me.id}>", f"@{me.display_name}").replace(f"<@!{me.id}>", f"@{me.display_name}")


def reply_quote(author_label, text, *, from_bot=False):
    # The quoted message goes inside the sender's own turn, so it has to be unmistakably
    # someone else's words: otherwise "I got laid off" in a quote reads as the sender's.
    if from_bot:
        return f"(They're replying to your earlier message: \"{text}\")"
    return (f"(They're replying to a message from {author_label}. Quoted, so \"I\"/\"me\" in it means "
            f"{author_label}, not the sender: \"{text}\")")


# --- files (blueprint Stage F) ---
#
# Documents go to the model natively (Responses API input_file): for a PDF the API sends each
# page's text *and* its image, so charts, photos and scanned pages are read; .docx/.pptx/
# .doc/.ppt/.rtf/.odt are text-extracted by OpenAI (no embedded images), which also covers
# formats the local parsers can't read. Checked live on gpt-6-luna 2026-10-09.
#
# Spreadsheets use the local parser: natively only the first 1,000 rows per sheet are read
# (the probe's 1,500-row sheet came back as "1,000 laps"), and exact cell values matter.
# Plain text and code go in as their exact text; native would do the same extraction.
#
# The local parsers stay as the fallback: a file over the size cap, a failed download, or
# an API that rejects the file (agent.run_agent retries with files_as_text).
NATIVE_DOCS = ('.pdf', '.docx', '.doc', '.pptx', '.ppt', '.rtf', '.odt')
SPREADSHEETS = ('.csv', '.tsv', '.xlsx', '.xls')
NATIVE_MAX_BYTES = 20 * 1024 * 1024     # per file (OpenAI allows 50 MB per request; base64 adds a third)
NATIVE_TOTAL_BYTES = 40 * 1024 * 1024   # per message
MIME_TYPES = {
    '.pdf': 'application/pdf', '.doc': 'application/msword', '.ppt': 'application/vnd.ms-powerpoint',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.rtf': 'application/rtf', '.odt': 'application/vnd.oasis.opendocument.text',
    '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
}


def _ext(name):
    name = (name or "").lower()
    return name[name.rfind('.'):] if '.' in name else ""


def _native_part(name, data, where):
    ext = _ext(name)
    reads = "each page's text and image" if ext == '.pdf' else "its text only (embedded images and charts aren't included)"
    return [{"type": "text", "text": f"File '{name}' {where}, attached below; you get {reads}."},
            {"type": "file", "filename": name, "mime": MIME_TYPES.get(ext, "application/octet-stream"), "data": data}]


async def _file_parts(attachment, where, budget):
    # The parts for one non-image attachment: native, parsed text, or nothing readable.
    name = attachment.filename or "file"
    ext = _ext(name)
    size = getattr(attachment, "size", None) or 0
    if ext in NATIVE_DOCS:
        if size <= NATIVE_MAX_BYTES and size <= budget["left"]:
            data = await download_file(attachment.url)
            if data and len(data) <= budget["left"]:
                budget["left"] -= len(data)
                print(f"[files] {name}: native ({len(data):,} bytes)")
                return _native_part(name, data, where)
        reason = "too big to send whole" if size > NATIVE_MAX_BYTES or size > budget["left"] else "couldn't be sent"
        text = await process_file_attachment(attachment) if ext in _TEXT_EXTRACTABLE else None
        print(f"[files] {name}: {reason}; {'parsed locally' if text else 'unreadable'}")
        if not text:
            return [{"type": "text", "text": f"(File '{name}' {where} {reason} and couldn't be read another way; say "
                                             f"so if they ask about it.)"}]
        return [{"type": "text", "text": f"Content from file '{name}' {where} (read with the backup text parser: "
                                         f"figures and scanned pages aren't included):\n\n{truncate_text(text)}"}]
    text = await process_file_attachment(attachment)
    if text:
        print(f"[files] {name}: parsed locally")
        return [{"type": "text", "text": f"Content from file '{name}' {where}:\n\n{truncate_text(text)}"}]
    if ext in SPREADSHEETS and size <= min(NATIVE_MAX_BYTES, budget["left"]):
        data = await download_file(attachment.url)  # e.g. .xls, which the local parser can't open
        if data:
            budget["left"] -= len(data)
            print(f"[files] {name}: native (the local parser couldn't read it)")
            return _native_part(name, data, where)
    return []


def has_files(content):
    return isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "file" for p in content)


async def files_as_text(content):
    # The same content with every native file swapped for the local parser's text (when the
    # API rejects a file). Files the parsers can't read become a short note.
    out = []
    for part in content:
        if not (isinstance(part, dict) and part.get("type") == "file"):
            out.append(part)
            continue
        text = await asyncio.to_thread(extract_text, part["filename"], part["data"])
        out.append({"type": "text", "text":
                    f"Content of '{part['filename']}' (backup text parser; figures and scanned pages aren't included):"
                    f"\n\n{truncate_text(text)}" if text else
                    f"(The file '{part['filename']}' couldn't be read; say so if they ask about it.)"})
    return out


# Files the last question in a channel came with, so "what's on page 3?" right after asking
# about a PDF still has it, like links (links/resolve.follow_up_links).
_turn_files: dict[int, tuple] = {}   # channel id -> (message id, sent at, parts)
FOLLOW_UP_MESSAGES = 6
FOLLOW_UP_AGE = datetime.timedelta(minutes=30)


def _remember_files(message, parts):
    now = message.created_at
    for channel_id in [c for c, (_, at, _) in _turn_files.items() if now - at > FOLLOW_UP_AGE]:
        del _turn_files[channel_id]  # don't hold old files' bytes in memory
    _turn_files[message.channel.id] = (message.id, now, parts)


def _follow_up_files(message, replied):
    me = getattr(message.guild, "me", None)
    if replied is not None and (me is None or replied.author.id != me.id):
        return []  # a reply to someone else's message is about that message
    entry = _turn_files.get(message.channel.id)
    if not entry:
        return []
    asked_id, asked_at, parts = entry
    if message.created_at - asked_at > FOLLOW_UP_AGE:
        return []
    if asked_id not in {s["id"] for s in recent_before(message.channel.id, message.id, FOLLOW_UP_MESSAGES)}:
        return []
    print(f"[files] follow-up: reusing the files from the previous question ({asked_id})")
    return [{"type": "text", "text": "(The files below came with the question just before this one; they're probably "
                                     "still what they mean.)"}] + parts


# Multimodal content helpers
async def build_multimodal_content(message):
    # Build multimodal content from a Discord message: its own text and attachments first,
    # then what it replies to, quoted and attributed to whoever actually wrote it. Files from
    # the previous question come along for a follow-up that brings none.
    content = []
    if message.content:
        content.append({"type": "text", "text": own_mention_as_name(message.content, message.guild)})

    budget = {"left": NATIVE_TOTAL_BYTES}
    files = []
    # Process main message attachments
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image"):
            url = attachment.url
            if not await is_expired_discord_cdn_url(url):
                content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            files += await _file_parts(attachment, "in this message", budget)

    replied = message.reference.resolved if message.reference else None
    if replied is not None and getattr(replied, "author", None) is not None:
        if replied.content:
            me = getattr(message.guild, "me", None)
            content.append({"type": "text", "text": reply_quote(
                snap_label(author_of(replied)), replied.clean_content,
                from_bot=me is not None and replied.author.id == me.id)})

        # Process replied message attachments
        for attachment in replied.attachments:
            if attachment.content_type and attachment.content_type.startswith("image"):
                url = attachment.url
                if not await is_expired_discord_cdn_url(url):
                    content.append({"type": "image_url", "image_url": {"url": url}})
            else:
                files += await _file_parts(attachment, "in the message being replied to", budget)
    else:
        replied = None

    if files:
        _remember_files(message, files)
    elif not message.attachments:
        files = _follow_up_files(message, replied)
    return content + files


async def download_file(url):
    """Download file from URL and return bytes"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30)
            response.raise_for_status()
            return response.content
    except Exception as e:
        print(f"Error downloading file: {e}")
        return None


def extract_text_from_pdf(file_bytes):
    """Extract text from PDF bytes with better layout preservation"""
    try:
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            text = ""
            
            for page_num, page in enumerate(pdf.pages):
                page_text = f"--- Page {page_num + 1} ---\n"
                
                # Extract tables first
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        page_text += "TABLE:\n"
                        for row in table:
                            if row:
                                page_text += " | ".join([str(cell) if cell else "" for cell in row]) + "\n"
                        page_text += "\n"
                
                # Extract regular text with better spacing
                words = page.extract_words()
                if words:
                    # Group words by lines based on y-coordinates
                    lines = {}
                    for word in words:
                        y = round(word['top'], 1)
                        if y not in lines:
                            lines[y] = []
                        lines[y].append(word)
                    
                    # Sort lines by y-coordinate and reconstruct text
                    for y in sorted(lines.keys()):
                        line_words = sorted(lines[y], key=lambda w: w['x0'])
                        line_text = ""
                        prev_x = 0
                        
                        for word in line_words:
                            # Add spacing based on x-coordinate gaps
                            gap = word['x0'] - prev_x
                            if gap > 20:  # Significant gap
                                line_text += "    "  # Add indentation
                            elif gap > 10:
                                line_text += "  "
                            line_text += word['text'] + " "
                            prev_x = word['x1']
                        
                        page_text += line_text.strip() + "\n"
                
                text += page_text + "\n"
            
            return text.strip()
    except Exception as e:
        print(f"Error extracting PDF text with pdfplumber: {e}")
        # Fallback to simple text extraction
        try:
            with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                text = ""
                for page_num, page in enumerate(pdf.pages):
                    page_text = f"--- Page {page_num + 1} ---\n"
                    page_text += page.extract_text() or ""
                    text += page_text + "\n"
                return text.strip()
        except Exception as e2:
            print(f"Error with fallback PDF extraction: {e2}")
            return None


def extract_text_from_docx(file_bytes):
    """Extract text from DOCX bytes"""
    try:
        doc = docx.Document(BytesIO(file_bytes))
        text = ""
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"
        return text.strip()
    except Exception as e:
        print(f"Error extracting DOCX text: {e}")
        return None


def extract_text_from_xlsx(file_bytes):
    """Extract text from XLSX bytes"""
    try:
        workbook = openpyxl.load_workbook(BytesIO(file_bytes))
        text = ""
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            text += f"Sheet: {sheet_name}\n"
            for row in sheet.iter_rows(values_only=True):
                row_text = "\t".join([str(cell) if cell is not None else "" for cell in row])
                if row_text.strip():
                    text += row_text + "\n"
            text += "\n"
        return text.strip()
    except Exception as e:
        print(f"Error extracting XLSX text: {e}")
        return None


def extract_text_from_csv(file_bytes):
    """Extract text from CSV bytes"""
    try:
        # Try different encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                text_content = file_bytes.decode(encoding)
                csv_reader = csv.reader(StringIO(text_content))
                text = ""
                row_count = 0
                max_rows = 5000  # truncate_text caps the total size anyway
                
                for row in csv_reader:
                    if row_count >= max_rows:
                        text += f"\n[CSV truncated after {max_rows} rows due to size limit...]"
                        break
                    
                    # Join columns with tabs for better formatting
                    row_text = "\t".join([str(cell) if cell is not None else "" for cell in row])
                    if row_text.strip():
                        text += row_text + "\n"
                    row_count += 1
                
                return text.strip()
            except (UnicodeDecodeError, csv.Error):
                continue
        return None
    except Exception as e:
        print(f"Error extracting CSV text: {e}")
        return None


def extract_text_from_txt(file_bytes):
    """Extract text from TXT bytes"""
    try:
        # Try different encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None
    except Exception as e:
        print(f"Error extracting TXT text: {e}")
        return None


_PLAIN_TEXT = ('.txt', '.md', '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json', '.xml', '.yaml', '.yml',
               '.toml', '.ini', '.log', '.sql', '.sh', '.java', '.kt', '.c', '.h', '.cpp', '.hpp', '.cs', '.go', '.rs',
               '.rb', '.php', '.swift', '.lua')
_TEXT_EXTRACTABLE = ('.pdf', '.docx', '.xlsx', '.csv') + _PLAIN_TEXT


async def process_file_attachment(attachment):
    """Process a file attachment and extract text content"""
    if not attachment.filename:
        return None

    filename = attachment.filename.lower()
    # Check the type before downloading: videos and other unreadable files can be 100+ MB.
    if not filename.endswith(_TEXT_EXTRACTABLE):
        return None

    file_bytes = await download_file(attachment.url)
    if not file_bytes:
        return None
    return extract_text(filename, file_bytes)


def extract_text(filename, file_bytes):
    """Text from a file's bytes with the local parsers, or None if this type isn't parseable."""
    filename = filename.lower()
    if filename.endswith('.pdf'):
        return extract_text_from_pdf(file_bytes)
    elif filename.endswith('.docx'):
        return extract_text_from_docx(file_bytes)
    elif filename.endswith('.xlsx'):
        return extract_text_from_xlsx(file_bytes)
    elif filename.endswith('.csv'):
        return extract_text_from_csv(file_bytes)
    elif filename.endswith(_PLAIN_TEXT):
        return extract_text_from_txt(file_bytes)
    else:
        return None



def truncate_text(text, max_chars=150_000):
    """Truncate extracted file text (~40k tokens: a long PDF, well inside the model's context)"""
    if len(text) <= max_chars:
        return text
    
    truncated = text[:max_chars]
    return truncated + "\n\n[Content truncated due to length...]"


def has_non_image_attachments(message):
    """Check if message has non-image file attachments"""
    for attachment in message.attachments:
        if not (attachment.content_type and attachment.content_type.startswith("image")):
            return True
    return False