# Recent channel context: what the bot sees of the conversation around it.
#
# Every guild message (everyone's, the bot's own included) is snapshotted into a small
# per-channel buffer, kept current by edit/delete events. After a restart the buffer is
# backfilled from Discord the first time the bot answers in a channel, so context
# survives restarts without any storage of our own.
#
# Before each reply, the recent window (last WINDOW_MESSAGES within WINDOW_AGE, under a
# character budget) becomes history entries for the agent: the bot's own messages as
# assistant turns, everyone else as [Name]-tagged user turns. Older images and files
# appear as placeholders; only the current message (and what it replies to) is sent
# with real attachments.
import asyncio
import datetime
from collections import deque

import discord

BUFFER_SIZE = 100
WINDOW_MESSAGES = 40
WINDOW_AGE = datetime.timedelta(hours=12)
WINDOW_CHAR_BUDGET = 24_000   # ~6k tokens
MAX_MESSAGE_CHARS = 1_500
MAX_EMBED_CHARS = 600

_buffers: dict[int, deque] = {}
_backfilled: set[int] = set()
_locks: dict[int, asyncio.Lock] = {}
# (user_id, channel_id) -> ignore channel messages before this time when answering that
# user. Set by /model reset and persona switches.
_resets: dict[tuple[int, int], datetime.datetime] = {}


# --- snapshots ---

def _attachment_note(attachment):
    content_type = attachment.content_type or ""
    if content_type.startswith("image"):
        return "[image]"
    if content_type.startswith("video"):
        return "[video]"
    return f"[file: {attachment.filename}]"


def _snapshot(message, bot_user_id):
    is_self = message.author.id == bot_user_id
    text = message.clean_content or ""
    extras = [_attachment_note(a) for a in message.attachments]
    for embed in message.embeds:
        if is_self:
            # The bot's own embeds carry real content (TLDR summaries, /rate, /build).
            embed_text = " - ".join(p for p in (embed.title, embed.description) if p)
            if embed_text:
                extras.append(f"[embed: {embed_text[:MAX_EMBED_CHARS]}]")
        elif not text and embed.title:
            extras.append(f"[link preview: {embed.title[:200]}]")
    if message.stickers:
        extras.append("[sticker]")
    return {
        "id": message.id,
        "author_id": message.author.id,
        "author_name": message.author.display_name,
        "is_self": is_self,
        "text": text[:MAX_MESSAGE_CHARS],
        "extras": extras,
        "reply_to": message.reference.message_id if message.reference else None,
        "created_at": message.created_at,
    }


def record_message(message, bot_user_id):
    buffer = _buffers.setdefault(message.channel.id, deque(maxlen=BUFFER_SIZE))
    buffer.append(_snapshot(message, bot_user_id))


def update_message(message, bot_user_id):
    buffer = _buffers.get(message.channel.id)
    if not buffer:
        return
    for i, snap in enumerate(buffer):
        if snap["id"] == message.id:
            buffer[i] = _snapshot(message, bot_user_id)
            return


def forget_message(channel_id, message_id):
    buffer = _buffers.get(channel_id)
    if buffer:
        _buffers[channel_id] = deque((s for s in buffer if s["id"] != message_id), maxlen=BUFFER_SIZE)


async def ensure_backfilled(channel, bot_user_id):
    # First reply in a channel since startup: pull recent history from Discord and merge
    # it with whatever was recorded live in the meantime.
    if channel.id in _backfilled:
        return
    lock = _locks.setdefault(channel.id, asyncio.Lock())
    async with lock:
        if channel.id in _backfilled:
            return
        try:
            fetched = [_snapshot(m, bot_user_id) async for m in channel.history(limit=BUFFER_SIZE)]
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[context] couldn't read history for channel {channel.id}: {e}")
            fetched = []
        merged = {s["id"]: s for s in fetched}
        merged.update({s["id"]: s for s in _buffers.get(channel.id, ())})  # live copies are newer
        _buffers[channel.id] = deque(sorted(merged.values(), key=lambda s: s["id"]), maxlen=BUFFER_SIZE)
        _backfilled.add(channel.id)


def reset_context(user_id, channel_id):
    _resets[(user_id, channel_id)] = datetime.datetime.now(datetime.timezone.utc)


# --- building the window ---

def _strip_bot_footers(text):
    # Drop the bot's own Sources blocks and "-# ..." footers so the model doesn't learn
    # to imitate them from its history.
    text = text.split("\n\nSources:\n", 1)[0]
    return "\n".join(line for line in text.split("\n") if not line.startswith("-# ")).strip()


def _render(snap, names):
    text = _strip_bot_footers(snap["text"]) if snap["is_self"] else snap["text"]
    if snap["extras"]:
        text = (text + " " + " ".join(snap["extras"])).strip()
    if not text:
        return None
    # Reply markers only on other people's messages; on the bot's own turns the model
    # would start copying the "(replying to ...)" prefix into its answers.
    if snap["reply_to"] and not snap["is_self"]:
        target = names.get(snap["reply_to"])
        text = f"(replying to {target}) {text}" if target else f"(replying to an earlier message) {text}"
    return text


def _window(message, bot_user_id, requester_id):
    # (history entries oldest-first, ids included) for the messages before `message`.
    buffer = list(_buffers.get(message.channel.id, ()))
    names = {s["id"]: ("you" if s["is_self"] else s["author_name"]) for s in buffer}
    cutoff = message.created_at - WINDOW_AGE
    reset = _resets.get((requester_id, message.channel.id))
    if reset and reset > cutoff:
        cutoff = reset

    window, ids, used = [], set(), 0
    for snap in reversed(buffer):
        if snap["id"] >= message.id:
            continue
        if snap["created_at"] < cutoff or len(window) >= WINDOW_MESSAGES:
            break
        text = _render(snap, names)
        if text is None:
            continue
        if used + len(text) > WINDOW_CHAR_BUDGET:
            break
        used += len(text)
        ids.add(snap["id"])
        if snap["is_self"]:
            window.append({"role": "assistant", "content": text})
        else:
            window.append({
                "role": "user",
                "content": text,
                "author": {"user_id": snap["author_id"], "display_name": snap["author_name"]},
            })
    window.reverse()
    return window, ids


async def _reply_parent(message, bot_user_id, visible_ids):
    # When someone replies to an older bot message that's outside the window, the reply
    # itself is quoted by build_multimodal_content, but not what the bot was answering.
    # Fetch that one parent message so the model knows what the old reply was about.
    ref = message.reference
    replied = ref.resolved if ref else None
    if not isinstance(replied, discord.Message) or replied.author.id != bot_user_id:
        return None
    if not replied.reference or not replied.reference.message_id:
        return None
    if replied.id in visible_ids:
        return None  # already visible in the window
    parent_id = replied.reference.message_id
    parent = next((s for s in _buffers.get(message.channel.id, ()) if s["id"] == parent_id), None)
    if parent is None:
        try:
            parent = _snapshot(await message.channel.fetch_message(parent_id), bot_user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    text = _render(parent, {})
    if not text:
        return None
    return {
        "role": "system",
        "content": f"Context: the bot message being replied to was answering {parent['author_name']}, who had said: {text}",
    }


async def build_context(message, bot_user_id, requester_id):
    # Everything the agent should see before the current message, oldest first.
    await ensure_backfilled(message.channel, bot_user_id)
    history, visible_ids = _window(message, bot_user_id, requester_id)
    parent = await _reply_parent(message, bot_user_id, visible_ids)
    if parent:
        history.append(parent)
    return history
