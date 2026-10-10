# Research profiles, picked per request without a model call.
#
#   normal    everyday chat and lookups: the configured reasoning effort (AI_REASONING_EFFORT)
#   research  "is this real?", fact checks, comparisons, "look into": at least medium effort
#             and a bigger search context, so the model checks more before answering
#   investigate  deep, multi-step checking (utils/ai/turn.py): high effort, more tool rounds;
#             never picked here, only by the requester (Deep button or /investigate)
import re

PROFILES = {
    "normal": {"min_effort": None, "search_context_size": "medium"},
    "research": {"min_effort": "medium", "search_context_size": "high"},
    # Deep investigation: only after the requester confirms (buttons) or uses /investigate.
    "investigate": {"min_effort": "high", "search_context_size": "high", "max_rounds": 8},
}
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh")

_RESEARCH_RE = re.compile(
    r"\b("
    r"(is|was|are) (this|that|it|he|she|they|any of (this|that)) (actually )?(true|real|legit|accurate|correct|fake|cap|bs)"
    r"|fact[- ]?check|verify|debunk|look into|dig into|research|deep dive|double[- ]check"
    r"|how (accurate|true|legit|real) is|is that right|can you confirm|any (sources|proof|evidence)"
    r"|compare|comparison|versus"
    r")\b",
    re.IGNORECASE,
)


def pick_profile(text: str) -> str:
    return "research" if _RESEARCH_RE.search(text or "") else "normal"


def effort_for(profile: str, configured: str) -> str:
    # The configured effort, raised to the profile's minimum (never lowered).
    floor = PROFILES[profile]["min_effort"]
    if floor and _EFFORT_ORDER.index(floor) > _EFFORT_ORDER.index(configured if configured in _EFFORT_ORDER else "low"):
        return floor
    return configured
