# Bot helper functions for message processing and conversation management
import re
import discord
from utils.core.text_formatting import format_discord_links
from utils.ai.agent import author_tag


def resolve_discord_user_id(user_str, guild):
    # Resolve a Discord user mention or name to a user ID. Accepts a raw mention token
    # (<@id>), or a display name / username / global name so users can target someone
    # by name without actually @-tagging (and pinging) them.
    if not user_str:
        return None
    match = re.match(r'<@!?(\d+)>', user_str)
    if match:
        return int(match.group(1))
    if guild is None:
        return None
    if user_str.strip().isdigit():
        # A bare user id (the model sometimes passes one); only if it's a member here.
        member = guild.get_member(int(user_str.strip()))
        return member.id if member else None

    needle = user_str.strip().lstrip('@').lower()
    if not needle:
        return None

    def names_of(m):
        return [n for n in (m.display_name, m.name, getattr(m, 'global_name', None)) if n]

    # 1) Exact, case-insensitive match on any of the member's names.
    for m in guild.members:
        if any(n.lower() == needle for n in names_of(m)):
            return m.id

    # 2) Fallback: substring match, but only if it uniquely identifies one member
    #    (avoids pinging the wrong person on an ambiguous partial name).
    matches = [m for m in guild.members if any(needle in n.lower() for n in names_of(m))]
    if len(matches) == 1:
        return matches[0].id
    return None


def build_user_message_content(message, content):
    # The current message for OpenAI: speaker label + multimodal content
    display_name = message.author.display_name if hasattr(message.author, 'display_name') else message.author.name
    username = message.author.name
    user_id = message.author.id

    # Speaker label, same format as the channel history (see agent.author_tag)
    author_info = author_tag(display_name, username)

    if isinstance(content, list):
        api_message_content = [{"type": "text", "text": author_info}] + content
    else:
        api_message_content = author_info + " " + (str(content) if content else message.content)

    return api_message_content, display_name, username, user_id


class ProgressNote:
    # Shows slow steps (watching a video) as a small "-# ..." line under the typing
    # indicator, and removes it once the reply is out. Nothing is posted for fast steps
    # like cache hits. The channel buffer drops "-#" lines from history anyway.
    def __init__(self, channel):
        self.channel, self.message = channel, None

    async def update(self, text):
        if text.startswith("Already watched"):
            return
        try:
            if self.message is None:
                self.message = await self.channel.send(f"-# {text}")
            else:
                await self.message.edit(content=f"-# {text}")
        except discord.HTTPException:
            pass

    async def done(self):
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass


MAX_MESSAGE_LEN = 2000
# Markdown links ("[[1]](<url>)", "[title](<url>)"): a split inside one breaks it.
_LINK_RE = re.compile(r"\[(?:\[[^\]\n]*\]|[^\[\]\n])*\]\(<?[^)\s]*>?\)")


def split_message(text, limit=MAX_MESSAGE_LEN):
    # Chunks of at most `limit` chars, split at a paragraph, line or word break that
    # isn't inside a link; a hard cut only when a single word is longer than the limit.
    chunks = []
    while len(text) > limit:
        links = [m.span() for m in _LINK_RE.finditer(text, 0, limit + 200)]
        cut = None
        for sep in ("\n\n", "\n", " "):
            pos = text.rfind(sep, 0, limit)
            while pos > 0 and any(start < pos < end for start, end in links):
                pos = text.rfind(sep, 0, pos)
            if pos > 0:
                cut = pos
                break
        cut = cut or limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


async def send_response(message, answer, suppress_mentions=False):
    # Send a response to Discord. Returns the primary sent message so callers can
    # act on it (e.g. attach a confirmation reaction for interactive actions).
    # suppress_mentions=True stops the reply from pinging anyone — used for the ack of
    # a ping/schedule action so the target isn't notified (spoiled) before it fires.
    answer = format_discord_links(answer)
    kwargs = {"allowed_mentions": discord.AllowedMentions.none()} if suppress_mentions else {}

    if len(answer) <= MAX_MESSAGE_LEN:
        return await message.reply(answer, **kwargs)
    else:
        first = None
        for chunk in split_message(answer):
            sent = await message.channel.send(chunk, **kwargs)
            if first is None:
                first = sent
        return first
