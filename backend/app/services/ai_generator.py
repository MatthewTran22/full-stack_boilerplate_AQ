import os
import re
import time
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# OpenRouter pricing per million tokens for each model
# Update these if switching models or if pricing changes
MODEL_PRICING = {
    "anthropic/claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
    "anthropic/claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "google/gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}
DEFAULT_PRICING = {"input": 1.25, "output": 10.00}


def _extract_usage(response) -> dict:
    """Extract token usage from an API response and calculate cost."""
    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
    tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0
    return {"tokens_in": tokens_in or 0, "tokens_out": tokens_out or 0}


def _calc_cost(tokens_in: int, tokens_out: int, model: str) -> float:
    """Calculate USD cost from token counts and model name."""
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    cost = (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000
    return round(cost, 6)


_client: AsyncOpenAI | None = None


def get_openrouter_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            timeout=300.0,
        )
    return _client


def build_prompt(
    html: str,
    image_urls: list,
    n: int,
    styles: dict | None = None,
    font_links: list[str] | None = None,
    icons: dict | None = None,
    svgs: list[dict] | None = None,
    logos: list[dict] | None = None,
    interactives: list[dict] | None = None,
    linked_pages: list[dict] | None = None,
) -> str:
    """Build the clone prompt with HTML, image URLs, styles, icons, SVGs, logos."""
    max_html = 30000
    truncated_html = html[:max_html]
    if len(html) > max_html:
        truncated_html += "\n\n... [skeleton truncated] ..."
        pct_kept = (max_html / len(html)) * 100
        logger.info(f"[ai] HTML truncated: {len(html)} → {max_html} chars ({pct_kept:.0f}% kept)")

    # Format image list with context metadata
    if image_urls:
        image_lines = []
        for img in image_urls:
            if isinstance(img, dict):
                line = f"  - {img['url']}"
                parts = []
                if img.get("alt"):
                    parts.append(f'alt="{img["alt"]}"')
                if img.get("width") and img.get("height"):
                    parts.append(f'{img["width"]}x{img["height"]}')
                if img.get("container"):
                    parts.append(f'in .{img["container"]}')
                if img.get("context") and img.get("context") != img.get("alt"):
                    parts.append(f'near "{img["context"]}"')
                if parts:
                    line += f" ({', '.join(parts)})"
                image_lines.append(line)
            else:
                image_lines.append(f"  - {img}")
        image_list = "\n".join(image_lines)
    else:
        image_list = "  (none extracted)"

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

    # Build interactive relationships section (hover + click)
    interactives_section = ""
    if interactives:
        parts = []
        for rel in interactives:
            trigger_label = rel.get("trigger", "?")
            trigger_tag = rel.get("triggerTag", "?")
            action = rel.get("action", "click")

            line = f'  - {action.upper()} "{trigger_label}" (<{trigger_tag}>)'

            if rel.get("revealed"):
                revealed_descs = []
                for r in rel["revealed"]:
                    desc = f'<{r["tag"]}'
                    if r.get("cls"):
                        desc += f' class="{r["cls"][:50]}"'
                    desc += ">"
                    if r.get("text"):
                        desc += f' "{r["text"][:60]}"'
                    revealed_descs.append(desc)
                line += " → REVEALS: " + "; ".join(revealed_descs)

            if rel.get("hid"):
                hid_descs = [f'<{h["tag"]}> "{h.get("text", "")[:40]}"' for h in rel["hid"]]
                line += " → HIDES: " + "; ".join(hid_descs)

            parts.append(line)

        interactives_section = (
            "\n\nINTERACTIONS detected by hovering and clicking elements on the live page:\n"
            + "(We tested each element with hover first, then click, and recorded what changed)\n"
            + "\n".join(parts)
            + "\n\nCRITICAL interactivity rules:\n"
            + "- Each interaction above MUST be functional in the clone.\n"
            + "- HOVER interactions: use onMouseEnter/onMouseLeave with useState to show/hide content.\n"
            + "- CLICK interactions: use onClick with useState to toggle content.\n"
            + "- Dropdowns/menus: match the original trigger (hover or click). Show/hide the content.\n"
            + "- Tabs: use useState to track the active tab. Render different content per tab.\n"
            + "- Accordions: use useState to toggle sections open/closed.\n"
            + "- Tooltips: use onMouseEnter/onMouseLeave to show/hide.\n"
            + "- ALL interactive elements must actually work — not just be static.\n"
        )

    # Build linked pages section (pages discovered when buttons navigated away)
    linked_pages_section = ""
    if linked_pages:
        page_lines = [f'  - "{lp["trigger"]}" links to: {lp["url"]}' for lp in linked_pages[:10]]
        linked_pages_section = (
            "\n\nLINKED PAGES discovered (buttons that navigate to other pages):\n"
            + "\n".join(page_lines)
            + "\n- For these, use <a> tags with the original URLs so they work as navigation links.\n"
        )

    return (
        "You are a pixel-perfect website cloning machine. Produce an EXACT visual replica of the screenshots.\n"
        f"{'You have a single screenshot showing the full page.' if n == 1 else f'You have {n} screenshots taken top-to-bottom covering the full page. They are labeled with scroll positions.'}\n"
        f"{'' if n == 1 else 'Sticky/repeated elements (headers, sidebars) that appear in multiple screenshots should only be rendered ONCE.'}\n\n"

        "## GOLDEN RULE: CLONE ONLY WHAT YOU SEE\n"
        "- ONLY reproduce UI visible in the screenshots. NEVER invent, add, or hallucinate elements.\n"
        "- If it's not in the screenshot, it does not exist. Missing > invented.\n"
        "- Use the HTML skeleton below for exact text content and image URLs. Use screenshots for layout and visual design.\n\n"

        "## Output format\n"
        "Output ONLY raw TSX code — no markdown fences, no explanation.\n"
        "Split into multiple files with this delimiter:\n"
        "  // === FILE: <path> ===\n\n"
        "Files to generate:\n"
        "  - app/page.tsx — imports and renders all section components\n"
        "  - components/<Name>.tsx — one per visual section (Navbar, Hero, Features, Footer, etc.)\n\n"
        "NEVER output package.json, layout.tsx, globals.css, tsconfig, or any config file.\n"
        "If you need an extra npm package, declare before the first file: // === DEPS: package-name ===\n\n"

        "## Component rules\n"
        '- Every file: "use client" at top, default export, valid TypeScript/JSX.\n'
        "- ZERO PROPS: all data hardcoded inside. Components render as <Name /> with no props.\n"
        "- Every JSX identifier (icons, components) MUST be imported. Missing imports crash the app.\n"
        "- Keep components under ~300 lines.\n\n"

        "## Stack\n"
        "Next.js 14 + Tailwind CSS. Prefer building UI from scratch with Tailwind.\n"
        "Available: lucide-react icons, cn() from @/lib/utils, cva from class-variance-authority.\n"
        "For complex UI (animated cards, modals, carousels, advanced effects), you MAY use shadcn/ui or aceternity components — declare them in the DEPS line.\n\n"

        "## Visual accuracy\n"
        "- **Text**: copy ALL text VERBATIM from the HTML skeleton. Never paraphrase or use placeholders.\n"
        "- **Colors**: use exact hex values from extracted styles. Match backgrounds, text colors, gradients.\n"
        "- **Layout**: count columns exactly. Side-by-side elements must be side-by-side, not stacked. Match flex/grid structure.\n"
        "- **Spacing**: match padding, margins, gaps from screenshots. Use specific Tailwind values.\n"
        "- **Typography**: match font sizes, weights, and line heights from screenshots.\n"
        "- **Images**: use <img> tags (NOT next/image) with original URLs. Match each image to its container using the alt text and context provided.\n"
        f"\n### Image URLs with context:\n{image_list}\n\n"
        "- **Logos**: ALWAYS use <img> with original URL or copy exact SVG markup. NEVER recreate logos with CSS/text.\n"
        "- **Fonts**: if Google Fonts detected, load via useEffect appending <link> to document.head.\n"
        "- **Interactivity**: use useState for dropdowns, tabs, accordions, mobile menus. Hover states via Tailwind or onMouseEnter/onMouseLeave.\n\n"

        f"{styles_section}"
        f"{logos_section}"
        f"{svgs_section}"
        f"{icons_section}"
        f"{interactives_section}"
        f"{linked_pages_section}\n"
        "## HTML Skeleton (use screenshots as PRIMARY visual reference, this for text/structure):\n\n"
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

    # Auto-fix missing lucide-react imports
    content = _fix_missing_imports(content)

    return content


def _fix_missing_imports(content: str) -> str:
    """Auto-fix missing lucide-react and React imports in generated TSX."""
    # Find all PascalCase JSX tags used: <Star />, <ChevronDown>, etc.
    jsx_tags = set(re.findall(r'<([A-Z][a-zA-Z0-9]+)[\s/>]', content))

    # Find all already-imported identifiers
    imported = set()
    for m in re.finditer(r'import\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]', content):
        names = [n.strip().split(' as ')[0].strip() for n in m.group(1).split(',')]
        imported.update(names)
    # Default imports
    for m in re.finditer(r'import\s+(\w+)\s+from\s+[\'"]', content):
        imported.add(m.group(1))

    # Known lucide-react icons (common ones the AI uses)
    lucide_icons = {
        'Star', 'ChevronDown', 'ChevronUp', 'ChevronRight', 'ChevronLeft',
        'Menu', 'X', 'Search', 'ArrowRight', 'ArrowLeft', 'ExternalLink',
        'Check', 'Copy', 'Eye', 'EyeOff', 'Heart', 'ThumbsUp', 'Share2',
        'Github', 'Twitter', 'Linkedin', 'Facebook', 'Instagram', 'Youtube',
        'Mail', 'Phone', 'MapPin', 'Calendar', 'Clock', 'User', 'Users',
        'Settings', 'Home', 'FileText', 'Folder', 'Download', 'Upload',
        'Plus', 'Minus', 'Edit', 'Trash2', 'Shield', 'Lock', 'Unlock',
        'Globe', 'Zap', 'Award', 'TrendingUp', 'BarChart', 'PieChart',
        'Code', 'Terminal', 'GitBranch', 'GitCommit', 'GitPullRequest',
        'GitMerge', 'GitFork', 'BookOpen', 'Book', 'Bookmark', 'Tag',
        'Hash', 'AtSign', 'Bell', 'AlertCircle', 'AlertTriangle', 'Info',
        'HelpCircle', 'MessageCircle', 'MessageSquare', 'Send', 'Paperclip',
        'Image', 'Camera', 'Video', 'Music', 'Headphones', 'Mic', 'Volume2',
        'Play', 'Pause', 'SkipForward', 'SkipBack', 'Repeat', 'Shuffle',
        'Maximize2', 'Minimize2', 'MoreHorizontal', 'MoreVertical', 'Grid',
        'List', 'Layout', 'Sidebar', 'Columns', 'Layers', 'Box', 'Package',
        'Cpu', 'Database', 'Server', 'Cloud', 'Wifi', 'Bluetooth',
        'Monitor', 'Smartphone', 'Tablet', 'Watch', 'Printer', 'Speaker',
        'Sun', 'Moon', 'CloudRain', 'Wind', 'Droplet', 'Thermometer',
        'Rocket', 'Sparkles', 'Flame', 'Target', 'Crosshair', 'Navigation',
        'Compass', 'Map', 'Flag', 'Anchor', 'Briefcase', 'DollarSign',
        'CreditCard', 'ShoppingCart', 'ShoppingBag', 'Gift', 'Percent',
        'Activity', 'Aperture', 'Battery', 'BatteryCharging', 'Feather',
        'Filter', 'Key', 'Link', 'Loader', 'LogIn', 'LogOut', 'Power',
        'RefreshCw', 'RotateCw', 'Save', 'Scissors', 'Slash', 'Tool',
        'Type', 'Underline', 'Bold', 'Italic', 'AlignLeft', 'AlignCenter',
        'AlignRight', 'AlignJustify', 'CircleDot', 'Circle', 'Square',
        'Triangle', 'Hexagon', 'Octagon', 'Pentagon', 'Diamond',
        'FileCode', 'FileCode2', 'Files', 'FolderOpen', 'FolderGit2',
        'ChevronDownIcon', 'StarIcon', 'CheckCircle', 'CheckCircle2',
        'XCircle', 'MinusCircle', 'PlusCircle', 'ArrowUpRight',
        'ArrowDownRight', 'MoveRight', 'Lightbulb', 'Wand2',
    }

    # Find missing lucide icons
    missing_lucide = []
    for tag in jsx_tags:
        if tag not in imported and tag in lucide_icons:
            missing_lucide.append(tag)

    if not missing_lucide:
        return content

    missing_str = ', '.join(sorted(missing_lucide))
    logger.warning(f"[ai] Auto-fixing missing lucide imports: {missing_str}")

    # Check if there's an existing lucide-react import to extend
    lucide_import_match = re.search(
        r'(import\s+\{)([^}]+)(\}\s+from\s+[\'"]lucide-react[\'"];?)',
        content,
    )

    if lucide_import_match:
        existing = lucide_import_match.group(2).strip().rstrip(',')
        new_imports = existing + ', ' + ', '.join(sorted(missing_lucide))
        content = content[:lucide_import_match.start()] + \
            lucide_import_match.group(1) + ' ' + new_imports + ' ' + lucide_import_match.group(3) + \
            content[lucide_import_match.end():]
    else:
        # No existing lucide import — add one after "use client"
        import_line = f'import {{ {", ".join(sorted(missing_lucide))} }} from "lucide-react";\n'
        content = re.sub(
            r'("use client";?\s*\n)',
            r'\1' + import_line,
            content,
            count=1,
        )

    return content


def parse_multi_file_output(raw: str) -> dict:
    """Parse AI output into multiple files using // === FILE: path === delimiters.

    Also extracts extra npm dependencies from a // === DEPS: pkg1, pkg2 === line.

    Returns {"files": [{"path": ..., "content": ...}], "deps": ["pkg1", "pkg2"]}.
    Falls back to single page.tsx if no delimiters found.
    """
    raw = raw.strip()

    # Extract optional DEPS declaration (before or between file delimiters)
    deps: list[str] = []
    deps_pattern = re.compile(r'^//\s*===\s*DEPS:\s*(.+?)\s*===\s*$', re.MULTILINE)
    deps_match = deps_pattern.search(raw)
    if deps_match:
        deps = [d.strip() for d in deps_match.group(1).split(",") if d.strip()]
        # Strip the DEPS line from raw so it doesn't interfere with file parsing
        raw = raw[:deps_match.start()] + raw[deps_match.end():]
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
        return {"files": files, "deps": deps}

    # Fallback: single file
    logger.info("No multi-file delimiters found, treating as single page.tsx")
    content = _clean_code(raw)
    files = [{"path": "app/page.tsx", "content": content}] if content else []
    return {"files": files, "deps": deps}


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
    interactives: list[dict] | None = None,
    linked_pages: list[dict] | None = None,
    scroll_positions: list[int] | None = None,
    total_height: int = 0,
    region_types: list[str] | None = None,
    on_status=None,
) -> dict:
    """Generate a Next.js clone from HTML + viewport screenshots.

    Returns {"files": [{"path": ..., "content": ...}], "deps": [...]}.
    If multiple screenshots, delegates to parallel multi-pass generation.
    """
    n = len(screenshots)

    if not screenshots:
        logger.error("No screenshots provided — cannot generate clone")
        return {"files": [], "deps": []}

    # Single-agent generation — all screenshots go to one AI call
    client = get_openrouter_client()

    prompt_len = len(html) + sum(len(u["url"] if isinstance(u, dict) else u) for u in image_urls)
    logger.info(f"[ai] Generating clone: {n} screenshot(s), {len(html)} chars HTML, {len(image_urls)} images, ~{prompt_len // 1000}k prompt chars")

    if on_status:
        await on_status("Analyzing page with AI...")

    prompt = build_prompt(
        html, image_urls, n,
        styles=styles, font_links=font_links,
        icons=icons, svgs=svgs, logos=logos,
        interactives=interactives,
        linked_pages=linked_pages,
    )

    # Build content: label each screenshot with its scroll position, then the prompt
    positions = scroll_positions or [0] * n
    content: list = []
    for i, ss in enumerate(screenshots):
        label = f"Screenshot {i + 1} of {n}"
        if n > 1:
            scroll_y = positions[i] if i < len(positions) else 0
            if total_height > 0:
                pct = int(scroll_y / total_height * 100)
                label += f" (scrolled to {pct}% — pixels {scroll_y}–{scroll_y + 720} of {total_height}px)"
            else:
                label += f" (scroll position: {scroll_y}px)"
        content.append({"type": "text", "text": label})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{ss}"},
        })
    content.append({
        "type": "text",
        "text": prompt,
    })

    if on_status:
        await on_status(f"Sending {n} screenshot(s) to AI for cloning...")

    t_ai = time.time()
    model = "anthropic/claude-sonnet-4.5"
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": content},
        ],
        max_tokens=64000,
        temperature=0,
    )

    raw_output = response.choices[0].message.content or ""
    t_elapsed = time.time() - t_ai
    u = _extract_usage(response)
    total_cost = _calc_cost(u["tokens_in"], u["tokens_out"], model)
    logger.info(
        f"[ai] Response: {len(raw_output)} chars in {t_elapsed:.1f}s | model={model} "
        f"tokens_in={u['tokens_in']} tokens_out={u['tokens_out']} cost=${total_cost:.4f}"
    )

    if not raw_output:
        return {"files": [], "deps": [], "usage": {"tokens_in": u["tokens_in"], "tokens_out": 0, "total_cost": _calc_cost(u["tokens_in"], 0, model), "api_calls": 1, "model": model, "duration_s": round(t_elapsed, 1)}}

    result = parse_multi_file_output(raw_output)
    files = result["files"]
    deps = result["deps"]

    if deps:
        logger.info(f"[ai] AI requested {len(deps)} extra dependencies: {', '.join(deps)}")

    if on_status:
        for f in files:
            line_count = f["content"].count("\n") + 1
            await on_status({
                "type": "file_write",
                "file": f["path"],
                "action": "create",
                "lines": line_count,
            })

    result["usage"] = {
        "tokens_in": u["tokens_in"],
        "tokens_out": u["tokens_out"],
        "total_cost": total_cost,
        "api_calls": 1,
        "model": model,
        "duration_s": round(t_elapsed, 1),
    }
    return result


async def fix_component(file_path: str, file_content: str, error_message: str) -> dict:
    """Send a broken component + error to the AI and get a fixed version back.

    Returns {"content": str, "usage": {"tokens_in": int, "tokens_out": int}}.
    """
    client = get_openrouter_client()
    model = "anthropic/claude-sonnet-4.5"

    prompt = (
        "A Next.js component has a runtime error. Fix it and return ONLY the corrected TSX code.\n"
        "- Output ONLY raw TSX code — no markdown fences, no explanation, no commentary.\n"
        "- Keep the same visual output, just fix the bug.\n"
        '- The file must start with "use client".\n\n'
        "## CRITICAL RULE\n"
        "This component is rendered as <ComponentName /> with ZERO PROPS. "
        "If the error is caused by undefined props, you MUST move ALL data inside the component as hardcoded constants. "
        "Remove all prop interfaces and destructured parameters. Hardcode arrays, strings, and objects directly in the component body.\n\n"
        "## Common fixes\n"
        "- Props are undefined → hardcode the data inside the component\n"
        "- Missing imports → add the import\n"
        "- Undefined variable → initialize it with useState or a const\n"
        "- Bad array access → add a fallback or default value\n\n"
        f"## File: {file_path}\n\n"
        f"## Error\n{error_message}\n\n"
        f"## Current code\n```\n{file_content}\n```\n\n"
        "Return ONLY the fixed TSX code."
    )

    t0 = time.time()
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=32000,
        temperature=0,
    )

    raw = response.choices[0].message.content or ""
    t_elapsed = time.time() - t0
    u = _extract_usage(response)
    cost = _calc_cost(u["tokens_in"], u["tokens_out"], model)
    logger.info(
        f"[ai-fix] Fixed {file_path} in {t_elapsed:.1f}s | "
        f"tokens_in={u['tokens_in']} tokens_out={u['tokens_out']} cost=${cost:.4f}"
    )

    fixed_content = _clean_code(raw) if raw else file_content
    return {"content": fixed_content, "usage": u}
