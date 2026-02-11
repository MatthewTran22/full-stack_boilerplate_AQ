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
    _preview_store,
)
from app.services.template_loader import get_template_files
from app.database import save_clone, get_clones, get_clone

logger = logging.getLogger(__name__)

router = APIRouter()


class CloneRequest(BaseModel):
    url: str


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/api/clone")
async def clone_website(request: CloneRequest):
    """Clone a website: scrape, screenshot, generate Next.js files with AI, deploy to sandbox."""
    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Queue for status updates from both AI generator and sandbox setup
    status_queue: asyncio.Queue = asyncio.Queue()

    async def on_status(message):
        """Accept both string messages and dict events."""
        await status_queue.put(message)

    async def event_stream():
        clone_id = str(uuid.uuid4())

        try:
            # Step 1: Scrape the website (HTML + viewport screenshots + image URLs)
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
            except Exception as e:
                logger.error(f"Scraping failed: {e}")
                yield sse_event({"status": "error", "message": f"Failed to scrape website: {e}"})
                return
            t_scrape = time.time() - t0
            logger.info(f"TIMING: Scrape took {t_scrape:.1f}s ({len(screenshots)} screenshots)")

            # Send first screenshot to frontend for verification
            yield sse_event({
                "status": "screenshot",
                "message": f"Captured {len(screenshots)} viewport screenshots ({t_scrape:.0f}s)",
                "screenshot": screenshots[0] if screenshots else "",
            })

            # Step 2: Generate code with AI FIRST
            yield sse_event({"status": "generating", "message": "AI is generating the clone..."})
            t1 = time.time()

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
                    on_status=on_status,
                )
            except Exception as e:
                logger.error(f"AI generation failed: {e}")
                yield sse_event({"status": "error", "message": str(e)})
                return
            t_ai = time.time() - t1
            logger.info(f"TIMING: AI generation took {t_ai:.1f}s")

            if not files:
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

            # Step 3: Set up sandbox with template + generated code
            # Scan ALL generated files for component imports
            all_code = "\n".join(f["content"] for f in files)
            used = get_used_components(all_code)
            used_ui = used["ui"]
            used_aceternity = used["aceternity"]
            logger.info(f"shadcn components used: {used_ui or 'none'}")
            logger.info(f"aceternity components used: {used_aceternity or 'none'}")

            all_template_files = get_template_files()

            # Filter: keep everything except unused component library files
            template_files = []
            for tf in all_template_files:
                if tf["path"].startswith("components/ui/"):
                    comp_name = tf["path"].replace("components/ui/", "").replace(".tsx", "")
                    if comp_name in used_ui:
                        template_files.append(tf)
                        logger.info(f"Including shadcn: {comp_name}")
                elif tf["path"].startswith("components/aceternity/"):
                    comp_name = tf["path"].replace("components/aceternity/", "").replace(".tsx", "")
                    if comp_name in used_aceternity:
                        template_files.append(tf)
                        logger.info(f"Including aceternity: {comp_name}")
                else:
                    template_files.append(tf)

            # Always include lib/utils.ts if any components are used
            if used_ui or used_aceternity:
                has_utils = any(tf["path"] == "lib/utils.ts" for tf in template_files)
                if not has_utils:
                    for tf in all_template_files:
                        if tf["path"] == "lib/utils.ts":
                            template_files.append(tf)
                            break

            total_components = len(used_ui) + len(used_aceternity)
            yield sse_event({"status": "deploying", "message": f"Setting up sandbox ({total_components} UI components)..."})
            sandbox_url = None
            t2 = time.time()

            try:
                sandbox_url = await setup_sandbox_shell(
                    template_files=template_files,
                    clone_id=clone_id,
                    on_status=on_status,
                )
            except Exception as e:
                logger.warning(f"Sandbox setup failed: {e}")
            t_sandbox = time.time() - t2
            logger.info(f"TIMING: Sandbox setup took {t_sandbox:.1f}s")

            # Step 4: Upload generated files AFTER sandbox is ready
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
                if logs:
                    logger.info(f"Sandbox next.log (last 2000 chars):\n{logs[-2000:]}")

            t_total = time.time() - t0
            logger.info(f"TIMING TOTAL: {t_total:.1f}s (scrape={t_scrape:.1f}s, ai={t_ai:.1f}s, sandbox={t_sandbox:.1f}s)")

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
                # Re-key all sandbox mappings to the DB-assigned ID
                from app.services.sandbox import _sandbox_urls, _sandbox_instances, _sandbox_project_dirs
                if old_clone_id in _sandbox_urls:
                    _sandbox_urls[clone_id] = _sandbox_urls.pop(old_clone_id)
                if old_clone_id in _sandbox_instances:
                    _sandbox_instances[clone_id] = _sandbox_instances.pop(old_clone_id)
                if old_clone_id in _sandbox_project_dirs:
                    _sandbox_project_dirs[clone_id] = _sandbox_project_dirs.pop(old_clone_id)
                if old_clone_id in _preview_store:
                    _preview_store[clone_id] = _preview_store.pop(old_clone_id)
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

            yield sse_event({
                "status": "done",
                "message": "Clone complete!",
                "preview_url": preview_url,
                "clone_id": clone_id,
                "files": file_list,
            })

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Clone failed for {url}")
            yield sse_event({"status": "error", "message": str(e)})

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
