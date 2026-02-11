import os
import time
import asyncio
import logging

logger = logging.getLogger(__name__)

# ── In-memory stores with TTL tracking ──
# Each entry is timestamped so we can evict stale clones.

_preview_store: dict[str, list[dict]] = {}
_sandbox_urls: dict[str, str] = {}
_sandbox_instances: dict[str, object] = {}
_sandbox_project_dirs: dict[str, str] = {}
_clone_timestamps: dict[str, float] = {}  # clone_id -> creation time

# Max age before auto-eviction (1 hour)
_MAX_CLONE_AGE_SECS = 3600
# Max clones to keep in memory
_MAX_CLONES = 50
# Daytona hard limit on concurrent sandboxes
DAYTONA_MAX_SANDBOXES = int(os.getenv("DAYTONA_MAX_SANDBOXES", "10"))


def _evict_stale_clones():
    """Remove clones older than _MAX_CLONE_AGE_SECS or if over _MAX_CLONES."""
    now = time.time()
    stale = [
        cid for cid, ts in _clone_timestamps.items()
        if now - ts > _MAX_CLONE_AGE_SECS
    ]
    # Also evict oldest if over capacity
    if len(_clone_timestamps) - len(stale) > _MAX_CLONES:
        by_age = sorted(_clone_timestamps.items(), key=lambda x: x[1])
        for cid, _ in by_age[:len(_clone_timestamps) - _MAX_CLONES]:
            if cid not in stale:
                stale.append(cid)

    for cid in stale:
        _preview_store.pop(cid, None)
        _sandbox_urls.pop(cid, None)
        _sandbox_instances.pop(cid, None)
        _sandbox_project_dirs.pop(cid, None)
        _clone_timestamps.pop(cid, None)

    if stale:
        logger.info(f"[cleanup] Evicted {len(stale)} stale clones, {len(_clone_timestamps)} remaining")


def release_clone(clone_id: str):
    """Release all in-memory resources for a clone. Call after sandbox is no longer needed."""
    _sandbox_instances.pop(clone_id, None)
    _sandbox_project_dirs.pop(clone_id, None)
    # Keep _preview_store and _sandbox_urls for preview access
    logger.info(f"[sandbox:{clone_id}] Released sandbox instance from memory")


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
        logger.info(f"[sandbox:{clone_id}] DAYTONA_API_KEY not set, using fallback preview")
        return None

    # Evict stale clones before creating a new one
    _evict_stale_clones()

    t_start = time.time()
    try:
        from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxFromImageParams, Image

        api_url = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, api_url=api_url)
        daytona = Daytona(config)

        if on_status:
            await on_status("Creating sandbox...")

        logger.info(f"[sandbox:{clone_id}] Creating Daytona sandbox...")
        image = Image.base("node:20-slim")
        params = CreateSandboxFromImageParams(
            image=image,
            language="typescript",
            public=True,
        )
        sandbox = daytona.create(params, timeout=180)
        home_dir = sandbox.get_user_home_dir()
        project_dir = f"{home_dir}/project"
        t_create = time.time() - t_start

        # Store sandbox for later file uploads
        _sandbox_instances[clone_id] = sandbox
        _sandbox_project_dirs[clone_id] = project_dir
        _clone_timestamps[clone_id] = time.time()

        sandbox_id = getattr(sandbox, "id", "unknown")
        logger.info(f"[sandbox:{clone_id}] Created sandbox {sandbox_id} in {t_create:.1f}s")

        if on_status:
            await on_status("Uploading template files...")

        t_upload = time.time()
        for tf in template_files:
            file_path = f"{project_dir}/{tf['path']}"
            sandbox.fs.upload_file(
                tf["content"].encode("utf-8"),
                file_path,
            )

        logger.info(f"[sandbox:{clone_id}] Uploaded {len(template_files)} template files in {time.time() - t_upload:.1f}s")

        # npm install
        if on_status:
            await on_status("Installing dependencies (npm install)...")

        t_npm = time.time()
        try:
            install_result = sandbox.process.exec(
                f"cd {project_dir} && npm install --prefer-offline",
                timeout=120,
            )
            logger.info(f"[sandbox:{clone_id}] npm install exit={install_result.exit_code} in {time.time() - t_npm:.1f}s")
            if install_result.exit_code != 0:
                logger.error(f"[sandbox:{clone_id}] npm install failed: {install_result.result[:500]}")
        except Exception as e:
            logger.error(f"[sandbox:{clone_id}] npm install exception: {e}")

        # Start next dev in background
        if on_status:
            await on_status("Starting Next.js dev server...")

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
        logger.info(f"[sandbox:{clone_id}] Polling for dev server (max 25 attempts)...")

        for attempt in range(25):
            await asyncio.sleep(4)
            try:
                check = sandbox.process.exec(
                    'node -e "const h=require(\'http\');h.get(\'http://localhost:3000\',(r)=>{process.stdout.write(String(r.statusCode));process.exit(0)}).on(\'error\',()=>{process.stdout.write(\'fail\');process.exit(1)})"',
                    timeout=10,
                )
                result_text = check.result.strip() if check.result else ""
                if "200" in result_text or "304" in result_text:
                    logger.info(f"[sandbox:{clone_id}] Dev server ready after {attempt + 1} attempts ({time.time() - t_start:.1f}s total)")
                    break
            except Exception as e:
                if attempt % 5 == 4:
                    logger.info(f"[sandbox:{clone_id}] Still polling (attempt {attempt + 1}): {e}")
        else:
            logger.warning(f"[sandbox:{clone_id}] Dev server did not start after 25 attempts")
            try:
                log_check = sandbox.process.exec("tail -20 /tmp/next.log", timeout=5)
                logger.warning(f"[sandbox:{clone_id}] next.log:\n{log_check.result}")
            except Exception:
                pass

        # Get preview URL
        preview = sandbox.get_preview_link(3000)
        preview_url = preview.url if hasattr(preview, "url") else str(preview)
        logger.info(f"[sandbox:{clone_id}] Ready: {preview_url} (total setup {time.time() - t_start:.1f}s)")

        _sandbox_urls[clone_id] = preview_url
        return preview_url

    except Exception as e:
        logger.error(f"[sandbox:{clone_id}] Creation failed after {time.time() - t_start:.1f}s: {e}")
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
        # Ensure parent directory exists
        parent_dir = "/".join(full_path.split("/")[:-1])
        try:
            sandbox.process.exec(f"mkdir -p {parent_dir}", timeout=5)
        except Exception:
            pass
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
        result = sandbox.process.exec("tail -100 /tmp/next.log", timeout=5)
        return result.result if result.result else None
    except Exception as e:
        logger.warning(f"Failed to read sandbox logs for {clone_id}: {e}")
        return None


async def cleanup_sandbox(clone_id: str) -> None:
    """Clean up a Daytona sandbox and release in-memory resources."""
    api_key = os.getenv("DAYTONA_API_KEY", "")
    if not api_key:
        return

    sandbox = _sandbox_instances.get(clone_id)
    if not sandbox:
        logger.info(f"[sandbox:{clone_id}] No sandbox instance to clean up")
        return

    sandbox_id = getattr(sandbox, "id", "unknown")
    try:
        from daytona_sdk import Daytona, DaytonaConfig

        api_url = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, api_url=api_url)
        daytona = Daytona(config)
        sb = daytona.get(sandbox_id)
        sb.delete()
        logger.info(f"[sandbox:{clone_id}] Deleted Daytona sandbox {sandbox_id}")
    except Exception as e:
        logger.error(f"[sandbox:{clone_id}] Failed to delete sandbox {sandbox_id}: {e}")
    finally:
        release_clone(clone_id)


def get_preview_files(clone_id: str) -> list[dict] | None:
    """Get stored files for preview fallback."""
    return _preview_store.get(clone_id)
