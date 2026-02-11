import base64
import io
import re
from bs4 import BeautifulSoup
from PIL import Image
from playwright.async_api import async_playwright

MAX_SCREENSHOT_DIM = 7000
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720


async def scrape_and_capture(url: str) -> dict:
    """
    Scrape a website: get HTML, extract image URLs, and take sequential
    viewport-sized screenshots scrolling from top to bottom.

    Returns:
        {
            "html": str,           # cleaned HTML source
            "screenshots": [str],  # list of base64 PNG screenshots (viewport chunks)
            "image_urls": [str],   # extracted image URLs from the page
        }
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Get HTML
        html = await page.content()

        # Get total page height
        total_height = await page.evaluate("document.body.scrollHeight")

        # Take viewport-sized screenshots scrolling top to bottom
        screenshots_b64 = []
        scroll_y = 0
        while scroll_y < total_height:
            await page.evaluate(f"window.scrollTo(0, {scroll_y})")
            await page.wait_for_timeout(300)
            screenshot_bytes = await page.screenshot(type="png")
            screenshots_b64.append(_resize_screenshot(screenshot_bytes))
            scroll_y += VIEWPORT_HEIGHT

        await browser.close()

    # Parse HTML and extract image URLs
    soup = BeautifulSoup(html, "html.parser")
    image_urls = _extract_image_urls(soup, url)

    # Clean HTML (remove scripts)
    for tag in soup.find_all(["script", "noscript"]):
        tag.decompose()
    cleaned_html = str(soup)

    return {
        "html": cleaned_html,
        "screenshots": screenshots_b64,
        "image_urls": image_urls,
    }


def _extract_image_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Extract all image URLs from the page."""
    urls = set()

    # <img src="...">
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            urls.add(src)

    # CSS background images
    for tag in soup.find_all(style=True):
        style = tag["style"]
        for match in re.findall(r'url\(["\']?(.*?)["\']?\)', style):
            urls.add(match)

    # <source srcset="...">
    for source in soup.find_all("source"):
        srcset = source.get("srcset", "")
        for part in srcset.split(","):
            url_part = part.strip().split(" ")[0]
            if url_part:
                urls.add(url_part)

    # Filter out data URIs and tiny icons
    filtered = []
    for u in urls:
        if u.startswith("data:"):
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            # Make relative URLs absolute
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            u = f"{parsed.scheme}://{parsed.netloc}{u}"
        filtered.append(u)

    return filtered


def _resize_screenshot(screenshot_bytes: bytes) -> str:
    """Resize screenshot if needed and return base64."""
    img = Image.open(io.BytesIO(screenshot_bytes))
    w, h = img.size
    if w > MAX_SCREENSHOT_DIM or h > MAX_SCREENSHOT_DIM:
        scale = min(MAX_SCREENSHOT_DIM / w, MAX_SCREENSHOT_DIM / h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def screenshot_website(url: str) -> str:
    """Take a full-page screenshot using Playwright, return base64 PNG (legacy)."""
    result = await scrape_and_capture(url)
    return result["screenshots"][0] if result["screenshots"] else ""
