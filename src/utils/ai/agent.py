# Multi-round agent loop on the OpenAI Responses API.
#
#   model(input, tools) -> run any requested function tools -> feed results back -> repeat
#
# until the model answers without calling a tool, or MAX_TOOL_ROUNDS is hit, in which
# case one last call is made with tools disabled so the user always gets an answer.
# Hosted web_search runs on OpenAI's side inside a single call; its citations come back
# as url_citation annotations and are turned into a Sources footer.
#
# Every turn prints one "[ai] {...}" JSON line (tools, rounds, tokens, latency) so bad
# answers can be traced to routing, retrieval, or synthesis.
import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

import openai

from utils.ai.tools import TOOLS, ToolContext, function_specs
from utils.conversation.context import MODELS

MAX_TOOL_ROUNDS = 5
TOOL_TIMEOUT_SECONDS = 20
MAX_SOURCES = 4
REASONING_EFFORT = os.getenv("AI_REASONING_EFFORT", "low")
LOG_TO_DB = os.getenv("AI_LOG_TO_DB") == "1"

_background_tasks = set()  # strong refs so fire-and-forget log writes aren't GC'd mid-flight

ERROR_TEXT = "⚠️ Sorry, I'm having trouble connecting to the AI service right now. Please try again in a bit."
EMPTY_TEXT = "drew a total blank on that one, try asking again"


@dataclass
class AgentResult:
    text: str
    pending_actions: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    failed: bool = False
    log: dict = field(default_factory=dict)


# --- input conversion (stored chat-format history -> Responses input items) ---

def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("text"))
    return str(content) if content else ""


def _user_parts(content):
    # Multimodal parts from utils/ai/multimodal.py use the chat-completions shape.
    if not isinstance(content, list):
        return _text_of(content)
    parts = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" and p.get("text"):
            parts.append({"type": "input_text", "text": p["text"]})
        elif p.get("type") == "image_url":
            image = p.get("image_url") or {}
            if image.get("url"):
                parts.append({"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")})
    return parts


def author_tag(display_name):
    # Speaker label put in front of every user message (current and stored), so the
    # model can tell one person from several.
    return f"[{display_name}]"


def _tagged(content, author):
    if not author:
        return content  # history saved before author tracking
    tag = author_tag(author.get("display_name") or "someone")
    if isinstance(content, list):
        return [{"type": "input_text", "text": tag}] + content
    return f"{tag} {content}"


def to_response_input(history, user_content):
    items = []
    for msg in history:
        role = msg.get("role")
        if role == "system":
            role = "developer"  # conversation summaries, injected TLDR context
        if role not in ("user", "assistant", "developer"):
            continue
        if role == "user":
            content = _user_parts(msg.get("content"))
            content = _tagged(content, msg.get("author")) if content else content
        else:
            content = _text_of(msg.get("content"))
        if content:
            items.append({"role": role, "content": content})
    items.append({"role": "user", "content": _user_parts(user_content) or "(empty message)"})
    return items


# --- sources ---

# Native web search cites inline as "([domain](url))" mid-sentence, which is noisy in
# chat. Those markers are stripped and the sources go in a footer instead.
_INLINE_CITATION_RE = re.compile(r"\s*\(\[[^\]\n]*\]\(https?://[^)\s]+\)\)")


def _clean_url(text):
    # Drop the utm_source=openai tracking param (works on a bare URL or a whole reply).
    return (text.replace("?utm_source=openai&", "?")
                .replace("?utm_source=openai", "")
                .replace("&utm_source=openai", ""))


def _strip_inline_citations(text):
    return _clean_url(_INLINE_CITATION_RE.sub("", text)).strip()


def _collect_sources(response, sources, seen):
    for item in response.output:
        if item.type != "message":
            continue
        for part in item.content:
            for ann in getattr(part, "annotations", None) or []:
                if getattr(ann, "type", None) != "url_citation":
                    continue
                url = _clean_url(ann.url)
                if url in seen:
                    continue
                seen.add(url)
                sources.append({"title": (ann.title or "").strip(), "url": url})


def _short_title(title, limit=70):
    title = " ".join(title.split()).rstrip(".")
    return title if len(title) <= limit else title[:limit].rstrip() + "…"


def _append_sources(text, sources):
    # Skip sources the model already linked inline.
    extra = [s for s in sources if s["url"] not in text][:MAX_SOURCES]
    if not extra:
        return text
    lines = [f"[{_short_title(s['title'])}]({s['url']})" if s["title"] else s["url"] for s in extra]
    return text.rstrip() + "\n\nSources:\n" + "\n".join(lines)


# --- model + tool calls ---

def _truncate(value, limit=200):
    value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return value if len(value) <= limit else value[:limit] + "…"


async def _create(client, model_id, instructions, input_items, opts, tools_enabled):
    kwargs = {
        "model": model_id,
        "instructions": instructions,
        "input": input_items,
        "store": False,
    }
    tools = function_specs() + ([{"type": "web_search"}] if opts["web_search"] else [])
    kwargs["tools"] = tools
    kwargs["parallel_tool_calls"] = True
    if not tools_enabled:
        kwargs["tool_choice"] = "none"
    if opts["reasoning"]:
        kwargs["reasoning"] = {"effort": REASONING_EFFORT}
        # store=False means reasoning items must round-trip encrypted between tool rounds
        kwargs["include"] = ["reasoning.encrypted_content"]
    return await client.responses.create(**kwargs)


async def _run_tool(call, ctx, log):
    tool = TOOLS.get(call.name)
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        args = None

    if tool is None:
        output = f"Error: unknown tool '{call.name}'."
    elif not isinstance(args, dict):
        output = "Error: tool arguments were not valid JSON."
    else:
        try:
            output = await asyncio.wait_for(tool.handler(args, ctx), TOOL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            output = f"Error: {call.name} timed out."
        except Exception as e:
            output = f"Error: {call.name} failed ({type(e).__name__}: {e})."

    log["tools"].append({"name": call.name, "args": _truncate(call.arguments or ""), "ok": not output.startswith("Error")})
    return {"type": "function_call_output", "call_id": call.call_id, "output": output}


def _record_usage(log, response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    log["input_tokens"] += usage.input_tokens or 0
    log["output_tokens"] += usage.output_tokens or 0
    details = getattr(usage, "input_tokens_details", None)
    log["cached_tokens"] += (getattr(details, "cached_tokens", 0) or 0) if details else 0
    details = getattr(usage, "output_tokens_details", None)
    log["reasoning_tokens"] += (getattr(details, "reasoning_tokens", 0) or 0) if details else 0


def _unsupported_feature(error, opts):
    # If a capability flag in MODELS is wrong, the API rejects the request with a 400 that
    # names the parameter. Returns the feature to drop, or None if it's some other error.
    message = str(error).lower()
    if opts["web_search"] and "web_search" in message:
        return "web_search"
    if opts["reasoning"] and "reasoning" in message:
        return "reasoning"
    return None


async def run_agent(client, model_name, instructions, history, user_content, ctx: ToolContext, log_meta=None):
    model = MODELS[model_name]
    model_id = model["id"]
    opts = dict(model.get("api", {"reasoning": False, "web_search": False}))
    input_items = to_response_input(history, user_content)

    log = {
        "request_id": uuid.uuid4().hex[:12],
        **(log_meta or {}),
        "model": model_id,
        "rounds": 0,
        "tools": [],
        "web_searches": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "error": None,
    }
    started = time.monotonic()
    result = AgentResult(text=ERROR_TEXT, failed=True, log=log)

    try:
        response = None
        sources, seen_urls = [], set()
        for round_num in range(MAX_TOOL_ROUNDS + 1):
            tools_enabled = round_num < MAX_TOOL_ROUNDS
            while True:
                try:
                    response = await _create(client, model_id, instructions, input_items, opts, tools_enabled)
                    break
                except openai.BadRequestError as e:
                    feature = _unsupported_feature(e, opts) if round_num == 0 else None
                    if feature is None:
                        raise
                    print(f"[ai] {model_id} rejected {feature}; retrying without it (fix its flag in MODELS)")
                    opts[feature] = False

            log["rounds"] += 1
            _record_usage(log, response)
            _collect_sources(response, sources, seen_urls)
            log["web_searches"] += sum(1 for item in response.output if item.type == "web_search_call")

            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                break
            input_items.extend(response.output)
            input_items.extend(await asyncio.gather(*(_run_tool(c, ctx, log) for c in calls)))

        text = _strip_inline_citations(response.output_text or "") if response is not None else ""
        result = AgentResult(
            text=_append_sources(text, sources) if text else EMPTY_TEXT,
            pending_actions=ctx.pending_actions,
            sources=sources,
            log=log,
        )
    except Exception as e:
        log["error"] = f"{type(e).__name__}: {_truncate(str(e), 300)}"

    log["latency_ms"] = int((time.monotonic() - started) * 1000)
    log["reply"] = _truncate(result.text)
    print("[ai] " + json.dumps(log, ensure_ascii=False))
    if LOG_TO_DB:
        task = asyncio.create_task(_persist_log(log))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return result


async def _persist_log(log):
    from utils.integrations import supabase_client as db  # lazy: optional dependency
    try:
        await db.insert_ai_log(log)
    except Exception as e:
        print(f"[ai] failed to persist log: {type(e).__name__}")
