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
from app.services.ai_generator import generate_clone, get_used_components
from app.services.sandbox import (
    setup_sandbox_shell,
    upload_file_to_sandbox,
    get_preview_files,
    get_sandbox_base_url,
    get_sandbox_logs,
    release_clone,
    _preview_store,
    _clone_timestamps,
)
from app.services.template_loader import get_template_files
from app.database import save_clone, get_clones, get_clone

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Concurrency & rate limiting ──
# Max simultaneous clone operations (each uses Playwright + AI + Daytona)
MAX_CONCURRENT_CLONES = int(os.getenv("MAX_CONCURRENT_CLONES", "5"))
_clone_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLONES)

# Simple per-IP rate limiter: track last request time
_rate_limit_map: dict[str, float] = {}
RATE_LIMIT_SECONDS = 10  # Min seconds between clone requests per IP


class CloneRequest(BaseModel):
    url: str


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/api/clone")
async def clone_website(request: CloneRequest, raw_request: Request):
    """Clone a website: scrape, screenshot, generate Next.js files with AI, deploy to sandbox."""
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

    # Queue for status updates from both AI generator and sandbox setup
    status_queue: asyncio.Queue = asyncio.Queue()

    async def on_status(message):
        """Accept both string messages and dict events."""
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

            # Send first screenshot to frontend for verification
            yield sse_event({
                "status": "screenshot",
                "message": f"Captured {len(screenshots)} viewport screenshots ({t_scrape:.0f}s)",
                "screenshot": screenshots[0] if screenshots else "",
            })

            # Step 2: AI generation + sandbox setup IN PARALLEL
            yield sse_event({"status": "generating", "message": "AI is generating the clone..."})
            t1 = time.time()

            # Start sandbox setup immediately (don't wait for AI)
            # Upload ALL template files — we'll add the generated code after
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
                    return None

            sandbox_task = asyncio.create_task(_setup_sandbox())

            # Run AI generation concurrently
            try:
                files = await generate_clone(
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

            # Wait for sandbox to be ready (may already be done)
            yield sse_event({"status": "deploying", "message": "Waiting for sandbox..."})
            t2 = time.time()
            sandbox_url = await sandbox_task
            t_sandbox = time.time() - t1
            t_sandbox_wait = time.time() - t2
            logger.info(f"[clone:{clone_id}] Sandbox ready (waited {t_sandbox_wait:.1f}s after AI, {t_sandbox:.1f}s total parallel)")

            # Step 3: Upload generated files AFTER sandbox is ready
            if sandbox_url:
                yield sse_event({"status": "deploying", "message": "Uploading generated code..."})

                for f in files:
                    success = upload_file_to_sandbox(clone_id, f["path"], f["content"])
                    if success:
                        yield sse_event({
                            "status": "file_upload",
                            "message": f"Uploaded {f['path']}",
                            "file": f["path"],
                        })
                    await asyncio.sleep(0.1)

                # Wait for Next.js to compile the new code
                yield sse_event({"status": "deploying", "message": "Compiling..."})
                await asyncio.sleep(5)

                logs = get_sandbox_logs(clone_id)
                if logs and ("error" in logs.lower() or "Error" in logs):
                    logger.warning(f"[clone:{clone_id}] Sandbox compile issues:\n{logs[-1000:]}")

            t_total = time.time() - t0
            total_lines = sum(f["content"].count("\n") + 1 for f in files)
            logger.info(
                f"[clone:{clone_id}] DONE: {t_total:.1f}s total "
                f"(scrape={t_scrape:.1f}s, ai={t_ai:.1f}s, sandbox_wait={t_sandbox_wait:.1f}s) "
                f"| {len(files)} files, {total_lines} lines"
            )

            # Step 5: Save to database
            _preview_store[clone_id] = files
            combined_html = "\n".join(
                f"// === {f['path']} ===\n{f['content']}" for f in files
            )
            if sandbox_url:
                preview_url = f"/api/sandbox/{clone_id}/"
            else:
                preview_url = f"/api/preview/{clone_id}"

            db_record = await save_clone(
                url=url,
                generated_html=combined_html,
                sandbox_url=sandbox_url,
            )

            if db_record and "id" in db_record:
                old_clone_id = clone_id
                clone_id = db_record["id"]
                logger.info(f"[clone:{clone_id}] Remapped from temp ID {old_clone_id[:8]}...")
                # Re-key all sandbox mappings to the DB-assigned ID
                from app.services.sandbox import _sandbox_urls, _sandbox_instances, _sandbox_project_dirs
                for store in [_sandbox_urls, _sandbox_instances, _sandbox_project_dirs, _preview_store, _clone_timestamps]:
                    if old_clone_id in store:
                        store[clone_id] = store.pop(old_clone_id)
                _preview_store[clone_id] = files
                if sandbox_url:
                    preview_url = f"/api/sandbox/{clone_id}/"
                else:
                    preview_url = f"/api/preview/{clone_id}"

            # Build file list for the frontend code viewer
            file_list = [
                {
                    "path": f["path"],
                    "content": f["content"],
                    "lines": f["content"].count("\n") + 1,
                }
                for f in files
            ]

            # Scaffold paths for the frontend file tree (template files already in sandbox)
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


@router.get("/api/preview/{clone_id}")
async def preview_clone(clone_id: str):
    """Serve a combined HTML preview of generated files (fallback when no sandbox)."""
    files = get_preview_files(clone_id)

    if files is None:
        record = await get_clone(clone_id)
        if record:
            html = record.get("generated_html")
            if html:
                return HTMLResponse(content=html)

    if files is None:
        raise HTTPException(status_code=404, detail="Clone not found")

    # Find the page.tsx content and render as simple HTML for preview
    page_content = ""
    for f in files:
        if f["path"].endswith("page.tsx"):
            page_content = f["content"]
            break

    if not page_content:
        page_content = "\n".join(f["content"] for f in files)

    # Wrap in a basic HTML page for preview
    preview_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preview</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body>
<pre style="padding: 2rem; font-family: monospace; font-size: 14px; white-space: pre-wrap;">{page_content}</pre>
</body>
</html>"""

    return HTMLResponse(content=preview_html)


@router.get("/api/clones")
async def list_clones():
    """List all clone history."""
    return await get_clones()


@router.get("/api/clones/{clone_id}")
async def get_clone_detail(clone_id: str):
    """Get a specific clone with all details."""
    record = await get_clone(clone_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Clone not found")

    record["preview_url"] = record.get("sandbox_url") or f"/api/preview/{clone_id}"
    return record


@router.get("/api/sandbox/{clone_id}/{path:path}")
async def proxy_sandbox(clone_id: str, path: str, request: Request):
    """Reverse proxy to Daytona sandbox, adding the skip-warning header."""
    base_url = get_sandbox_base_url(clone_id)
    if not base_url:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    # Strip trailing slash from base, ensure path starts clean
    target = f"{base_url.rstrip('/')}/{path}"

    # Forward query string if present
    if request.url.query:
        target += f"?{request.url.query}"

    headers = {"X-Daytona-Skip-Preview-Warning": "true"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(target, headers=headers)

    if resp.status_code >= 500:
        logger.error(f"Sandbox 500 for {path}: {resp.text[:1000]}")

    content_type = resp.headers.get("content-type", "")

    proxy_base = f"/api/sandbox/{clone_id}"

    # For HTML responses, rewrite /_next/ paths to go through our proxy
    if "text/html" in content_type:
        body = resp.text
        body = body.replace('"/_next/', f'"{proxy_base}/_next/')
        body = body.replace("'/_next/", f"'{proxy_base}/_next/")
        return HTMLResponse(content=body, status_code=resp.status_code)

    # For JS responses, rewrite chunk loading paths so webpack loads through proxy
    if "javascript" in content_type:
        body = resp.text
        # Rewrite webpack's dynamic chunk loading paths
        body = body.replace('"/_next/', f'"{proxy_base}/_next/')
        body = body.replace("\"/_next/", f"\"{proxy_base}/_next/")
        body = body.replace("'/_next/", f"'{proxy_base}/_next/")
        # Rewrite the public path webpack uses for chunk loading
        body = body.replace('"static/', f'"{proxy_base}/_next/static/')
        return Response(content=body.encode(), status_code=resp.status_code, media_type=content_type)

    # For everything else (CSS, images, fonts, etc.), pass through as-is
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type,
    )
