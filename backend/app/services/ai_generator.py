import os
import re
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


_client: AsyncOpenAI | None = None


def get_openrouter_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
        )
    return _client


def build_prompt(
    html: str,
    image_urls: list[str],
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
        "You are a pixel-perfect website cloning machine. Your ONLY job is to produce an EXACT visual replica.\n"
        f"You have {n} sequential viewport screenshots (top→bottom) and the HTML skeleton below.\n\n"

        "## Output format — MULTI-FILE\n"
        "- Output ONLY raw TSX code — no markdown fences, no explanation, no commentary.\n"
        "- Split the output into MULTIPLE files using this delimiter format:\n"
        "  // === FILE: <path> ===\n"
        "  (code for that file)\n\n"
        "- Required file structure:\n"
        "  // === FILE: app/page.tsx ===          (main page — imports and assembles section components)\n"
        "  // === FILE: components/Navbar.tsx ===  (navigation bar / header)\n"
        "  // === FILE: components/Hero.tsx ===    (hero / above-the-fold section)\n"
        "  // === FILE: components/Footer.tsx ===  (footer)\n"
        "  ... plus additional component files for each major visual section (Features, Pricing, Testimonials, CTA, etc.)\n\n"
        "- Rules for each file:\n"
        '  - Every file starts with "use client" and exports a default function component.\n'
        "  - Keep each component under ~300 lines. If a section is large, split it further.\n"
        "  - app/page.tsx imports all section components and renders them in order.\n"
        "  - Components import from lucide-react and @/lib/utils as needed.\n"
        "  - Components that need shared state (e.g. mobile menu) can use useState locally.\n"
        "- Must be valid TypeScript/JSX. Close all brackets. Use {'<'} or {'>'} for literal angle brackets in text.\n\n"

        "## Coding structure — CRITICAL (follow exactly)\n"
        "Each file MUST follow this exact structure in order:\n"
        "```\n"
        '"use client";\n'
        "// 1. React imports (useState, useEffect, etc.)\n"
        "import { useState } from 'react';\n"
        "// 2. lucide-react icons — import EVERY icon you use (Star, ChevronDown, Menu, X, etc.)\n"
        "import { Star, ChevronDown, Menu, X } from 'lucide-react';\n"
        "// 3. Utility imports\n"
        "import { cn } from '@/lib/utils';\n"
        "// 4. Section component imports (only in page.tsx)\n"
        "import Navbar from '@/components/Navbar';\n"
        "\n"
        "// 5. Component function with default export\n"
        "export default function ComponentName() {\n"
        "  // 6. State declarations at the top\n"
        "  const [isOpen, setIsOpen] = useState(false);\n"
        "  // 7. Return JSX\n"
        "  return (...);\n"
        "}\n"
        "```\n\n"
        "IMPORT RULES (violations will crash the app):\n"
        "- EVERY lucide-react icon used in JSX MUST be imported at the top. If you write <Star />, you MUST have `import { Star } from 'lucide-react'`.\n"
        "- EVERY component used in JSX MUST be imported. If you write <Button>, you MUST import Button.\n"
        "- NEVER use a variable, component, or icon that is not imported or declared in the same file.\n"
        "- In page.tsx, import section components as: `import ComponentName from '@/components/ComponentName'`\n"
        "- Double-check: scan your JSX for any identifier starting with uppercase — each one MUST be imported.\n\n"

        "## Project structure (already set up in sandbox — DO NOT regenerate these)\n"
        "The sandbox has a minimal Next.js 14 scaffold (9 files):\n"
        "```\n"
        "package.json            ← base deps (react, next, tailwindcss, lucide-react, clsx, tailwind-merge, cva) — DO NOT OUTPUT\n"
        "next.config.js          ← static export config — DO NOT OUTPUT\n"
        "tsconfig.json           ← @/* path alias maps to project root — DO NOT OUTPUT\n"
        "tailwind.config.ts      ← Tailwind config — DO NOT OUTPUT\n"
        "postcss.config.js       ← PostCSS config — DO NOT OUTPUT\n"
        "app/layout.tsx          ← root layout (Inter font, Tailwind globals) — DO NOT OUTPUT\n"
        "app/globals.css         ← Tailwind @import + CSS variables — DO NOT OUTPUT\n"
        "app/page.tsx            ← placeholder (you REPLACE this) — OUTPUT THIS\n"
        "lib/utils.ts            ← cn() helper — DO NOT OUTPUT\n"
        "```\n"
        "There are NO pre-installed component libraries (no shadcn/ui, no aceternity). You generate ALL components from scratch.\n"
        "You generate:\n"
        "  - app/page.tsx (imports and assembles your section components)\n"
        "  - components/<SectionName>.tsx (one per visual section: Navbar, Hero, Features, Footer, etc.)\n"
        "NEVER output package.json, layout.tsx, globals.css, tsconfig, or any config file.\n\n"

        "## Tech stack\n"
        "- React 18 + Next.js 14 App Router + Tailwind CSS\n"
        "- PREFER Tailwind-only styling. Build all UI components from scratch with Tailwind classes.\n"
        "- NO pre-installed component libraries (no shadcn/ui, no aceternity, no Radix UI). Do NOT import from @/components/ui/* or @/components/aceternity/*.\n"
        '- lucide-react: import { IconName } from "lucide-react" (pre-installed)\n'
        '- import { cn } from "@/lib/utils" (pre-installed — merges Tailwind classes)\n'
        "- class-variance-authority: import { cva } from 'class-variance-authority' (pre-installed — for component variants)\n\n"
        "## Declaring extra npm dependencies\n"
        "If you absolutely need an npm package that is NOT pre-installed (e.g. framer-motion, a Radix primitive),\n"
        "declare it on a SINGLE line BEFORE your first // === FILE: ... === delimiter:\n"
        "  // === DEPS: framer-motion, @radix-ui/react-dialog ===\n"
        "Rules:\n"
        "- Only declare deps you actually import in your code.\n"
        "- PREFER Tailwind-only solutions. Most clones need ZERO extra deps.\n"
        "- If you don't need extra deps, omit the DEPS line entirely.\n\n"

        "## PIXEL-PERFECT RULES (these are the highest priority)\n\n"

        "### 1. Exact text content\n"
        "- Copy ALL text VERBATIM from the HTML skeleton — every heading, paragraph, button label, nav link, footer line.\n"
        "- NEVER paraphrase, abbreviate, summarize, or use placeholder text like 'Lorem ipsum'.\n"
        "- If the original says 'Get started for free' the clone must say exactly 'Get started for free', not 'Get Started' or 'Start Free'.\n\n"

        "### 2. Exact colors\n"
        "- Look at each section's background in the screenshots and reproduce the EXACT color.\n"
        "- Use the hex colors from the extracted styles below — don't approximate.\n"
        "- If a section has a dark background (#0a0a0a, #1a1a2e, etc.), use that exact value with bg-[#hex].\n"
        "- If text is white-on-dark, use text-white. If it's gray on white, match the exact shade.\n"
        "- Gradients must match: direction, start color, end color. Use bg-gradient-to-r/b/br with from-[#hex] via-[#hex] to-[#hex].\n\n"

        "### 3. Exact spacing and dimensions\n"
        "- Study the screenshots carefully for padding and margins between sections.\n"
        "- Use specific Tailwind values: py-16, py-20, py-24, px-6, px-8, gap-6, gap-8, etc.\n"
        "- Container max-widths: look at how wide the content area is relative to the viewport. Use max-w-7xl, max-w-6xl, max-w-5xl, etc.\n"
        "- Section padding: most sections use py-16 to py-24. Match what you see.\n"
        "- Card padding: usually p-6 to p-8. Match exactly.\n\n"

        "### 4. Exact typography\n"
        "- Match font sizes precisely: text-sm, text-base, text-lg, text-xl, text-2xl, text-3xl, text-4xl, text-5xl, text-6xl.\n"
        "- Match font weights: font-normal, font-medium, font-semibold, font-bold, font-extrabold.\n"
        "- Match line heights: leading-tight, leading-snug, leading-normal, leading-relaxed.\n"
        "- Match letter spacing: tracking-tight, tracking-normal, tracking-wide.\n"
        "- Headings are usually text-3xl to text-6xl font-bold. Body is text-base to text-lg.\n\n"

        "### 5. Exact layout\n"
        "- Count the columns in grids: 2-col, 3-col, 4-col. Use grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 etc.\n"
        "- Match flex layouts: flex-row vs flex-col, items-center, justify-between, gap values.\n"
        "- Match border radius: rounded-md, rounded-lg, rounded-xl, rounded-2xl, rounded-full.\n"
        "- Match shadows: shadow-sm, shadow-md, shadow-lg, shadow-xl.\n"
        "- Match borders: border, border-2, border color.\n\n"

        "### 6. Exact images\n"
        "- Use original image URLs with <img> tags (NOT next/image).\n"
        "- Match image dimensions and aspect ratios from screenshots.\n"
        "- Use object-cover/object-contain as appropriate.\n"
        f"- Image URLs extracted:\n{image_list}\n"
        "- If an image is in the HTML but not listed above, use it anyway.\n\n"

        "### 7. Logos and SVGs\n"
        "- If a logo is an <img>, use the original URL.\n"
        "- If a logo is an inline SVG, copy the EXACT SVG markup as inline JSX (class→className, style string→object).\n"
        "- NEVER replace a logo/SVG with text or a placeholder icon.\n\n"

        "### 8. Fonts\n"
        "- If Google Fonts are detected, load them in a useEffect with a <link> tag appended to document.head.\n"
        "- Apply the font with style={{ fontFamily: 'Font Name, sans-serif' }} on the outer wrapper.\n\n"

        "### 9. Completeness\n"
        f"- There are {n} screenshots capturing the FULL page top to bottom. EVERY section must be in your output.\n"
        "- Go through each screenshot one by one and make sure every visible section is reproduced.\n"
        "- The page must scroll naturally like the original — same section order, same relative heights.\n"
        "- Include the footer. Include sub-footers. Include every small detail.\n\n"

        "### 10. Interactivity\n"
        "- Use useState for any interactive elements (dropdowns, tabs, accordions, mobile menus).\n"
        "- Hover states should work (use hover: Tailwind classes or onMouseEnter/onMouseLeave).\n"
        "- Links should use <a> tags with the original URLs where possible.\n\n"

        f"{styles_section}"
        f"{logos_section}"
        f"{svgs_section}"
        f"{icons_section}"
        f"{interactives_section}"
        f"{linked_pages_section}\n"
        "## HTML Skeleton\n"
        "Use the screenshots as the PRIMARY visual reference. Use this skeleton for structure, text content, and image URLs:\n\n"
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
    on_status=None,
) -> dict:
    """Generate a Next.js clone from HTML + viewport screenshots.

    Returns {"files": [{"path": ..., "content": ...}], "deps": [...]}.
    """
    client = get_openrouter_client()
    n = len(screenshots)

    if not screenshots:
        logger.error("No screenshots provided — cannot generate clone")
        return {"files": [], "deps": []}

    prompt_len = len(html) + sum(len(u) for u in image_urls)
    logger.info(f"[ai] Generating clone: {n} screenshots, {len(html)} chars HTML, {len(image_urls)} images, ~{prompt_len // 1000}k prompt chars")

    if on_status:
        await on_status("Analyzing page with AI...")

    prompt = build_prompt(
        html, image_urls, n,
        styles=styles, font_links=font_links,
        icons=icons, svgs=svgs, logos=logos,
        interactives=interactives,
        linked_pages=linked_pages,
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

    t_ai = __import__("time").time()
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
    t_elapsed = __import__("time").time() - t_ai
    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", "?") if usage else "?"
    tokens_out = getattr(usage, "completion_tokens", "?") if usage else "?"
    logger.info(f"[ai] Response: {len(raw_output)} chars in {t_elapsed:.1f}s | model={model} tokens_in={tokens_in} tokens_out={tokens_out}")

    if not raw_output:
        return {"files": [], "deps": []}

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

    return result
