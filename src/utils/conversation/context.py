# Model registry and per-user, per-channel persona/model selections.
# System instructions and personas live in utils/ai/prompts.py; recent conversation
# context comes from the channel itself (utils/conversation/channel_context.py).

# Model definitions. "api" holds capability flags the agent uses when calling the model:
#   reasoning  -> accepts the `reasoning` (effort) parameter
#   web_search -> supports the hosted web_search tool
MODELS = {
    "GPT-4.1": {
        "id": "gpt-4.1-2025-04-14",
        "api": {"reasoning": False, "web_search": True},
        "name": "GPT-4.1",
        "description": "The best model for coding and agentic tasks across domains",
        "reasoning": "●●●●",
        "speed": "●●●",
        "input_cost": "$1.25",
        "cached_input_cost": "$0.13",
        "output_cost": "$10.00",
        "context_window": "1,047,576",
        "max_output": "32,768",
        "knowledge_cutoff": "May 31, 2024"
    },
    "GPT-4.1 Mini": {
        "id": "gpt-4.1-mini-2025-04-14",
        "api": {"reasoning": False, "web_search": True},
        "name": "GPT-4.1 Mini",
        "description": "A faster, cost-efficient version of GPT-4.1 for well-defined tasks",
        "reasoning": "●●●",
        "speed": "●●●●",
        "input_cost": "$0.25",
        "cached_input_cost": "$0.03",
        "output_cost": "$2.00",
        "context_window": "1,047,576",
        "max_output": "32,768",
        "knowledge_cutoff": "May 31, 2024"
    },
    "GPT-4.1 Nano": {
        "id": "gpt-4.1-nano-2025-04-14",
        "api": {"reasoning": False, "web_search": False},
        "name": "GPT-4.1 Nano",
        "description": "Fastest, most cost-efficient version of GPT-4.1",
        "reasoning": "●●",
        "speed": "●●●●●",
        "input_cost": "$0.05",
        "cached_input_cost": "$0.01",
        "output_cost": "$0.40",
        "context_window": "1,047,576",
        "max_output": "32,768",
        "knowledge_cutoff": "May 31, 2024"
    },
    "GPT-5": {
        "id": "gpt-5-2025-08-07",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-5",
        "description": "Fast, highly intelligent model with largest context window",
        "reasoning": "●●●●",
        "speed": "●●●",
        "input_cost": "$2.00",
        "cached_input_cost": "$0.50",
        "output_cost": "$8.00",
        "context_window": "400,000",
        "max_output": "128,000",
        "knowledge_cutoff": "Sep 29, 2024"
    },
    "GPT-5 Mini": {
        "id": "gpt-5-mini-2025-08-07",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-5 Mini",
        "description": "Balanced for intelligence, speed, and cost",
        "reasoning": "●●●",
        "speed": "●●●●",
        "input_cost": "$0.40",
        "cached_input_cost": "$0.10",
        "output_cost": "$1.60",
        "context_window": "400,000",
        "max_output": "128,000",
        "knowledge_cutoff": "May 30, 2024"
    },
    "GPT-5 Nano": {
        "id": "gpt-5-nano-2025-08-07",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-5 Nano",
        "description": "Fastest, most cost-effective GPT-5 model",
        "reasoning": "●●",
        "speed": "●●●●●",
        "input_cost": "$0.10",
        "cached_input_cost": "$0.03",
        "output_cost": "$0.40",
        "context_window": "400,000",
        "max_output": "128,000",
        "knowledge_cutoff": "May 30, 2024"
    },
    "GPT-5.4": {
        "id": "gpt-5.4-2026-03-17",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-5.4",
        "description": "A more affordable model for coding and professional work.",
        "reasoning": "●●●●●",
        "speed": "●●●",
        "input_cost": "$2.50",
        "cached_input_cost": "$0.25",
        "output_cost": "$15.00",
        "context_window": "1,050,000",
        "max_output": "128,000",
        "knowledge_cutoff": "Aug 31, 2025"
    },
    "GPT-5.4 Mini": {
        "id": "gpt-5.4-mini-2026-03-17",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-5.4 Mini",
        "description": "Our strongest mini model yet for coding, computer use, and subagents",
        "reasoning": "●●●●",
        "speed": "●●●●",
        "input_cost": "$0.75",
        "cached_input_cost": "$0.08",
        "output_cost": "$4.50",
        "context_window": "400,000",
        "max_output": "128,000",
        "knowledge_cutoff": "Aug 31, 2025"
    },
    "GPT-5.4 Nano": {
        "id": "gpt-5.4-nano-2026-03-17",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-5.4 Nano",
        "description": "Our cheapest GPT-5.4-class model for simple high-volume tasks",
        "reasoning": "●●●",
        "speed": "●●●●",
        "input_cost": "$0.20",
        "cached_input_cost": "$0.02",
        "output_cost": "$1.25",
        "context_window": "400,000",
        "max_output": "128,000",
        "knowledge_cutoff": "Aug 31, 2025"
    },
    "GPT-6 Luna": {
        # Undated alias: OpenAI may repoint it to newer snapshots.
        # Capabilities verified against the API; display fields below still need filling in.
        "id": "gpt-6-luna",
        "api": {"reasoning": True, "web_search": True},
        "name": "GPT-6 Luna",
        "description": "GPT-6 generation model; the bot's default",
        "reasoning": "—",
        "speed": "—",
        "input_cost": "—",
        "cached_input_cost": "—",
        "output_cost": "—",
        "context_window": "—",
        "max_output": "—",
        "knowledge_cutoff": "—"
    }
}

DEFAULT_MODEL = "GPT-6 Luna"
# Model id for every non-chat call (/tldr, /build helpers).
DEFAULT_MODEL_ID = MODELS[DEFAULT_MODEL]["id"]


def resolve_model_name(name):
    # Map a stored/selected model label to a known one, falling back to the default.
    return name if name in MODELS else DEFAULT_MODEL


user_personas = {}  # (user_id, channel_id) -> persona name
user_models = {}    # (user_id, channel_id) -> model label
