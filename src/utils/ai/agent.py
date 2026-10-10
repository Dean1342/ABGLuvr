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
import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import openai

from utils.ai.multimodal import files_as_text, has_files
from utils.ai.tools import TOOLS, ToolContext, function_specs
from utils.conversation.channel_context import person_label
from utils.conversation.context import MODELS
from utils.research.router import PROFILES, effort_for

MAX_TOOL_ROUNDS = 5
TOOL_TIMEOUT_SECONDS = 20
MAX_SOURCES = 4
REASONING_EFFORT = os.getenv("AI_REASONING_EFFORT", "medium")  # main chat model, every reply (Dean, 2026-10-09)
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
        elif p.get("type") == "file" and p.get("data"):
            # A document read natively (multimodal.py): sent inline as base64, so nothing is
            # uploaded and nothing is left behind in OpenAI's file storage.
            encoded = base64.b64encode(p["data"]).decode()
            parts.append({"type": "input_file", "filename": p["filename"],
                          "file_data": f"data:{p.get('mime') or 'application/octet-stream'};base64,{encoded}"})
    return parts


def author_tag(display_name, username=None):
    # Speaker label put in front of every user message (current and stored), so the
    # model can tell one person from several: "[Frogs (@frogs123)]", or "[bob]".
    return f"[{person_label(display_name, username)}]"


def _tagged(content, author):
    if not author:
        return content  # history saved before author tracking
    tag = author_tag(author.get("display_name"), author.get("username"))
    if isinstance(content, list):
        return [{"type": "input_text", "text": tag}] + content
    return f"{tag} {content}"


def to_response_input(history, user_content):
    items = []
    for msg in history:
        role = msg.get("role")
        if role == "system":
            # Only the bot's own framing (TLDR, Videos notes). Anything people wrote goes in
            # as user content (channel_context.BOT_NOTE), never at instruction priority.
            role = "developer"
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

# Native web search cites inline as "([domain](url))" mid-sentence. "inline" (kept after a trial,
# 2026-10-09) turns those into numbered links right where the claim is ("473 hp [1]"),
# one number per URL; "footer" strips them and lists up to MAX_SOURCES under the answer.
CITATION_STYLE = os.getenv("AI_CITATION_STYLE", "inline")
# URLs may hold one level of balanced parentheses (Wikipedia: ".../BMW_M2_(G87)").
_INLINE_CITATION_RE = re.compile(r"\s*\(\[[^\]\n]*\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)\)")
_REPEATED_MARKER_RE = re.compile(r"( \[\[(\d+)\]\]\([^)\s]+\))(?: \[\[\2\]\]\([^)\s]+\))+")


_LINK_CITATION_RE = re.compile(r"\s*\[([lvwg]\d{1,2})\](?!\()")  # l1 links, v1 YouTube, w1 weather, g1 GitHub


def _link_citations(text, cite_urls):
    # "[l1]" markers for links the bot read (inspect_link, pre-read X posts) become the same
    # "([site](url))" form web search uses, so both get one numbering. A ref that wasn't
    # read can't be cited: its marker is dropped. Returns (text, refs dropped).
    dropped = []

    def mark(m):
        url = cite_urls.get(m.group(1))
        if not url:
            dropped.append(m.group(1))
            return ""
        return f" ([{urlsplit(url).hostname or 'link'}]({url}))"

    return _LINK_CITATION_RE.sub(mark, text), dropped


# The model sometimes writes its internal citation markup instead of a [ref]: private-use
# characters around "cite" and an id, e.g. "\ue200cite\ue202turn0forecast0\ue201", which
# Discord shows as "citeturn0forecast0".
_PRIVATE_CITATION_RE = re.compile("\\s*\ue200cite\ue202([^\ue201]*)\ue201")
_PRIVATE_USE_RE = re.compile("[\ue200-\ue2ff]")


def _private_citations(text, cite_urls):
    # Map that markup onto a source this turn registered ("…w1…" -> [w1]; with only one such
    # source, it's that one), else drop it, and strip any stray markers.
    def mark(m):
        ref = re.search(r"\b([lvwg]\d{1,2})\b", m.group(1))
        if ref and ref.group(1) in cite_urls:
            return f" [{ref.group(1)}]"
        if len(cite_urls) == 1:
            return f" [{next(iter(cite_urls))}]"
        return ""
    return _PRIVATE_USE_RE.sub("", _PRIVATE_CITATION_RE.sub(mark, text))


def _number_citations(text):
    # (text with numbered citation links, the cited URLs in number order). Parentheses in
    # a URL are percent-encoded so they can't end the Markdown link early.
    numbers = {}

    def mark(m):
        url = _clean_url(m.group(1))
        n = numbers.setdefault(url, len(numbers) + 1)
        return f" [[{n}]]({url.replace('(', '%28').replace(')', '%29')})"

    text = _REPEATED_MARKER_RE.sub(r"\1", _INLINE_CITATION_RE.sub(mark, text))
    return _clean_url(text).strip(), list(numbers)


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


async def _create(client, model_id, instructions, input_items, opts, tools_enabled, cache_key=None, exclude=()):
    kwargs = {
        "model": model_id,
        "instructions": instructions,
        "input": input_items,
        "store": False,
    }
    if cache_key:
        # Routes a channel's turns to the same prompt cache. channel_context keeps the
        # window's start fixed between trims, so consecutive turns share almost their
        # whole input and it's billed at the cached rate. 24h retention costs nothing extra.
        kwargs["prompt_cache_key"] = cache_key
        kwargs["prompt_cache_retention"] = "24h"
    search = [{"type": "web_search", "search_context_size": opts["search_context_size"]}] if opts["web_search"] else []
    kwargs["tools"] = function_specs(exclude) + search
    kwargs["parallel_tool_calls"] = True
    if not tools_enabled:
        kwargs["tool_choice"] = "none"
    include = []
    if opts["reasoning"]:
        kwargs["reasoning"] = {"effort": opts["effort"]}
        # store=False means reasoning items must round-trip encrypted between tool rounds
        include.append("reasoning.encrypted_content")
    if opts["web_search"]:
        include.append("web_search_call.action.sources")  # what each search consulted, for the log
    if include:
        kwargs["include"] = include
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
            output = await asyncio.wait_for(tool.handler(args, ctx), tool.timeout or TOOL_TIMEOUT_SECONDS)
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


def _record_searches(log, response, consulted):
    # Hosted web search does several things per call: search (with queries), open_page,
    # find_in_page. Count each, and every URL it consulted (cited or not).
    for item in response.output:
        if item.type != "web_search_call":
            continue
        log["web_searches"] += 1
        action = getattr(item, "action", None)
        kind = getattr(action, "type", None)
        if kind == "search":
            queries = getattr(action, "queries", None) or [q for q in [getattr(action, "query", None)] if q]
            log["search_queries"].extend(_truncate(q, 80) for q in queries)
            consulted.update(getattr(s, "url", None) for s in getattr(action, "sources", None) or [])
        elif kind == "open_page":
            log["pages_opened"] += 1
            consulted.add(getattr(action, "url", None))
        elif kind in ("find", "find_in_page"):
            log["finds_in_page"] += 1
    consulted.discard(None)


def _unsupported_feature(error, opts):
    # If a capability flag in MODELS is wrong, the API rejects the request with a 400 that
    # names the parameter. Returns the feature to drop, or None if it's some other error.
    message = str(error).lower()
    if opts["web_search"] and "web_search" in message:
        return "web_search"
    if opts["reasoning"] and "reasoning" in message:
        return "reasoning"
    return None


async def run_agent(client, model_name, instructions, history, user_content, ctx: ToolContext, log_meta=None,
                    research_profile="normal"):
    # research_profile (utils/research/router.py): how hard to think and search this turn.
    model = MODELS[model_name]
    model_id = model["id"]
    opts = dict(model.get("api", {"reasoning": False, "web_search": False}))
    opts["effort"] = effort_for(research_profile, REASONING_EFFORT)
    opts["search_context_size"] = PROFILES[research_profile]["search_context_size"]
    input_items = to_response_input(history, user_content)

    log = {
        "request_id": uuid.uuid4().hex[:12],
        **(log_meta or {}),
        "model": model_id,
        "research_profile": research_profile,
        "reasoning_effort": opts["effort"] if opts["reasoning"] else None,
        "rounds": 0,
        "tools": [],
        "web_searches": 0,
        "search_queries": [],
        "pages_opened": 0,
        "finds_in_page": 0,
        "consulted_sources": 0,
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
        sources, seen_urls, consulted = [], set(), set()
        max_rounds = PROFILES[research_profile].get("max_rounds", MAX_TOOL_ROUNDS)
        for round_num in range(max_rounds + 1):
            tools_enabled = round_num < max_rounds
            while True:
                try:
                    response = await _create(client, model_id, instructions, input_items, opts, tools_enabled,
                                             f"abg-{ctx.channel_id}" if ctx.channel_id else None, ctx.disabled_tools)
                    break
                except openai.BadRequestError as e:
                    if round_num == 0 and has_files(user_content) and "file" in str(e).lower():
                        # The model or API wouldn't take a document natively: the local parsers'
                        # text instead (figures and scanned pages are lost, and the model is told).
                        print(f"[ai] {model_id} rejected a file ({_truncate(str(e), 160)}); retrying with parsed text")
                        log["file_fallback"] = True
                        user_content = await files_as_text(user_content)
                        input_items = to_response_input(history, user_content)
                        continue
                    feature = _unsupported_feature(e, opts) if round_num == 0 else None
                    if feature is None:
                        raise
                    print(f"[ai] {model_id} rejected {feature}; retrying without it (fix its flag in MODELS)")
                    opts[feature] = False

            log["rounds"] += 1
            _record_usage(log, response)
            _collect_sources(response, sources, seen_urls)
            _record_searches(log, response, consulted)

            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                break
            input_items.extend(response.output)
            input_items.extend(await asyncio.gather(*(_run_tool(c, ctx, log) for c in calls)))

        text = (response.output_text or "") if response is not None else ""
        text, dropped = _link_citations(_private_citations(text, ctx.cite_urls), ctx.cite_urls)
        log["consulted_sources"] = len(consulted)
        if dropped:
            log["citation_unknown_refs"] = dropped  # cited a link it never read
        if CITATION_STYLE == "inline":
            text, cited = _number_citations(text)
            log["cited_sources"] = len(cited)
            if not cited:
                # No inline markers to number: list only what the final answer itself cited.
                final, final_seen = [], set()
                _collect_sources(response, final, final_seen)
                text = _append_sources(text, final)
        else:
            read = [{"title": "", "url": u} for u in ctx.cite_urls.values()
                    if f"({u})" in text and all(u != s["url"] for s in sources)]
            text = _append_sources(_strip_inline_citations(text), read + sources)
        result = AgentResult(
            text=text if text else EMPTY_TEXT,
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
