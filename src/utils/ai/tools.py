# Tool registry for the agent loop (utils/ai/agent.py).
#
# Each Tool pairs a Responses-API function schema with an async handler that returns a
# string result for the model. Hosted tools (web_search) run on OpenAI's side and are
# added by the agent based on the model's capability flags.
#
# Tools that need Discord objects (pinging) don't execute here: they queue a normalized
# pending action on the ToolContext, which bot.py's on_message runs after the reply is
# sent (confirmation gate, scheduling, etc. live in utils/interactions/actions.py).
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from openai import AsyncOpenAI

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


TOOLS = {tool.name: tool for tool in (CONVERT_CURRENCY, PING_USER)}


def function_specs() -> list[dict]:
    return [tool.spec() for tool in TOOLS.values()]
