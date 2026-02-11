import os
import re
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def get_openrouter_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
    )


def build_prompt(
    html: str,
    image_urls: list[str],
    n: int,
    styles: dict | None = None,
    font_links: list[str] | None = None,
    icons: dict | None = None,
    svgs: list[dict] | None = None,
    logos: list[dict] | None = None,
) -> str:
    """Build the clone prompt with HTML, image URLs, styles, icons, SVGs, logos."""
    max_html = 30000
    truncated_html = html[:max_html]
    if len(html) > max_html:
        truncated_html += "\n\n... [skeleton truncated] ..."

    image_list = "\n".join(f"  - {u}" for u in image_urls) if image_urls else "  (none extracted)"

    # Build styles section from Playwright-extracted data
    styles_section = ""
    if styles:
        parts = []
        if styles.get("fonts"):
            parts.append(f"Fonts detected: {', '.join(styles['fonts'])}")
        if styles.get("colors"):
            parts.append(f"Colors detected: {', '.join(styles['colors'][:20])}")
        if styles.get("gradients"):
            parts.append(f"Gradients detected: {'; '.join(styles['gradients'][:5])}")
        if font_links:
            parts.append(f"Font/icon CDN links: {', '.join(font_links)}")
        if parts:
            styles_section = (
                "\n\nComputed styles extracted from the live page via Playwright:\n"
                + "\n".join(f"- {p}" for p in parts)
                + "\nUse these exact fonts, colors, and gradients to match the original.\n"
            )

    # Build logos section
    logos_section = ""
    if logos:
        logo_parts = []
        for logo in logos[:10]:
            desc = f"  - URL: {logo['url']}"
            if logo.get("alt"):
                desc += f" (alt: \"{logo['alt']}\")"
            desc += f" ({logo.get('width', '?')}x{logo.get('height', '?')}px)"
            if logo.get("reason"):
                desc += f" [detected: {logo['reason']}]"
            logo_parts.append(desc)
        logos_section = (
            "\n\nLOGO IMAGES detected (CRITICAL — reproduce these exactly):\n"
            + "\n".join(logo_parts)
            + "\nUse <img> tags with these EXACT URLs. NEVER replace logos with text or placeholders.\n"
        )

    # Build SVGs section — now uses rendered SVGs from Playwright (resolved <use> refs)
    svgs_section = ""
    if svgs:
        logo_svgs = [s for s in svgs if s.get("isLogo")]
        other_svgs = [s for s in svgs if not s.get("isLogo")]

        svg_parts = []
        # Logo SVGs are critical — include full markup
        for s in logo_svgs[:5]:
            svg_parts.append(
                f"  LOGO SVG ({s.get('width', 0):.0f}x{s.get('height', 0):.0f}px, "
                f"viewBox=\"{s.get('viewBox', '')}\", "
                f"aria-label=\"{s.get('ariaLabel', '')}\"):\n"
                f"  {s['markup'][:3000]}"
            )
        # Other SVGs — icons, decorations
        for s in other_svgs[:8]:
            svg_parts.append(
                f"  SVG icon ({s.get('width', 0):.0f}x{s.get('height', 0):.0f}px, "
                f"class=\"{s.get('classes', '')}\", viewBox=\"{s.get('viewBox', '')}\"):\n"
                f"  {s['markup'][:1000]}"
            )

        if svg_parts:
            svgs_section = (
                "\n\nRENDERED SVGs extracted from the live page (these are the actual SVG content, <use> refs resolved):\n"
                + "\n".join(svg_parts)
                + "\n\nIMPORTANT SVG instructions:\n"
                + "- For LOGO SVGs: copy them EXACTLY as inline <svg> elements in JSX. "
                + "Convert class= to className=, convert style strings to style objects.\n"
                + "- For icon SVGs: use the exact SVG markup or the closest lucide-react icon.\n"
                + "- NEVER replace an SVG logo with text or a placeholder.\n"
            )

    # Build icons section (Font Awesome, Material Icons, etc.)
    icons_section = ""
    if icons:
        icon_parts = []
        if icons.get("fontAwesome"):
            icon_parts.append(f"Font Awesome icons used: {', '.join(icons['fontAwesome'][:20])}")
            icon_parts.append("Replace Font Awesome icons with the closest lucide-react equivalent.")
        if icons.get("materialIcons"):
            icon_parts.append(f"Material Icons used: {', '.join(icons['materialIcons'][:20])}")
            icon_parts.append("Replace Material Icons with the closest lucide-react equivalent.")
        if icons.get("customIconClasses"):
            icon_parts.append(f"Other icon classes: {', '.join(icons['customIconClasses'][:15])}")
        if icon_parts:
            icons_section = (
                "\n\nICON USAGE detected on the page:\n"
                + "\n".join(f"- {p}" for p in icon_parts) + "\n"
            )

    return (
        "You are a website cloning expert. Given the HTML source and a series of screenshots capturing "
        f"the ENTIRE page (scrolled top to bottom in {n} viewport-sized chunks), "
        "generate a Next.js page component (page.tsx) that visually replicates the ENTIRE page.\n\n"
        "Tech stack available in the project:\n"
        "- React 18 with Next.js 14 App Router\n"
        "- Tailwind CSS for all styling\n"
        "- shadcn/ui components — import from \"@/components/ui/<name>\"\n"
        "  Available: button, card, badge, avatar, separator, accordion, tabs,\n"
        "  input, textarea, navigation-menu, dialog, dropdown-menu,\n"
        "  tooltip, select, checkbox, switch, progress,\n"
        "  scroll-area, skeleton, table\n"
        "- lucide-react icons — import { IconName } from \"lucide-react\"\n"
        "- Aceternity UI animated components — import from \"@/components/aceternity/<name>\"\n"
        "  Available: moving-border, background-beams, spotlight, text-generate-effect,\n"
        "  typewriter-effect, card-hover-effect, lamp-effect, wavy-background,\n"
        "  infinite-moving-cards, meteors, bento-grid, sparkles, tabs,\n"
        "  tracing-beam, hero-highlight\n"
        "  Use these for animated backgrounds, glowing borders, text effects, card hover animations, etc.\n"
        "- Utility: import { cn } from \"@/lib/utils\"\n\n"
        "Rules:\n"
        "- CRITICAL: Output ONLY raw TSX code. No markdown fences (```), no explanation, no preamble, no commentary. "
        "Your response must start with \"use client\" and contain NOTHING else but valid TSX code.\n"
        '- The file MUST start with "use client" and export a default function component.\n'
        "- Everything goes in ONE file (page.tsx). Define all sub-components in the same file.\n"
        "- CRITICAL: The code must be valid TypeScript/JSX with no syntax errors. "
        "Ensure all brackets, braces, and parentheses are properly closed. "
        "In JSX, use {'<'} or {'>'} for literal angle brackets in text content. "
        "Never put raw < or > in JSX text — they cause syntax errors.\n"
        "- Use Tailwind CSS utility classes for layout, spacing, colors, typography.\n"
        "- Use shadcn/ui components where they match the original UI (buttons, cards, nav menus, dialogs, badges, etc.).\n"
        "- Use lucide-react for icons that match the original site.\n"
        "- IMAGES: Use the original image URLs with regular <img> tags (NOT next/image). Here are the image URLs extracted:\n"
        f"{image_list}\n"
        "  Use these exact URLs. Match sizing and position from the screenshots.\n"
        "  If an image URL is not listed, check the HTML source for it.\n"
        "- LOGOS & SVGs: This is CRITICAL for visual accuracy. Reproduce all logos and brand icons exactly:\n"
        "  1. If a logo is an <img>, use the original URL with an <img> tag.\n"
        "  2. If a logo is an inline SVG, copy the SVG markup exactly as inline JSX (fix attributes: class→className, etc.).\n"
        "  3. For other icons, use the closest lucide-react icon or reproduce the SVG inline.\n"
        "  4. NEVER replace a logo with a text placeholder — always use the original image/SVG.\n"
        "- FONTS: Use Tailwind font utilities. If Google Fonts are detected, load them with a <link> tag in a useEffect.\n"
        "- You may use React hooks (useState, useEffect, useRef, etc.) for interactivity and animations.\n"
        "- For animations, use Tailwind animate classes (animate-pulse, animate-bounce, etc.) or CSS transitions via className.\n"
        "- IMPORTANT: Reproduce the ENTIRE page from top to bottom, including ALL sections visible across ALL screenshots. "
        f"The {n} screenshots are sequential viewport captures from top to bottom — every section must appear in your output. "
        "The page should scroll naturally just like the original.\n"
        "- Match colors, spacing, font sizes, and layout as closely as possible to the screenshots.\n"
        f"{styles_section}"
        f"{logos_section}"
        f"{svgs_section}"
        f"{icons_section}\n"
        "Here is the page structure skeleton (tags, classes, text, image placements — "
        "full SVGs/logos/icons are provided in the sections above). "
        "Use the SCREENSHOTS as the primary visual reference, and this skeleton for structure:\n\n"
        f"{truncated_html}"
    )


def _clean_code(content: str) -> str:
    """Clean a single code block — fix quotes, invisible chars, ensure 'use client'."""
    content = content.strip()

    # Strip ALL markdown fences (the AI sometimes wraps each file in ```tsx...```)
    # Remove opening fences like ```tsx or ```typescript
    content = re.sub(r'^```(?:tsx|typescript|jsx|ts|javascript)?\s*\n?', '', content, flags=re.MULTILINE)
    # Remove closing fences
    content = re.sub(r'\n?```\s*$', '', content, flags=re.MULTILINE)
    content = content.strip()

    # Strip preamble text (anything before the first "use client" or import)
    if not content.startswith('"use client"') and not content.startswith("'use client'") and not content.startswith("import "):
        code_start = re.search(r'^(?:"use client"|\'use client\'|import )', content, re.MULTILINE)
        if code_start:
            content = content[code_start.start():]

    # Fix smart quotes and invisible chars
    content = content.replace("\u201c", '"').replace("\u201d", '"')
    content = content.replace("\u2018", "'").replace("\u2019", "'")
    for ch in ["\u200b", "\u200c", "\u200d", "\ufeff", "\u00a0"]:
        content = content.replace(ch, "")

    content = content.strip()

    # Ensure "use client" for TSX files
    if '"use client"' not in content and "'use client'" not in content:
        content = '"use client";\n' + content

    return content


def parse_multi_file_output(raw: str) -> list[dict]:
    """Parse AI output into multiple files using // === FILE: path === delimiters.

    Falls back to single page.tsx if no delimiters found.
    """
    raw = raw.strip()

    # Try to find multi-file delimiters
    # Match: // === FILE: app/page.tsx ===
    file_pattern = re.compile(r'^//\s*===\s*FILE:\s*(.+?)\s*===\s*$', re.MULTILINE)
    matches = list(file_pattern.finditer(raw))

    if len(matches) >= 2:
        # Multi-file output
        files = []
        for i, match in enumerate(matches):
            path = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            code = _clean_code(raw[start:end])
            if code:
                files.append({"path": path, "content": code})
        logger.info(f"Parsed {len(files)} files from multi-file output")
        return files

    # Fallback: single file
    logger.info("No multi-file delimiters found, treating as single page.tsx")
    content = _clean_code(raw)
    return [{"path": "app/page.tsx", "content": content}] if content else []


def get_used_components(tsx_content: str) -> dict[str, set[str]]:
    """Scan TSX code for @/components/ui/* and @/components/aceternity/* imports."""
    ui = re.findall(r"""from\s+['"]@/components/ui/([^'"]+)['"]""", tsx_content)
    aceternity = re.findall(r"""from\s+['"]@/components/aceternity/([^'"]+)['"]""", tsx_content)
    return {"ui": set(ui), "aceternity": set(aceternity)}


async def generate_clone(
    html: str,
    screenshots: list[str],
    image_urls: list[str],
    url: str,
    styles: dict | None = None,
    font_links: list[str] | None = None,
    icons: dict | None = None,
    svgs: list[dict] | None = None,
    logos: list[dict] | None = None,
    on_status=None,
) -> list[dict]:
    """Generate a Next.js clone from HTML + viewport screenshots.

    Returns a list of file dicts: [{"path": "app/page.tsx", "content": "..."}]
    """
    client = get_openrouter_client()
    n = len(screenshots)

    if not screenshots:
        logger.error("No screenshots provided — cannot generate clone")
        return []

    logger.info(f"Generating clone with {n} viewport screenshots and {len(html)} chars of HTML")

    if on_status:
        await on_status("Analyzing page with AI...")

    prompt = build_prompt(
        html, image_urls, n,
        styles=styles, font_links=font_links,
        icons=icons, svgs=svgs, logos=logos,
    )

    content: list = []
    for screenshot_b64 in screenshots:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        })

    content.append({
        "type": "text",
        "text": prompt,
    })

    if on_status:
        await on_status(f"Sending {n} screenshots to AI for cloning...")

    response = await client.chat.completions.create(
        model="anthropic/claude-sonnet-4.5",
        messages=[
            {"role": "user", "content": content},
        ],
        max_tokens=64000,
        temperature=0,
    )

    raw_output = response.choices[0].message.content or ""
    logger.info(f"AI response: {len(raw_output)} chars")

    if not raw_output:
        return []

    tsx_content = _clean_code(raw_output)
    files = [{"path": "app/page.tsx", "content": tsx_content}]

    if on_status:
        line_count = tsx_content.count("\n") + 1
        await on_status({
            "type": "file_write",
            "file": "app/page.tsx",
            "action": "create",
            "lines": line_count,
        })

    return files
