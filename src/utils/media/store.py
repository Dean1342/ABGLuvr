# Evidence cache: what the bot learned from watching a video, saved in Supabase
# media_analyses and found by any id the video is known by. The same video isn't
# downloaded and watched twice within 30 days (/tldr in the other mode, someone else
# posting it, a follow-up question), and concurrent requests for one video share one job.
#
# Keys come from the link before downloading (url_key) and from yt-dlp after (info_key),
# because short links like tiktok.com/t/ZPLjsNRF9 only reveal the video's id once fetched.
# Only complete results are cached; a missing table just means no caching (logged, then
# retried after a pause, like memory.py).
import asyncio
import datetime
import re
import time
from urllib.parse import urlsplit

from utils.integrations import supabase_client as db
from utils.integrations.youtube import extract_youtube_id
from utils.media.evidence import VideoEvidence

VERSION = 1  # bump when the watchers' prompts or schemas change enough that old evidence should be redone
RETENTION = datetime.timedelta(days=30)
_RETRY_SECONDS = 300

_retry_at = 0.0
_inflight: dict[str, asyncio.Future] = {}
_background_tasks = set()  # strong refs so fire-and-forget saves aren't GC'd mid-flight


# --- keys ---

def url_key(url: str) -> str | None:
    # Stable id for a (normalized, un-proxied) post link, ignoring tracking params and
    # www/m. variants. None for sites we don't recognize.
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("mobile.")
    path = parts.path
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        video_id = extract_youtube_id(url)
        return f"youtube:{video_id}" if video_id else None
    if host == "instagram.com":
        m = re.match(r"/(?:reels?|p|tv)/([\w-]+)", path)
        return f"instagram:{m.group(1)}" if m else None
    if host in ("x.com", "twitter.com"):
        m = re.search(r"/status/(\d+)(?:/video/(\d+))?", path)
        if m:
            # /video/1 is the post's only (or first) video; later ones are different videos.
            return f"x:{m.group(1)}" + (f"/{m.group(2)}" if m.group(2) and m.group(2) != "1" else "")
        return None
    if host.endswith("tiktok.com"):
        m = re.search(r"/video/(\d+)", path)
        if m:
            return f"tiktok:{m.group(1)}"
        m = re.match(r"/t/([\w-]+)", path) if host == "tiktok.com" else re.match(r"/([\w-]+)", path)
        return f"tiktok-short:{m.group(1)}" if m else None
    if host.endswith("reddit.com") or host == "redd.it":
        m = re.search(r"/comments/(\w+)", path) or (re.match(r"/(\w+)", path) if host == "redd.it" else None)
        return f"reddit:{m.group(1)}" if m else None
    return None


def info_key(metadata: dict) -> str | None:
    # The id yt-dlp reported after downloading, named like url_key's where the sites match.
    extractor, video_id = (metadata.get("extractor") or "").lower(), metadata.get("id")
    if not extractor or not video_id:
        return None
    prefix = {"twitter": "x"}.get(extractor, extractor)
    return f"{prefix}:{video_id}"


# --- cache ---

def _available() -> bool:
    return time.monotonic() >= _retry_at


def _unavailable(what, error) -> None:
    global _retry_at
    _retry_at = time.monotonic() + _RETRY_SECONDS
    print(f"[media] couldn't {what} the analysis cache ({type(error).__name__}: {error}); "
          "watching without it (run the media_analyses SQL?)")


async def load(keys: list[str]):
    # -> (VideoEvidence, metadata, primary key) for a fresh enough analysis under any of
    # these keys, or None. New keys get added to the row, so the next lookup is direct.
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys or not _available():
        return None
    try:
        row = await db.find_media_analysis(keys, VERSION, datetime.datetime.now(datetime.timezone.utc) - RETENTION)
    except Exception as e:
        _unavailable("read", e)
        return None
    if not row:
        return None
    missing = [k for k in keys if k not in row["keys"]]
    if missing:
        try:
            await db.set_media_keys(row["id"], row["keys"] + missing)
        except Exception as e:
            print(f"[media] couldn't add keys {missing} to analysis {row['id']}: {type(e).__name__}")
    print(f"[media] cache hit: {row['keys'][0]} (analysis {row['id']}, from {row['created_at'][:10]})")
    return VideoEvidence.from_dict(row["evidence"]), row.get("metadata") or {}, row["keys"][0]


def save(keys: list[str], evidence: VideoEvidence, metadata: dict) -> None:
    # Fire-and-forget: a slow or missing table never holds up the reply.
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys or not evidence.complete or not _available():
        return

    async def _save():
        try:
            await db.insert_media_analysis({"keys": keys, "version": VERSION, "metadata": metadata,
                                            "evidence": evidence.to_dict()})
        except Exception as e:
            _unavailable("save to", e)

    task = asyncio.create_task(_save())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def prune() -> None:
    try:
        await db.prune_media_analyses(datetime.datetime.now(datetime.timezone.utc) - RETENTION)
    except Exception as e:
        print(f"[media] couldn't prune the analysis cache: {type(e).__name__}: {e}")


async def shared(key: str | None, work, on_wait=None):
    # Runs work() once per key at a time: a second request for a video that's already
    # being watched waits for that job instead of starting another.
    if not key:
        return await work()
    running = _inflight.get(key)
    if running is not None:
        if on_wait:
            await on_wait("Someone just asked for this video too — waiting for that...")
        return await asyncio.shield(running)
    task = asyncio.ensure_future(work())
    _inflight[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        if _inflight.get(key) is task:
            del _inflight[key]
