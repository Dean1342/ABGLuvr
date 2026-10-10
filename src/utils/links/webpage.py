# Articles and other HTML pages -> readable text plus metadata, for inspect_link.
# Extraction is plain BeautifulSoup: drop scripts/navigation/boilerplate, prefer <article>
# or <main>, keep headings, list items and table rows readable. Pages that need
# JavaScript, a login or a subscription come back short and are marked partial; nothing
# here ever swaps in a different page.
import json
import re
import time
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from utils.links import safety
from utils.links.cache import TTLCache
from utils.links.urls import site_of

MAX_TEXT_CHARS = 40_000     # ~10k tokens: a long article
SHORT_TEXT_CHARS = 400      # less than this and the page probably didn't render for us
_cache = TTLCache(ttl=10 * 60, failure_ttl=2 * 60)
_DROP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe", "select",
              "button", "template", "dialog")


@dataclass
class Page:
    url: str                 # final URL (after redirects)
    requested_url: str
    site: str
    title: str = ""
    author: str = ""
    published: str = ""
    description: str = ""
    text: str = ""
    access: str = "full"     # full | partial
    notes: list = field(default_factory=list)


async def read_page(url) -> Page:
    # Raises safety.LinkError when the page can't be fetched at all.
    return await _cache.get(url, lambda: _read(url))


async def _read(url):
    started = time.monotonic()
    try:
        fetched = await safety.safe_get(url)
    except safety.LinkError as e:
        print(f"[links] web {site_of(url)}: {e.code} ({int((time.monotonic() - started) * 1000)}ms)")
        raise
    page = extract(fetched.body.decode(_charset(fetched.body), errors="replace"), fetched.url, url,
                   plain=fetched.content_type == "text/plain")
    if fetched.truncated:
        page.notes.append("The page was very large; only its first part was read.")
    print(f"[links] web {page.site}: {page.access} {len(page.text)} chars ({int((time.monotonic() - started) * 1000)}ms)")
    return page


def _charset(body):
    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.IGNORECASE)
    return m.group(1).decode() if m else "utf-8"


def extract(html, final_url, requested_url, plain=False) -> Page:
    page = Page(url=final_url, requested_url=requested_url, site=site_of(final_url))
    if plain:
        page.text = html.strip()
        return _finish(page)
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return " ".join(tag["content"].split())
        return ""

    ld = _json_ld(soup)
    page.title = meta("og:title", "twitter:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    page.description = meta("og:description", "description", "twitter:description")
    page.published = meta("article:published_time", "datePublished", "date") or str(ld.get("datePublished") or "")
    page.author = meta("author", "article:author") or _ld_author(ld)
    page.site = meta("og:site_name") or page.site

    for tag in soup(_DROP_TAGS):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    if len(main.get_text(" ", strip=True)) < SHORT_TEXT_CHARS and soup.body is not None:
        main = soup.body
    for table in main.find_all("table"):
        rows = [" | ".join(c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])) for tr in table.find_all("tr")]
        table.replace_with(soup.new_string("\n" + "\n".join(r for r in rows if r.strip()) + "\n"))
    # Each block becomes one line, so links and bold text inside a sentence don't split it.
    for level in range(1, 7):
        for h in main.find_all(f"h{level}"):
            h.replace_with(soup.new_string(f"\n{'#' * min(level, 4)} {h.get_text(' ', strip=True)}\n"))
    for li in main.find_all("li"):
        li.replace_with(soup.new_string(f"\n- {li.get_text(' ', strip=True)}\n"))
    for block in main.find_all(["p", "blockquote", "figcaption", "dt", "dd", "pre"]):
        block.replace_with(soup.new_string(f"\n{block.get_text(' ', strip=True)}\n"))
    lines = [" ".join(line.split()) for line in main.get_text("\n").split("\n")]
    page.text = "\n".join(_drop_boilerplate([line for line in lines if line]))

    body = ld.get("articleBody")
    if isinstance(body, str) and len(body) > len(page.text):
        page.text = body.strip()  # some sites render with JavaScript but ship the article in JSON-LD
    if ld.get("isAccessibleForFree") in (False, "False", "false"):
        page.notes.append("The site marks this article as subscriber-only, so the text may be cut off.")
    return _finish(page)


def _drop_boilerplate(lines):
    # Widgets repeat short lines ("Learn More", "Starting at", photo credits) and year
    # pickers leave bare numbers; real sentences rarely repeat word for word.
    counts = {}
    for line in lines:
        counts[line] = counts.get(line, 0) + 1
    return [line for line in lines
            if not (len(line) < 80 and counts[line] >= 3) and not re.fullmatch(r"\W*\d{1,4}\W*", line)]


def _finish(page):
    if len(page.text) > MAX_TEXT_CHARS:
        page.text = page.text[:MAX_TEXT_CHARS]
        page.notes.append(f"Only the first {MAX_TEXT_CHARS:,} characters were read.")
    if len(page.text) < SHORT_TEXT_CHARS:
        page.access = "partial"
        page.notes.append("Very little text came through; the page probably needs JavaScript, a login or a "
                          "subscription, so this may not be the actual content.")
    return page


def _json_ld(soup):
    # The first JSON-LD object that looks like an article (or anything with a headline).
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and (item.get("articleBody") or item.get("headline")):
                return item
    return {}


def _ld_author(ld):
    author = ld.get("author")
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        return author.get("name") or ""
    return author if isinstance(author, str) else ""


def clear_cache():
    _cache.clear()
