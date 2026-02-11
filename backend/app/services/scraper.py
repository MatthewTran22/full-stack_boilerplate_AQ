import base64
import io
import re
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from PIL import Image
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MAX_SCREENSHOT_DIM = 7000
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
MAX_SCREENSHOTS = 8  # Cap to avoid huge AI payloads


# JS to extract computed styles, fonts, colors from the live page
EXTRACT_STYLES_JS = """
() => {
    const result = { fonts: new Set(), colors: new Set(), gradients: [] };
    const els = document.querySelectorAll('body *');

    for (const el of els) {
        const cs = getComputedStyle(el);

        const ff = cs.fontFamily;
        if (ff) {
            ff.split(',').forEach(f => {
                const clean = f.trim().replace(/['"]/g, '');
                if (clean && !['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui',
                    '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Helvetica Neue', 'Arial', 'Noto Sans'].includes(clean)) {
                    result.fonts.add(clean);
                }
            });
        }

        const color = cs.color;
        const bg = cs.backgroundColor;
        const borderColor = cs.borderColor;
        if (color && color !== 'rgba(0, 0, 0, 0)') result.colors.add(color);
        if (bg && bg !== 'rgba(0, 0, 0, 0)') result.colors.add(bg);
        if (borderColor && borderColor !== 'rgba(0, 0, 0, 0)' && borderColor !== 'rgb(0, 0, 0)') result.colors.add(borderColor);

        const bgImage = cs.backgroundImage;
        if (bgImage && bgImage !== 'none' && bgImage.includes('gradient')) {
            result.gradients.push(bgImage);
        }
    }

    return {
        fonts: [...result.fonts],
        colors: [...result.colors].slice(0, 40),
        gradients: result.gradients.slice(0, 10),
    };
}
"""

# JS to extract font/icon CDN links
EXTRACT_FONT_LINKS_JS = """
() => {
    const links = [];
    document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
        const href = link.href || '';
        if (href.includes('fonts.googleapis.com') || href.includes('fonts.gstatic.com') ||
            href.includes('typekit') || href.includes('fontawesome') ||
            href.includes('cdnjs.cloudflare.com') || href.includes('cdn.jsdelivr.net')) {
            links.push(href);
        }
    });
    return links;
}
"""

# JS to extract icon font classes
EXTRACT_ICONS_JS = """
() => {
    const icons = { fontAwesome: [], materialIcons: [], customIconClasses: [] };
    const els = document.querySelectorAll('[class]');

    for (const el of els) {
        const classes = el.className;
        if (typeof classes !== 'string') continue;

        const faMatches = classes.match(/\\bfa[srbl]?\\s+fa-[\\w-]+/g);
        if (faMatches) faMatches.forEach(m => icons.fontAwesome.push(m));

        if (classes.includes('material-icons') || classes.includes('material-symbols')) {
            icons.materialIcons.push(el.textContent?.trim() || '');
        }

        const iconMatches = classes.match(/\\b(?:icon|bi|ri|ti|feather)-[\\w-]+/g);
        if (iconMatches) iconMatches.forEach(m => icons.customIconClasses.push(m));
    }

    return {
        fontAwesome: [...new Set(icons.fontAwesome)].slice(0, 50),
        materialIcons: [...new Set(icons.materialIcons)].slice(0, 50),
        customIconClasses: [...new Set(icons.customIconClasses)].slice(0, 30),
    };
}
"""

# JS to extract RENDERED SVGs from the live DOM (resolves <use> refs, gets actual content)
EXTRACT_RENDERED_SVGS_JS = """
() => {
    const results = [];
    const svgs = document.querySelectorAll('svg');

    for (const svg of svgs) {
        // Skip hidden/zero-size SVGs
        const rect = svg.getBoundingClientRect();
        if (rect.width < 2 && rect.height < 2) continue;

        // Check if this SVG is in the header/nav area (likely a logo)
        let isLogo = false;
        let el = svg;
        let ancestorInfo = '';
        while (el && el !== document.body) {
            const tag = el.tagName?.toLowerCase();
            const cls = el.className && typeof el.className === 'string' ? el.className : '';
            const id = el.id || '';
            const searchable = (tag + ' ' + cls + ' ' + id).toLowerCase();

            if (tag === 'nav' || tag === 'header' || searchable.includes('nav') || searchable.includes('header')) {
                isLogo = true;
                ancestorInfo = tag + '.' + cls;
                break;
            }
            if (searchable.includes('logo') || searchable.includes('brand')) {
                isLogo = true;
                ancestorInfo = tag + '.' + cls;
                break;
            }
            el = el.parentElement;
        }

        // Also flag SVGs near the top of the page as likely logos
        if (rect.top < 100 && rect.width > 20 && rect.width < 400) {
            isLogo = true;
        }

        // Check if SVG uses <use> refs — if so, try to resolve them
        let markup = svg.outerHTML;
        const useEls = svg.querySelectorAll('use');
        if (useEls.length > 0) {
            // Clone the SVG and inline the referenced content
            const clone = svg.cloneNode(true);
            for (const use of clone.querySelectorAll('use')) {
                const href = use.getAttribute('href') || use.getAttribute('xlink:href') || '';
                if (href.startsWith('#')) {
                    const refEl = document.querySelector(href);
                    if (refEl) {
                        // Replace <use> with the actual content
                        const content = refEl.cloneNode(true);
                        // If it's a <symbol>, extract its children and viewBox
                        if (content.tagName === 'symbol') {
                            const vb = content.getAttribute('viewBox');
                            if (vb && !clone.getAttribute('viewBox')) {
                                clone.setAttribute('viewBox', vb);
                            }
                            while (content.firstChild) {
                                use.parentNode.insertBefore(content.firstChild, use);
                            }
                        } else {
                            use.parentNode.insertBefore(content, use);
                        }
                        use.remove();
                    }
                }
            }
            markup = clone.outerHTML;
        }

        // Truncate very large SVGs
        if (markup.length > 5000) {
            markup = markup.substring(0, 5000) + '<!-- truncated -->';
        }

        results.push({
            markup: markup,
            isLogo: isLogo,
            ancestorInfo: ancestorInfo,
            width: rect.width,
            height: rect.height,
            top: rect.top,
            ariaLabel: svg.getAttribute('aria-label') || '',
            classes: (typeof svg.className === 'string' ? svg.className : svg.className?.baseVal) || '',
            viewBox: svg.getAttribute('viewBox') || '',
        });
    }

    // Sort: logos first, then by position on page
    results.sort((a, b) => {
        if (a.isLogo !== b.isLogo) return a.isLogo ? -1 : 1;
        return a.top - b.top;
    });

    return results.slice(0, 30);
}
"""

# JS to extract logo images (broadened detection — position-based + ancestor-based)
EXTRACT_LOGO_IMAGES_JS = """
() => {
    const logos = [];
    const imgs = document.querySelectorAll('img');

    for (const img of imgs) {
        const rect = img.getBoundingClientRect();
        const src = img.src || img.dataset?.src || '';
        if (!src || src.startsWith('data:')) continue;

        let isLogo = false;
        let reason = '';

        // Check attributes
        const searchable = (src + ' ' + (img.alt || '') + ' ' + (img.className || '') + ' ' + (img.id || '')).toLowerCase();
        if (searchable.includes('logo') || searchable.includes('brand')) {
            isLogo = true;
            reason = 'attribute match';
        }

        // Check ancestor chain (nav, header, logo containers)
        let el = img.parentElement;
        let depth = 0;
        while (el && el !== document.body && depth < 8) {
            const tag = el.tagName?.toLowerCase();
            const cls = typeof el.className === 'string' ? el.className.toLowerCase() : '';
            const id = (el.id || '').toLowerCase();
            const all = tag + ' ' + cls + ' ' + id;

            if (all.includes('logo') || all.includes('brand')) {
                isLogo = true;
                reason = 'ancestor has logo/brand';
                break;
            }
            if (tag === 'nav' || tag === 'header' || cls.includes('nav') || cls.includes('header')) {
                isLogo = true;
                reason = 'inside nav/header';
                break;
            }
            // Link to homepage is often the logo container
            if (tag === 'a') {
                const href = el.getAttribute('href') || '';
                if (href === '/' || href === '#' || href.endsWith('.com') || href.endsWith('.com/') || href.endsWith('.io') || href.endsWith('.io/')) {
                    isLogo = true;
                    reason = 'link to homepage';
                    break;
                }
            }
            el = el.parentElement;
            depth++;
        }

        // Position: images near top of page and reasonable size
        if (!isLogo && rect.top < 80 && rect.width > 20 && rect.width < 400 && rect.height < 200) {
            isLogo = true;
            reason = 'top of page';
        }

        if (isLogo) {
            logos.push({
                url: src,
                alt: img.alt || '',
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                reason: reason,
            });
        }
    }

    return logos;
}
"""


async def scrape_and_capture(url: str) -> dict:
    """
    Full Playwright scrape: HTML, viewport screenshots, image URLs,
    computed styles, font/icon links, rendered SVGs, logos.
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

        # Extract computed styles
        try:
            styles = await page.evaluate(EXTRACT_STYLES_JS)
        except Exception:
            styles = {"fonts": [], "colors": [], "gradients": []}

        # Extract font/icon CDN links
        try:
            font_links = await page.evaluate(EXTRACT_FONT_LINKS_JS)
        except Exception:
            font_links = []

        # Extract icon usage
        try:
            icons = await page.evaluate(EXTRACT_ICONS_JS)
        except Exception:
            icons = {"fontAwesome": [], "materialIcons": [], "customIconClasses": []}

        # Extract RENDERED SVGs (resolves <use> refs in the live DOM)
        try:
            svgs = await page.evaluate(EXTRACT_RENDERED_SVGS_JS)
        except Exception as e:
            logger.warning(f"SVG extraction failed: {e}")
            svgs = []

        # Extract logo images (position + ancestor based)
        try:
            logos = await page.evaluate(EXTRACT_LOGO_IMAGES_JS)
        except Exception as e:
            logger.warning(f"Logo extraction failed: {e}")
            logos = []

        # Get total page height
        total_height = await page.evaluate("document.body.scrollHeight")

        # Take viewport-sized screenshots scrolling top to bottom (capped)
        screenshots_b64 = []
        scroll_y = 0
        while scroll_y < total_height and len(screenshots_b64) < MAX_SCREENSHOTS:
            await page.evaluate(f"window.scrollTo(0, {scroll_y})")
            await page.wait_for_timeout(300)
            screenshot_bytes = await page.screenshot(type="png")
            screenshots_b64.append(_resize_screenshot(screenshot_bytes))
            scroll_y += VIEWPORT_HEIGHT
        if total_height > VIEWPORT_HEIGHT * MAX_SCREENSHOTS:
            logger.info(f"Capped at {MAX_SCREENSHOTS} screenshots (page height: {total_height}px)")

        await browser.close()

    # Parse HTML for image URLs
    soup = BeautifulSoup(html, "html.parser")
    image_urls = _extract_image_urls(soup, url)

    # Aggressively clean HTML — keep only structural/semantic content
    cleaned_html = _clean_html(soup)

    logo_svgs = len([s for s in svgs if s.get("isLogo")])
    logger.info(
        f"Scraped {url}: {len(screenshots_b64)} screenshots, {len(image_urls)} images, "
        f"{len(svgs)} SVGs ({logo_svgs} logos), {len(logos)} logo images, "
        f"{len(styles.get('fonts', []))} fonts"
    )

    return {
        "html": cleaned_html,
        "screenshots": screenshots_b64,
        "image_urls": image_urls,
        "styles": styles,
        "font_links": font_links,
        "icons": icons,
        "svgs": svgs,
        "logos": logos,
    }


def _extract_image_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Extract all image URLs from the page."""
    urls = set()
    parsed = urlparse(base_url)

    for img in soup.find_all("img"):
        for attr in ["src", "data-src", "data-lazy-src"]:
            src = img.get(attr)
            if src:
                urls.add(src)

    for tag in soup.find_all(["img", "source"]):
        srcset = tag.get("srcset", "")
        for part in srcset.split(","):
            url_part = part.strip().split(" ")[0]
            if url_part:
                urls.add(url_part)

    for tag in soup.find_all(style=True):
        style = tag["style"]
        for match in re.findall(r'url\(["\']?(.*?)["\']?\)', style):
            urls.add(match)

    for link in soup.find_all("link", rel=True):
        rels = link.get("rel", [])
        if any(r in rels for r in ["icon", "shortcut", "apple-touch-icon"]):
            href = link.get("href")
            if href:
                urls.add(href)

    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        urls.add(og_img["content"])

    filtered = []
    for u in urls:
        if u.startswith("data:"):
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = f"{parsed.scheme}://{parsed.netloc}{u}"
        elif not u.startswith("http"):
            u = f"{parsed.scheme}://{parsed.netloc}/{u}"
        filtered.append(u)

    return filtered


def _clean_html(soup: BeautifulSoup) -> str:
    """Convert full HTML into a compact structural skeleton.

    The AI already gets screenshots for visual accuracy and separate extractions
    for logos, SVGs, icons, fonts, and colors. The HTML just needs to convey
    page structure: tag hierarchy, classes, text content, and image placements.

    This typically shrinks HTML from 200k+ chars down to 10-20k.
    """
    SKIP_TAGS = {
        "script", "noscript", "style", "link", "meta", "head",
        "iframe", "object", "embed", "template",
    }
    # Attributes worth keeping for structural understanding
    KEEP_ATTRS = {"class", "href", "src", "alt", "id", "role", "aria-label", "type", "placeholder"}
    # Tags that are just wrappers — include children but skip the tag itself if no useful attrs
    WRAPPER_TAGS = {"div", "span"}
    # Max text content per element (truncate long paragraphs)
    MAX_TEXT = 80
    # Max repeated siblings of the same type (e.g., 50 product cards → keep 2)
    MAX_REPEAT = 2

    lines = []
    _seen_repeat: dict[str, int] = {}

    def _process(tag, depth=0):
        if not hasattr(tag, "name") or tag.name is None:
            # Text node
            text = tag.strip()
            if text:
                truncated = text[:MAX_TEXT] + ("..." if len(text) > MAX_TEXT else "")
                lines.append("  " * depth + truncated)
            return

        if tag.name in SKIP_TAGS:
            return

        # Skip hidden elements
        style = tag.get("style", "")
        if isinstance(style, str) and re.search(r"display\s*:\s*none", style, re.I):
            return
        if tag.get("hidden"):
            return

        # SVG — replace with a placeholder (full SVGs come from Playwright extraction)
        if tag.name == "svg":
            cls = tag.get("class", [])
            cls_str = " ".join(cls) if isinstance(cls, list) else str(cls)
            label = tag.get("aria-label", "")
            desc = f'<svg class="{cls_str}"' if cls_str else "<svg"
            if label:
                desc += f' aria-label="{label}"'
            desc += "/>"
            lines.append("  " * depth + desc)
            return

        # Deduplicate repeated siblings (e.g., 50 list items → keep 2 + note)
        parent_key = f"{id(tag.parent)}:{tag.name}"
        count = _seen_repeat.get(parent_key, 0)
        _seen_repeat[parent_key] = count + 1
        if count == MAX_REPEAT:
            # Count remaining siblings of same type
            remaining = sum(
                1 for s in tag.next_siblings
                if hasattr(s, "name") and s.name == tag.name
            )
            if remaining > 0:
                lines.append("  " * depth + f"<!-- +{remaining + 1} more <{tag.name}> -->")
            return
        elif count > MAX_REPEAT:
            return

        # Build tag line with kept attributes
        attrs_str = ""
        for attr in KEEP_ATTRS:
            val = tag.get(attr)
            if val:
                if isinstance(val, list):
                    val = " ".join(val)
                # Truncate long class lists
                if attr == "class" and len(val) > 100:
                    val = val[:100] + "..."
                attrs_str += f' {attr}="{val}"'

        # Self-closing tags
        if tag.name in ("img", "br", "hr", "input"):
            lines.append("  " * depth + f"<{tag.name}{attrs_str}/>")
            return

        # Check if this is a simple wrapper div/span with no useful attrs
        is_bare_wrapper = (
            tag.name in WRAPPER_TAGS
            and not attrs_str
            and len(list(tag.children)) == 1
        )
        if is_bare_wrapper:
            # Skip the wrapper, process child directly
            for child in tag.children:
                _process(child, depth)
            return

        # Open tag
        lines.append("  " * depth + f"<{tag.name}{attrs_str}>")

        # Process children
        for child in tag.children:
            _process(child, depth + 1)

        # Close tag
        lines.append("  " * depth + f"</{tag.name}>")

    # Start from <body> if it exists, otherwise root
    body = soup.find("body")
    root = body if body else soup

    for child in root.children:
        _process(child, 0)

    result = "\n".join(lines)
    logger.info(f"HTML skeleton: {len(result)} chars (from {len(str(soup))} original)")
    return result


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
