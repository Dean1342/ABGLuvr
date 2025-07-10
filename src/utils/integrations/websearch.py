import httpx
import os
from dotenv import load_dotenv
import asyncio
from playwright.async_api import async_playwright
import openai

load_dotenv()

# Google search and web scraping integration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not GOOGLE_API_KEY or not GOOGLE_CSE_ID or not OPENAI_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY, GOOGLE_CSE_ID, and OPENAI_API_KEY must be set in the environment!")

default_model = "gpt-4.1-mini-2025-04-14"

def truncate_to_token_limit(text, max_tokens, model=default_model):
    # Truncate text to a token limit
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])

async def google_search(query, num_results=5):
    # Perform a Google search using the Custom Search API
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": num_results,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        return [
            {"title": item.get("title", ""), "url": item.get("link", ""), "description": item.get("snippet", "")}
            for item in items
        ]

async def extract_main_text_with_playwright(url):
    # Extract main text from a web page using Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route(
            "**/*",
            lambda route, request: route.abort() if request.resource_type in ["image", "stylesheet", "font"] else route.continue_()
        )
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            content = await page.evaluate('''() => {
                function getText(selector) {
                    return Array.from(document.querySelectorAll(selector)).map(e => e.innerText).join(' ');
                }
                let headings = getText('h1, h2, h3, h4, h5, h6');
                let tableCells = getText('td, th');
                let captions = getText('caption');
                let bodyText = document.body.innerText;
                return [headings, tableCells, captions, bodyText].filter(Boolean).join('\n');
            }''')
        except Exception:
            try:
                content = await page.evaluate('document.body.innerText')
            except Exception:
                content = None
        await browser.close()
        return content

async def fetch_main_text(url):
    # Fetch and extract main text from a URL
    try:
        return await extract_main_text_with_playwright(url)
    except Exception:
        return None

async def web_search_and_summarize(query, openai_api_key, num_results=3):
    # Search the web and summarize results
    results = await google_search(query, num_results=num_results)
    tasks = [fetch_main_text(r["url"]) for r in results]
    main_texts = await asyncio.gather(*tasks)
    combined_text = "\n\n".join([
        f"Source: {r['title']} ({r['url']})\n{text}" for r, text in zip(results, main_texts) if text
    ])
    if not combined_text:
        return "No useful information found."
    combined_text = truncate_to_token_limit(combined_text, max_tokens=1000000, model=default_model)
    return combined_text
