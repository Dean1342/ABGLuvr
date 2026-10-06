import os
import re
import asyncio
import logging
import aiohttp

from utils.integrations.video import VideoDownloadError

# The SDK warns whenever GOOGLE_API_KEY (our CSE key) is also set, even though we
# pass GEMINI_API_KEY explicitly and it takes precedence — keep that out of the logs.
logging.getLogger("google_genai._api_client").setLevel(logging.ERROR)

# YouTube blocks cloud-provider IPs (Heroku), so the bot never fetches from YouTube itself.
# Metadata comes from the YouTube Data API / oEmbed, and Gemini fetches the video server-side.

_GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
# Free-tier models often return 503 "high demand"; quotas are also per model, so fall through these
_GEMINI_FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
_GEMINI_RETRY_PASSES    = 2       # if every model is overloaded, wait briefly and run the chain once more
_GEMINI_RETRY_DELAY_S   = 5
_GEMINI_TIMEOUT_MS    = 480_000   # lite fallback models take ~100–160s for a 20-min video
_GEMINI_MAX_OUTPUT    = 32_768    # ~60 min transcript + visual notes, with headroom

_TRANSCRIBE_PROMPT = (
    "Transcribe all spoken words in this video verbatim, in the original language. "
    "Start directly with the transcript — no introduction or preamble. "
    "Output plain text only — no timestamps, and no speaker labels unless it would be "
    "ambiguous who is speaking. After the transcript, add a line 'Visual notes:' followed by "
    "2–5 short bullet points describing important on-screen content (text, demonstrations, "
    "scenes) that the speech doesn't convey. If there is no speech, write '[No speech]' "
    "and still include the visual notes."
)


def youtube_max_seconds() -> int:
    try:
        return int(os.getenv("YOUTUBE_MAX_MINUTES", "60")) * 60
    except ValueError:
        return 3600


def extract_youtube_id(url: str) -> str | None:
    for pattern in (
        r'youtu\.be/([^?&\s/]+)',
        r'[?&]v=([^&\s]+)',
        r'youtube\.com/(?:shorts|embed|live)/([^?&\s/]+)',
    ):
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _parse_iso_duration(value: str) -> int:
    """Convert an ISO-8601 duration like PT1H2M3S (or P1DT2H) to seconds."""
    m = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', value or "")
    if not m:
        return 0
    d, h, mins, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mins * 60 + s


async def _data_api_metadata(session: aiohttp.ClientSession, video_id: str) -> dict | None:
    """YouTube Data API v3 — gives duration. Returns None if the API is unavailable."""
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return None
    params = {"part": "snippet,contentDetails", "id": video_id, "key": api_key}
    async with session.get("https://www.googleapis.com/youtube/v3/videos", params=params) as resp:
        if resp.status != 200:
            print(f"[youtube] Data API returned HTTP {resp.status} — is YouTube Data API v3 enabled for YOUTUBE_API_KEY's project?")
            return None
        data = await resp.json()

    items = data.get("items") or []
    if not items:
        raise VideoDownloadError("That video is private or unavailable.", "unavailable")

    snippet = items[0].get("snippet", {})
    if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
        raise VideoDownloadError("Live streams and premieres can't be summarized until they've ended.", "unavailable")

    thumbs = snippet.get("thumbnails", {})
    thumb  = next((thumbs[k]["url"] for k in ("high", "medium", "default") if k in thumbs), None)
    return {
        "title":       snippet.get("title", "YouTube Video"),
        "duration":    _parse_iso_duration(items[0].get("contentDetails", {}).get("duration", "")),
        "thumbnail":   thumb,
        "uploader":    snippet.get("channelTitle", ""),
        "webpage_url": _watch_url(video_id),
    }


async def _oembed_metadata(session: aiohttp.ClientSession, video_id: str) -> dict:
    """oEmbed fallback — no key needed, but no duration."""
    fallback = {"title": "YouTube Video", "duration": 0, "thumbnail": None, "uploader": "", "webpage_url": _watch_url(video_id)}
    params = {"url": _watch_url(video_id), "format": "json"}
    try:
        async with session.get("https://www.youtube.com/oembed", params=params) as resp:
            if resp.status != 200:
                return fallback
            data = await resp.json()
    except Exception:
        return fallback
    return {**fallback,
            "title":     data.get("title", "YouTube Video"),
            "thumbnail": data.get("thumbnail_url"),
            "uploader":  data.get("author_name", "")}


async def get_youtube_metadata(video_id: str) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        try:
            metadata = await _data_api_metadata(session, video_id)
        except VideoDownloadError:
            raise
        except Exception as e:
            print(f"[youtube] Data API request failed ({type(e).__name__})")
            metadata = None
        if metadata:
            print(f"[youtube] metadata via Data API: duration={metadata['duration']}s")
            return metadata
        print("[youtube] metadata via oEmbed — duration unknown, length cap not enforced")
        return await _oembed_metadata(session, video_id)


async def transcribe_youtube(video_id: str) -> str:
    """Have Gemini fetch the public YouTube video server-side and return its transcript."""
    from google import genai
    from google.genai import types, errors

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise VideoDownloadError(
            "YouTube summaries aren't configured — the bot operator needs to set GEMINI_API_KEY.",
            "configuration_failure",
        )
    primary = os.getenv("GEMINI_MODEL", "").strip() or _GEMINI_DEFAULT_MODEL
    models  = [primary] + [m for m in _GEMINI_FALLBACK_MODELS if m != primary]

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=_GEMINI_TIMEOUT_MS))
    contents = [
        # Part.from_uri needs a mime type it can't infer from a YouTube URL
        types.Part(
            file_data=types.FileData(file_uri=_watch_url(video_id)),
            media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_LOW,
        ),
        _TRANSCRIBE_PROMPT,
    ]
    config = types.GenerateContentConfig(
        max_output_tokens=_GEMINI_MAX_OUTPUT,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    response = None
    last_error = None
    for attempt in range(_GEMINI_RETRY_PASSES):
        if attempt:
            await asyncio.sleep(_GEMINI_RETRY_DELAY_S)
        for model in models:
            try:
                response = await client.aio.models.generate_content(model=model, contents=contents, config=config)
                print(f"[gemini] model={model} succeeded")
                break
            except errors.APIError as e:
                print(f"[gemini] model={model} error code={e.code} status={e.status}: {str(e.message)[:150]}")
                last_error = e
                # Overloaded (5xx) or out of quota (429, tracked per model) — the next model may work.
                # Anything else is a problem with the request/video itself.
                if e.code != 429 and not (e.code or 0) >= 500:
                    break
            except Exception as e:
                # Timeouts/network failures — don't retry another model after waiting minutes already
                print(f"[gemini] model={model} failed: {type(e).__name__}")
                raise VideoDownloadError(
                    "Gemini took too long or couldn't be reached — try again, or use a shorter video.", "format_failure",
                ) from None
        # Stop on success, or on an error that another pass won't fix
        if response is not None or (last_error and last_error.code != 429 and (last_error.code or 0) < 500):
            break

    if response is None:
        code = last_error.code if last_error else None
        if code == 402:
            raise VideoDownloadError(
                "YouTube summaries are paused — the bot's Gemini billing balance needs a top-up (bot operator).",
                "configuration_failure",
            )
        if code == 429:
            raise VideoDownloadError(
                "Gemini's free usage limit was hit — try again later or use a shorter video.", "access_blocked",
            )
        if code in (400, 403, 404):
            raise VideoDownloadError(
                "Gemini couldn't access that video — it must be public (not private, unlisted, or age-restricted).",
                "unavailable",
            )
        raise VideoDownloadError(
            "Gemini couldn't process that video — it may be private or unavailable, or Gemini is overloaded. "
            "Try again in a bit.", "format_failure",
        )

    usage = response.usage_metadata
    if usage:
        print(f"[gemini] model={model} prompt_tokens={usage.prompt_token_count} output_tokens={usage.candidates_token_count}")

    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned no transcript for this video.")
    return text
