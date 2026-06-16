import os
import re
import uuid
import asyncio
import base64
import tempfile
import httpx
from openai import AsyncOpenAI

MAX_DURATION_SECONDS = 1800   # 30-minute cap
MAX_FILESIZE_BYTES   = 24 * 1024 * 1024  # 24 MB (Whisper hard limit is 25 MB)

# Matches original and bot-proxy video URLs in plain text
_VIDEO_URL_RE = re.compile(
    r'https?://(?:www\.|m\.|vm\.|vt\.)?'
    r'(?:'
    r'youtube\.com/(?:watch|shorts|embed|live)|youtu\.be/'
    r'|tiktok\.com|twitter\.com|x\.com'
    r'|instagram\.com/(?:reel|p|tv|stories)'
    r'|reddit\.com/r/|redd\.it/'
    r'|tnktok\.com|fixupx\.com|kkinstagram\.com|vxreddit\.com'
    r')'
    r'[^\s<>"\']*',
    re.IGNORECASE,
)


def extract_url_from_text(text: str) -> str | None:
    """Return the first recognized video URL from text, or None."""
    if not text:
        return None
    m = _VIDEO_URL_RE.search(text)
    return m.group(0).rstrip('.,)') if m else None


def normalize_url(url: str) -> str:
    """Convert bot embed-fix proxy domains back to originals that yt-dlp understands."""
    url = url.strip()
    if "fixupx.com" in url:
        return re.sub(r'fixupx\.com', 'x.com', url, flags=re.IGNORECASE)
    if "tnktok.com" in url:
        # tnktok.com/t/{code} was originally vm.tiktok.com/{code} — restore that form
        # as yt-dlp handles vm short-links more reliably than www.tiktok.com/t/
        m = re.match(r'https?://(?:www\.)?tnktok\.com/t/([^/?#\s]+)', url, re.IGNORECASE)
        if m:
            return f"https://vm.tiktok.com/{m.group(1)}"
        return re.sub(r'tnktok\.com', 'tiktok.com', url, flags=re.IGNORECASE)
    if "kkinstagram.com" in url:
        return re.sub(r'kkinstagram\.com', 'instagram.com', url, flags=re.IGNORECASE)
    if "vxreddit.com" in url:
        return re.sub(r'vxreddit\.com', 'reddit.com', url, flags=re.IGNORECASE)
    return url


def _find_output_file(tmp_dir: str, temp_id: str, prefix: str = "abg_audio") -> str | None:
    for ext in ("m4a", "webm", "mp4", "mp3", "opus", "ogg", "mpeg", "wav"):
        candidate = os.path.join(tmp_dir, f"{prefix}_{temp_id}.{ext}")
        if os.path.exists(candidate):
            return candidate
    return None


def _extract_metadata(info: dict, fallback_url: str) -> dict:
    return {
        "title":       info.get("title", "Unknown Title"),
        "duration":    info.get("duration", 0),
        "thumbnail":   info.get("thumbnail"),
        "uploader":    info.get("uploader") or info.get("channel", ""),
        "webpage_url": info.get("webpage_url", fallback_url),
    }


def _write_youtube_cookies() -> str | None:
    """
    Write YOUTUBE_COOKIES env var content to a temp Netscape cookies file.
    Returns the file path, or None if the env var isn't set.
    The file persists for the dyno/process lifetime — safe to reuse across calls.
    """
    content = os.getenv("YOUTUBE_COOKIES", "").strip()
    if not content:
        return None
    path = os.path.join(tempfile.gettempdir(), "abg_yt_cookies.txt")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return path


def _build_ydl_opts(out_tmpl: str, fmt: str) -> dict:
    import yt_dlp
    opts = {
        "format": fmt,
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration <= {MAX_DURATION_SECONDS}"
        ),
        "max_filesize": MAX_FILESIZE_BYTES,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web"],
            },
        },
    }
    cookie_file = _write_youtube_cookies()
    if cookie_file:
        opts["cookiefile"] = cookie_file
    return opts


def _translate_ydl_error(msg: str) -> str:
    if "private" in msg.lower():
        return "That video is private or unavailable."
    if "match_filter" in msg.lower() or "duration" in msg.lower():
        return "Video is too long — max 30 minutes."
    if "not available" in msg.lower() or "removed" in msg.lower():
        return "That video is unavailable or has been removed."
    if "unexpected response" in msg.lower():
        return "Could not download — the platform blocked the request (bot detection). Try again in a moment."
    if "sign in" in msg.lower() or "confirm" in msg.lower() and "bot" in msg.lower():
        return "YouTube blocked the download (bot detection on server IPs). Try again — it often succeeds on retry."
    return f"Could not download video: {msg[:200]}"


async def _ydl_download(url: str, ydl_opts: dict) -> dict:
    import yt_dlp

    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _run)
    except yt_dlp.utils.DownloadError as e:
        raw = str(e)
        print(f"[yt-dlp error] {raw[:500]}")
        raise ValueError(_translate_ydl_error(raw))
    except Exception as e:
        raw = str(e)
        print(f"[yt-dlp unexpected] {raw[:500]}")
        raise ValueError(f"Unexpected download error: {raw[:200]}")


async def download_audio(url: str) -> tuple[str, dict]:
    """
    Download audio-only from a video URL.
    Prefers Whisper-compatible formats (m4a, webm, mp4) — no ffmpeg required.
    Caller must delete the returned temp file in a finally block.
    """
    temp_id  = str(uuid.uuid4())[:10]
    tmp_dir  = tempfile.gettempdir()
    out_tmpl = os.path.join(tmp_dir, f"abg_audio_{temp_id}.%(ext)s")
    opts     = _build_ydl_opts(out_tmpl, "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio[ext=mp4]/bestaudio/best")

    info     = await _ydl_download(url, opts)
    out_path = _find_output_file(tmp_dir, temp_id, "abg_audio")
    if not out_path:
        raise ValueError("Audio download failed — no output file was produced.")
    return out_path, _extract_metadata(info, url)


async def download_video(url: str) -> tuple[str, dict]:
    """
    Download full video as mp4 (for frame extraction + Whisper transcription).
    Used for short-form platforms: TikTok, Instagram, Twitter/X.
    Caller must delete the returned temp file in a finally block.
    """
    temp_id  = str(uuid.uuid4())[:10]
    tmp_dir  = tempfile.gettempdir()
    out_tmpl = os.path.join(tmp_dir, f"abg_video_{temp_id}.%(ext)s")
    opts     = _build_ydl_opts(out_tmpl, "best[ext=mp4]/best")

    info     = await _ydl_download(url, opts)
    out_path = _find_output_file(tmp_dir, temp_id, "abg_video")
    if not out_path:
        raise ValueError("Video download failed — no output file was produced.")
    return out_path, _extract_metadata(info, url)


def _extract_youtube_id(url: str) -> str | None:
    for pattern in (
        r'youtu\.be/([^?&\s/]+)',
        r'[?&]v=([^&\s]+)',
        r'youtube\.com/(?:shorts|embed|live)/([^?&\s/]+)',
    ):
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


async def get_youtube_transcript(url: str) -> tuple[str, int] | tuple[None, None]:
    """
    Fetch transcript from YouTube's caption system — no video download, no bot detection.
    Returns (transcript_text, duration_seconds) or (None, None) if unavailable.
    Falls back to any available auto-generated language if English is missing.
    """
    video_id = _extract_youtube_id(url)
    if not video_id:
        return None, None

    loop = asyncio.get_event_loop()

    def _fetch():
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        # Try English first, then fall back to any available transcript
        try:
            fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB", "en-CA"])
        except Exception:
            try:
                tl = api.list(video_id)
                transcript_obj = next(iter(tl))
                fetched = transcript_obj.fetch()
            except Exception:
                return None, None

        snippets = list(fetched)
        if not snippets:
            return None, None

        text     = " ".join(s.text for s in snippets).strip()
        last     = snippets[-1]
        duration = int(getattr(last, "start", 0) + getattr(last, "duration", 0))
        return text, duration

    return await loop.run_in_executor(None, _fetch)


async def get_youtube_metadata(url: str, video_id: str | None = None) -> dict:
    """Fetch YouTube title and thumbnail via oEmbed — no API key needed."""
    vid = video_id or _extract_youtube_id(url) or ""
    oembed = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(oembed)
            r.raise_for_status()
            data = r.json()
        return {
            "title":       data.get("title", "YouTube Video"),
            "duration":    0,
            "thumbnail":   data.get("thumbnail_url"),
            "uploader":    data.get("author_name", ""),
            "webpage_url": url,
        }
    except Exception:
        return {"title": "YouTube Video", "duration": 0, "thumbnail": None, "uploader": "", "webpage_url": url}


def extract_frames(video_path: str, duration: int) -> list[str]:
    """
    Extract evenly-spaced frames from a video using OpenCV.
    Returns base64-encoded JPEG strings, or [] if cv2 is unavailable or fails.

    Frame count by duration:
      ≤ 30 sec  → 4 frames
      30–90 sec → 6 frames
      90–180 sec → 8 frames
    Skips first/last second to avoid intro/outro cards.
    """
    try:
        import cv2
    except ImportError:
        return []

    n_frames = 4 if duration <= 30 else (6 if duration <= 90 else 8)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps          = cap.get(cv2.CAP_PROP_FPS) or 25
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    start_frame  = int(fps)
    end_frame    = max(start_frame + 1, total_frames - int(fps))
    usable       = end_frame - start_frame

    if usable < 1:
        cap.release()
        return []

    step       = max(1, usable // n_frames)
    frames_b64 = []

    for i in range(n_frames):
        frame_idx = start_frame + i * step
        if frame_idx >= total_frames:
            break
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue
        h, w = frame.shape[:2]
        if w > 640:
            frame = cv2.resize(frame, (640, int(h * 640 / w)))
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        frames_b64.append(base64.b64encode(buf).decode("utf-8"))

    cap.release()
    return frames_b64


async def transcribe_audio(path: str, openai_client: AsyncOpenAI) -> str:
    """Send an audio or video file to OpenAI Whisper API, return transcript text."""
    with open(path, "rb") as f:
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )
    return (response if isinstance(response, str) else response.text).strip()


async def summarize_transcript(
    transcript: str,
    metadata: dict,
    mode: str,
    openai_client: AsyncOpenAI,
    frames: list[str] | None = None,
) -> str:
    """
    Summarize a transcript using GPT.
    mode: "brief"    → one-sentence intro + 3–5 bullet points
          "detailed" → 2–3 paragraphs
    frames: optional list of base64 JPEG strings for visual context (short-form video)
    """
    has_frames = bool(frames)

    if mode == "brief":
        instruction = (
            "You are a helpful assistant that summarizes video content. "
            "Give a brief TLDR of this video. "
            "Start with one sentence capturing the core topic, then use 3–5 concise bullet points. "
            "Be direct and skimmable."
        )
        max_completion_tokens = 500
    else:
        instruction = (
            "You are a helpful assistant that summarizes video content. "
            "Give a detailed summary of this video. "
            "Write 2–3 short paragraphs covering the main topic, key points or arguments, "
            "and any notable details, quotes, or conclusions. Be thorough but clear."
        )
        max_completion_tokens = 1000

    if has_frames:
        instruction += (
            " You are also given frames sampled from the video at even intervals. "
            "Use them to add visual context to your summary."
        )

    title_hint       = f'Video title: "{metadata.get("title", "Unknown")}"'
    transcript_body  = transcript[:12000]
    if len(transcript) > 12000:
        transcript_body += "\n\n[Transcript truncated — only the first portion was summarized]"

    if has_frames:
        user_content: list = [
            {"type": "text", "text": f"{title_hint}\n\nTranscript:\n{transcript_body}"},
        ]
        for b64 in frames:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            })
    else:
        user_content = f"{title_hint}\n\nTranscript:\n{transcript_body}"

    messages = [
        {"role": "system", "content": instruction},
        {"role": "user",   "content": user_content},
    ]

    resp = await openai_client.chat.completions.create(
        model="gpt-5.4-mini-2026-03-17",
        messages=messages,
        max_completion_tokens=max_completion_tokens,
    )
    return resp.choices[0].message.content.strip()
