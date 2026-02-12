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

import re as _re
import urllib.parse as _urlparse
from app.services.scraper import scrape_and_capture
from app.services.ai_generator import generate_clone, fix_component
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


async def _check_page_error(dev_url: str, clone_id: str | None = None) -> dict | None:
    """Fetch the page from the dev server and check for Next.js errors.

    Returns None if the page is healthy, or {"file": ..., "message": ...} if an error is detected.
    """
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(dev_url.rstrip("/") + "/")

            # 200 with no error markers = healthy
            if resp.status_code == 200 and "__next_error__" not in resp.text:
                return None

            body = resp.text
            error_msg = ""
            error_file = ""

            # Strategy 1: Check the dev server log for errors (most reliable)
            if clone_id:
                from app.services.sandbox import _sandbox_instances
                sandbox = _sandbox_instances.get(clone_id)
                if sandbox:
                    try:
                        log_output = sandbox.process.exec("tail -50 /tmp/next-dev.log 2>/dev/null", timeout=5)
                        log_text = log_output.output if hasattr(log_output, "output") else str(log_output)

                        # Look for compilation/runtime errors in Next.js log
                        # Patterns: "Error:", "TypeError:", "SyntaxError:", "Module not found"
                        for line in log_text.split("\n"):
                            if any(kw in line for kw in ["Error:", "TypeError:", "ReferenceError:", "SyntaxError:", "Module not found"]):
                                error_msg = line.strip()[:300]
                                break

                        # Look for file references in the log
                        file_match = _re.search(r'[./]*(components/\w+\.tsx)', log_text)
                        if file_match:
                            error_file = file_match.group(1)
                        if not error_file:
                            file_match = _re.search(r'[./]*(app/page\.tsx)', log_text)
                            if file_match:
                                error_file = file_match.group(1)
                    except Exception:
                        pass

            # Strategy 2: Parse error from the HTML response
            if not error_msg:
                msg_match = _re.search(
                    r'(?:Error|TypeError|ReferenceError|SyntaxError):\s*(.+?)(?:<|\\n|")',
                    body,
                )
                if msg_match:
                    error_msg = msg_match.group(0).split("<")[0].strip()[:300]

            # Strategy 3: Look for file references in HTML
            if not error_file:
                file_match = _re.search(r'components[/\\](\w+)\.tsx', body)
                if file_match:
                    error_file = f"components/{file_match.group(1)}.tsx"

            if not error_msg:
                error_msg = f"Page returned HTTP {resp.status_code}"

            if error_msg or resp.status_code >= 500:
                logger.info(f"[health-check] Error detected: {error_msg} in {error_file or '(unknown file)'}")
                return {"file": error_file, "message": error_msg}

            return None
    except Exception as e:
        logger.warning(f"[health-check] Failed to check page: {e}")
        return None



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
                scroll_positions = scrape_result.get("scroll_positions", [])
                page_total_height = scrape_result.get("total_height", 0)
            except Exception as e:
                logger.error(f"[clone:{clone_id}] Scraping failed for {url}: {e}")
                yield sse_event({"status": "error", "message": f"Failed to scrape website: {e}"})
                return
            t_scrape = time.time() - t0
            logger.info(
                f"[clone:{clone_id}] Scrape complete: {len(screenshots)} screenshots, "
                f"{len(html_source)} chars HTML, {len(image_urls)} images ({t_scrape:.1f}s)"
            )

            section_info = f" (scroll positions: {scroll_positions})" if len(scroll_positions) > 1 else ""
            yield sse_event({
                "status": "screenshot",
                "message": f"Captured {len(screenshots)} viewport screenshots ({t_scrape:.0f}s){section_info}",
                "screenshot": screenshots[0] if screenshots else "",
            })

            # Step 2: AI generation + sandbox setup IN PARALLEL
            gen_msg = f"AI is generating the clone ({len(screenshots)} sections)..." if len(screenshots) > 1 else "AI is generating the clone..."
            yield sse_event({"status": "generating", "message": gen_msg})
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

            # Run AI generation as a background task so we can stream progress
            ai_result_box: list = []
            ai_error_box: list = []

            async def _run_ai():
                try:
                    result = await generate_clone(
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
                        scroll_positions=scroll_positions,
                        total_height=page_total_height,
                        on_status=on_status,
                    )
                    ai_result_box.append(result)
                except Exception as e:
                    ai_error_box.append(e)
                await status_queue.put({"_done": True})

            ai_task = asyncio.create_task(_run_ai())

            # Stream progress events in real-time while AI generates
            while True:
                try:
                    event = await asyncio.wait_for(status_queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    if ai_task.done():
                        break
                    continue

                if isinstance(event, dict) and event.get("_done"):
                    break

                # Forward event to SSE stream
                if isinstance(event, str):
                    yield sse_event({"status": "generating", "message": event})
                elif isinstance(event, dict):
                    evt_type = event.get("type", "")
                    if evt_type == "section_complete":
                        names = ", ".join(event.get("components", []))
                        sec = event.get("section", "?")
                        tot = event.get("total", "?")
                        msg = f"Section {sec}/{tot} complete ({names})" if names else f"Section {sec}/{tot} complete"
                        yield sse_event({
                            "status": "section_complete",
                            "message": msg,
                            "section": sec,
                            "total": tot,
                            "components": event.get("components", []),
                        })
                    elif evt_type == "file_write":
                        yield sse_event({
                            "status": "file_write",
                            "type": "file_write",
                            "file": event.get("file", ""),
                            "action": event.get("action", "create"),
                            "lines": event.get("lines", 0),
                        })
                    else:
                        yield sse_event(event)

            # Drain any remaining queue events
            while not status_queue.empty():
                try:
                    event = status_queue.get_nowait()
                    if isinstance(event, dict) and event.get("_done"):
                        continue
                    if isinstance(event, str):
                        yield sse_event({"status": "generating", "message": event})
                    elif isinstance(event, dict):
                        evt_type = event.get("type", "")
                        if evt_type == "file_write":
                            yield sse_event({"status": "file_write", "type": "file_write", "file": event.get("file", ""), "action": event.get("action", "create"), "lines": event.get("lines", 0)})
                        elif evt_type != "section_complete":
                            yield sse_event(event)
                except asyncio.QueueEmpty:
                    break

            if ai_error_box:
                logger.error(f"[clone:{clone_id}] AI generation failed: {ai_error_box[0]}")
                sandbox_task.cancel()
                yield sse_event({"status": "error", "message": str(ai_error_box[0])})
                return

            ai_result = ai_result_box[0] if ai_result_box else {"files": [], "deps": []}
            files = ai_result["files"]
            ai_usage = ai_result.get("usage", {})
            t_ai = time.time() - t1
            logger.info(f"[clone:{clone_id}] AI generation complete: {len(files)} files ({t_ai:.1f}s)")

            if not files:
                sandbox_task.cancel()
                yield sse_event({"status": "error", "message": "AI returned no files"})
                return

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

                    # Auto-fix loop: check for runtime errors, fix with AI, hot reload
                    MAX_FIX_RETRIES = 3
                    files_map = {f["path"]: f for f in files}

                    for fix_attempt in range(MAX_FIX_RETRIES):
                        await asyncio.sleep(2)  # let Next.js compile
                        error_info = await _check_page_error(dev_url, clone_id)
                        if not error_info:
                            break  # page is healthy

                        err_file = error_info.get("file", "")
                        err_msg = error_info.get("message", "Unknown error")
                        logger.warning(
                            f"[clone:{clone_id}] Fix attempt {fix_attempt + 1}/{MAX_FIX_RETRIES}: "
                            f"{err_msg} in {err_file}"
                        )
                        yield sse_event({
                            "status": "fixing",
                            "message": f"Fixing error in {err_file} (attempt {fix_attempt + 1}/{MAX_FIX_RETRIES})...",
                        })

                        # Find the broken file in our generated files
                        broken_file = files_map.get(err_file)
                        if not broken_file:
                            # Try matching by component name
                            for path, f in files_map.items():
                                if err_file.split("/")[-1] in path:
                                    broken_file = f
                                    err_file = path
                                    break
                        if not broken_file:
                            logger.warning(f"[clone:{clone_id}] Could not find {err_file} in generated files, skipping fix")
                            break

                        # Ask AI to fix the component
                        try:
                            fix_result = await fix_component(
                                file_path=err_file,
                                file_content=broken_file["content"],
                                error_message=err_msg,
                            )
                            fixed_content = fix_result["content"]
                            fix_usage = fix_result["usage"]

                            # Track fix cost in overall usage
                            ai_usage["tokens_in"] = ai_usage.get("tokens_in", 0) + fix_usage["tokens_in"]
                            ai_usage["tokens_out"] = ai_usage.get("tokens_out", 0) + fix_usage["tokens_out"]
                            ai_usage["api_calls"] = ai_usage.get("api_calls", 0) + 1

                            # Update file in our list and upload to sandbox (hot reload)
                            broken_file["content"] = fixed_content
                            upload_file_to_sandbox(clone_id, err_file, fixed_content)
                            logger.info(f"[clone:{clone_id}] Uploaded fix for {err_file}, waiting for hot reload...")

                            yield sse_event({
                                "status": "file_write",
                                "type": "file_write",
                                "file": err_file,
                                "action": "fix",
                                "lines": fixed_content.count("\n") + 1,
                            })
                        except Exception as e:
                            logger.error(f"[clone:{clone_id}] Fix attempt failed: {e}")
                            break
                    else:
                        logger.warning(f"[clone:{clone_id}] Exhausted {MAX_FIX_RETRIES} fix attempts")

                    # Recalculate total cost after fixes
                    if ai_usage:
                        from app.services.ai_generator import _calc_cost
                        ai_usage["total_cost"] = _calc_cost(
                            ai_usage.get("tokens_in", 0),
                            ai_usage.get("tokens_out", 0),
                            ai_usage.get("model", "anthropic/claude-sonnet-4.5"),
                        )

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
            cost_str = f", cost=${ai_usage.get('total_cost', 0):.4f}" if ai_usage else ""
            logger.info(
                f"[clone:{clone_id}] DONE: {t_total:.1f}s total "
                f"(scrape={t_scrape:.1f}s, ai={t_ai:.1f}s, sandbox_wait={t_sandbox_wait:.1f}s) "
                f"| {len(files)} files, {total_lines} lines{cost_str}"
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
                "usage": ai_usage,
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

    # Only recreate on gateway errors (502/503/504) — means sandbox is dead.
    # 500 = Next.js compilation error (sandbox alive, code is broken) — pass through.
    if resp.status_code in (502, 503, 504):
        logger.warning(f"[proxy:{clone_id}] Sandbox returned {resp.status_code} (gateway error), triggering re-creation")
        _sandbox_urls.pop(clone_id, None)
        if clone_id not in _recreating:
            asyncio.create_task(_recreate_sandbox(clone_id))
        return HTMLResponse(content=LOADING_HTML)

    if resp.status_code >= 400:
        # Log the error body so we can debug compilation failures
        error_snippet = resp.text[:500] if resp.text else "(empty)"
        logger.warning(f"[proxy:{clone_id}] Sandbox returned {resp.status_code}: {error_snippet}")

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
