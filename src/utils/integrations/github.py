# Reading public GitHub repositories, strictly read-only (blueprint Stage G).
#
# REST API for repo info, the commit a branch points to, the file tree and issues/PRs;
# file contents come from raw.githubusercontent.com at a pinned commit, which doesn't count
# against the API rate limit. Only GET requests exist in this module, and private repos are
# refused even if a token could see them.
#
# Rate limit: 60 API calls an hour per IP without a token (an overview costs 3, then files
# are free), 5,000 with GITHUB_TOKEN. Use a fine-grained token with no repository
# permissions beyond public read; it's only sent to api.github.com and never logged.
import os
import time

import httpx

from utils.links.cache import TTLCache

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_FILE_BYTES = 300_000
_cache = TTLCache(ttl=10 * 60, failure_ttl=60)
_pinned = TTLCache(ttl=24 * 3600, failure_ttl=60)  # things addressed by a commit sha never change


class GitHubError(Exception):
    # code: not_found | private | rate_limited | too_large | binary | failed. detail is safe to show.
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def _headers(accept="application/vnd.github+json"):
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "ABGLuvr/1.0 (+https://github.com/Dean1342/ABGLuvr)"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _api(path, params=None, accept="application/vnd.github+json"):
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            resp = await client.get(API + path, params=params, headers=_headers(accept))
    except httpx.HTTPError as e:
        raise GitHubError("failed", f"Couldn't reach GitHub ({type(e).__name__}).")
    remaining = resp.headers.get("x-ratelimit-remaining")
    print(f"[github] GET {path}: {resp.status_code}, {remaining} calls left this hour "
          f"({int((time.monotonic() - started) * 1000)}ms)")
    if resp.status_code in (403, 429) and remaining == "0":
        hint = "" if os.getenv("GITHUB_TOKEN") else " (the bot has no GITHUB_TOKEN, so it gets 60 calls an hour)"
        raise GitHubError("rate_limited", f"GitHub's rate limit for the bot is used up for now{hint}.")
    if resp.status_code == 404:
        raise GitHubError("not_found", "GitHub says that doesn't exist, or it's private.")
    if resp.status_code != 200:
        raise GitHubError("failed", f"GitHub returned an error (HTTP {resp.status_code}).")
    return resp.text if accept.endswith(".sha") else resp.json()


async def get_repo(owner, repo) -> dict:
    data = await _cache.get(("repo", owner.lower(), repo.lower()), lambda: _api(f"/repos/{owner}/{repo}"))
    if data.get("private"):
        raise GitHubError("private", "That repository is private; the bot only reads public ones.")
    return data


async def resolve_commit(owner, repo, ref) -> str:
    # The commit sha a branch/tag/sha points to right now, so everything read is pinned to it.
    return (await _cache.get(("sha", owner.lower(), repo.lower(), ref),
                             lambda: _api(f"/repos/{owner}/{repo}/commits/{ref}",
                                          accept="application/vnd.github.sha"))).strip()


async def get_tree(owner, repo, sha) -> dict:
    # {"entries": [{"path", "type": "blob"|"tree", "size"}], "truncated": bool} for the whole repo.
    async def fetch():
        data = await _api(f"/repos/{owner}/{repo}/git/trees/{sha}", {"recursive": "1"})
        entries = [{"path": e["path"], "type": e["type"], "size": e.get("size")}
                   for e in data.get("tree") or [] if e.get("type") in ("blob", "tree")]
        return {"entries": entries, "truncated": bool(data.get("truncated"))}
    return await _pinned.get(("tree", owner.lower(), repo.lower(), sha), fetch)


async def read_file(owner, repo, sha, path, size=None) -> str:
    # A text file's contents at that commit (raw.githubusercontent.com: not rate limited).
    if size is not None and size > MAX_FILE_BYTES:
        raise GitHubError("too_large", f"{path} is {size:,} bytes; only files up to {MAX_FILE_BYTES:,} are read.")

    async def fetch():
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                resp = await client.get(f"{RAW}/{owner}/{repo}/{sha}/{path}",
                                        headers={"User-Agent": _headers()["User-Agent"]})
        except httpx.HTTPError as e:
            raise GitHubError("failed", f"Couldn't download {path} ({type(e).__name__}).")
        if resp.status_code == 404:
            raise GitHubError("not_found", f"There's no file {path} at that commit.")
        if resp.status_code != 200:
            raise GitHubError("failed", f"Downloading {path} failed (HTTP {resp.status_code}).")
        body = resp.content[:MAX_FILE_BYTES + 1]
        if len(body) > MAX_FILE_BYTES:
            raise GitHubError("too_large", f"{path} is over {MAX_FILE_BYTES:,} bytes; only smaller files are read.")
        if b"\x00" in body[:8000]:
            raise GitHubError("binary", f"{path} is a binary file, not source text.")
        return body.decode("utf-8", errors="replace")
    return await _pinned.get(("file", owner.lower(), repo.lower(), sha, path), fetch)


async def get_issue(owner, repo, number, comments=10) -> dict:
    # An issue or pull request (same endpoint) with its first comments.
    async def fetch():
        issue = await _api(f"/repos/{owner}/{repo}/issues/{number}")
        notes = await _api(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": comments}) \
            if issue.get("comments") else []
        return {"issue": issue, "comments": notes}
    return await _cache.get(("issue", owner.lower(), repo.lower(), int(number)), fetch)


def clear_cache():
    _cache.clear()
    _pinned.clear()
