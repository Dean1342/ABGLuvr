import os
import re
import aiohttp

from utils.integrations.video import VideoDownloadError

# YouTube blocks cloud-provider IPs (Heroku), so the bot never fetches from YouTube itself.
# Metadata comes from the YouTube Data API / oEmbed, and Gemini fetches the video server-side
# (utils/media/evidence.py).


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
