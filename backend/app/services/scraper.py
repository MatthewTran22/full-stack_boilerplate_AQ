import base64
import io
from bs4 import BeautifulSoup
from PIL import Image
from playwright.async_api import async_playwright

MAX_SCREENSHOT_DIM = 7000


async def scrape_website(url: str) -> dict:
    """Fetch website HTML using Playwright (avoids 403s) and extract metadata."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"]

    css_texts = []
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            css_texts.append(style_tag.string)

    for tag in soup.find_all(["script", "noscript"]):
        tag.decompose()

    cleaned_html = str(soup)

    return {
        "html": cleaned_html,
        "title": title,
        "description": description,
        "css": "\n".join(css_texts),
    }


async def screenshot_website(url: str) -> str:
    """Take a full-page screenshot using Playwright, return base64 PNG."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        screenshot_bytes = await page.screenshot(full_page=True, type="png")
        await browser.close()

    # Resize if either dimension exceeds the API limit
    img = Image.open(io.BytesIO(screenshot_bytes))
    w, h = img.size
    if w > MAX_SCREENSHOT_DIM or h > MAX_SCREENSHOT_DIM:
        scale = min(MAX_SCREENSHOT_DIM / w, MAX_SCREENSHOT_DIM / h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")
