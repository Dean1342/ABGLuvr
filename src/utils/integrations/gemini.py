# Gemini calls for video analysis (utils/media/evidence.py): the client, uploads for
# files too big to send inline, and the model fallback chain.
#
# Free-tier models often return 503 "high demand", and quotas are per model, so a request
# falls through _FALLBACK_MODELS (and the whole chain once more after a short wait)
# before giving up. API errors become VideoDownloadError messages users can read.
import asyncio
import logging
import mimetypes
import os

from utils.integrations.video import VideoDownloadError

# The SDK warns whenever GOOGLE_API_KEY (our CSE key) is also set, even though we
# pass GEMINI_API_KEY explicitly and it takes precedence — keep that out of the logs.
logging.getLogger("google_genai._api_client").setLevel(logging.ERROR)

DEFAULT_MODEL = "gemini-3.8-flash"
_FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
_RETRY_PASSES = 2       # if every model is overloaded, wait briefly and run the chain once more
_RETRY_DELAY_S = 5
TIMEOUT_MS = 480_000    # lite fallback models take ~100–160s for a 20-min video
INLINE_MAX_BYTES = 18 * 1024 * 1024  # inline requests cap near 20 MB; bigger files go through the Files API

_client = None


def models() -> list[str]:
    primary = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    return [primary] + [m for m in _FALLBACK_MODELS if m != primary]


def get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise VideoDownloadError(
                "Video analysis isn't configured — the bot operator needs to set GEMINI_API_KEY.",
                "configuration_failure",
            )
        _client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    return _client


def video_mime(path: str) -> str:
    # Gemini lists video/mov, not the video/quicktime mimetypes guesses for iPhone .mov files.
    ext = os.path.splitext(path)[1].lower()
    return {".mov": "video/mov"}.get(ext) or mimetypes.guess_type(path)[0] or "video/mp4"


async def upload(path: str):
    # Returns the uploaded File once Gemini has finished processing it. Caller deletes it.
    from google.genai import types
    client = get_client()
    uploaded = await client.aio.files.upload(file=path, config=types.UploadFileConfig(mime_type=video_mime(path)))
    while uploaded.state == types.FileState.PROCESSING:
        await asyncio.sleep(2)
        uploaded = await client.aio.files.get(name=uploaded.name)
    if uploaded.state != types.FileState.ACTIVE:
        await delete(uploaded)
        raise VideoDownloadError("Gemini couldn't process that video file.", "format_failure")
    return uploaded


async def delete(uploaded) -> None:
    try:
        await get_client().aio.files.delete(name=uploaded.name)
    except Exception as e:
        print(f"[gemini] couldn't delete uploaded file: {type(e).__name__}")


async def generate(contents, config, *, youtube: bool = False):
    # -> (response, model that answered). Raises VideoDownloadError with a user-facing message.
    from google.genai import errors
    client = get_client()
    chain = models()
    response, used, last_error = None, None, None
    for attempt in range(_RETRY_PASSES):
        if attempt:
            await asyncio.sleep(_RETRY_DELAY_S)
        for model in chain:
            try:
                response = await client.aio.models.generate_content(model=model, contents=contents, config=config)
                used = model
                break
            except errors.APIError as e:
                print(f"[gemini] model={model} error code={e.code} status={e.status}: {str(e.message)[:150]}")
                if e.code == 400 and getattr(config, "service_tier", None) and "tier" in str(e.message).lower():
                    # A fallback model without the priority tier: retry it at the standard tier.
                    config = config.model_copy(update={"service_tier": None})
                    try:
                        response = await client.aio.models.generate_content(model=model, contents=contents, config=config)
                        used = model
                        break
                    except errors.APIError as retry_error:
                        e = retry_error
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
                "Video analysis is paused — the bot's Gemini billing balance needs a top-up (bot operator).",
                "configuration_failure",
            )
        if code == 429:
            raise VideoDownloadError(
                "Gemini's usage limit was hit — try again later or use a shorter video.", "access_blocked",
            )
        if code in (400, 403, 404):
            if youtube:
                raise VideoDownloadError(
                    "Gemini couldn't access that video — it must be public (not private, unlisted, or age-restricted).",
                    "unavailable",
                )
            raise VideoDownloadError("Gemini couldn't read that video file.", "format_failure")
        raise VideoDownloadError(
            "Gemini couldn't process that video — it may be private or unavailable, or Gemini is overloaded. "
            "Try again in a bit.", "format_failure",
        )
    return response, used
