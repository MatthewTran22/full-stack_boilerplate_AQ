import os
from openai import AsyncOpenAI


def get_openrouter_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
    )


SYSTEM_PROMPT = """You are a website cloning expert. Given a screenshot of a website and its HTML source code, generate a single-file HTML clone that visually reproduces the original as closely as possible.

Rules:
1. Output ONLY valid HTML - no markdown, no explanations, no code fences
2. Use Tailwind CSS via CDN for styling: <script src="https://cdn.tailwindcss.com"></script>
3. Include all visual elements: layout, colors, typography, spacing, images
4. For images, use placeholder images from https://placehold.co with appropriate dimensions
5. Make the page responsive
6. Include inline <style> tags for any custom CSS that Tailwind can't handle
7. The output must be a complete, self-contained HTML document starting with <!DOCTYPE html>
8. Preserve the overall structure and visual hierarchy of the original page
9. Use appropriate semantic HTML elements"""


def strip_code_fences(content: str) -> str:
    """Remove markdown code fences from AI output."""
    content = content.strip()
    if content.startswith("```html"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


async def generate_clone(html: str, screenshot_b64: str, url: str) -> str:
    """Generate a clone of the website using OpenRouter vision model."""
    client = get_openrouter_client()

    truncated_html = html[:100000]
    if len(html) > 100000:
        truncated_html += "\n<!-- HTML truncated -->"

    user_content: list = [
        {
            "type": "text",
            "text": f"Clone this website: {url}\n\nHere is the source HTML for reference:\n\n{truncated_html}",
        },
    ]

    if screenshot_b64:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        })

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    response = await client.chat.completions.create(
        model="anthropic/claude-sonnet-4",
        messages=messages,
        max_tokens=64000,
        temperature=0,
    )

    content = response.choices[0].message.content or ""
    return strip_code_fences(content)
