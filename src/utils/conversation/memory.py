# Long-term memory beyond the recent channel window.
#
# Two layers, injected together as one developer note placed before the window:
#   1. Remembered facts: things someone explicitly asked the bot to remember, scoped to
#      a user, a channel, or the whole server. Only the remember_fact / forget_fact
#      tools and /memory write them; nothing is saved automatically.
#   2. Rolling channel summary: once enough messages have slid out of the window, they
#      are fetched from Discord and folded into a per-channel summary by the default
#      model. Normally that runs in the background after a reply. When the window is
#      nearly empty (the channel went quiet for hours) it runs before replying instead,
#      so "what were we talking about yesterday" still works.
#
# Both are stored in Supabase and cached here. A missing table or DB error is logged
# and memory is left out of the prompt; it never blocks a reply.
import asyncio
import datetime
import time

import discord

from utils.conversation.context import DEFAULT_MODEL_ID
from utils.conversation.channel_context import (
    _render, _snapshot, buffered_between, reset_cutoff, window_floor,
)
from utils.integrations import supabase_client as db

SUMMARY_MIN_NEW = 20             # messages out of the window before the summary refreshes
SUMMARY_MAX_FETCH = 300          # newest unsummarized messages fetched per refresh
SUMMARY_FIRST_LOOKBACK = datetime.timedelta(days=7)  # how far back a first summary reaches
SUMMARY_CHUNK_CHARS = 200_000    # transcript folded in per model call (~50k tokens; one call in practice)
SUMMARY_MAX_WORDS = 600
SUMMARY_INLINE_BELOW = 5         # window smaller than this -> refresh before replying
SUMMARY_INLINE_TIMEOUT = 25      # seconds; the refresh keeps going in the background after
MAX_FACT_CHARS = 300
MAX_FACTS_PER_GUILD = 300
FACTS_NOTE_CHARS = 6_000         # ~1.5k tokens of facts per turn
LOAD_RETRY_SECONDS = 300

SCOPES = ("user", "channel", "server")

SUMMARY_INSTRUCTIONS = f"""You keep the long-term memory of one Discord channel for ABGLuvr, a bot in a private server of adult friends. You get the current memory and a batch of newer messages. Return the updated memory.

Keep what someone might bring up again later:
- who said or did what, by name: plans, dates, purchases, cars, jobs, trips, events
- ongoing topics and arguments, and how they ended
- questions ABGLuvr answered and what it said (one line each)
- running jokes and nicknames

Drop greetings, filler, one-off banter, and link or embed noise. Fold the new messages into the existing memory: update or remove things that changed or are clearly over, keep the rest. Put dates (like "Oct 6") on anything time-sensitive.

Write short plain-text bullet points grouped by topic, under {SUMMARY_MAX_WORDS} words in total. Only include what the messages actually say; never guess or fill gaps. Output only the memory."""


class MemoryUnavailable(Exception):
    pass


_summaries: dict[int, dict | None] = {}   # channel_id -> channel_summaries row (None: no summary yet)
_facts: dict[int, list[dict]] = {}        # guild_id -> remembered_facts rows, oldest first
_scanned_to: dict[int, int] = {}          # channel_id -> last id considered (even if nothing was worth keeping)
_refreshing: dict[int, asyncio.Task] = {}
_retry_at: dict[tuple, float] = {}


async def _cached(cache, key, loader, what):
    # Load-once cache. A failed load is retried after a pause instead of on every message.
    if key in cache:
        return True
    if time.monotonic() < _retry_at.get((what, key), 0):
        return False
    try:
        cache[key] = await loader(key)
        return True
    except Exception as e:
        _retry_at[(what, key)] = time.monotonic() + LOAD_RETRY_SECONDS
        print(f"[memory] couldn't load {what} for {key}: {type(e).__name__}: {e}")
        return False


# --- remembered facts ---

async def get_facts(guild_id):
    # All of a guild's facts, or None if the store is unavailable.
    if not await _cached(_facts, guild_id, db.get_facts, "facts"):
        return None
    return _facts[guild_id]


def visible_facts(facts, channel_id):
    # Channel-scoped facts only show up in their own channel.
    return [f for f in facts if f["scope"] != "channel" or int(f["channel_id"] or 0) == channel_id]


def subjects(row):
    # User ids a fact is about (empty for channel/server facts).
    return [int(i) for i in row.get("subject_ids") or []]


def _same_text(a, b):
    def norm(s):
        return " ".join(s.split()).rstrip(".!").casefold()
    return norm(a) == norm(b)


async def remember(guild_id, scope, fact, *, channel_id=None, subject_ids=(), added_by=None,
                   source_message_id=None, replaces=()):
    # Returns (row, ids actually replaced, created). An identical fact that's already
    # saved is returned with created=False instead of being stored twice.
    # Raises MemoryUnavailable or ValueError.
    facts = await get_facts(guild_id)
    if facts is None:
        raise MemoryUnavailable()
    fact = " ".join((fact or "").split())
    subject_ids = list(dict.fromkeys(int(i) for i in subject_ids or ()))
    if not fact:
        raise ValueError("the fact is empty")
    if len(fact) > MAX_FACT_CHARS:
        raise ValueError(f"facts are limited to {MAX_FACT_CHARS} characters; save a shorter version")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    if scope == "user" and not subject_ids:
        raise ValueError("a user fact needs the person it's about")

    duplicate = next((f for f in visible_facts(facts, channel_id)
                      if f["scope"] == scope and _same_text(f["fact"], fact)), None)
    if duplicate:
        stale = [i for i in replaces if i != duplicate["id"]]
        removed = await forget(guild_id, stale) if stale else []
        return duplicate, removed, False

    if len(facts) - len(replaces) >= MAX_FACTS_PER_GUILD:
        raise ValueError(f"memory is full ({MAX_FACTS_PER_GUILD} facts); some have to be forgotten first")
    row = await db.insert_fact({
        "guild_id": guild_id,
        "scope": scope,
        "channel_id": channel_id if scope == "channel" else None,
        "subject_ids": subject_ids if scope == "user" else None,
        "fact": fact,
        "added_by": added_by,
        "source_message_id": source_message_id,
    })
    if not row:
        raise MemoryUnavailable()
    facts.append(row)
    removed = await forget(guild_id, list(replaces)) if replaces else []
    print(f"[memory] guild {guild_id}: user {added_by} saved #{row['id']} ({scope}), replaced {removed}")
    return row, removed, True


async def forget(guild_id, fact_ids):
    # Deletes the given ids within this guild; returns the ids that existed.
    facts = await get_facts(guild_id)
    if facts is None:
        raise MemoryUnavailable()
    wanted = {int(i) for i in fact_ids}
    if not wanted & {f["id"] for f in facts}:
        return []
    deleted = set(await db.delete_facts(guild_id, sorted(wanted)))
    _facts[guild_id] = [f for f in facts if f["id"] not in deleted]
    return sorted(deleted)


def fact_label(row, name_of):
    if row["scope"] == "user":
        return "about " + ", ".join(name_of(i) for i in subjects(row))
    return "this channel" if row["scope"] == "channel" else "server"


def member_label(member):
    # "Display (@username)" when they differ, so the model can match either name to a
    # [Display]-tagged speaker or to how people refer to each other.
    if member.name.casefold() == member.display_name.casefold():
        return member.display_name
    return f"{member.display_name} (@{member.name})"


def fact_line(row, name_of):
    return f"- [#{row['id']}] ({fact_label(row, name_of)}) {row['fact']}"


def _facts_section(facts, name_of, requester_id):
    if not facts:
        return None
    lines = [fact_line(f, name_of) for f in facts]
    if sum(len(l) + 1 for l in lines) > FACTS_NOTE_CHARS:
        # Too many to show: the sender's own facts first, then server/channel, then others.
        def priority(f):
            if requester_id in subjects(f):
                return 0
            return 1 if f["scope"] != "user" else 2
        lines, used = [], 0
        ranked = sorted(facts, key=lambda f: (priority(f), -f["id"]))
        for f in ranked:
            line = fact_line(f, name_of)
            if used + len(line) + 1 > FACTS_NOTE_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        lines.append(f"({len(facts) - len(lines)} older facts not shown)")
    return (
        "## Remembered facts\n"
        "Things people explicitly asked you to remember. [#id] is what forget_fact and "
        "remember_fact's `replaces` take.\n" + "\n".join(lines)
    )


# --- rolling channel summary ---

def _progress(channel_id):
    # Newest message id already covered by the summary (or scanned and found empty).
    row = _summaries.get(channel_id)
    marks = [int(row["up_to_message_id"])] if row else []
    if channel_id in _scanned_to:
        marks.append(_scanned_to[channel_id])
    return max(marks) if marks else None


async def _needs_refresh(channel_id, floor_id):
    if time.monotonic() < _retry_at.get(("refresh", channel_id), 0):
        return False  # the last attempt failed; give it a rest
    if not await _cached(_summaries, channel_id, db.get_channel_summary, "summary"):
        return False
    count, _ = buffered_between(channel_id, _progress(channel_id), floor_id)
    return count >= SUMMARY_MIN_NEW


def _start_refresh(channel, bot_user_id, floor_id, client):
    task = _refreshing.get(channel.id)
    if task is None or task.done():
        task = asyncio.create_task(_refresh(channel, bot_user_id, floor_id, client))
        _refreshing[channel.id] = task
    return task


def _transcript(messages, bot_user_id, bot_name):
    snaps = [_snapshot(m, bot_user_id) for m in messages]
    names = {s["id"]: (bot_name if s["is_self"] else s["author_name"]) for s in snaps}
    lines = []
    for s in snaps:
        text = _render(s, names)
        if text:
            who = f"{bot_name} (you)" if s["is_self"] else s["author_name"]
            lines.append(f"[{s['created_at']:%b %d %H:%M}] {who}: {text}")
    return lines


def _chunks(lines, limit):
    chunk, used = [], 0
    for line in lines:
        if chunk and used + len(line) > limit:
            yield chunk
            chunk, used = [], 0
        chunk.append(line)
        used += len(line) + 1
    if chunk:
        yield chunk


async def _fold(client, previous, lines, skipped):
    note = "\n(Some older messages before these were skipped.)" if skipped else ""
    resp = await client.chat.completions.create(
        model=DEFAULT_MODEL_ID,
        messages=[
            {"role": "system", "content": SUMMARY_INSTRUCTIONS},
            {"role": "user", "content": (
                f"Current memory:\n{previous or '(empty)'}\n\n"
                f"New messages, oldest first (times are UTC):{note}\n" + "\n".join(lines)
            )},
        ],
        # Reasoning tokens count toward the cap; length is set by the instructions.
        max_completion_tokens=4000,
        reasoning_effort="low",
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("empty summary")
    return text


async def _refresh(channel, bot_user_id, floor_id, client):
    started = time.monotonic()
    try:
        after_id = _progress(channel.id)
        floor_time = discord.utils.snowflake_time(floor_id)
        after = discord.Object(after_id) if after_id else floor_time - SUMMARY_FIRST_LOOKBACK
        messages = [m async for m in channel.history(
            limit=SUMMARY_MAX_FETCH, before=discord.Object(floor_id), after=after, oldest_first=False)]
        messages.reverse()
        skipped = bool(after_id) and len(messages) >= SUMMARY_MAX_FETCH

        guild_me = getattr(channel.guild, "me", None)
        lines = _transcript(messages, bot_user_id, guild_me.display_name if guild_me else "ABGLuvr")
        if not lines:
            _scanned_to[channel.id] = floor_id - 1
            return

        row = _summaries.get(channel.id)
        summary = row["summary"] if row else ""
        for i, chunk in enumerate(_chunks(lines, SUMMARY_CHUNK_CHARS)):
            summary = await _fold(client, summary, chunk, skipped and i == 0)

        row = {
            "channel_id": channel.id,
            "guild_id": channel.guild.id,
            "summary": summary,
            "up_to_message_id": floor_id - 1,
            "up_to_at": floor_time.isoformat(),
        }
        _summaries[channel.id] = row  # used this session even if saving fails
        _scanned_to[channel.id] = floor_id - 1
        try:
            await db.upsert_channel_summary(row)
        except Exception as e:
            print(f"[memory] couldn't save summary for {channel.id}: {type(e).__name__}: {e}")
        print(f"[memory] channel {channel.id}: folded {len(lines)} message(s) into the summary "
              f"({len(summary)} chars, {int((time.monotonic() - started) * 1000)}ms)")
    except Exception as e:
        _retry_at[("refresh", channel.id)] = time.monotonic() + LOAD_RETRY_SECONDS
        print(f"[memory] summary refresh failed for {channel.id}: {type(e).__name__}: {e}")


async def refresh_after_reply(message, bot_user_id, client):
    # Called once the reply is sent: start a background refresh if enough has piled up.
    floor_id, _ = window_floor(message, bot_user_id)
    if await _needs_refresh(message.channel.id, floor_id):
        _start_refresh(message.channel, bot_user_id, floor_id, client)


def _summary_section(row):
    through = db.parse_timestamp(row["up_to_at"]).strftime("%b %d, %Y")
    return (
        f"## Earlier in this channel\n"
        f"Summary of the conversation before the recent messages (through {through}). It's a "
        f"lossy recap: trust the recent messages over it, and don't treat it as something "
        f"anyone just said.\n{row['summary']}"
    )


def format_memory_note(facts, summary_row, name_of, requester_id):
    sections = [s for s in (_facts_section(facts, name_of, requester_id),
                            _summary_section(summary_row) if summary_row else None) if s]
    if not sections:
        return None
    return {"role": "system", "content": "# Long-term memory\n\n" + "\n\n".join(sections)}


async def build_memory_note(message, bot_user_id, requester_id, history, client):
    # The developer note that goes in front of the channel window, or None.
    channel_id = message.channel.id
    floor_id, window_size = window_floor(message, bot_user_id)
    if window_size < SUMMARY_INLINE_BELOW and await _needs_refresh(channel_id, floor_id):
        task = _start_refresh(message.channel, bot_user_id, floor_id, client)
        try:
            await asyncio.wait_for(asyncio.shield(task), SUMMARY_INLINE_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"[memory] summary for {channel_id} still running; answering without it")

    facts = await get_facts(message.guild.id) or []
    facts = visible_facts(facts, channel_id)

    summary_row = _summaries.get(channel_id)
    if summary_row:
        reset = reset_cutoff(requester_id, channel_id)
        if reset and reset > db.parse_timestamp(summary_row["up_to_at"]):
            summary_row = None  # the user asked to start fresh

    names = {h["author"]["user_id"]: h["author"]["display_name"] for h in history if h.get("author")}

    def name_of(user_id):
        member = message.guild.get_member(user_id)
        if member:
            return member_label(member)
        return names.get(user_id, f"user {user_id}")

    return format_memory_note(facts, summary_row, name_of, requester_id)


async def get_summary(channel_id):
    # For /memory summary: the stored row, or None.
    if not await _cached(_summaries, channel_id, db.get_channel_summary, "summary"):
        raise MemoryUnavailable()
    return _summaries[channel_id]
