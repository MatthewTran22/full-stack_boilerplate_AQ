import os
import asyncio
import logging

logger = logging.getLogger(__name__)

# Store generated files in memory for preview fallback
_preview_store: dict[str, list[dict]] = {}

# Map clone_id -> Daytona preview base URL (e.g. "https://3000-abc123.proxy.daytona.works")
_sandbox_urls: dict[str, str] = {}

# Map clone_id -> live sandbox object (for uploading files after creation)
_sandbox_instances: dict[str, object] = {}

# Map clone_id -> project directory inside the sandbox
_sandbox_project_dirs: dict[str, str] = {}


def get_sandbox_base_url(clone_id: str) -> str | None:
    """Get the stored Daytona preview base URL for a clone."""
    return _sandbox_urls.get(clone_id)


async def setup_sandbox_shell(
    template_files: list[dict],
    clone_id: str,
    on_status=None,
) -> str | None:
    """
    Create a Daytona sandbox, upload template files (including placeholder page),
    run npm install + next dev, and return the preview URL.

    The sandbox stays alive so AI-generated files can be uploaded later via
    upload_file_to_sandbox().
    """
    api_key = os.getenv("DAYTONA_API_KEY", "")

    if not api_key:
        logger.info("DAYTONA_API_KEY not set, using fallback preview")
        return None

    try:
        from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxFromImageParams, Image

        api_url = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, api_url=api_url)
        daytona = Daytona(config)

        if on_status:
            await on_status("Creating sandbox...")

        logger.info("Creating Daytona sandbox with Node.js image...")
        image = Image.base("node:20-slim")
        params = CreateSandboxFromImageParams(
            image=image,
            language="typescript",
            public=True,
        )
        sandbox = daytona.create(params, timeout=180)
        home_dir = sandbox.get_user_home_dir()
        project_dir = f"{home_dir}/project"

        # Store sandbox for later file uploads
        _sandbox_instances[clone_id] = sandbox
        _sandbox_project_dirs[clone_id] = project_dir

        logger.info("Uploading template files...")
        if on_status:
            await on_status("Uploading template files...")

        for tf in template_files:
            file_path = f"{project_dir}/{tf['path']}"
            sandbox.fs.upload_file(
                tf["content"].encode("utf-8"),
                file_path,
            )

        logger.info(f"Uploaded {len(template_files)} template files")

        # npm install
        if on_status:
            await on_status("Installing dependencies (npm install)...")
        logger.info("Running npm install...")

        try:
            install_result = sandbox.process.exec(
                f"cd {project_dir} && npm install --prefer-offline",
                timeout=120,
            )
            logger.info(f"npm install exit code: {install_result.exit_code}")
            if install_result.exit_code != 0:
                logger.warning(f"npm install stderr: {install_result.result}")
        except Exception as e:
            logger.warning(f"npm install issue: {e}")

        # Start next dev in background
        if on_status:
            await on_status("Starting Next.js dev server...")
        logger.info("Starting next dev...")

        try:
            sandbox.process.exec(
                f"cd {project_dir} && nohup npx next dev -p 3000 > /tmp/next.log 2>&1 & disown",
                timeout=10,
            )
        except Exception:
            pass  # timeout expected for background process

        # Poll for port 3000 to be ready
        if on_status:
            await on_status("Waiting for dev server to start...")
        logger.info("Polling for Next.js dev server...")

        for attempt in range(25):
            await asyncio.sleep(4)
            try:
                check = sandbox.process.exec(
                    'node -e "const h=require(\'http\');h.get(\'http://localhost:3000\',(r)=>{process.stdout.write(String(r.statusCode));process.exit(0)}).on(\'error\',()=>{process.stdout.write(\'fail\');process.exit(1)})"',
                    timeout=10,
                )
                result_text = check.result.strip() if check.result else ""
                logger.info(f"Poll attempt {attempt + 1}: '{result_text}'")
                if "200" in result_text or "304" in result_text:
                    logger.info(f"Next.js dev server ready after {attempt + 1} attempts")
                    break
            except Exception as e:
                logger.info(f"Poll attempt {attempt + 1} exception: {e}")
        else:
            logger.warning("Next.js dev server did not become ready in time")
            try:
                log_check = sandbox.process.exec("tail -20 /tmp/next.log", timeout=5)
                logger.info(f"next dev log:\n{log_check.result}")
            except Exception:
                pass

        # Get preview URL
        preview = sandbox.get_preview_link(3000)
        preview_url = preview.url if hasattr(preview, "url") else str(preview)
        logger.info(f"Sandbox preview URL: {preview_url}")

        _sandbox_urls[clone_id] = preview_url
        return preview_url

    except Exception as e:
        logger.warning(f"Daytona sandbox creation failed: {e}")
        return None


def upload_file_to_sandbox(clone_id: str, file_path: str, content: str) -> bool:
    """
    Upload a single file to a running sandbox. Next.js hot-reload will pick it up.

    Returns True on success, False on failure.
    """
    sandbox = _sandbox_instances.get(clone_id)
    project_dir = _sandbox_project_dirs.get(clone_id)

    if not sandbox or not project_dir:
        logger.warning(f"No sandbox instance for clone {clone_id}")
        return False

    try:
        full_path = f"{project_dir}/{file_path}"
        sandbox.fs.upload_file(
            content.encode("utf-8"),
            full_path,
        )
        logger.info(f"Uploaded {file_path} to sandbox {clone_id}")
        return True
    except Exception as e:
        logger.warning(f"Failed to upload {file_path} to sandbox {clone_id}: {e}")
        return False


def get_sandbox_logs(clone_id: str) -> str | None:
    """Read the last part of the sandbox's Next.js dev server log."""
    sandbox = _sandbox_instances.get(clone_id)
    if not sandbox:
        return None
    try:
        result = sandbox.process.exec("tail -50 /tmp/next.log", timeout=5)
        return result.result if result.result else None
    except Exception as e:
        logger.warning(f"Failed to read sandbox logs for {clone_id}: {e}")
        return None


async def cleanup_sandbox(sandbox_id: str) -> None:
    """Clean up a Daytona sandbox."""
    api_key = os.getenv("DAYTONA_API_KEY", "")
    if not api_key:
        return

    try:
        from daytona_sdk import Daytona, DaytonaConfig

        api_url = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, api_url=api_url)
        daytona = Daytona(config)
        sandbox = daytona.get(sandbox_id)
        sandbox.delete()
    except Exception as e:
        logger.warning(f"Failed to cleanup sandbox {sandbox_id}: {e}")


def get_preview_files(clone_id: str) -> list[dict] | None:
    """Get stored files for preview fallback."""
    return _preview_store.get(clone_id)
