import os
import json
import uuid
import asyncio
import logging
import time
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from pydantic import BaseModel

from app.services.scraper import scrape_and_capture
from app.services.ai_generator import generate_clone
from app.services.sandbox import (
    setup_sandbox_shell,
    upload_file_to_sandbox,
    start_dev_server,
)
from app.services.template_loader import get_template_files
from app.database import (
    save_clone,
    get_clones,
    get_clone,
    upload_static_files,
    download_static_file,
    _guess_content_type,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Concurrency & rate limiting ──
MAX_CONCURRENT_CLONES = int(os.getenv("MAX_CONCURRENT_CLONES", "5"))
_clone_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLONES)

# Simple per-IP rate limiter
_rate_limit_map: dict[str, float] = {}
RATE_LIMIT_SECONDS = 10

# Map clone_id → (Daytona sandbox preview URL, creation timestamp)
_sandbox_urls: dict[str, tuple[str, float]] = {}
SANDBOX_MAX_AGE = 600  # 10 minutes — assume dead after this

# Clones currently being re-created (to avoid duplicate sandbox creation)
_recreating: set[str] = set()
# Clones where re-creation permanently failed (no source files in storage)
_recreation_failed: set[str] = set()

LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<style>body{display:flex;align-items:center;justify-content:center;height:100vh;margin:0;
font-family:system-ui,sans-serif;background:#0a0a0a;color:#fff}
.spinner{width:24px;height:24px;border:3px solid #333;border-top-color:#fff;
border-radius:50%;animation:spin 0.8s linear infinite;margin-right:12px}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div class="spinner"></div>Rebuilding sandbox… this page will auto-refresh.</body></html>"""


class CloneRequest(BaseModel):
    url: str


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"



@router.post("/api/clone")
async def clone_website(request: CloneRequest, raw_request: Request):
    """Clone a website: scrape, screenshot, generate Next.js files, build static, upload to Supabase Storage."""
    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Rate limit per IP
    client_ip = raw_request.client.host if raw_request.client else "unknown"
    now = time.time()
    last_request = _rate_limit_map.get(client_ip, 0)
    if now - last_request < RATE_LIMIT_SECONDS:
        raise HTTPException(status_code=429, detail="Please wait before starting another clone")
    _rate_limit_map[client_ip] = now

    # Check concurrency
    if _clone_semaphore._value == 0:
        logger.warning(f"[clone] Rejected request from {client_ip}: all {MAX_CONCURRENT_CLONES} slots busy")
        raise HTTPException(status_code=503, detail=f"Server busy — {MAX_CONCURRENT_CLONES} clones already in progress. Try again shortly.")

    status_queue: asyncio.Queue = asyncio.Queue()

    async def on_status(message):
        await status_queue.put(message)

    async def event_stream():
        clone_id = str(uuid.uuid4())
        logger.info(f"[clone:{clone_id}] Starting clone for {url} (slots={_clone_semaphore._value}/{MAX_CONCURRENT_CLONES})")

        await _clone_semaphore.acquire()
        try:
            # Step 1: Scrape the website
            yield sse_event({"status": "scraping", "message": "Scraping website..."})
            t0 = time.time()
            try:
                scrape_result = await scrape_and_capture(url)
                screenshots = scrape_result["screenshots"]
                html_source = scrape_result["html"]
                image_urls = scrape_result["image_urls"]
                scraped_styles = scrape_result.get("styles")
                font_links = scrape_result.get("font_links", [])
                scraped_icons = scrape_result.get("icons")
                scraped_svgs = scrape_result.get("svgs", [])
                scraped_logos = scrape_result.get("logos", [])
                scraped_interactives = scrape_result.get("interactives", [])
                scraped_linked_pages = scrape_result.get("linked_pages", [])
            except Exception as e:
                logger.error(f"[clone:{clone_id}] Scraping failed for {url}: {e}")
                yield sse_event({"status": "error", "message": f"Failed to scrape website: {e}"})
                return
            t_scrape = time.time() - t0
            logger.info(
                f"[clone:{clone_id}] Scrape complete: {len(screenshots)} screenshots, "
                f"{len(html_source)} chars HTML, {len(image_urls)} images ({t_scrape:.1f}s)"
            )

            yield sse_event({
                "status": "screenshot",
                "message": f"Captured {len(screenshots)} viewport screenshots ({t_scrape:.0f}s)",
                "screenshot": screenshots[0] if screenshots else "",
            })

            # Step 2: AI generation + sandbox setup IN PARALLEL
            yield sse_event({"status": "generating", "message": "AI is generating the clone..."})
            t1 = time.time()

            all_template_files = get_template_files()

            async def _setup_sandbox():
                try:
                    return await setup_sandbox_shell(
                        template_files=all_template_files,
                        clone_id=clone_id,
                        on_status=on_status,
                    )
                except Exception as e:
                    logger.error(f"[clone:{clone_id}] Sandbox setup failed: {e}")
                    return False

            sandbox_task = asyncio.create_task(_setup_sandbox())

            # Run AI generation concurrently
            try:
                ai_result = await generate_clone(
                    html=html_source,
                    screenshots=screenshots,
                    image_urls=image_urls,
                    url=url,
                    styles=scraped_styles,
                    font_links=font_links,
                    icons=scraped_icons,
                    svgs=scraped_svgs,
                    logos=scraped_logos,
                    interactives=scraped_interactives,
                    linked_pages=scraped_linked_pages,
                    on_status=on_status,
                )
                files = ai_result["files"]
            except Exception as e:
                logger.error(f"[clone:{clone_id}] AI generation failed: {e}")
                sandbox_task.cancel()
                yield sse_event({"status": "error", "message": str(e)})
                return
            t_ai = time.time() - t1
            logger.info(f"[clone:{clone_id}] AI generation complete: {len(files)} files ({t_ai:.1f}s)")

            if not files:
                sandbox_task.cancel()
                yield sse_event({"status": "error", "message": "AI returned no files"})
                return

            # Stream file info to frontend
            for f in files:
                line_count = f["content"].count("\n") + 1
                yield sse_event({
                    "status": "file_write",
                    "type": "file_write",
                    "file": f["path"],
                    "action": "create",
                    "lines": line_count,
                })

            # Wait for sandbox to be ready
            yield sse_event({"status": "deploying", "message": "Waiting for sandbox..."})
            t2 = time.time()
            sandbox_ready = await sandbox_task
            t_sandbox_wait = time.time() - t2
            logger.info(f"[clone:{clone_id}] Sandbox ready={sandbox_ready} (waited {t_sandbox_wait:.1f}s)")

            preview_url = None

            if sandbox_ready:
                # Upload generated files to sandbox
                yield sse_event({"status": "deploying", "message": "Uploading generated code to sandbox..."})

                for f in files:
                    success = upload_file_to_sandbox(clone_id, f["path"], f["content"])
                    if success:
                        yield sse_event({
                            "status": "file_upload",
                            "message": f"Uploaded {f['path']}",
                            "file": f["path"],
                        })
                    await asyncio.sleep(0.05)

                # Start dev server and get live preview URL
                yield sse_event({"status": "deploying", "message": "Starting live preview..."})
                dev_url = await start_dev_server(
                    clone_id=clone_id,
                    on_status=on_status,
                )

                if dev_url:
                    _sandbox_urls[clone_id] = (dev_url, time.time())
                    preview_url = f"/api/sandbox/{clone_id}/"
                    logger.info(f"[clone:{clone_id}] Live preview proxied at {preview_url} → {dev_url}")

                    # Persist source files to storage for sandbox re-creation later
                    source_files = {f["path"]: f["content"].encode("utf-8") for f in files}
                    upload_static_files(clone_id, source_files)
                else:
                    logger.warning(f"[clone:{clone_id}] Dev server failed to start")
                    yield sse_event({"status": "error", "message": "Preview failed to start"})
                    return
            else:
                logger.warning(f"[clone:{clone_id}] No sandbox available")
                yield sse_event({"status": "error", "message": "Sandbox unavailable"})
                return

            t_total = time.time() - t0
            total_lines = sum(f["content"].count("\n") + 1 for f in files)
            logger.info(
                f"[clone:{clone_id}] DONE: {t_total:.1f}s total "
                f"(scrape={t_scrape:.1f}s, ai={t_ai:.1f}s, sandbox_wait={t_sandbox_wait:.1f}s) "
                f"| {len(files)} files, {total_lines} lines"
            )

            # Only save to DB if we have a working preview
            db_record = await save_clone(
                url=url,
                preview_url=preview_url,
            )

            if db_record and "id" in db_record:
                clone_id = db_record["id"]
                logger.info(f"[clone:{clone_id}] Saved to database")

            # Build file list for the frontend code viewer
            file_list = [
                {
                    "path": f["path"],
                    "content": f["content"],
                    "lines": f["content"].count("\n") + 1,
                }
                for f in files
            ]

            scaffold_paths = sorted(set(tf["path"] for tf in all_template_files))

            yield sse_event({
                "status": "done",
                "message": "Clone complete!",
                "preview_url": preview_url,
                "clone_id": clone_id,
                "files": file_list,
                "scaffold_paths": scaffold_paths,
            })

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"[clone:{clone_id}] Unhandled error for {url}")
            yield sse_event({"status": "error", "message": str(e)})
        finally:
            _clone_semaphore.release()
            logger.info(f"[clone:{clone_id}] Released slot (slots={_clone_semaphore._value}/{MAX_CONCURRENT_CLONES})")

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/static/{clone_id}/{path:path}")
async def serve_static(clone_id: str, path: str):
    """Serve static files from Supabase Storage for a clone.

    Handles path rewriting for _next/ assets in HTML/JS responses.
    """
    # Default to index.html for root/empty path
    if not path or path == "/" or path.endswith("/"):
        path = path.rstrip("/")
        path = f"{path}/index.html" if path else "index.html"
        path = path.lstrip("/")

    data = download_static_file(clone_id, path)

    # If not found and no extension, try path/index.html (Next.js static pages)
    if data is None and "." not in path.split("/")[-1]:
        data = download_static_file(clone_id, f"{path}/index.html")
        if data is None:
            data = download_static_file(clone_id, f"{path}.html")

    if data is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    content_type = _guess_content_type(path)
    proxy_base = f"/api/static/{clone_id}"

    # For HTML responses, rewrite /_next/ paths to go through our proxy
    if "text/html" in content_type:
        body = data.decode("utf-8", errors="replace")
        body = body.replace('"/_next/', f'"{proxy_base}/_next/')
        body = body.replace("'/_next/", f"'{proxy_base}/_next/")
        return HTMLResponse(content=body)

    # For JS responses, rewrite chunk loading paths
    if "javascript" in content_type:
        body = data.decode("utf-8", errors="replace")
        body = body.replace('"/_next/', f'"{proxy_base}/_next/')
        body = body.replace("\"/_next/", f"\"{proxy_base}/_next/")
        body = body.replace("'/_next/", f"'{proxy_base}/_next/")
        return Response(content=body.encode("utf-8"), media_type=content_type)

    # Everything else (CSS, images, fonts) pass through as-is
    return Response(content=data, media_type=content_type)


async def _recreate_sandbox(clone_id: str) -> None:
    """Recreate a Daytona sandbox from source files stored in Supabase Storage."""
    if clone_id in _recreating:
        return
    _recreating.add(clone_id)

    try:
        from app.database import get_supabase, STORAGE_BUCKET
        client = get_supabase()
        if not client:
            logger.error(f"[recreate:{clone_id}] No Supabase client")
            return

        # Download source files from storage
        bucket = client.storage.from_(STORAGE_BUCKET)
        source_files: list[dict] = []

        def _list_recursive(prefix: str):
            items = bucket.list(prefix)
            for item in items:
                name = item["name"] if isinstance(item, dict) else str(item)
                full_path = f"{prefix}/{name}"
                if "." in name:
                    try:
                        data = bucket.download(full_path)
                        rel_path = full_path.replace(f"{clone_id}/", "", 1)
                        source_files.append({"path": rel_path, "content": data.decode("utf-8")})
                    except Exception:
                        pass
                else:
                    _list_recursive(full_path)

        _list_recursive(clone_id)

        if not source_files:
            logger.error(f"[recreate:{clone_id}] No source files found in storage")
            _recreation_failed.add(clone_id)
            return

        logger.info(f"[recreate:{clone_id}] Downloaded {len(source_files)} source files, creating sandbox...")

        # Create sandbox, upload template + source files, start dev server
        all_template_files = get_template_files()
        sandbox_ok = await setup_sandbox_shell(
            template_files=all_template_files,
            clone_id=clone_id,
        )

        if not sandbox_ok:
            logger.error(f"[recreate:{clone_id}] Sandbox setup failed")
            return

        for f in source_files:
            upload_file_to_sandbox(clone_id, f["path"], f["content"])

        dev_url = await start_dev_server(clone_id=clone_id)
        if dev_url:
            _sandbox_urls[clone_id] = (dev_url, time.time())
            logger.info(f"[recreate:{clone_id}] Sandbox recreated: {dev_url}")
        else:
            logger.error(f"[recreate:{clone_id}] Dev server failed to start")
    except Exception as e:
        logger.error(f"[recreate:{clone_id}] Recreation failed: {e}")
    finally:
        _recreating.discard(clone_id)


@router.get("/api/sandbox/{clone_id}/{path:path}")
async def proxy_sandbox(clone_id: str, path: str):
    """Reverse-proxy requests to the Daytona sandbox dev server.

    If the sandbox is dead, triggers re-creation from stored source files
    and returns a loading page that auto-refreshes.
    """
    entry = _sandbox_urls.get(clone_id)

    # If sandbox is stale (older than max age), treat as dead
    if entry:
        base_url, created_at = entry
        if time.time() - created_at > SANDBOX_MAX_AGE:
            logger.info(f"[proxy:{clone_id}] Sandbox stale ({int(time.time() - created_at)}s old), will recreate")
            _sandbox_urls.pop(clone_id, None)
            entry = None

    # Sandbox not in memory or stale — trigger re-creation or show error
    if not entry:
        if clone_id in _recreation_failed:
            return HTMLResponse(
                content='<!DOCTYPE html><html><body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:system-ui;background:#0a0a0a;color:#fff"><p>This clone has no saved source files and cannot be recreated.</p></body></html>',
                status_code=404,
            )
        if clone_id not in _recreating:
            asyncio.create_task(_recreate_sandbox(clone_id))
        return HTMLResponse(content=LOADING_HTML)

    base_url = entry[0]
    target = f"{base_url.rstrip('/')}/{path}"
    proxy_base = f"/api/sandbox/{clone_id}"

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(target)
    except Exception as e:
        logger.warning(f"[proxy:{clone_id}] Sandbox unreachable, triggering re-creation: {e}")
        _sandbox_urls.pop(clone_id, None)
        if clone_id not in _recreating:
            asyncio.create_task(_recreate_sandbox(clone_id))
        return HTMLResponse(content=LOADING_HTML)

    # If Daytona returns an error (sandbox deleted), trigger re-creation
    if resp.status_code >= 400:
        logger.warning(f"[proxy:{clone_id}] Sandbox returned {resp.status_code}, triggering re-creation")
        _sandbox_urls.pop(clone_id, None)
        if clone_id not in _recreating:
            asyncio.create_task(_recreate_sandbox(clone_id))
        return HTMLResponse(content=LOADING_HTML)

    content_type = resp.headers.get("content-type", "")

    if "text/html" in content_type:
        body = resp.text
        body = body.replace('"/_next/', f'"{proxy_base}/_next/')
        body = body.replace("'/_next/", f"'{proxy_base}/_next/")
        return HTMLResponse(content=body, status_code=resp.status_code)

    if "javascript" in content_type:
        body = resp.text
        body = body.replace('"/_next/', f'"{proxy_base}/_next/')
        body = body.replace("'/_next/", f"'{proxy_base}/_next/")
        return Response(content=body.encode("utf-8"), media_type=content_type)

    return Response(content=resp.content, media_type=content_type, status_code=resp.status_code)


@router.get("/api/preview/{clone_id}")
async def preview_clone(clone_id: str):
    """Redirect to sandbox proxy or static preview."""
    if clone_id in _sandbox_urls:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/api/sandbox/{clone_id}/")

    record = await get_clone(clone_id)
    if record and record.get("preview_url", "").startswith("/api/"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=record["preview_url"])

    raise HTTPException(status_code=404, detail="Clone not found")


@router.get("/api/clones")
async def list_clones(page: int = 1, per_page: int = 30):
    """List clone history with pagination."""
    page = max(1, page)
    per_page = max(1, min(100, per_page))
    return await get_clones(page=page, per_page=per_page)


@router.get("/api/clones/{clone_id}")
async def get_clone_detail(clone_id: str):
    """Get a specific clone with all details."""
    record = await get_clone(clone_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Clone not found")

    record["preview_url"] = record.get("preview_url") or f"/api/preview/{clone_id}"
    return record
