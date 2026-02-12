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
}
DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


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
        f"You have {'a single full-page screenshot showing the ENTIRE page layout' if n == 1 else f'{n} screenshots'} and the HTML skeleton below.\n"
        "Study the screenshot carefully — every section, image, SVG, and component must appear in the EXACT position shown.\n\n"

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
        "  - ZERO PROPS: Components are rendered as <ComponentName /> with NO props. ALL data (text, images, links, arrays) must be hardcoded INSIDE the component. NEVER use component props or interfaces for data.\n"
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

        "### 7. Logos and SVGs (CRITICAL)\n"
        "- If a logo is an <img>, use the EXACT original image URL with an <img> tag. NEVER recreate a logo using CSS, text, spans, divs, or Unicode characters.\n"
        "- If a logo is an inline SVG, copy the EXACT SVG markup as inline JSX (class→className, style string→object).\n"
        "- NEVER replace a logo/SVG with text, a placeholder icon, or a CSS recreation.\n"
        "- When in doubt, use an <img> tag with the logo URL from the LOGO IMAGES section below.\n\n"

        "### 8. Fonts\n"
        "- If Google Fonts are detected, load them in a useEffect with a <link> tag appended to document.head.\n"
        "- Apply the font with style={{ fontFamily: 'Font Name, sans-serif' }} on the outer wrapper.\n\n"

        "### 9. Completeness\n"
        "- The full-page screenshot shows the ENTIRE page top to bottom. EVERY section visible must be in your output.\n"
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


def build_section_prompt(
    section_index: int,
    total_sections: int,
    html: str,
    image_urls: list[str],
    styles: dict | None = None,
    font_links: list[str] | None = None,
    icons: dict | None = None,
    svgs: list[dict] | None = None,
    logos: list[dict] | None = None,
    interactives: list[dict] | None = None,
    linked_pages: list[dict] | None = None,
    previously_generated: list[dict] | None = None,
    scroll_y: int = 0,
    total_height: int = 0,
) -> str:
    """Build a focused prompt for generating one section of the page.

    Each call gets a single viewport screenshot and produces 1-3 component files.
    """
    max_html = 30000
    truncated_html = html[:max_html]
    if len(html) > max_html:
        truncated_html += "\n\n... [skeleton truncated] ..."

    image_list = "\n".join(f"  - {u}" for u in image_urls) if image_urls else "  (none extracted)"

    # Build styles section
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
                "\n\nComputed styles extracted from the live page:\n"
                + "\n".join(f"- {p}" for p in parts)
                + "\nUse these exact fonts, colors, and gradients.\n"
            )

    # Build logos section — pass to ALL sections (logos appear in headers AND footers)
    logos_section = ""
    if logos:
        logo_parts = []
        for logo in logos[:10]:
            desc = f"  - URL: {logo['url']}"
            if logo.get("alt"):
                desc += f" (alt: \"{logo['alt']}\")"
            desc += f" ({logo.get('width', '?')}x{logo.get('height', '?')}px)"
            logo_parts.append(desc)
        logos_section = (
            "\n\nLOGO IMAGES detected on this site (CRITICAL):\n"
            + "\n".join(logo_parts)
            + "\n\nLOGO RULES (MANDATORY):\n"
            + "- Use <img> tags with these EXACT URLs for ANY logo visible in your screenshot.\n"
            + "- NEVER recreate a logo using CSS, text, SVG paths, or Unicode characters.\n"
            + "- NEVER approximate a logo — always use the original image URL.\n"
            + "- If the logo appears in your viewport, it MUST be an <img> tag pointing to one of the URLs above.\n"
        )

    # Build SVGs section (logo SVGs for first section, icons for all)
    svgs_section = ""
    if svgs:
        if section_index == 0:
            logo_svgs = [s for s in svgs if s.get("isLogo")]
            svg_parts = []
            for s in logo_svgs[:5]:
                svg_parts.append(
                    f"  LOGO SVG ({s.get('width', 0):.0f}x{s.get('height', 0):.0f}px, "
                    f"viewBox=\"{s.get('viewBox', '')}\"):\n"
                    f"  {s['markup'][:3000]}"
                )
            if svg_parts:
                svgs_section = (
                    "\n\nLOGO SVGs (copy exactly as inline JSX, class→className):\n"
                    + "\n".join(svg_parts) + "\n"
                )
        other_svgs = [s for s in svgs if not s.get("isLogo")]
        if other_svgs:
            icon_parts = []
            for s in other_svgs[:6]:
                icon_parts.append(
                    f"  SVG icon ({s.get('width', 0):.0f}x{s.get('height', 0):.0f}px, "
                    f"class=\"{s.get('classes', '')}\"):\n"
                    f"  {s['markup'][:800]}"
                )
            svgs_section += (
                "\n\nIcon SVGs from the page:\n"
                + "\n".join(icon_parts) + "\n"
            )

    # Build icons section
    icons_section = ""
    if icons:
        icon_parts = []
        if icons.get("fontAwesome"):
            icon_parts.append(f"Font Awesome icons: {', '.join(icons['fontAwesome'][:15])}")
            icon_parts.append("Replace with closest lucide-react equivalent.")
        if icons.get("materialIcons"):
            icon_parts.append(f"Material Icons: {', '.join(icons['materialIcons'][:15])}")
            icon_parts.append("Replace with closest lucide-react equivalent.")
        if icon_parts:
            icons_section = "\n\nICON USAGE:\n" + "\n".join(f"- {p}" for p in icon_parts) + "\n"

    # Build interactives section (only relevant sections)
    interactives_section = ""
    if interactives:
        parts = []
        for rel in interactives:
            trigger_label = rel.get("trigger", "?")
            action = rel.get("action", "click")
            line = f'  - {action.upper()} "{trigger_label}"'
            if rel.get("revealed"):
                revealed_descs = [f'<{r["tag"]}> "{r.get("text", "")[:40]}"' for r in rel["revealed"][:3]]
                line += " → REVEALS: " + "; ".join(revealed_descs)
            parts.append(line)
        interactives_section = (
            "\n\nINTERACTIONS detected:\n"
            + "\n".join(parts)
            + "\n- Use useState for interactive elements (dropdowns, tabs, accordions, mobile menus).\n"
        )

    # Build linked pages section
    linked_pages_section = ""
    if linked_pages:
        page_lines = [f'  - "{lp["trigger"]}" → {lp["url"]}' for lp in linked_pages[:10]]
        linked_pages_section = (
            "\n\nLINKED PAGES:\n"
            + "\n".join(page_lines)
            + "\n- Use <a> tags with original URLs.\n"
        )

    # Previously generated context
    prev_section = ""
    if previously_generated:
        prev_parts = [f"  - {pg['name']}: {pg['description']}" for pg in previously_generated]
        prev_section = (
            "\n\nALREADY GENERATED components (DO NOT regenerate these — avoid duplicating their content):\n"
            + "\n".join(prev_parts) + "\n"
        )

    # Viewport position info
    viewport_h = 720
    viewport_top = scroll_y
    viewport_bottom = scroll_y + viewport_h
    page_h = total_height or viewport_bottom

    # Build edge guidance based on position — clear ownership rule:
    # You OWN content cut off at the bottom (include it fully).
    # You SKIP content cut off at the top (previous section owns it).
    edge_rules = []
    if section_index > 0:
        edge_rules.append(
            "- Content cut off at the TOP edge: SKIP it. The previous section already includes it."
        )
        edge_rules.append(
            "- SIDEBARS / ASIDE COLUMNS: If you see a sidebar or aside panel (e.g. related videos, recommended content, article sidebar) "
            "that runs alongside the main content and likely appears in ALL viewports, SKIP IT — section 1 already generates it. "
            "Focus ONLY on the primary/center content column that CHANGES as the page scrolls (e.g. comments, description, additional sections)."
        )
    if section_index < total_sections - 1:
        edge_rules.append(
            "- Content cut off at the BOTTOM edge: INCLUDE it fully. You own it — the next section will skip it."
        )
    edge_guidance = "\n".join(edge_rules) if edge_rules else ""

    return (
        f"You are generating section {section_index + 1} of {total_sections} of a pixel-perfect website clone.\n"
        f"All {total_sections} sections are being generated IN PARALLEL by separate AI calls, so you cannot see what other sections produce.\n"
        f"Your screenshot covers pixels {viewport_top}–{viewport_bottom} of a {page_h}px tall page.\n"
        "Generate components for ALL content visible in your screenshot — even if partially cut off.\n"
        f"{edge_guidance}\n"
        "Name your components descriptively based on what they show (e.g. VideoInfo, CommentSection, RelatedVideos, PricingCards) — NEVER generic names like Section3.\n\n"

        f"{prev_section}"

        "## Output format (CRITICAL — follow EXACTLY or the build will fail)\n"
        "- Output ONLY raw TSX code — no markdown fences, no explanation.\n"
        "- You MUST use this EXACT delimiter before each file (the build system parses it):\n"
        "  // === FILE: components/<SectionName>.tsx ===\n"
        "- Even if you only have ONE component, you MUST use the delimiter line above.\n"
        "- DO NOT output app/page.tsx — that will be assembled separately.\n"
        "- After the last file, add a comment describing what each component renders:\n"
        "  // === COMPONENTS: Navbar (top navigation with logo and links), Hero (main hero section with CTA) ===\n\n"

        "## Rules for each file\n"
        '- Start with "use client" and export a default function component.\n'
        "- ZERO PROPS: Components are rendered as <ComponentName /> with NO props. ALL data (text, images, links, arrays) must be hardcoded INSIDE the component. NEVER use component props or interfaces for data.\n"
        "- Keep each component under ~300 lines.\n"
        "- Import from lucide-react and @/lib/utils as needed.\n"
        "- Use useState for interactive elements.\n"
        "- Must be valid TypeScript/JSX.\n\n"

        "## Tech stack\n"
        "- React 18 + Next.js 14 App Router + Tailwind CSS\n"
        "- Build ALL UI from scratch with Tailwind — NO component libraries.\n"
        '- lucide-react: import { IconName } from "lucide-react"\n'
        '- import { cn } from "@/lib/utils"\n\n'

        "## Declaring extra npm dependencies\n"
        "If needed, declare on a single line BEFORE your first file delimiter:\n"
        "  // === DEPS: framer-motion ===\n"
        "Prefer Tailwind-only solutions.\n\n"

        "## PIXEL-PERFECT RULES\n"
        "- Copy ALL text VERBATIM from the HTML skeleton.\n"
        "- Match exact colors, spacing, typography from the screenshot.\n"
        "- Use original image URLs with <img> tags (NOT next/image).\n"
        "- LOGOS: ALWAYS use <img src=\"...\"> with the original logo URL from the list below. NEVER recreate logos with CSS, text, spans, divs, or SVG paths.\n"
        "- If a logo is an inline SVG in the HTML skeleton, copy the EXACT SVG markup as inline JSX.\n"
        "- NEVER use placeholder text.\n"
        f"- Image URLs:\n{image_list}\n"

        f"{styles_section}"
        f"{logos_section}"
        f"{svgs_section}"
        f"{icons_section}"
        f"{interactives_section}"
        f"{linked_pages_section}\n"
        "## HTML Skeleton (use screenshots as PRIMARY visual reference, this for text/structure):\n\n"
        f"{truncated_html}"
    )


def build_assembly_prompt(components: list[dict]) -> str:
    """Build a prompt that creates app/page.tsx from a list of generated components.

    Each component dict has: {"name": str, "path": str, "description": str}
    """
    component_list = "\n".join(
        f"  - import {c['name']} from '@/components/{c['name']}' — {c['description']}"
        for c in components
    )

    return (
        "You are assembling the final page.tsx for a cloned website.\n"
        "All section components have already been generated. Your ONLY job is to create app/page.tsx that imports and renders them in order.\n\n"

        "## Generated components (import and render ALL of these in this order):\n"
        f"{component_list}\n\n"

        "## Output format\n"
        "- Output ONLY the raw TSX code for app/page.tsx — no markdown fences, no explanation.\n"
        '- Start with "use client";\n'
        "- Import all components from '@/components/<Name>'\n"
        "- Export a default function Page() that renders all components in a fragment or div.\n"
        "- Do NOT add any styles, wrappers, or extra elements — just render the components in order.\n\n"

        "## Example structure:\n"
        '"use client";\n'
        "import Navbar from '@/components/Navbar';\n"
        "import Hero from '@/components/Hero';\n"
        "// ... more imports\n\n"
        "export default function Page() {\n"
        "  return (\n"
        "    <>\n"
        "      <Navbar />\n"
        "      <Hero />\n"
        "      {/* ... more components */}\n"
        "    </>\n"
        "  );\n"
        "}\n"
    )


def _parse_components_comment(raw: str) -> list[dict]:
    """Extract component descriptions from a // === COMPONENTS: ... === comment."""
    match = re.search(r'//\s*===\s*COMPONENTS:\s*(.+?)\s*===', raw)
    if not match:
        return []

    components_str = match.group(1)
    result = []
    # Parse "Name (description), Name2 (description2)"
    for part in re.findall(r'(\w+)\s*\(([^)]+)\)', components_str):
        result.append({"name": part[0], "description": part[1].strip()})

    return result


async def _generate_one_section(
    client: AsyncOpenAI,
    model: str,
    section_index: int,
    total_sections: int,
    screenshot_b64: str,
    html: str,
    image_urls: list[str],
    styles: dict | None,
    font_links: list[str] | None,
    icons: dict | None,
    svgs: list[dict] | None,
    logos: list[dict] | None,
    interactives: list[dict] | None,
    linked_pages: list[dict] | None,
    scroll_y: int = 0,
    total_height: int = 0,
    on_status=None,
) -> dict:
    """Generate components for a single viewport section. Runs as a parallel task."""
    section_num = section_index + 1
    prompt = build_section_prompt(
        section_index=section_index,
        total_sections=total_sections,
        html=html,
        image_urls=image_urls,
        styles=styles,
        font_links=font_links,
        icons=icons,
        svgs=svgs,
        logos=logos,
        interactives=interactives,
        linked_pages=linked_pages,
        scroll_y=scroll_y,
        total_height=total_height,
    )

    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
        {"type": "text", "text": prompt},
    ]

    t0 = time.time()
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=16000,
        temperature=0,
    )

    raw_output = response.choices[0].message.content or ""
    t_elapsed = time.time() - t0
    u = _extract_usage(response)
    call_cost = _calc_cost(u["tokens_in"], u["tokens_out"], model)
    logger.info(
        f"[ai] Section {section_num}/{total_sections}: {len(raw_output)} chars in {t_elapsed:.1f}s | "
        f"tokens_in={u['tokens_in']} tokens_out={u['tokens_out']} cost=${call_cost:.4f}"
    )

    # Parse output
    files: list[dict] = []
    deps: list[str] = []
    components: list[dict] = []

    if raw_output:
        comp_descs = _parse_components_comment(raw_output)
        result = parse_multi_file_output(raw_output)
        deps = result["deps"]

        for f in result["files"]:
            if f["path"] == "app/page.tsx":
                # Try to extract the actual component name from the code
                name_match = re.search(r'export\s+default\s+function\s+(\w+)', f["content"])
                fallback_name = name_match.group(1) if name_match else f"Section{section_num}"
                # Suffix with section number to avoid collisions across parallel sections
                if fallback_name != f"Section{section_num}":
                    fallback_name = f"{fallback_name}S{section_num}"
                f["path"] = f"components/{fallback_name}.tsx"
                # Also rename the function export to match the file name
                if name_match:
                    f["content"] = f["content"].replace(
                        f"export default function {name_match.group(1)}",
                        f"export default function {fallback_name}",
                        1,
                    )
                logger.info(f"[ai] Section {section_num}: renamed fallback → {f['path']}")
            files.append(f)

            comp_name = f["path"].replace("components/", "").replace(".tsx", "")
            desc = ""
            for cd in comp_descs:
                if cd["name"] == comp_name:
                    desc = cd["description"]
                    break
            components.append({
                "name": comp_name,
                "path": f["path"],
                "description": desc or f"Section {section_num} component",
            })

        logger.info(f"[ai] Section {section_num}/{total_sections} produced {len(files)} files: {[f['path'] for f in files]}")
    else:
        logger.warning(f"[ai] Section {section_num}/{total_sections} returned empty output")

    # Report section completion and files in real-time
    if on_status:
        comp_names = [c['name'] for c in components]
        await on_status({
            "type": "section_complete",
            "section": section_num,
            "total": total_sections,
            "components": comp_names,
        })
        for f in files:
            await on_status({
                "type": "file_write",
                "file": f["path"],
                "action": "create",
                "lines": f["content"].count("\n") + 1,
            })

    return {
        "section_index": section_index,
        "files": files,
        "deps": deps,
        "components": components,
        "usage": u,
    }


async def generate_clone_parallel(
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
    on_status=None,
) -> dict:
    """Parallel multi-pass clone: all section AI calls run concurrently, then one assembly call.

    Returns {"files": [...], "deps": [...], "usage": {...}}.
    """
    import asyncio as _asyncio

    client = get_openrouter_client()
    n = len(screenshots)
    model = "anthropic/claude-sonnet-4.5"
    t_start = time.time()

    logger.info(f"[ai] Parallel generation: {n} sections")
    if on_status:
        await on_status(f"Generating {n} sections in parallel...")

    # Fire all section calls concurrently
    positions = scroll_positions or [0] * n
    tasks = [
        _generate_one_section(
            client=client,
            model=model,
            section_index=i,
            total_sections=n,
            screenshot_b64=screenshot_b64,
            html=html,
            image_urls=image_urls,
            styles=styles,
            font_links=font_links,
            icons=icons,
            svgs=svgs,
            logos=logos,
            interactives=interactives,
            linked_pages=linked_pages,
            scroll_y=positions[i] if i < len(positions) else 0,
            total_height=total_height,
            on_status=on_status,
        )
        for i, screenshot_b64 in enumerate(screenshots)
    ]

    section_results = await _asyncio.gather(*tasks, return_exceptions=True)

    # Collect results in section order
    all_files: list[dict] = []
    all_deps: list[str] = []
    all_components: list[dict] = []
    total_tokens_in = 0
    total_tokens_out = 0
    api_calls = 0

    for i, result in enumerate(section_results):
        if isinstance(result, Exception):
            logger.error(f"[ai] Section {i + 1}/{n} failed: {result}")
            continue

        for f in result["files"]:
            all_files.append(f)
        for d in result["deps"]:
            if d not in all_deps:
                all_deps.append(d)
        all_components.extend(result["components"])
        total_tokens_in += result["usage"]["tokens_in"]
        total_tokens_out += result["usage"]["tokens_out"]
        api_calls += 1

    # Deduplicate files: if two sections produced the same filename,
    # keep the one from the more appropriate section (first occurrence wins
    # since results are in section order — section 1's Navbar beats section 3's).
    seen_paths: dict[str, int] = {}  # path → section index
    deduped_files: list[dict] = []
    deduped_components: list[dict] = []
    dupes_removed = 0
    for f, comp in zip(all_files, all_components):
        if f["path"] in seen_paths:
            dupes_removed += 1
            logger.info(f"[ai] Dedup: dropping duplicate '{f['path']}' (kept from section {seen_paths[f['path']] + 1})")
            continue
        seen_paths[f["path"]] = comp.get("section_index", 0)
        deduped_files.append(f)
        deduped_components.append(comp)
    all_files = deduped_files
    all_components = deduped_components

    t_sections = time.time() - t_start
    dedup_note = f", {dupes_removed} duplicates removed" if dupes_removed else ""
    logger.info(f"[ai] All {n} sections complete in {t_sections:.1f}s (parallel), {len(all_files)} component files{dedup_note}")

    if not all_components:
        logger.error("[ai] No components generated across all sections")
        return {"files": [], "deps": []}

    # Assembly — build page.tsx programmatically (no AI call needed)
    if on_status:
        await on_status("Assembling page.tsx...")

    logger.info(f"[ai] Assembling page.tsx from {len(all_components)} components")

    imports = [f"import {c['name']} from '@/components/{c['name']}';" for c in all_components]
    renders = "\n      ".join(f"<{c['name']} />" for c in all_components)
    page_content = (
        '"use client";\n'
        + "\n".join(imports)
        + "\n\nexport default function Page() {\n"
        + "  return (\n"
        + "    <>\n"
        + f"      {renders}\n"
        + "    </>\n"
        + "  );\n"
        + "}\n"
    )

    all_files.insert(0, {"path": "app/page.tsx", "content": page_content})

    if on_status:
        await on_status({
            "type": "file_write",
            "file": "app/page.tsx",
            "action": "create",
            "lines": page_content.count("\n") + 1,
        })

    unique_deps = list(dict.fromkeys(all_deps))
    if unique_deps:
        logger.info(f"[ai] Combined deps: {', '.join(unique_deps)}")

    total_cost = _calc_cost(total_tokens_in, total_tokens_out, model)
    t_total = time.time() - t_start
    logger.info(
        f"[ai] Parallel generation complete: {len(all_files)} files, {api_calls} API calls, "
        f"{total_tokens_in} tokens in, {total_tokens_out} tokens out, "
        f"${total_cost:.4f} total cost, {t_total:.1f}s"
    )
    return {
        "files": all_files,
        "deps": unique_deps,
        "usage": {
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
            "total_cost": total_cost,
            "api_calls": api_calls,
            "model": model,
            "duration_s": round(t_total, 1),
        },
    }


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

    # Multi-pass for multiple screenshots — all sections in parallel
    if n > 1:
        logger.info(f"[ai] {n} screenshots → using parallel multi-pass generation")
        return await generate_clone_parallel(
            html=html,
            screenshots=screenshots,
            image_urls=image_urls,
            url=url,
            styles=styles,
            font_links=font_links,
            icons=icons,
            svgs=svgs,
            logos=logos,
            interactives=interactives,
            linked_pages=linked_pages,
            scroll_positions=scroll_positions,
            total_height=total_height,
            on_status=on_status,
        )

    # Single screenshot — original single-call behavior
    client = get_openrouter_client()

    prompt_len = len(html) + sum(len(u) for u in image_urls)
    logger.info(f"[ai] Generating clone: 1 screenshot, {len(html)} chars HTML, {len(image_urls)} images, ~{prompt_len // 1000}k prompt chars")

    if on_status:
        await on_status("Analyzing page with AI...")

    prompt = build_prompt(
        html, image_urls, n,
        styles=styles, font_links=font_links,
        icons=icons, svgs=svgs, logos=logos,
        interactives=interactives,
        linked_pages=linked_pages,
    )

    content: list = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshots[0]}"},
        },
        {
            "type": "text",
            "text": prompt,
        },
    ]

    if on_status:
        await on_status("Sending screenshot to AI for cloning...")

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
        max_tokens=16000,
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
