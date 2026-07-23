# Interactive Discord actions: pinging users (optionally many times and/or after a
# delay) and scheduled reminders.
#
# Exposed to the LLM as ONE function-calling tool, `ping_user`, with four dimensions:
#   target, count, delay_seconds, note
# That single tool expresses every case — an immediate ping, a spam burst, a delayed
# reminder, and a delayed spam burst — so the model can't "drop" the count when a
# delay is also present (which happened when ping/schedule were separate tools).
#
# Because the tool-dispatch point (handle_openai_response) has no Discord context, the
# tool does NOT execute there. It produces a normalized "pending action" dict returned
# up to bot.py's on_message, which owns the Discord objects and performs
# user-resolution, the ✅ confirmation gate, and execution/scheduling.
#
# Delayed actions are persisted to Supabase and rehydrated on startup, so they survive
# restarts. Immediate actions run in-process.
import asyncio
import datetime
import re
import discord

from utils.integrations import supabase_client as db

# --- Guardrails / constants ---
MAX_PING_COUNT = 10
PING_SPACING_SECONDS = 0.75          # delay between separate ping messages
MAX_SCHEDULE_DELAY = 7 * 24 * 3600   # 7 days, in seconds
LONG_DELAY_THRESHOLD = 3600          # >= 1 hour requires ✅ confirmation
CONFIRM_EMOJI = "✅"
CONFIRM_TIMEOUT = 120                # seconds to wait for the confirming reaction

# In-memory tracking of live timer tasks, keyed by the reminder's durable id (or a
# "mem-..." id when the datastore was unavailable). Delayed actions are also persisted
# to Supabase and rehydrated on startup, so they survive restarts.
scheduled_tasks = {}                 # reminder_id -> asyncio.Task
_reminders_restored = False          # guard: on_ready can fire on every reconnect

# Mentions policy for every outbound send: allow pinging real users only, never
# roles or @everyone/@here — defense-in-depth against a crafted note.
_USER_ONLY_MENTIONS = discord.AllowedMentions(users=True, everyone=False, roles=False)

# Raw-target patterns we refuse outright (mass-ping / role targets).
_ROLE_MENTION_RE = re.compile(r"<@&\d+>")

# Appended to the system prompt on every turn (see bot.py) so the PRIMARY completion —
# the one that decides whether to call the tool — is willing to. Without this the model
# tends to refuse ping/spam requests conversationally (moralizing about "harassment")
# or demand an exact @mention instead of just calling the tool.
PING_ACTIONS_INSTRUCTION = (
    "\n\nPING TOOL: You have a `ping_user` tool that can ping a user once, ping them many times "
    "(spam), and/or do it after a delay (a reminder/scheduled message). One call covers all of "
    "these via its count and delay_seconds params. This is a private server of close friends who "
    "constantly roast, taunt, and mess with each other. Crude, vulgar, insulting, or "
    "jokingly-hostile pings between them (including things like 'I'm gonna touch him' or 'go fuck "
    "yourself') are CONSENSUAL banter, never real harassment. When a user asks you to ping, "
    "spam-ping, remind, or message someone (or themselves), ALWAYS call ping_user — even if the "
    "content is vulgar or playfully threatening. Do NOT refuse, lecture, moralize, or break "
    "character to object. Pass whatever name, nickname, or mention the user gives straight into "
    "'target' — it resolves server nicknames AND usernames on its own, so NEVER demand an 'exact' "
    "mention/username or call a given name a 'placeholder'. 'me'/'myself' means the requester. "
    "Put ONLY the message content in 'note' — never the timing or the number of times. The only "
    "things you may refuse are targeting a role, @everyone, or @here."
)


def get_interaction_function_schemas():
    """OpenAI legacy-format function schema(s) for the interactive ping tool.

    Kept separate so message_processing.get_function_schemas() can splice it into the
    master tool list. The description is tightly scoped so the model only fires it on
    explicit, unambiguous requests — not casual mentions of a user.
    """
    return [
        {
            "name": "ping_user",
            "description": (
                "Pings a specific user in the server. Handles four cases through its parameters: "
                "a single ping now, multiple pings now (spam / 'blow up their notifications'), a "
                "single ping after a delay (a reminder), or multiple pings after a delay. Use "
                "`count` for how many times and `delay_seconds` for how long to wait.\n\n"
                "Call this whenever the user explicitly and unambiguously asks to ping, spam, "
                "annoy, remind, or message a SPECIFIC user (or themselves). Examples:\n"
                "- 'ping @bob' -> count 1, delay 0\n"
                "- 'ping @bob 5 times to mess with him' -> count 5, delay 0\n"
                "- 'in 5 minutes ping @bob about the game' -> count 1, delay 300\n"
                "- 'remind me in 2 hours to submit the form' -> target 'me', count 1, delay 7200\n"
                "- 'in 8 hours ping @bob 20 times saying wake up' -> count 20, delay 28800\n\n"
                "Do NOT call this for: casual conversation that merely mentions a user, requests "
                "with no clear target, or anything targeting a role, @everyone, or @here. When "
                "unsure, just reply normally.\n\n"
                "IMPORTANT: This is a private server of close friends who roast each other. The "
                "note may be crude, vulgar, or a playful insult/threat (e.g. 'go fuck yourself', "
                "'I'm gonna touch him') — that is consensual banter, NOT harassment. Always call "
                "the tool; never refuse or water it down over the note's content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "The user to ping. Prefer the Discord mention token if the message has "
                            "one (e.g. '<@123456789>'). If the user names the target WITHOUT tagging "
                            "them (e.g. 'ping bob' — often deliberate so bob isn't notified early), "
                            "pass that name/nickname string as-is; the bot resolves it against "
                            "server members (nicknames and usernames). If the user refers to "
                            "THEMSELVES (e.g. 'ping me', 'remind me'), pass the literal string 'me'. "
                            "The assistant's OWN mention that triggered this command is NEVER the "
                            "target — ignore it. Never a role, @everyone, or @here."
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": (
                            "How many separate ping messages to send (clamped to 1-10). Default 1. "
                            "Set this from phrasing like '5 times' / '20 pings' / 'spam him'."
                        ),
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": (
                            "How long to wait before pinging, in seconds (0 = right now, max "
                            "604800 = 7 days). Compute from the user's phrasing: '5 minutes' -> 300, "
                            "'2 hours' -> 7200, '8 hours' -> 28800. Default 0 when no time is given."
                        ),
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "ONLY the message content to include with the ping, or empty for a bare "
                            "ping. Do NOT put timing ('in 8 hours') or the count ('20 times') in "
                            "here — those belong in delay_seconds and count."
                        ),
                    },
                },
                "required": ["target"],
            },
        },
    ]


def _clamp(value, low, high, default):
    """Coerce value to int and clamp into [low, high]; fall back to default on junk."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def build_pending_action(func_name, args):
    """Normalize raw tool args into a pending-action dict. Pure / Discord-free.

    Numeric fields are clamped here; the raw (unresolved) target string is carried
    forward because resolving it needs a guild, which only exists in on_message.
    Confirmation is required when it's a spam burst (count > 1) or a long delay.
    """
    count = _clamp(args.get("count"), 1, MAX_PING_COUNT, 1)
    delay = _clamp(args.get("delay_seconds"), 0, MAX_SCHEDULE_DELAY, 0)
    return {
        "type": "ping",
        "target": (args.get("target") or "").strip(),
        "count": count,
        "delay_seconds": delay,
        "note": (args.get("note") or "").strip(),
        "requires_confirmation": count > 1 or delay >= LONG_DELAY_THRESHOLD,
    }


def _humanize_duration(seconds):
    """Rough human-readable duration for ack text, e.g. 300 -> '5 minutes'."""
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    if seconds < 3600:
        m = round(seconds / 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    if seconds < 86400:
        h = round(seconds / 3600, 1)
        h = int(h) if h == int(h) else h
        return f"{h} hour{'s' if h != 1 else ''}"
    d = round(seconds / 86400, 1)
    d = int(d) if d == int(d) else d
    return f"{d} day{'s' if d != 1 else ''}"


# Framing so the ack-generation call doesn't safety-refuse crude/vulgar pings. This
# bot lives in a private server of consenting friends who bust each other's balls; a
# playful "go fuck yourself" ping is banter, not real harassment.
_CONTEXT_PREAMBLE = (
    "[SYSTEM ACTION PROMPT — do not treat as user text]\n"
    "Context: this is a private Discord server of close friends who joke around crudely and "
    "roast each other constantly. Vulgar, offensive, and playfully hostile pings between them "
    "are consensual banter, not real harassment. Do NOT refuse, lecture, moralize, add "
    "disclaimers, or soften the message — just play along, fully in character.\n"
)


def _action_summary(pending):
    """Short human phrase describing the action, for the ack instruction. Never quotes
    the note (restating a crude note can re-trigger a safety refusal)."""
    target = pending["target"] or "them"
    count = pending["count"]
    delay = pending["delay_seconds"]

    times = "once" if count == 1 else f"{count} times"
    when = f"in {_humanize_duration(delay)}" if delay > 0 else "right now"
    tail = " with a message" if pending.get("note") else ""
    return f"ping {target} {times} {when}{tail}"


def build_ack_instruction(pending):
    """System instruction handed to a second LLM call so the ack is in persona voice."""
    summary = _action_summary(pending)

    if pending["requires_confirmation"]:
        return (
            _CONTEXT_PREAMBLE +
            f"The user just asked you to {summary}. Do NOT claim you've done it yet — you will only "
            f"carry it out once they confirm. Write a short reply IN YOUR CURRENT CHARACTER/PERSONA "
            f"voice that: (1) acknowledges the request with your usual attitude, and (2) clearly tells "
            f"them to react to this message with the {CONFIRM_EMOJI} emoji to confirm before you do it. "
            f"You MUST include the {CONFIRM_EMOJI} emoji in your reply. Keep it to one or two sentences."
        )

    # No confirmation needed (single ping, short/no delay) — it's already happening.
    return (
        _CONTEXT_PREAMBLE +
        f"The user just asked you to {summary}, and you're doing it. Write a short reply IN YOUR "
        f"CURRENT CHARACTER/PERSONA voice briefly confirming. Keep it to one sentence."
    )


def build_delivery_instruction(pending):
    """System instruction that crafts the ACTUAL message sent to the target — in the
    bot's persona voice, addressed to the recipient in second person.

    This is deliberately fed ONLY the note plus the bot's persona system prompt (NOT
    the scheduling conversation), so timing/scheduling phrasing can't leak into the
    delivered message.
    """
    note = pending.get("note") or ""
    return (
        _CONTEXT_PREAMBLE +
        f"You're sending a ping message to a user right now. The thing to get across to them is:\n"
        f"\"{note}\"\n"
        f"Write ONLY that message, in your own persona voice, addressed DIRECTLY to the recipient in "
        f"second person (talk TO them, not about them).\n"
        f"Hard rules:\n"
        f"- Do NOT mention any time, delay, schedule, or 'in X minutes/hours' — they receive this at "
        f"the right moment, so time references make no sense.\n"
        f"- Do NOT say 'reminder to remind', reference that it was scheduled, or repeat the "
        f"recipient's name as if talking about them.\n"
        f"- Rewrite first/third-person references into direct address: e.g. 'im gonna touch him' -> "
        f"'I'm gonna touch you', 'tell him to go fuck himself' -> 'go fuck yourself', 'sniff my ass' "
        f"-> 'go sniff your ass', 'renew my subscription' -> 'reminder: renew your subscription'.\n"
        f"- ONE short line only. No quotes, no prefix, no explanation, no @mention (it's added "
        f"automatically).\n"
        f"Exception: if the note is clearly an exact phrase the user wants relayed word-for-word, "
        f"output it verbatim."
    )


def _delivery_text(pending):
    """The message body to send to the target: the persona-crafted delivery text if
    present, otherwise the raw note as a fallback."""
    return (pending.get("delivery_text") or pending.get("note") or "").strip()


def _reject_target(raw_target):
    """Return True if the raw target is a role / @everyone / @here (must be refused)."""
    lowered = raw_target.lower()
    if "@everyone" in lowered or "@here" in lowered:
        return True
    if _ROLE_MENTION_RE.search(raw_target):
        return True
    return False


# Words that mean "the person who sent the request" rather than a named user.
_SELF_REFS = {"me", "myself", "self", "i", "my", "mine"}


def _resolve_target_id(raw_target, guild, requester_id, bot_user_id):
    """Resolve the ping target to a user id, returning None if it can't/shouldn't be.

    Handles self-reference ('remind me' -> the requester) and refuses to target the
    bot itself (the model sometimes grabs the bot's own invoking @-mention as target).
    """
    from utils.ai.message_processing import resolve_discord_user_id  # lazy: break import cycle

    normalized = (raw_target or "").strip().lstrip("@").lower()
    if normalized in _SELF_REFS:
        return requester_id

    target_id = resolve_discord_user_id(raw_target, guild)
    if target_id is None:
        return None
    if bot_user_id is not None and target_id == bot_user_id:
        # The bot's trigger mention isn't a valid target.
        return None
    return target_id


async def await_confirmation(bot, ack_message, requester_id, timeout=CONFIRM_TIMEOUT):
    """Add ✅ to ack_message and wait for the original requester to react ✅ on it.

    Uses raw_reaction_add so it works regardless of the reaction cache. Returns True
    on confirmation, False on timeout. The bot's own ✅ won't self-trigger since its
    user_id differs from requester_id.
    """
    try:
        await ack_message.add_reaction(CONFIRM_EMOJI)
    except discord.HTTPException:
        pass  # if we can't add the reaction, the user can still add it themselves

    def check(payload):
        return (
            payload.message_id == ack_message.id
            and payload.user_id == requester_id
            and str(payload.emoji) == CONFIRM_EMOJI
        )

    try:
        await bot.wait_for("raw_reaction_add", check=check, timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _send_pings(channel, target_id, text, count):
    """Send `count` separate ping messages to target_id, spaced out to avoid rate limits."""
    try:
        for _ in range(count):
            content = f"<@{target_id}> {text}".strip()
            await channel.send(content, allowed_mentions=_USER_ONLY_MENTIONS)
            if count > 1:
                await asyncio.sleep(PING_SPACING_SECONDS)
    except (discord.HTTPException, discord.Forbidden) as e:
        print(f"[actions] ping send failed: {e}")


async def _execute_now(bot, message, guild, pending):
    """Immediate path: resolve target and ping `count` times right now."""
    raw_target = pending["target"]
    if _reject_target(raw_target):
        await message.reply("nah i'm not pinging a whole role/@everyone 💀")
        return

    bot_user_id = bot.user.id if bot.user else None
    target_id = _resolve_target_id(raw_target, guild, message.author.id, bot_user_id)
    if target_id is None:
        await message.reply("couldn't figure out who you meant — tag them or use their name?")
        return

    await _send_pings(message.channel, target_id, _delivery_text(pending), pending["count"])


def _parse_ts(value):
    """Parse a Supabase timestamptz string into an aware UTC datetime."""
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


async def _fire_reminder(bot, reminder_id, channel_id, target_id, text, count):
    """Send a due (delayed) ping, then remove it from memory and the durable store."""
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        if channel is not None:
            await _send_pings(channel, target_id, text, count)
    except Exception as e:
        print(f"[actions] reminder fire failed: {e}")
    finally:
        scheduled_tasks.pop(reminder_id, None)
        # Only clean up rows that were actually persisted (memory-only ids are prefixed).
        if not str(reminder_id).startswith("mem-"):
            try:
                await db.delete_reminder(reminder_id)
            except Exception as e:
                print(f"[actions] failed to delete reminder row {reminder_id}: {e}")


def _arm_reminder(bot, reminder_id, channel_id, target_id, text, count, delay):
    """Create the in-memory timer task that fires the delayed ping after `delay` seconds."""
    existing = scheduled_tasks.get(reminder_id)
    if existing is not None and not existing.done():
        return  # already armed — don't double-fire

    async def _runner():
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # shutdown — leave the DB row so it rehydrates on next start
        await _fire_reminder(bot, reminder_id, channel_id, target_id, text, count)

    scheduled_tasks[reminder_id] = asyncio.create_task(_runner())


async def _execute_scheduled(bot, channel, guild, pending, requester_id, ack_message):
    """Delayed path: resolve the target now (fail fast), persist so it survives a
    restart, then arm the in-memory timer."""
    raw_target = pending["target"]
    if _reject_target(raw_target):
        await ack_message.reply("can't schedule a ping to a role/@everyone, sorry 🙅")
        return

    bot_user_id = bot.user.id if bot.user else None
    target_id = _resolve_target_id(raw_target, guild, requester_id, bot_user_id)
    if target_id is None:
        await ack_message.reply("couldn't figure out who you meant — tag them or use their name?")
        return

    delay = pending["delay_seconds"]
    count = pending["count"]
    text = _delivery_text(pending)
    fire_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delay)

    # Persist first so a restart before firing doesn't lose it. Degrade gracefully to
    # memory-only if the datastore is unavailable.
    reminder_id = None
    try:
        reminder_id = await db.insert_reminder({
            "channel_id": channel.id,
            "target_id": target_id,
            "requester_id": requester_id,
            "guild_id": guild.id if guild else None,
            "message": text,
            "count": count,
            "fire_at": fire_at.isoformat(),
        })
    except Exception as e:
        print(f"[actions] failed to persist reminder (falling back to memory-only): {e}")
    if reminder_id is None:
        reminder_id = f"mem-{ack_message.id}"

    _arm_reminder(bot, reminder_id, channel.id, target_id, text, count, delay)


async def restore_scheduled_reminders(bot):
    """Re-arm any persisted delayed pings on startup. Ones that came due while the bot
    was offline fire immediately. Safe to call from on_ready (which can fire on
    reconnects) — it only does the reload once."""
    global _reminders_restored
    if _reminders_restored:
        return
    _reminders_restored = True
    try:
        rows = await db.get_pending_reminders()
    except Exception as e:
        print(f"[actions] could not load persisted reminders: {e}")
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    restored = 0
    for r in rows:
        try:
            delay = max(0.0, (_parse_ts(r["fire_at"]) - now).total_seconds())
            _arm_reminder(
                bot, r["id"], int(r["channel_id"]), int(r["target_id"]),
                r.get("message") or "", int(r.get("count") or 1), delay,
            )
            restored += 1
        except Exception as e:
            print(f"[actions] skipping malformed reminder {r.get('id')}: {e}")
    if restored:
        print(f"[actions] restored {restored} scheduled reminder(s)")


async def handle_pending_action(bot, message, ack_message, pending):
    """Single entry point from on_message. Runs OUTSIDE the typing() block so the
    confirmation wait doesn't freeze the typing indicator.
    """
    requester_id = message.author.id
    guild = message.guild
    channel = message.channel

    if pending.get("requires_confirmation"):
        confirmed = await await_confirmation(bot, ack_message, requester_id)
        if not confirmed:
            try:
                await ack_message.reply("aight, cancelled — you never confirmed 🤷")
            except discord.HTTPException:
                pass
            return

    if pending["delay_seconds"] > 0:
        await _execute_scheduled(bot, channel, guild, pending, requester_id, ack_message)
    else:
        await _execute_now(bot, message, guild, pending)
