# Recent channel context: what the bot sees of the conversation around it.
#
# Every guild message (everyone's, the bot's own included) is snapshotted into a small
# per-channel buffer, kept current by edit/delete events. After a restart the buffer is
# backfilled from Discord the first time the bot answers in a channel, so context
# survives restarts without any storage of our own.
#
# Before each reply, the recent window (up to WINDOW_MESSAGES within WINDOW_AGE, under a
# character budget, trimmed in chunks) becomes history entries for the agent: the bot's own messages as
# assistant turns, everyone else as [Name]-tagged user turns. Older images and files
# appear as placeholders; only the current message (and what it replies to) is sent
# with real attachments.
import asyncio
import datetime
from collections import deque

import discord

# Sizes are set by cost and relevance, not the model's context (GPT-6 Luna takes ~1M
# tokens; a full window is ~30k). A full window costs ~$0.0005 per reply when cached and
# ~$0.004 when not (see _window for how it stays cached), and too much stale chatter
# makes replies drift to old topics.
BUFFER_SIZE = 300             # the window plus room to count what has scrolled out, for the summary trigger
WINDOW_MESSAGES = 200
WINDOW_AGE = datetime.timedelta(hours=48)   # older than this -> rolling summary
WINDOW_CHAR_BUDGET = 120_000  # ~30k tokens
TRIM_TO = 0.75                # when the window outgrows a limit, cut back to this fraction of them
MAX_MESSAGE_CHARS = 4_000     # Discord's max (Nitro), so nothing is cut
MAX_EMBED_CHARS = 1_500       # fits a detailed TLDR summary

_buffers: dict[int, deque] = {}
_window_starts: dict[int, int] = {}  # channel_id -> id the window starts at (moves only on a trim)
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
    if is_self and message.embeds and getattr(message, "interaction_metadata", None):
        # Slash-command output leaves no message from the person who ran it, so without
        # this the bot's TLDR/rate/build embeds look like they came out of nowhere.
        extras.append(f"(posted for {message.interaction_metadata.user.display_name}'s slash command)")
    for embed in message.embeds:
        if is_self:
            # The bot's own embeds carry real content (TLDR summaries, /rate, /build).
            # The footer says what kind it is (e.g. "TikTok • 0:12 • Brief summary").
            embed_text = " - ".join(p for p in (embed.title, embed.description) if p)
            if embed_text:
                footer = f" ({embed.footer.text})" if embed.footer and embed.footer.text else ""
                extras.append(f"[embed: {embed_text[:MAX_EMBED_CHARS]}{footer}]")
        elif not text and embed.title:
            extras.append(f"[link preview: {embed.title[:200]}]")
    if message.stickers:
        extras.append("[sticker]")
    return {
        "id": message.id,
        "author_id": message.author.id,
        "author_name": message.author.display_name,
        "is_self": is_self,
        "has_embed": any(e.type == "rich" for e in message.embeds),  # posted embeds, not link previews
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
    # Returns the cutoff so callers can persist it (settings.save_context_reset).
    when = datetime.datetime.now(datetime.timezone.utc)
    _resets[(user_id, channel_id)] = when
    return when


def restore_reset(user_id, channel_id, when):
    # Saved cutoffs coming back after a restart; a newer live reset wins.
    current = _resets.get((user_id, channel_id))
    if current is None or when > current:
        _resets[(user_id, channel_id)] = when


def reset_cutoff(user_id, channel_id):
    return _resets.get((user_id, channel_id))


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


def _fits(entries, now, fraction):
    # Whether (snap, text) entries, oldest first, are within `fraction` of every limit.
    return (len(entries) <= WINDOW_MESSAGES * fraction
            and sum(len(t) for _, t in entries) <= WINDOW_CHAR_BUDGET * fraction
            and (not entries or entries[0][0]["created_at"] >= now - WINDOW_AGE * fraction))


def _trim(entries, now):
    # Keep the newest entries that fit within TRIM_TO of every limit.
    kept, used = [], 0
    for snap, text in reversed(entries):
        if (len(kept) + 1 > WINDOW_MESSAGES * TRIM_TO or used + len(text) > WINDOW_CHAR_BUDGET * TRIM_TO
                or snap["created_at"] < now - WINDOW_AGE * TRIM_TO):
            break
        kept.append((snap, text))
        used += len(text)
    return kept[::-1]


def _window(message, bot_user_id, requester_id):
    # (history entries oldest-first, ids included) for the messages before `message`.
    #
    # The window's start stays put while new messages are appended, and only jumps
    # forward (cutting back to TRIM_TO of the limits) once a limit is exceeded. Between
    # trims every turn re-sends the same prefix, so OpenAI's prompt cache bills it at the
    # cached rate instead of the full one.
    channel_id = message.channel.id
    buffer = list(_buffers.get(channel_id, ()))
    names = {s["id"]: ("you" if s["is_self"] else s["author_name"]) for s in buffer}
    start = _window_starts.get(channel_id, 0)

    entries = []
    for snap in buffer:
        if snap["id"] < start or snap["id"] >= message.id:
            continue
        text = _render(snap, names)
        if text is not None:
            entries.append((snap, text))
    if not _fits(entries, message.created_at, 1.0):
        entries = _trim(entries, message.created_at)
        _window_starts[channel_id] = entries[0][0]["id"] if entries else message.id

    reset = _resets.get((requester_id, channel_id))
    if reset:
        entries = [(s, t) for s, t in entries if s["created_at"] >= reset]

    window, ids = [], set()
    for snap, text in entries:
        ids.add(snap["id"])
        if snap["is_self"]:
            window.append({"role": "assistant", "content": text})
        else:
            window.append({
                "role": "user",
                "content": text,
                "author": {"user_id": snap["author_id"], "display_name": snap["author_name"]},
            })
    return window, ids


def window_floor(message, bot_user_id):
    # (oldest message id in the channel-level window, window size), ignoring per-user
    # resets. Everything older than the floor is what the rolling summary covers.
    _, ids = _window(message, bot_user_id, None)
    return (min(ids) if ids else message.id), len(ids)


def recent_before(channel_id, anchor_id, limit):
    # Up to `limit` buffered snapshots just before anchor_id, newest first.
    older = [s for s in _buffers.get(channel_id, ()) if s["id"] < anchor_id]
    return older[::-1][:limit]


def buffered_between(channel_id, after_id, before_id):
    # (messages buffered strictly between the two ids, whether the buffer reaches back
    # to after_id). If it doesn't, there may be more that only Discord still has.
    buffer = _buffers.get(channel_id, ())
    count = sum(1 for s in buffer if (after_id is None or s["id"] > after_id) and s["id"] < before_id)
    reaches = bool(buffer) and after_id is not None and buffer[0]["id"] <= after_id
    return count, reaches


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
