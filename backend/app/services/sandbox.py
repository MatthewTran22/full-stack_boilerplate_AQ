import os
import re
import time
import asyncio
import logging

logger = logging.getLogger(__name__)

# ── In-memory stores ──
_sandbox_instances: dict[str, object] = {}
_sandbox_project_dirs: dict[str, str] = {}
# clone_id → Daytona sandbox ID (persists even if object is lost)
_sandbox_id_map: dict[str, str] = {}

# Daytona hard limit on concurrent sandboxes
DAYTONA_MAX_SANDBOXES = int(os.getenv("DAYTONA_MAX_SANDBOXES", "10"))


async def setup_sandbox_shell(
    template_files: list[dict],
    clone_id: str,
    on_status=None,
) -> bool:
    """
    Create a Daytona sandbox, upload template files, and run npm install.

    The sandbox stays alive so AI-generated files can be uploaded later,
    then build_and_download_static() will export and tear it down.

    Returns True on success, False on failure.
    """
    api_key = os.getenv("DAYTONA_API_KEY", "")

    if not api_key:
        logger.info(f"[sandbox:{clone_id}] DAYTONA_API_KEY not set, skipping sandbox")
        return False

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

        # Store sandbox for later file uploads + build
        _sandbox_instances[clone_id] = sandbox
        _sandbox_project_dirs[clone_id] = project_dir

        sandbox_id = getattr(sandbox, "id", "unknown")
        _sandbox_id_map[clone_id] = sandbox_id
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
                return False
        except Exception as e:
            logger.error(f"[sandbox:{clone_id}] npm install exception: {e}")
            return False

        logger.info(f"[sandbox:{clone_id}] Setup complete in {time.time() - t_start:.1f}s")
        return True

    except Exception as e:
        logger.error(f"[sandbox:{clone_id}] Creation failed after {time.time() - t_start:.1f}s: {e}")
        return False


def upload_file_to_sandbox(clone_id: str, file_path: str, content: str) -> bool:
    """
    Upload a single file to a running sandbox.

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


async def install_extra_deps(
    clone_id: str,
    deps: list[str],
    on_status=None,
) -> bool:
    """Install extra npm dependencies requested by the AI into the sandbox.

    Sanitizes package names to prevent shell injection.
    Returns True on success, False on failure.
    """
    sandbox = _sandbox_instances.get(clone_id)
    project_dir = _sandbox_project_dirs.get(clone_id)

    if not sandbox or not project_dir:
        logger.warning(f"[deps:{clone_id}] No sandbox instance for extra deps install")
        return False

    # Sanitize: only allow valid npm package names
    safe_pattern = re.compile(r'^@?[a-zA-Z0-9][\w./-]*$')
    safe_deps = [d for d in deps if safe_pattern.match(d)]
    rejected = [d for d in deps if not safe_pattern.match(d)]

    if rejected:
        logger.warning(f"[deps:{clone_id}] Rejected invalid dep names: {rejected}")

    if not safe_deps:
        logger.info(f"[deps:{clone_id}] No valid extra deps to install")
        return True

    dep_str = " ".join(safe_deps)
    logger.info(f"[deps:{clone_id}] Installing {len(safe_deps)} extra deps: {dep_str}")

    if on_status:
        await on_status(f"Installing extra dependencies: {', '.join(safe_deps)}...")

    try:
        result = sandbox.process.exec(
            f"cd {project_dir} && npm install --save {dep_str}",
            timeout=60,
        )
        if result.exit_code != 0:
            logger.error(f"[deps:{clone_id}] npm install failed (exit={result.exit_code}): {result.result[:500]}")
            return False
        logger.info(f"[deps:{clone_id}] Extra deps installed successfully")
        return True
    except Exception as e:
        logger.error(f"[deps:{clone_id}] Extra deps install exception: {e}")
        return False


async def start_dev_server(
    clone_id: str,
    on_status=None,
) -> str | None:
    """
    Start `next dev` in the sandbox and return the public preview URL.

    The sandbox stays alive so the preview remains accessible.
    Returns the public URL or None on failure.
    """
    sandbox = _sandbox_instances.get(clone_id)
    project_dir = _sandbox_project_dirs.get(clone_id)

    if not sandbox or not project_dir:
        logger.error(f"[dev:{clone_id}] No sandbox instance found")
        return None

    try:
        if on_status:
            await on_status("Starting dev server...")

        logger.info(f"[dev:{clone_id}] Starting next dev on port 3000...")
        # Start next dev in background (non-blocking)
        sandbox.process.exec(
            f"cd {project_dir} && npx next dev -p 3000 > /tmp/next-dev.log 2>&1 &",
            timeout=10,
        )

        # Wait for the dev server to be ready
        for attempt in range(15):
            await asyncio.sleep(2)
            try:
                check = sandbox.process.exec(
                    "wget -q --spider http://localhost:3000 2>&1 && echo OK || echo FAIL",
                    timeout=5,
                )
                result = check.result.strip()
                if "OK" in result:
                    logger.info(f"[dev:{clone_id}] Dev server ready after {(attempt + 1) * 2}s")
                    break
                # Also check if the log shows "Ready"
                log_check = sandbox.process.exec("grep -q 'Ready' /tmp/next-dev.log && echo READY || echo WAIT", timeout=5)
                if "READY" in log_check.result:
                    logger.info(f"[dev:{clone_id}] Dev server ready (from log) after {(attempt + 1) * 2}s")
                    break
            except Exception:
                pass
        else:
            try:
                log_result = sandbox.process.exec("tail -50 /tmp/next-dev.log 2>/dev/null", timeout=5)
                logger.error(f"[dev:{clone_id}] Dev server failed to start. Log:\n{log_result.result[:2000]}")
            except Exception:
                pass
            logger.error(f"[dev:{clone_id}] Dev server did not become ready in 30s")
            await cleanup_sandbox(clone_id)
            return None

        # Get public preview URL from Daytona
        preview_link = sandbox.get_preview_link(3000)
        preview_url = preview_link.url if hasattr(preview_link, "url") else str(preview_link)
        logger.info(f"[dev:{clone_id}] Preview URL: {preview_url}")

        return preview_url

    except Exception as e:
        logger.error(f"[dev:{clone_id}] Failed to start dev server: {e}")
        await cleanup_sandbox(clone_id)
        return None


async def cleanup_sandbox(clone_id: str) -> None:
    """Clean up a Daytona sandbox and release in-memory resources."""
    api_key = os.getenv("DAYTONA_API_KEY", "")
    if not api_key:
        return

    sandbox = _sandbox_instances.pop(clone_id, None)
    _sandbox_project_dirs.pop(clone_id, None)
    sandbox_id = _sandbox_id_map.pop(clone_id, None)

    # Get the Daytona sandbox ID from either the object or the ID map
    if sandbox and not sandbox_id:
        sandbox_id = getattr(sandbox, "id", None)

    if not sandbox_id:
        logger.info(f"[sandbox:{clone_id}] No sandbox ID found to clean up")
        return

    try:
        from daytona_sdk import Daytona, DaytonaConfig

        api_url = os.getenv("DAYTONA_URL", "https://app.daytona.io/api")
        config = DaytonaConfig(api_key=api_key, api_url=api_url)
        daytona = Daytona(config)
        sb = daytona.get(sandbox_id)
        daytona.delete(sb)
        logger.info(f"[sandbox:{clone_id}] Deleted Daytona sandbox {sandbox_id}")
    except Exception as e:
        logger.error(f"[sandbox:{clone_id}] Failed to delete sandbox {sandbox_id}: {e}")
