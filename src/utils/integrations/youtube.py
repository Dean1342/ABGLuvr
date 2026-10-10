import asyncio
import datetime
import os
import re

import aiohttp

from utils.integrations.video import VideoDownloadError
from utils.links.cache import TTLCache

# YouTube blocks cloud-provider IPs (Heroku), so the bot never fetches from YouTube itself.
# Metadata comes from the YouTube Data API / oEmbed, and Gemini fetches the video server-side
# (utils/media/evidence.py). The Data API also gives the details, comments and channel info
# behind the inspect_youtube tool (below).


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


# --- what YouTube says about a video (Stage Y: the inspect_youtube tool) ---
# Data API v3, on the key /tldr already uses. Quota: videos.list, commentThreads.list and
# channels.list cost 1 unit each, out of 10,000 a day (resets at midnight Pacific). Counted
# below so the logs show the day's use; nothing here comes close to the limit.

API = "https://www.googleapis.com/youtube/v3/"
_details_cache = TTLCache(ttl=10 * 60, failure_ttl=2 * 60)
_units = {"day": None, "used": 0}
_PACIFIC = datetime.timezone(datetime.timedelta(hours=-8))  # zoneinfo needs tzdata on Windows; not worth a dependency


class YouTubeError(Exception):
    # code: no_key | not_found | comments_disabled | quota | forbidden | failed. detail is safe to show.
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def _count_unit(endpoint):
    day = datetime.datetime.now(_PACIFIC).date()  # quota day; a fixed offset is close enough for a log line
    if _units["day"] != day:
        _units.update(day=day, used=0)
    _units["used"] += 1
    print(f"[youtube] {endpoint}: 1 unit ({_units['used']} today)")


async def _api(endpoint, params):
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise YouTubeError("no_key", "YouTube info isn't set up (no YOUTUBE_API_KEY).")
    _count_unit(endpoint)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(API + endpoint, params={**params, "key": api_key}) as resp:
                data = await resp.json(content_type=None)
                status = resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise YouTubeError("failed", f"Couldn't reach YouTube ({type(e).__name__}).")
    if status == 200:
        return data
    reason = ((data.get("error") or {}).get("errors") or [{}])[0].get("reason", "") if isinstance(data, dict) else ""
    if reason == "commentsDisabled":
        raise YouTubeError("comments_disabled", "Comments are turned off on that video.")
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        raise YouTubeError("quota", "The bot's YouTube quota for today is used up.")
    if status == 404 or reason in ("videoNotFound", "channelNotFound"):
        raise YouTubeError("not_found", "YouTube says that video doesn't exist or is private.")
    raise YouTubeError("forbidden" if status == 403 else "failed", f"YouTube refused the request (HTTP {status}).")


def _count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None  # hidden (likes, subscribers) or not reported


async def get_video_details(video_id: str) -> dict:
    # Title, channel, date, stats, description and tags. Raises YouTubeError.
    async def fetch():
        data = await _api("videos", {"part": "snippet,statistics,contentDetails", "id": video_id})
        items = data.get("items") or []
        if not items:
            raise YouTubeError("not_found", "YouTube says that video doesn't exist or is private.")
        snippet, stats = items[0].get("snippet", {}), items[0].get("statistics", {})
        return {
            "id": video_id,
            "url": _watch_url(video_id),
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "published_at": snippet.get("publishedAt", ""),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags") or [],
            "duration": _parse_iso_duration(items[0].get("contentDetails", {}).get("duration", "")),
            "live": snippet.get("liveBroadcastContent", "none"),
            "views": _count(stats.get("viewCount")),
            "likes": _count(stats.get("likeCount")),
            "comments": _count(stats.get("commentCount")),
        }
    return await _details_cache.get(("video", video_id), fetch)


async def get_comments(video_id: str, order: str = "relevance", limit: int = 20) -> list[dict]:
    # Top-level comments (YouTube's "top" order by default, or "time"), each with the few
    # replies the API includes. Raises YouTubeError (comments_disabled, not_found, quota...).
    async def fetch():
        data = await _api("commentThreads", {"part": "snippet,replies", "videoId": video_id, "order": order,
                                             "maxResults": limit, "textFormat": "plainText"})
        threads = []
        for item in data.get("items") or []:
            snippet = item.get("snippet", {})
            top = snippet.get("topLevelComment", {}).get("snippet", {})
            threads.append({
                **_comment(top),
                "reply_count": snippet.get("totalReplyCount", 0),
                "replies": [_comment(r.get("snippet", {})) for r in (item.get("replies") or {}).get("comments", [])],
            })
        return threads
    return await _details_cache.get(("comments", video_id, order, limit), fetch)


def _comment(s):
    return {"author": s.get("authorDisplayName", ""), "author_channel_id": (s.get("authorChannelId") or {}).get("value"),
            "text": s.get("textOriginal") or s.get("textDisplay") or "", "likes": s.get("likeCount", 0),
            "published_at": s.get("publishedAt", "")}


async def get_channel(channel_id: str) -> dict:
    async def fetch():
        data = await _api("channels", {"part": "snippet,statistics", "id": channel_id})
        items = data.get("items") or []
        if not items:
            raise YouTubeError("not_found", "YouTube couldn't find that channel.")
        snippet, stats = items[0].get("snippet", {}), items[0].get("statistics", {})
        return {"title": snippet.get("title", ""), "handle": snippet.get("customUrl", ""),
                "description": snippet.get("description", ""), "created_at": snippet.get("publishedAt", ""),
                "subscribers": None if stats.get("hiddenSubscriberCount") else _count(stats.get("subscriberCount")),
                "videos": _count(stats.get("videoCount")), "views": _count(stats.get("viewCount"))}
    return await _details_cache.get(("channel", channel_id), fetch)


def clear_cache():
    _details_cache.clear()


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
