# Tool registry for the agent loop (utils/ai/agent.py).
#
# Each Tool pairs a Responses-API function schema with an async handler that returns a
# string result for the model. Hosted tools (web_search) run on OpenAI's side and are
# added by the agent based on the model's capability flags.
#
# Tools that need Discord objects (pinging) don't execute here: they queue a normalized
# pending action on the ToolContext, which bot.py's on_message runs after the reply is
# sent (confirmation gate, scheduling, etc. live in utils/interactions/actions.py).
# Memory tools only read/write the fact store, so they run inline; the ids and member
# resolver they need come in on the ToolContext.
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from openai import AsyncOpenAI

from utils.conversation import memory
from utils.integrations.currency import convert_currency, format_conversion
from utils.interactions.actions import (
    get_interaction_function_schemas, build_pending_action, build_ack_instruction,
    build_delivery_instruction
)


@dataclass
class ToolContext:
    client: AsyncOpenAI
    model_id: str
    instructions: str
    reasoning: bool = False
    pending_actions: list = field(default_factory=list)
    # Where the message came from (None outside Discord, e.g. some evals).
    guild_id: int | None = None
    channel_id: int | None = None
    requester_id: int | None = None
    message_id: int | None = None
    resolve_user: Callable[[str], int | None] | None = None  # name or mention -> member id


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], Awaitable[str]]

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": False,
        }


# --- convert_currency ---

async def _convert_currency(args, ctx):
    result = await convert_currency(args["amount"], args["from_currency"], args["to_currency"])
    if result.get("success"):
        result["formatted"] = format_conversion(result)
    return json.dumps(result)


CONVERT_CURRENCY = Tool(
    name="convert_currency",
    description=(
        "Converts an amount of money between currencies using the live exchange rate. "
        "Call it when the user asks to convert a value ('convert 50 USD to EUR', 'how much is 100 euros "
        "in dollars'), or when you need a current rate to answer a question, such as comparing prices "
        "quoted in different currencies (then reason about the comparison yourself).\n\n"
        "Don't call it when currencies are only mentioned in passing, for exchange-rate trends or "
        "economics questions, or when the target currency is unclear (ask instead). The result includes "
        "a 'formatted' line you can reuse when the user just wants the conversion."
    ),
    parameters={
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "The amount to convert"},
            "from_currency": {"type": "string", "description": "Source ISO currency code (e.g. USD, EUR, GBP)"},
            "to_currency": {"type": "string", "description": "Target ISO currency code (e.g. USD, EUR, GBP)"},
        },
        "required": ["amount", "from_currency", "to_currency"],
    },
    handler=_convert_currency,
)


# --- ping_user ---

async def _ping_user(args, ctx):
    # Check-and-queue happens before any await, so parallel calls can't both get through.
    if ctx.pending_actions:
        return ("Error: only one ping action can be set up per message. "
                "Tell the user to send the other request separately.")
    pending = build_pending_action("ping_user", args)
    ctx.pending_actions.append(pending)

    # Craft the ACTUAL message sent to the target in persona voice. Only the note goes in
    # (not the scheduling conversation) so timing/count phrasing can't leak into it.
    # Done now so scheduled sends stay LLM-free. On failure the raw note is used.
    if pending.get("note"):
        try:
            kwargs = {
                "model": ctx.model_id,
                "instructions": ctx.instructions,
                "input": [{"role": "developer", "content": build_delivery_instruction(pending)}],
                "store": False,
            }
            if ctx.reasoning:
                kwargs["reasoning"] = {"effort": "low"}
            resp = await ctx.client.responses.create(**kwargs)
            crafted = (resp.output_text or "").strip().strip('"').strip()
            if crafted:
                pending["delivery_text"] = crafted
        except Exception as e:
            print(f"[tools] ping delivery text failed, using raw note: {type(e).__name__}: {e}")

    # The tool result tells the model how to word its reply, so the agent's final answer
    # doubles as the persona-voiced acknowledgement.
    return build_ack_instruction(pending)


_PING_SCHEMA = get_interaction_function_schemas()[0]

PING_USER = Tool(
    name=_PING_SCHEMA["name"],
    description=_PING_SCHEMA["description"],
    parameters=_PING_SCHEMA["parameters"],
    handler=_ping_user,
)


# --- remember_fact / forget_fact ---

_UNAVAILABLE = "Error: memory storage is unavailable right now, so nothing was saved or deleted. Say so."


async def _remember_fact(args, ctx):
    if ctx.guild_id is None:
        return _UNAVAILABLE
    scope = args.get("scope") or "user"
    subject_ids = []
    if scope == "user":
        about = args.get("about") or ["me"]
        for name in [about] if isinstance(about, str) else about:
            name = str(name).strip()
            if name.lower() in ("me", "myself", "i", "sender"):
                subject_ids.append(ctx.requester_id)
                continue
            user_id = ctx.resolve_user(name) if ctx.resolve_user else None
            if user_id is None:
                return f"Error: couldn't find a server member matching '{name}'. Ask who they mean."
            subject_ids.append(user_id)
    try:
        row, removed, created = await memory.remember(
            ctx.guild_id, scope, args.get("fact"),
            channel_id=ctx.channel_id, subject_ids=subject_ids, added_by=ctx.requester_id,
            source_message_id=ctx.message_id, replaces=args.get("replaces") or [],
        )
    except memory.MemoryUnavailable:
        return _UNAVAILABLE
    except ValueError as e:
        return f"Error: {e}."
    if not created:
        result = f"Already saved as #{row['id']}; nothing new was stored."
    else:
        result = f"Saved as #{row['id']} ({scope})."
    if removed:
        result += " Replaced " + ", ".join(f"#{i}" for i in removed) + "."
    return result + " Confirm briefly in your own voice; don't read the fact back word for word."


REMEMBER_FACT = Tool(
    name="remember_fact",
    description=(
        "Saves a fact to your long-term memory so you still know it in later conversations. "
        "Only call it when someone explicitly asks you to remember, note, or save something "
        "('remember that...', 'don't forget...', 'note that I...'). Never call it on your own just "
        "because someone shared information. Facts already in your memory note are saved; never "
        "save them again.\n\n"
        "Write the fact as a short standalone statement that names who it's about "
        "(\"Dean's M4 is getting downpipes installed Thursday\"), not with 'I' or 'my'. If it updates "
        "or contradicts a fact already in your memory, pass that fact's id in `replaces` so the old "
        "one is removed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "The fact, as one short standalone sentence"},
            "scope": {
                "type": "string",
                "enum": list(memory.SCOPES),
                "description": "user: about one or more specific people. "
                               "server: about the whole group, not anyone in particular (meetups, "
                               "traditions, server rules). channel: only matters in this channel.",
            },
            "about": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For scope=user: everyone the fact involves. 'me' for the sender, otherwise "
                               "their name or mention (e.g. 'X owes me $20' -> ['me', 'X']).",
            },
            "replaces": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Ids of remembered facts this one supersedes",
            },
        },
        "required": ["fact", "scope"],
    },
    handler=_remember_fact,
)


async def _forget_fact(args, ctx):
    if ctx.guild_id is None:
        return _UNAVAILABLE
    ids = [i for i in args.get("fact_ids") or [] if isinstance(i, int)]
    if not ids:
        return "Error: pass the [#id] numbers of the facts to forget."
    try:
        texts = {f["id"]: f["fact"] for f in await memory.get_facts(ctx.guild_id) or []}
        deleted = await memory.forget(ctx.guild_id, ids)
    except memory.MemoryUnavailable:
        return _UNAVAILABLE
    missing = sorted(set(ids) - set(deleted))
    if deleted:
        print(f"[memory] guild {ctx.guild_id}: user {ctx.requester_id} forgot {deleted}")
    parts = []
    if deleted:
        # Spelled out so a wrong deletion is visible in the reply and easy to undo.
        parts.append("Deleted: " + "; ".join(f"#{i} \"{texts.get(i, '?')}\"" for i in deleted) + ". "
                     "Say briefly which fact(s) you forgot so the user can catch a mistake.")
    if missing:
        parts.append("No remembered fact with id " + ", ".join(f"#{i}" for i in missing) + ".")
    return " ".join(parts)


FORGET_FACT = Tool(
    name="forget_fact",
    description=(
        "Deletes facts from your long-term memory by id (the [#id] shown in your memory). Call it "
        "when someone asks you to forget something or says a remembered fact is wrong and doesn't give "
        "a replacement. If it's unclear which fact they mean, ask instead of guessing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "fact_ids": {"type": "array", "items": {"type": "integer"}, "description": "Ids to delete"},
        },
        "required": ["fact_ids"],
    },
    handler=_forget_fact,
)


TOOLS = {tool.name: tool for tool in (CONVERT_CURRENCY, PING_USER, REMEMBER_FACT, FORGET_FACT)}


def function_specs() -> list[dict]:
    return [tool.spec() for tool in TOOLS.values()]
