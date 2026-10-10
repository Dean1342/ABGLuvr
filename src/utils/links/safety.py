# Outbound HTTP for links people post. Anyone in the server can post a URL, so a fetch
# must not reach the bot's own network (SSRF): only http(s) on the standard ports, no
# credentials in the URL, and every hostname (on every redirect hop) must resolve only
# to public addresses. Bodies are streamed under a byte cap, with a total time limit.
#
# Residual risk, accepted for a private bot on a Heroku dyno: httpx resolves the name again
# when it connects, so a DNS answer that changes between our check and the connection
# (rebinding) isn't caught. Heroku dynos don't expose a cloud metadata endpoint.
import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS = 5
MAX_BYTES = 3 * 1024 * 1024
TIMEOUT_SECONDS = 15
# Says who's asking and how to reach the owner: Wikimedia (and others) 403 bots without contact info.
USER_AGENT = "Mozilla/5.0 (compatible; ABGLuvr/1.0; +https://github.com/Dean1342/ABGLuvr)"
_transport = None  # tests swap in an httpx.MockTransport


class LinkError(Exception):
    # code: blocked_address | bad_url | timed_out | too_large | http_<status> | unsupported_type |
    # unreachable | not_found | unavailable. detail is safe to show the model (no secrets).
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass
class Fetched:
    url: str            # final URL after redirects
    status: int
    content_type: str
    body: bytes
    truncated: bool     # hit MAX_BYTES


async def _resolve(host, port):
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return {info[4][0] for info in infos}


async def check_url(url):
    # Raises LinkError unless url is a plain http(s) URL whose host resolves only to public IPs.
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise LinkError("bad_url", "That isn't a valid web address.")
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise LinkError("bad_url", "Only http(s) links can be read.")
    if parts.username or parts.password:
        raise LinkError("bad_url", "Links with a username or password in them aren't read.")
    if port not in (None, 80, 443):
        raise LinkError("blocked_address", "Links to non-standard ports aren't read.")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise LinkError("blocked_address", "That link points at a private address.")
    try:
        addresses = await _resolve(host, port or (443 if parts.scheme.lower() == "https" else 80))
    except (socket.gaierror, UnicodeError, OSError):
        raise LinkError("unreachable", f"The site {host} couldn't be found.")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise LinkError("blocked_address", "That link points at a private address.")


async def safe_get(url, accept=("text/html", "application/xhtml+xml", "text/plain"), headers=None):
    # GET url, following redirects by hand so every hop is checked. Raises LinkError.
    try:
        return await asyncio.wait_for(_get(url, accept, headers or {}), TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise LinkError("timed_out", "The site took too long to respond.")


async def _get(url, accept, headers):
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, transport=_transport,
                                 timeout=httpx.Timeout(TIMEOUT_SECONDS),
                                 headers={"User-Agent": USER_AGENT, "Accept": ", ".join(accept) + ", */*;q=0.5",
                                          **headers}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            try:
                async with client.stream("GET", url) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        url = urljoin(url, resp.headers["location"])
                        continue
                    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if resp.status_code >= 400:
                        raise LinkError(f"http_{resp.status_code}", _status_detail(resp.status_code))
                    if accept and content_type and not any(content_type == a for a in accept):
                        raise LinkError("unsupported_type", f"The link is a {content_type} file, not a page.")
                    body, truncated = bytearray(), False
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            truncated = True
                            del body[MAX_BYTES:]
                            break
                    return Fetched(str(resp.url), resp.status_code, content_type, bytes(body), truncated)
            except httpx.TimeoutException:
                raise LinkError("timed_out", "The site took too long to respond.")
            except httpx.HTTPError as e:
                raise LinkError("unreachable", f"Couldn't connect to the site ({type(e).__name__}).")
        raise LinkError("unreachable", "The link redirected too many times.")


def _status_detail(status):
    if status in (401, 403):
        return f"The site refused to show the page to the bot (HTTP {status}); it may block bots or need a login."
    if status == 404:
        return "The page doesn't exist (HTTP 404)."
    if status == 429:
        return "The site is rate-limiting the bot (HTTP 429)."
    return f"The site returned an error (HTTP {status})."
