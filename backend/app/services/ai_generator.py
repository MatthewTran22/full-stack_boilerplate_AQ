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


def build_prompt(html: str, image_urls: list[str], n: int) -> str:
    """Build the clone prompt with HTML, image URLs, and screenshot count."""
    # Truncate HTML to ~60k chars to stay within context limits
    max_html = 60000
    truncated_html = html[:max_html]
    if len(html) > max_html:
        truncated_html += "\n\n... [HTML truncated] ..."

    image_list = "\n".join(f"  - {u}" for u in image_urls) if image_urls else "  (none extracted)"

    return (
        "You are a website cloning expert. Given the HTML source and a series of screenshots capturing "
        f"the ENTIRE page (scrolled top to bottom in {n} viewport-sized chunks), "
        "generate a Next.js page component (page.tsx) that visually replicates the ENTIRE page.\n\n"
        "Tech stack available in the project:\n"
        "- React 19 with Next.js App Router\n"
        "- Tailwind CSS for ALL styling (no other CSS libraries)\n"
        "- lucide-react icons — import { IconName } from \"lucide-react\"\n\n"
        "DO NOT import from @/components/ui/ or shadcn — these are NOT installed.\n"
        "DO NOT import from @/lib/utils — not available.\n"
        "Build everything with plain Tailwind CSS utility classes and native HTML elements.\n\n"
        "Rules:\n"
        "- CRITICAL: Output ONLY raw TSX code. No markdown fences (```), no explanation, no preamble, no commentary. "
        "Your response must start with \"use client\" and contain NOTHING else but valid TSX code.\n"
        '- The file MUST start with "use client" and export a default function component.\n'
        "- Everything goes in ONE file (page.tsx). Define all sub-components in the same file.\n"
        "- CRITICAL: The code must be valid TypeScript/JSX with no syntax errors. "
        "Ensure all brackets, braces, and parentheses are properly closed. "
        "In JSX, use {'<'} or {'>'} for literal angle brackets in text content. "
        "Never put raw < or > in JSX text — they cause syntax errors.\n"
        "- Use Tailwind CSS utility classes for ALL layout, spacing, colors, typography. No component libraries.\n"
        "- Use lucide-react for icons that match the original site.\n"
        "- IMAGES: Use the original image URLs with regular <img> tags (NOT next/image). Here are the image URLs extracted:\n"
        f"{image_list}\n"
        "  Use these exact URLs. Match sizing and position from the screenshots.\n"
        "  If an image URL is not listed, check the HTML source for it.\n"
        "  For SVG logos/icons inlined in the HTML, reproduce them as inline SVGs or use lucide-react equivalents.\n"
        "- FONTS: Use Tailwind font utilities. For specific Google Fonts, add a <link> tag via useEffect or next/head.\n"
        "- You may use React hooks (useState, useEffect, useRef, etc.) for interactivity and animations.\n"
        "- For animations, use Tailwind animate classes (animate-pulse, animate-bounce, etc.) or CSS transitions via className.\n"
        "- IMPORTANT: Reproduce the ENTIRE page from top to bottom, including ALL sections visible across ALL screenshots. "
        f"The {n} screenshots are sequential viewport captures from top to bottom — every section must appear in your output. "
        "The page should scroll naturally just like the original.\n"
        "- Match colors, spacing, font sizes, and layout as closely as possible to the screenshots.\n\n"
        f"Here is the page HTML (may be truncated):\n\n{truncated_html}"
    )


def parse_tsx_output(content: str) -> str:
    """Parse AI output — extract TSX code, handling preamble text and markdown fences."""
    content = content.strip()

    # If the AI wrapped code in markdown fences (possibly with preamble text before),
    # extract the code from inside the fences
    fence_match = re.search(r"```(?:tsx|typescript|jsx|ts)?\s*\n(.*?)```", content, re.DOTALL)
    if fence_match:
        content = fence_match.group(1).strip()
        logger.info("Extracted TSX from markdown code fence")
    elif content.startswith("```"):
        # Fences without closing — strip opening fence line
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    # If content still starts with non-code text (AI explanation), try to find where
    # the actual TSX starts ("use client" or import statement)
    if not content.startswith('"use client"') and not content.startswith("'use client'") and not content.startswith("import "):
        # Look for the start of actual code
        code_start = re.search(r'^(?:"use client"|\'use client\'|import )', content, re.MULTILINE)
        if code_start:
            content = content[code_start.start():]
            logger.info("Stripped preamble text before TSX code")

    # Replace smart quotes
    content = content.replace("\u201c", '"').replace("\u201d", '"')
    content = content.replace("\u2018", "'").replace("\u2019", "'")

    # Remove zero-width characters
    for ch in ["\u200b", "\u200c", "\u200d", "\ufeff", "\u00a0"]:
        content = content.replace(ch, "")

    content = content.strip()

    # Ensure "use client" is present
    if '"use client"' not in content and "'use client'" not in content:
        content = '"use client";\n' + content

    return content


async def generate_clone(
    html: str,
    screenshots: list[str],
    image_urls: list[str],
    url: str,
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

    # Build prompt
    prompt = build_prompt(html, image_urls, n)

    # Build message content: all screenshots + prompt text
    content: list = []
    for i, screenshot_b64 in enumerate(screenshots):
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

    tsx_content = parse_tsx_output(raw_output)
    files = [{"path": "app/page.tsx", "content": tsx_content}]

    # Stream file_write event
    if on_status:
        line_count = tsx_content.count("\n") + 1
        await on_status({
            "type": "file_write",
            "file": "app/page.tsx",
            "action": "create",
            "lines": line_count,
        })

    return files
