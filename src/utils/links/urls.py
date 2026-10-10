# Finding URLs in Discord text and deciding what each one is. Embed-fixer domains
# (fixupx.com etc.) map back to the platform they stand in for, so a link and its
# rewritten repost are the same link.
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com", "fixupx.com", "fxtwitter.com",
           "vxtwitter.com", "fixvx.com", "twittpr.com"}
_X_STATUS_RE = re.compile(r"^/(?:[A-Za-z0-9_]{1,15}|i(?:/web)?)/status(?:es)?/(\d{1,25})")

# Handled elsewhere (videos, music) or deliberately not read (Reddit), or not pages at all.
_SKIP_HOSTS = (
    "youtube.com", "youtu.be", "tiktok.com", "tnktok.com", "instagram.com", "kkinstagram.com", "ddinstagram.com",
    "reddit.com", "redd.it", "vxreddit.com", "rxddit.com",
    "spotify.com", "fxspotify.com", "spotify.link",
    "discord.com", "discordapp.com", "discordapp.net", "discord.gg",
    "tenor.com", "giphy.com",
)
_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|igsh|igshid|si|ref_src|ref_url|mc_cid|mc_eid)$", re.IGNORECASE)


def find_urls(text):
    # URLs in text, in order, without trailing punctuation (a closing paren only when unbalanced).
    out = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?*_~")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(".,;:!?")
        out.append(url)
    return out


def _host(url):
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def x_post_id(url):
    # The post id of an X/Twitter status link (any embed-fixer domain), else None.
    if not _matches(_host(url), X_HOSTS):
        return None
    m = _X_STATUS_RE.match(urlsplit(url).path)
    return m.group(1) if m else None


_GITHUB_RESERVED = {"settings", "marketplace", "explore", "topics", "orgs", "notifications", "login", "about",
                    "features", "pricing", "sponsors", "search", "trending", "collections", "apps", "new"}


def github_target(url):
    # What a GitHub link points at, or None: {"owner", "repo", "kind": repo|path|issue|commit,
    # "ref", "path", "number"}. blob/tree refs are taken as one segment (a branch, tag or sha).
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    if host == "raw.githubusercontent.com" and len(segs) >= 4:
        return {"owner": segs[0], "repo": segs[1], "kind": "path", "ref": segs[2], "path": "/".join(segs[3:]),
                "number": None}
    if host not in ("github.com", "www.github.com") or len(segs) < 2 or segs[0].lower() in _GITHUB_RESERVED:
        return None
    owner, repo = segs[0], segs[1].removesuffix(".git")
    target = {"owner": owner, "repo": repo, "kind": "repo", "ref": None, "path": "", "number": None}
    if len(segs) >= 4 and segs[2] in ("tree", "blob"):
        target.update(kind="path", ref=segs[3], path="/".join(segs[4:]))
    elif len(segs) >= 4 and segs[2] in ("issues", "pull") and segs[3].isdigit():
        target.update(kind="issue", number=int(segs[3]))
    elif len(segs) >= 4 and segs[2] == "commit":
        target.update(kind="path", ref=segs[3])
    return target


def classify(url):
    # (kind, canonical url, post id): kind is "x" (an X post), "github" (a repo, file or issue),
    # "web" (a page to read), or None (not ours to read: videos, music, Reddit, Discord, X profiles...).
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, None, None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None, None, None
    host = _host(url)
    post_id = x_post_id(url)
    if post_id:
        return "x", f"https://x.com/i/status/{post_id}", post_id
    if _matches(host, X_HOSTS) or _matches(host, _SKIP_HOSTS):
        return None, None, None
    if github_target(url):
        return "github", urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", "")), None
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _TRACKING_PARAMS.match(k)])
    canonical = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))
    return "web", canonical, None


def site_of(url):
    return _host(url)
