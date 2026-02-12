import base64
import io
import re
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from PIL import Image
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

MAX_SCREENSHOT_DIM = 7000
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
MAX_SCREENSHOTS = 5  # Cap parallel AI calls; screenshots are evenly spaced if page is tall


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
    const seen = new Set();

    function addLogo(url, alt, width, height, reason) {
        if (!url || url.startsWith('data:') || seen.has(url)) return;
        seen.add(url);
        logos.push({ url, alt: alt || '', width: Math.round(width), height: Math.round(height), reason });
    }

    // 1. Check <img> tags
    const imgs = document.querySelectorAll('img');
    for (const img of imgs) {
        const rect = img.getBoundingClientRect();
        const src = img.src || img.dataset?.src || '';
        if (!src || src.startsWith('data:')) continue;

        let isLogo = false;
        let reason = '';

        const searchable = (src + ' ' + (img.alt || '') + ' ' + (img.className || '') + ' ' + (img.id || '')).toLowerCase();
        if (searchable.includes('logo') || searchable.includes('brand')) {
            isLogo = true;
            reason = 'attribute match';
        }

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

        if (!isLogo && rect.top < 80 && rect.width > 20 && rect.width < 400 && rect.height < 200) {
            isLogo = true;
            reason = 'top of page';
        }

        if (isLogo) {
            addLogo(src, img.alt, rect.width, rect.height, reason);
        }
    }

    // 2. Check CSS background-image on elements in header/nav area (catches Amazon-style logos)
    const headerEls = document.querySelectorAll('header, nav, [class*="header"], [class*="nav"], [id*="header"], [id*="nav"], [id*="logo"], [class*="logo"]');
    for (const container of headerEls) {
        const allEls = container.querySelectorAll('*');
        for (const el of allEls) {
            const style = window.getComputedStyle(el);
            const bgImg = style.backgroundImage;
            if (bgImg && bgImg !== 'none') {
                const urlMatch = bgImg.match(/url\\(["']?(https?:\\/\\/.+?)["']?\\)/);
                if (urlMatch) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 20 && rect.height > 10) {
                        addLogo(urlMatch[1], '', rect.width, rect.height, 'CSS background-image in header');
                    }
                }
            }
        }
    }

    // 3. Check <input type="image"> (some sites use this for logos)
    const inputImgs = document.querySelectorAll('input[type="image"]');
    for (const inp of inputImgs) {
        const src = inp.src || '';
        if (!src || src.startsWith('data:')) continue;
        const rect = inp.getBoundingClientRect();
        if (rect.top < 80) {
            addLogo(src, inp.alt, rect.width, rect.height, 'input[type=image] near top');
        }
    }

    // 4. Check <a> elements with background-image that link to homepage
    const links = document.querySelectorAll('a[href="/"], a[href="#"]');
    for (const a of links) {
        const style = window.getComputedStyle(a);
        const bgImg = style.backgroundImage;
        if (bgImg && bgImg !== 'none') {
            const urlMatch = bgImg.match(/url\\(["']?(https?:\\/\\/.+?)["']?\\)/);
            if (urlMatch) {
                const rect = a.getBoundingClientRect();
                if (rect.width > 20 && rect.height > 10) {
                    addLogo(urlMatch[1], '', rect.width, rect.height, 'homepage link with background-image');
                }
            }
        }
    }

    return logos;
}
"""


# JS to find clickable trigger elements and tag them for Playwright clicking
FIND_TRIGGERS_JS = """
() => {
    const triggers = [];

    // Semantic triggers — anything that smells interactive
    const selectors = [
        'button', '[role="button"]', '[role="tab"]', '[role="switch"]',
        '[aria-expanded]', '[aria-haspopup]', 'summary',
        '[data-toggle]', '[data-bs-toggle]',
        'a[href="#"]', 'a[href=""]', 'a[aria-expanded]', 'a[aria-haspopup]',
        'a[data-toggle]', 'a[data-bs-toggle]',
    ];
    const els = document.querySelectorAll(selectors.join(', '));

    // Also cursor:pointer elements (custom clickables)
    const allEls = document.querySelectorAll('div, span, li, label, a, figure, section, h1, h2, h3, h4, h5, h6, p, img');
    const selectorSet = new Set([...els]);
    const pointerEls = [];
    for (const el of allEls) {
        if (selectorSet.has(el)) continue;
        const cs = getComputedStyle(el);
        if (cs.cursor === 'pointer') {
            const rect = el.getBoundingClientRect();
            if (rect.width > 5 && rect.height > 5) {
                // Skip if a parent is already in our set
                let parentMatched = false;
                let p = el.parentElement;
                let depth = 0;
                while (p && p !== document.body && depth < 5) {
                    if (selectorSet.has(p)) { parentMatched = true; break; }
                    p = p.parentElement;
                    depth++;
                }
                if (!parentMatched) pointerEls.push(el);
            }
        }
    }

    const combined = [...els, ...pointerEls];

    for (let i = 0; i < combined.length; i++) {
        const el = combined[i];
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 && rect.height < 2) continue;

        const tag = el.tagName.toLowerCase();
        // Skip form submits and text inputs
        if (tag === 'button' && el.getAttribute('type') === 'submit') continue;
        if (tag === 'input' || tag === 'textarea' || tag === 'select') continue;

        const text = (el.textContent || '').trim().slice(0, 60);
        const ariaLabel = el.getAttribute('aria-label') || '';
        const label = ariaLabel || text || ('<' + tag + '>');

        // Tag element so Playwright can find it
        const tid = 'clonr-trigger-' + i;
        el.setAttribute('data-clonr-trigger', tid);

        triggers.push({
            tid: tid,
            tag: tag,
            label: label,
            href: (tag === 'a') ? (el.getAttribute('href') || '') : '',
            role: el.getAttribute('role') || '',
            ariaExpanded: el.getAttribute('aria-expanded'),
            ariaHaspopup: el.getAttribute('aria-haspopup') || '',
        });
    }

    return triggers.slice(0, 30);
}
"""

# JS to snapshot all hidden/invisible elements on the page
SNAPSHOT_HIDDEN_JS = """
() => {
    const hidden = {};
    const all = document.querySelectorAll('*');
    for (const el of all) {
        if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'HEAD') continue;
        const cs = getComputedStyle(el);
        const isHidden = (
            cs.display === 'none' ||
            cs.visibility === 'hidden' ||
            cs.opacity === '0' ||
            (cs.maxHeight === '0px' && cs.overflow === 'hidden') ||
            (cs.height === '0px' && cs.overflow === 'hidden') ||
            el.getAttribute('aria-hidden') === 'true'
        );
        if (isHidden) {
            // Use a stable identifier
            const path = _getPath(el);
            const text = (el.textContent || '').trim().slice(0, 120);
            const tag = el.tagName.toLowerCase();
            const cls = typeof el.className === 'string' ? el.className.slice(0, 80) : '';
            hidden[path] = { tag, cls, text, path };
        }
    }

    function _getPath(el) {
        const parts = [];
        let node = el;
        while (node && node !== document.body && parts.length < 6) {
            let id = node.tagName?.toLowerCase() || '?';
            if (node.id) id += '#' + node.id;
            else if (node.className && typeof node.className === 'string') {
                const first = node.className.trim().split(/\\s+/)[0];
                if (first) id += '.' + first;
            }
            parts.unshift(id);
            node = node.parentElement;
        }
        return parts.join(' > ');
    }

    return hidden;
}
"""

async def _detect_interactives(page) -> list[dict]:
    """Click buttons on the page and observe what hidden elements become visible.

    Returns a list of interaction relationships:
    [{"trigger": "Features", "action": "hover", "revealed": [...]}]

    For each trigger: tries hover first, then click. Uses DOM hidden-element
    diffing to detect what changed. If a click navigates away, records the
    destination URL and bounces back.
    """
    results = []
    linked_pages: list[dict] = []
    original_url = page.url

    try:
        triggers = await page.evaluate(FIND_TRIGGERS_JS)
        if not triggers:
            return {"toggles": [], "linked_pages": []}

        logger.info(f"Found {len(triggers)} trigger candidates to interact with")

        async def _bounce_back():
            await page.goto(original_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
            await page.evaluate(FIND_TRIGGERS_JS)

        def _diff_hidden(before: dict, after: dict) -> tuple[list[dict], list[dict]]:
            revealed = [info for path, info in before.items() if path not in after]
            newly_hidden = [info for path, info in after.items() if path not in before]
            return revealed, newly_hidden

        def _build_entry(trigger: dict, action: str, revealed: list, newly_hidden: list) -> dict:
            entry = {
                "trigger": trigger["label"],
                "triggerTag": trigger["tag"],
                "triggerRole": trigger.get("role", ""),
                "action": action,
            }
            if revealed:
                entry["revealed"] = [
                    {"tag": r["tag"], "cls": r["cls"], "text": r["text"][:100]}
                    for r in revealed[:5]
                ]
            if newly_hidden:
                entry["hid"] = [
                    {"tag": h["tag"], "cls": h["cls"], "text": h["text"][:60]}
                    for h in newly_hidden[:3]
                ]
            return entry

        for trigger in triggers:
            tid = trigger["tid"]
            selector = f'[data-clonr-trigger="{tid}"]'

            try:
                if page.url != original_url:
                    logger.info(f"Bouncing back from {page.url}")
                    await _bounce_back()

                el = page.locator(selector)
                if await el.count() == 0:
                    continue

                await el.scroll_into_view_if_needed(timeout=2000)

                # --- HOVER first ---
                hidden_before = await page.evaluate(SNAPSHOT_HIDDEN_JS)

                await el.hover(timeout=2000)
                await page.wait_for_timeout(350)

                hidden_after_hover = await page.evaluate(SNAPSHOT_HIDDEN_JS)
                hover_revealed, hover_hidden = _diff_hidden(hidden_before, hidden_after_hover)

                if hover_revealed or hover_hidden:
                    results.append(_build_entry(trigger, "hover", hover_revealed, hover_hidden))
                    logger.info(
                        f"Hover '{trigger['label'][:30]}': "
                        f"revealed {len(hover_revealed)}, hid {len(hover_hidden)}"
                    )
                    await page.mouse.move(0, 0)
                    await page.wait_for_timeout(250)
                    continue

                # --- CLICK (hover didn't reveal anything) ---
                hidden_before = await page.evaluate(SNAPSHOT_HIDDEN_JS)

                await el.click(timeout=2000, no_wait_after=True)
                await page.wait_for_timeout(400)

                if page.url != original_url:
                    nav_url = page.url
                    logger.info(f"Click '{trigger['label'][:30]}' navigated to {nav_url}")
                    linked_pages.append({
                        "trigger": trigger["label"],
                        "url": nav_url,
                    })
                    await _bounce_back()
                    continue

                hidden_after_click = await page.evaluate(SNAPSHOT_HIDDEN_JS)
                click_revealed, click_hidden = _diff_hidden(hidden_before, hidden_after_click)

                if click_revealed or click_hidden:
                    results.append(_build_entry(trigger, "click", click_revealed, click_hidden))
                    logger.info(
                        f"Click '{trigger['label'][:30]}': "
                        f"revealed {len(click_revealed)}, hid {len(click_hidden)}"
                    )

                # Click again to restore state
                try:
                    await el.click(timeout=1000, no_wait_after=True)
                    await page.wait_for_timeout(200)
                except Exception:
                    pass

            except Exception as e:
                logger.debug(f"Trigger '{trigger['label'][:30]}' failed: {e}")
                continue

    except Exception as e:
        logger.warning(f"Interactive detection failed: {e}")

    if linked_pages:
        logger.info(f"Discovered {len(linked_pages)} linked pages: {[lp['url'] for lp in linked_pages]}")

    logger.info(f"Detected {len(results)} interactions ({sum(1 for r in results if r.get('action') == 'hover')} hover, {sum(1 for r in results if r.get('action') == 'click')} click)")
    return {"toggles": results, "linked_pages": linked_pages}



async def scrape_and_capture(url: str) -> dict:
    """
    Full Playwright scrape: HTML, viewport screenshots, image URLs,
    computed styles, font/icon links, rendered SVGs, logos.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        context = await browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            color_scheme="light",
            java_script_enabled=True,
        )
        stealth = Stealth(
            navigator_webdriver=True,
            chrome_runtime=True,
            navigator_plugins=True,
            navigator_permissions=True,
            webgl_vendor=True,
        )
        await stealth.apply_stealth_async(context)
        page = await context.new_page()
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
            for logo in logos:
                logger.info(f"  Logo detected: {logo.get('url', '?')[:100]} ({logo.get('width')}x{logo.get('height')}, reason={logo.get('reason')})")
        except Exception as e:
            logger.warning(f"Logo extraction failed: {e}")
            logos = []

        # Get total page height — handle SPAs where body.scrollHeight is 0
        # by finding the actual scrollable container
        scroll_info = await page.evaluate("""
        () => {
            // Strategy 1: body.scrollHeight (works for most sites)
            let height = document.body.scrollHeight;
            let scrollTarget = 'window';

            // Strategy 2: if body height is tiny, find the real scrollable container
            if (height <= window.innerHeight) {
                // Check document.documentElement
                const docHeight = document.documentElement.scrollHeight;
                if (docHeight > height) {
                    height = docHeight;
                }

                // Still too small? Find the largest scrollable child
                if (height <= window.innerHeight) {
                    const candidates = document.querySelectorAll('main, [role="main"], #content, #app, #root, [class*="content"], [class*="scroll"]');
                    let bestEl = null;
                    let bestHeight = 0;
                    // Check explicit candidates first
                    for (const el of candidates) {
                        if (el.scrollHeight > bestHeight) {
                            bestHeight = el.scrollHeight;
                            bestEl = el;
                        }
                    }
                    // Also check all direct children of body and common wrappers
                    const wrappers = [...document.body.children, ...document.querySelectorAll('body > * > *')];
                    for (const el of wrappers) {
                        const style = window.getComputedStyle(el);
                        const isScrollable = style.overflow === 'auto' || style.overflow === 'scroll'
                            || style.overflowY === 'auto' || style.overflowY === 'scroll';
                        if (el.scrollHeight > bestHeight && (isScrollable || el.scrollHeight > window.innerHeight)) {
                            bestHeight = el.scrollHeight;
                            bestEl = el;
                        }
                    }
                    if (bestEl && bestHeight > height) {
                        // Tag it so we can scroll it later
                        bestEl.setAttribute('data-clone-scroll', 'true');
                        height = bestHeight;
                        scrollTarget = 'element';
                    }
                }
            }

            return { height: Math.max(height, window.innerHeight), scrollTarget };
        }
        """)
        total_height = scroll_info["height"]
        scroll_target = scroll_info["scrollTarget"]
        logger.info(f"Page height: {total_height}px (scroll target: {scroll_target})")

        # Build scroll JS based on whether we scroll window or a container element
        if scroll_target == "element":
            scroll_js = "(y) => { const el = document.querySelector('[data-clone-scroll]'); if (el) el.scrollTop = y; else window.scrollTo(0, y); }"
        else:
            scroll_js = "(y) => window.scrollTo(0, y)"

        # Take viewport screenshots evenly spaced to cover the full page
        screenshots_b64 = []
        scroll_positions = []
        region_types = []
        viewports_needed = max(1, -(-total_height // VIEWPORT_HEIGHT))  # ceil division
        # Single-agent mode: cap at 3 screenshots to keep payload manageable
        num_screenshots = min(viewports_needed, 3)
        logger.info(f"Page needs {viewports_needed} viewports → using {num_screenshots} screenshots")
        # Stride: for short pages use viewport height (no gaps),
        # for tall pages space evenly so all sections are captured
        if num_screenshots <= 1:
            stride = 0
        elif viewports_needed <= MAX_SCREENSHOTS:
            stride = VIEWPORT_HEIGHT  # page fits within cap — no gaps
        else:
            # Distribute evenly: last screenshot anchored to bottom
            stride = (total_height - VIEWPORT_HEIGHT) / (num_screenshots - 1)

        for i in range(num_screenshots):
            scroll_y = min(int(i * stride), max(0, total_height - VIEWPORT_HEIGHT))
            await page.evaluate(scroll_js, scroll_y)
            await page.wait_for_timeout(400)
            vp_bytes = await page.screenshot(type="png")
            full_b64 = _resize_screenshot(vp_bytes)
            screenshots_b64.append(full_b64)
            region_types.append("full")
            scroll_positions.append(scroll_y)

        logger.info(
            f"{len(screenshots_b64)} viewport screenshots captured "
            f"(page height: {total_height}px, stride: {stride:.0f}px, "
            f"positions: {scroll_positions}, regions: {region_types})"
        )

        # Detect interactive toggle relationships by clicking buttons
        # Done AFTER screenshots so clicks don't affect the visual captures
        await page.evaluate(scroll_js, 0)
        await page.wait_for_timeout(300)
        interactive_result = await _detect_interactives(page)
        interactives = interactive_result["toggles"]
        linked_pages = interactive_result["linked_pages"]

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
        f"{len(styles.get('fonts', []))} fonts, {len(interactives)} toggles, "
        f"{len(linked_pages)} linked pages"
    )

    return {
        "html": cleaned_html,
        "screenshots": screenshots_b64,
        "scroll_positions": scroll_positions,
        "region_types": region_types,
        "total_height": total_height,
        "image_urls": image_urls,
        "styles": styles,
        "font_links": font_links,
        "icons": icons,
        "svgs": svgs,
        "logos": logos,
        "interactives": interactives,
        "linked_pages": linked_pages,
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
