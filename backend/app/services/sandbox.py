import os
import logging

logger = logging.getLogger(__name__)

# Store generated HTML in memory for preview fallback
_preview_store: dict[str, str] = {}


async def create_sandbox(html_content: str, clone_id: str) -> str | None:
    """
    Create a Daytona sandbox and deploy the HTML content.
    Returns the public preview URL, or None if Daytona is unavailable.
    """
    api_key = os.getenv("DAYTONA_API_KEY", "")

    if not api_key:
        logger.info("DAYTONA_API_KEY not set, using fallback preview")
        _preview_store[clone_id] = html_content
        return None

    try:
        from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxParams

        target = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, target=target)
        daytona = Daytona(config)

        sandbox = daytona.create(CreateSandboxParams(language="python"))

        # Write the HTML file
        sandbox.fs.upload_file(
            "/home/daytona/index.html",
            html_content.encode("utf-8"),
        )

        # Start a simple HTTP server
        sandbox.process.exec(
            "cd /home/daytona && python3 -m http.server 8080 &",
        )

        preview_url = sandbox.get_preview_url(8080)
        logger.info(f"Sandbox created: {preview_url}")

        # Also store locally as backup
        _preview_store[clone_id] = html_content

        return preview_url

    except Exception as e:
        logger.warning(f"Daytona sandbox creation failed: {e}")
        _preview_store[clone_id] = html_content
        return None


async def cleanup_sandbox(sandbox_id: str) -> None:
    """Clean up a Daytona sandbox."""
    api_key = os.getenv("DAYTONA_API_KEY", "")
    if not api_key:
        return

    try:
        from daytona_sdk import Daytona, DaytonaConfig

        target = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, target=target)
        daytona = Daytona(config)
        sandbox = daytona.get(sandbox_id)
        daytona.delete(sandbox)
    except Exception as e:
        logger.warning(f"Failed to cleanup sandbox {sandbox_id}: {e}")


def get_preview_html(clone_id: str) -> str | None:
    """Get stored HTML content for preview fallback."""
    return _preview_store.get(clone_id)
