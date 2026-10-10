# X/Twitter posts through the free fxtwitter API (api.fxtwitter.com/status/<id>): no key,
# no cost; they ask callers to identify themselves in the User-Agent. Gives the text,
# author, time, photos/videos, the quoted post and what it replies to.
#
# It's an unofficial reader of X's own data: deleted, protected or age-gated posts come back
# as unavailable, and it can change without notice. Checked live 2026-10-09 (ROADMAP §8).
import datetime
import json
import time
from dataclasses import dataclass, field

from utils.links import safety
from utils.links.cache import TTLCache

API = "https://api.fxtwitter.com/status/{id}"
_cache = TTLCache(ttl=15 * 60, failure_ttl=2 * 60)


@dataclass
class XPost:
    id: str
    url: str
    text: str
    author_name: str
    author_handle: str
    created_at: datetime.datetime | None
    photos: list = field(default_factory=list)   # [{"url", "alt"}]
    video_count: int = 0
    quote: "XPost | None" = None
    replying_to_handle: str | None = None
    replying_to_id: str | None = None
    community_note: str | None = None
    views: int | None = None
    likes: int | None = None
    reply_count: int = 0

    @property
    def has_video(self):
        return self.video_count > 0


async def fetch_post(post_id) -> XPost:
    # Raises safety.LinkError (not_found / unavailable / ...) when the post can't be read.
    return await _cache.get(str(post_id), lambda: _fetch(str(post_id)))


async def _fetch(post_id):
    started = time.monotonic()
    try:
        fetched = await safety.safe_get(API.format(id=post_id), accept=("application/json",))
        data = json.loads(fetched.body.decode("utf-8", errors="replace"))
    except safety.LinkError as e:
        code = {"http_404": "not_found", "http_401": "unavailable", "http_403": "unavailable"}.get(e.code, e.code)
        detail = {"not_found": "That X post doesn't exist or was deleted.",
                  "unavailable": "That X post is private, age-restricted or otherwise unavailable."}.get(code, e.detail)
        print(f"[links] x post {post_id}: {code} ({int((time.monotonic() - started) * 1000)}ms)")
        raise safety.LinkError(code, detail)
    except (ValueError, UnicodeError):
        raise safety.LinkError("unavailable", "The X reader returned something unreadable.")
    tweet = data.get("tweet") if isinstance(data, dict) else None
    if not tweet:
        raise safety.LinkError("not_found", "That X post doesn't exist or was deleted.")
    post = parse(tweet)
    print(f"[links] x post {post_id}: ok (photos={len(post.photos)}, videos={post.video_count}, "
          f"quote={'yes' if post.quote else 'no'}) {int((time.monotonic() - started) * 1000)}ms")
    return post


def parse(tweet, depth=0) -> XPost:
    # Takes a v1 "tweet" or a v2 "status": the same fields, except v2's replying_to is
    # {"screen_name", "status"} where v1 has replying_to + replying_to_status.
    author = tweet.get("author") or {}
    media = tweet.get("media") or {}
    created = tweet.get("created_timestamp")
    quote = tweet.get("quote")
    note = tweet.get("community_note")
    replying = tweet.get("replying_to")
    if isinstance(replying, dict):
        replying_handle, replying_id = replying.get("screen_name"), replying.get("status")
    else:
        replying_handle, replying_id = replying, tweet.get("replying_to_status")
    return XPost(
        id=str(tweet.get("id") or ""),
        url=f"https://x.com/{author.get('screen_name') or 'i'}/status/{tweet.get('id')}",
        text=(tweet.get("text") or "").strip(),
        author_name=author.get("name") or "",
        author_handle=author.get("screen_name") or "",
        created_at=datetime.datetime.fromtimestamp(created, datetime.timezone.utc) if created else None,
        photos=[{"url": p.get("url"), "alt": p.get("altText") or p.get("alt_text")}
                for p in media.get("photos") or [] if p.get("url")],
        video_count=len(media.get("videos") or []),
        quote=parse(quote, depth + 1) if isinstance(quote, dict) and depth < 1 else None,
        replying_to_handle=replying_handle or None,
        replying_to_id=str(replying_id) if replying_id else None,
        community_note=(note.get("text") if isinstance(note, dict) else note) or None,
        views=tweet.get("views"),
        likes=tweet.get("likes"),
        reply_count=tweet.get("replies") or 0,
    )


# --- context around a post (Stage X): undocumented fxtwitter v2, checked live 2026-10-09 ---
#   /2/thread/<id>        the chain from the conversation's root down to this post (any authors)
#   /2/conversation/<id>  one page (~36) of replies to it, roughly by engagement; the author's
#                         own follow-ups are among them. Later pages came back empty, so a page
#                         is treated as a sample.

THREAD_API = "https://api.fxtwitter.com/2/thread/{id}"
CONVERSATION_API = "https://api.fxtwitter.com/2/conversation/{id}"
_context_cache = TTLCache(ttl=10 * 60, failure_ttl=2 * 60)


async def fetch_parents(post_id) -> list[XPost]:
    # The posts above this one, oldest first (empty for a post that isn't a reply).
    data = await _context_cache.get(("thread", str(post_id)), lambda: _get_json(THREAD_API.format(id=post_id)))
    chain = [parse(item) for item in data.get("thread") or [] if isinstance(item, dict)]
    return [p for p in chain if p.id != str(post_id)]


async def fetch_replies(post_id) -> list[XPost]:
    # One page of replies to this post (and some nested ones), in fxtwitter's order.
    data = await _context_cache.get(("conversation", str(post_id)),
                                    lambda: _get_json(CONVERSATION_API.format(id=post_id)))
    return [parse(item) for item in data.get("replies") or [] if isinstance(item, dict)]


def continuation(post, replies, limit=8) -> list[XPost]:
    # The author's own follow-ups: their reply to this post, then their reply to that, and so on.
    chain, current = [], post.id
    by_parent = {r.replying_to_id: r for r in replies if r.author_handle.lower() == post.author_handle.lower()}
    while current in by_parent and len(chain) < limit:
        current_post = by_parent.pop(current)
        chain.append(current_post)
        current = current_post.id
    return chain


async def _get_json(url):
    started = time.monotonic()
    try:
        fetched = await safety.safe_get(url, accept=("application/json",))
        data = json.loads(fetched.body.decode("utf-8", errors="replace"))
    except safety.LinkError as e:
        print(f"[links] x context {url.rsplit('/', 2)[-2]}: {e.code} ({int((time.monotonic() - started) * 1000)}ms)")
        raise
    except (ValueError, UnicodeError):
        raise safety.LinkError("unavailable", "The X reader returned something unreadable.")
    if not isinstance(data, dict):
        raise safety.LinkError("unavailable", "The X reader returned something unreadable.")
    return data


def clear_cache():
    _cache.clear()
    _context_cache.clear()
