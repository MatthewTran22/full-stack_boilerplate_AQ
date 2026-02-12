import os
import json
import uuid
import asyncio
import logging
import time
import hmac
import hashlib
import ipaddress
import socket
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
    cleanup_sandbox,
)
from app.services.template_loader import get_template_files
from app.database import (
    save_clone,
    get_clones,
    get_clone,
    get_daily_clone_count,
    upload_static_files,
    download_static_file,
    list_storage_files,
    _guess_content_type,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Auth configuration ──
# Store only the SHA-256 hash of the password in the env var — never plaintext.
# Generate with: python3 -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"
SITE_PASSWORD_HASH = os.getenv("SITE_PASSWORD_HASH", "")
DAILY_CLONE_LIMIT = int(os.getenv("DAILY_CLONE_LIMIT", "10"))


def _hash_password(password: str) -> str:
    """SHA-256 hash a plaintext password."""
    return hashlib.sha256(password.encode()).hexdigest()


def _make_token(password_hash: str) -> str:
    """Generate a deterministic HMAC session token from the password hash."""
    return hmac.new(password_hash.encode(), b"clonr-session", hashlib.sha256).hexdigest()


def _verify_token(token: str) -> bool:
    """Check if the provided token matches the expected HMAC."""
    if not SITE_PASSWORD_HASH:
        return True  # No password configured — open access
    expected = _make_token(SITE_PASSWORD_HASH)
    return hmac.compare_digest(token, expected)


def _get_token_from_request(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


class AuthRequest(BaseModel):
    password: str


@router.post("/api/auth")
async def authenticate(body: AuthRequest):
    """Validate the site password and return a session token."""
    if not SITE_PASSWORD_HASH:
        raise HTTPException(status_code=500, detail="SITE_PASSWORD_HASH not configured")
    incoming_hash = _hash_password(body.password)
    if not hmac.compare_digest(incoming_hash, SITE_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = _make_token(SITE_PASSWORD_HASH)
    count = await get_daily_clone_count()
    return {"token": token, "daily_clones_used": count, "daily_clone_limit": DAILY_CLONE_LIMIT}


@router.get("/api/auth/status")
async def auth_status(request: Request):
    """Return remaining daily clones for an authenticated session."""
    token = _get_token_from_request(request)
    if not token or not _verify_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    count = await get_daily_clone_count()
    return {"daily_clones_used": count, "daily_clone_limit": DAILY_CLONE_LIMIT}


def _is_safe_url(url: str) -> bool:
    """Block SSRF: reject URLs targeting private/internal networks."""
    parsed = _urlparse.urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for _family, _type, _proto, _canonname, sockaddr in resolved:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True

# ── Concurrency & rate limiting ──
MAX_CONCURRENT_CLONES = int(os.getenv("MAX_CONCURRENT_CLONES", "5"))
_clone_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLONES)

# Simple per-IP rate limiter
_rate_limit_map: dict[str, float] = {}
RATE_LIMIT_SECONDS = 10

# Per-IP active clone lock: IP → clone_id (only one clone at a time per user)
_active_clones: dict[str, str] = {}
# clone_id → asyncio.Queue for SSE streaming
_clone_queues: dict[str, asyncio.Queue] = {}

# Map clone_id → (Daytona sandbox preview URL, creation timestamp)
_sandbox_urls: dict[str, tuple[str, float]] = {}
SANDBOX_MAX_AGE = 600  # 10 minutes — assume dead after this

# Clones currently being re-created (to avoid duplicate sandbox creation)
_recreating: set[str] = set()
# Clones where re-creation permanently failed (no source files in storage)
_recreation_failed: set[str] = set()

LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;align-items:center;justify-content:center;height:100vh;
font-family:system-ui,sans-serif;background:#111;color:#fff;overflow:hidden}
.wrap{text-align:center;position:relative}
.cpu{position:relative;width:200px;height:200px;margin:0 auto 28px}
.core{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
width:64px;height:64px;border-radius:14px;background:#151515;
border:1px solid rgba(255,255,255,.12);display:flex;align-items:center;
justify-content:center;z-index:2;animation:core-glow 3s ease-in-out infinite}
@keyframes core-glow{
0%,100%{box-shadow:0 0 20px rgba(80,150,255,.25),0 0 50px rgba(80,150,255,.08),inset 0 0 15px rgba(80,150,255,.04)}
50%{box-shadow:0 0 40px rgba(80,150,255,.45),0 0 80px rgba(80,150,255,.15),inset 0 0 25px rgba(80,150,255,.08)}
}
.core svg{width:36px;height:36px;opacity:.9}
svg.traces{position:absolute;inset:0;width:100%;height:100%}
.trace-bg{stroke:rgba(255,255,255,.05);stroke-width:1.5;fill:none}
.trace{stroke-width:2;fill:none;stroke-dasharray:30 70;stroke-dashoffset:100;
stroke-linecap:round}
@keyframes pulse{0%{stroke-dashoffset:100;opacity:0}12%{opacity:1}88%{opacity:1}100%{stroke-dashoffset:0;opacity:0}}
.t1{stroke:#4a9eff;animation:pulse 2.4s ease-in-out 0s infinite;filter:drop-shadow(0 0 3px #4a9eff)}
.t2{stroke:#5cb8ff;animation:pulse 2.4s ease-in-out .35s infinite;filter:drop-shadow(0 0 3px #5cb8ff)}
.t3{stroke:#3d8be6;animation:pulse 2.4s ease-in-out .7s infinite;filter:drop-shadow(0 0 3px #3d8be6)}
.t4{stroke:#6ac4ff;animation:pulse 2.4s ease-in-out 1.05s infinite;filter:drop-shadow(0 0 3px #6ac4ff)}
.t5{stroke:#2e7dd6;animation:pulse 2.4s ease-in-out 1.4s infinite;filter:drop-shadow(0 0 3px #2e7dd6)}
.dot{fill:rgba(255,255,255,.15);r:1.5}
.label{font-size:12px;color:rgba(255,255,255,.4);letter-spacing:.08em;font-variant:small-caps}
</style></head>
<body><div class="wrap">
<div class="cpu">
<svg class="traces" viewBox="0 0 200 200" fill="none">
<path class="trace-bg" d="M100 0v60"/><path class="trace t1" d="M100 0v60"/>
<path class="trace-bg" d="M70 5v45l20 15"/><path class="trace t2" d="M70 5v45l20 15"/>
<path class="trace-bg" d="M130 5v45l-20 15"/><path class="trace t3" d="M130 5v45l-20 15"/>
<path class="trace-bg" d="M100 200v-60"/><path class="trace t1" d="M100 200v-60"/>
<path class="trace-bg" d="M70 195v-45l20-15"/><path class="trace t4" d="M70 195v-45l20-15"/>
<path class="trace-bg" d="M130 195v-45l-20-15"/><path class="trace t5" d="M130 195v-45l-20-15"/>
<path class="trace-bg" d="M0 100h60"/><path class="trace t3" d="M0 100h60"/>
<path class="trace-bg" d="M5 70h45l15 20"/><path class="trace t4" d="M5 70h45l15 20"/>
<path class="trace-bg" d="M5 130h45l15-20"/><path class="trace t2" d="M5 130h45l15-20"/>
<path class="trace-bg" d="M200 100h-60"/><path class="trace t5" d="M200 100h-60"/>
<path class="trace-bg" d="M195 70h-45l-15 20"/><path class="trace t1" d="M195 70h-45l-15 20"/>
<path class="trace-bg" d="M195 130h-45l-15-20"/><path class="trace t3" d="M195 130h-45l-15-20"/>
<path class="trace-bg" d="M25 25l40 40"/><path class="trace t2" d="M25 25l40 40"/>
<path class="trace-bg" d="M175 25l-40 40"/><path class="trace t5" d="M175 25l-40 40"/>
<path class="trace-bg" d="M25 175l40-40"/><path class="trace t4" d="M25 175l40-40"/>
<path class="trace-bg" d="M175 175l-40-40"/><path class="trace t1" d="M175 175l-40-40"/>
<circle class="dot" cx="100" cy="60"/><circle class="dot" cx="90" cy="65"/><circle class="dot" cx="110" cy="65"/>
<circle class="dot" cx="100" cy="140"/><circle class="dot" cx="90" cy="135"/><circle class="dot" cx="110" cy="135"/>
<circle class="dot" cx="60" cy="100"/><circle class="dot" cx="65" cy="90"/><circle class="dot" cx="65" cy="110"/>
<circle class="dot" cx="140" cy="100"/><circle class="dot" cx="135" cy="90"/><circle class="dot" cx="135" cy="110"/>
<circle class="dot" cx="65" cy="65"/><circle class="dot" cx="135" cy="65"/>
<circle class="dot" cx="65" cy="135"/><circle class="dot" cx="135" cy="135"/>
</svg>
<div class="core">
<svg viewBox="0 0 173 173" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect width="172.339" height="172.339" rx="10" fill="#fff"/>
<rect x="79" y="36" width="20" height="7.5" rx=".96" fill="#111"/>
<rect x="72" y="49" width="20" height="7.5" rx=".96" fill="#111"/>
<rect x="65" y="63" width="33" height="7.5" rx=".96" fill="#111"/>
<rect x="59" y="76" width="19" height="7.5" rx=".96" fill="#111"/>
<rect x="51" y="90" width="60" height="7.5" rx=".96" fill="#111"/>
<rect x="45" y="104" width="20" height="7.5" rx=".96" fill="#111"/>
<rect x="110" y="104" width="18" height="7.5" rx=".96" fill="#111"/>
<rect x="40" y="118" width="20" height="7.5" rx=".96" fill="#111"/>
<rect x="115" y="118" width="21" height="7.5" rx=".96" fill="#111"/>
<rect x="23" y="131" width="45" height="7.5" rx=".96" fill="#111"/>
<rect x="109" y="131" width="40" height="7.5" rx=".96" fill="#111"/>
</svg>
</div>
</div>
<p class="label">rebuilding sandbox</p>
</div></body></html>"""


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



async def _run_clone(clone_id: str, url: str, status_queue: asyncio.Queue, client_ip: str):
    """Background task that runs the full clone pipeline. Survives SSE disconnect."""
    logger.info(f"[clone:{clone_id}] Starting clone for {url} (slots={_clone_semaphore._value}/{MAX_CONCURRENT_CLONES})")

    await _clone_semaphore.acquire()
    try:
        async def on_status(message):
            await status_queue.put(message)

        # Step 1: Scrape the website
        await status_queue.put({"status": "scraping", "message": "Scraping website...", "clone_id": clone_id})
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
            scraped_region_types = scrape_result.get("region_types")
            page_total_height = scrape_result.get("total_height", 0)
        except Exception as e:
            logger.error(f"[clone:{clone_id}] Scraping failed for {url}: {e}")
            await status_queue.put({"status": "error", "message": f"Failed to scrape website: {e}"})
            return
        t_scrape = time.time() - t0
        logger.info(
            f"[clone:{clone_id}] Scrape complete: {len(screenshots)} screenshots, "
            f"{len(html_source)} chars HTML, {len(image_urls)} images ({t_scrape:.1f}s)"
        )

        section_info = f" (scroll positions: {scroll_positions})" if len(scroll_positions) > 1 else ""
        await status_queue.put({
            "status": "screenshot",
            "message": f"Captured {len(screenshots)} viewport screenshots ({t_scrape:.0f}s){section_info}",
            "screenshots": screenshots,
        })

        # Step 2: AI generation + sandbox setup IN PARALLEL
        await status_queue.put({"status": "generating", "message": "AI is generating the clone..."})
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
                scroll_positions=scroll_positions,
                total_height=page_total_height,
                region_types=scraped_region_types,
                on_status=on_status,
            )
        except Exception as e:
            logger.error(f"[clone:{clone_id}] AI generation failed: {e}")
            sandbox_task.cancel()
            await status_queue.put({"status": "error", "message": str(e)})
            return

        files = ai_result["files"]
        ai_usage = ai_result.get("usage", {})
        t_ai = time.time() - t1
        logger.info(f"[clone:{clone_id}] AI generation complete: {len(files)} files ({t_ai:.1f}s)")

        if not files:
            sandbox_task.cancel()
            await status_queue.put({"status": "error", "message": "AI returned no files"})
            return

        # Wait for sandbox to be ready
        await status_queue.put({"status": "deploying", "message": "Waiting for sandbox..."})
        t2 = time.time()
        sandbox_ready = await sandbox_task
        t_sandbox_wait = time.time() - t2
        logger.info(f"[clone:{clone_id}] Sandbox ready={sandbox_ready} (waited {t_sandbox_wait:.1f}s)")

        if not sandbox_ready:
            logger.warning(f"[clone:{clone_id}] No sandbox available")
            await status_queue.put({"status": "error", "message": "Sandbox unavailable"})
            return

        # Upload generated files to sandbox
        await status_queue.put({"status": "deploying", "message": "Uploading generated code to sandbox..."})
        for f in files:
            success = upload_file_to_sandbox(clone_id, f["path"], f["content"])
            if success:
                await status_queue.put({
                    "status": "file_upload",
                    "message": f"Uploaded {f['path']}",
                    "file": f["path"],
                })
            await asyncio.sleep(0.05)

        # Start dev server and get live preview URL
        await status_queue.put({"status": "deploying", "message": "Starting live preview..."})
        dev_url = await start_dev_server(clone_id=clone_id, on_status=on_status)

        if not dev_url:
            logger.warning(f"[clone:{clone_id}] Dev server failed to start")
            await status_queue.put({"status": "error", "message": "Preview failed to start"})
            return

        _sandbox_urls[clone_id] = (dev_url, time.time())
        preview_url = f"/api/sandbox/{clone_id}/"
        logger.info(f"[clone:{clone_id}] Live preview proxied at {preview_url} → {dev_url}")

        # Auto-fix loop
        MAX_FIX_RETRIES = 3
        files_map = {f["path"]: f for f in files}

        for fix_attempt in range(MAX_FIX_RETRIES):
            await asyncio.sleep(2)
            error_info = await _check_page_error(dev_url, clone_id)
            if not error_info:
                break

            err_file = error_info.get("file", "")
            err_msg = error_info.get("message", "Unknown error")
            logger.warning(f"[clone:{clone_id}] Fix attempt {fix_attempt + 1}/{MAX_FIX_RETRIES}: {err_msg} in {err_file}")
            await status_queue.put({
                "status": "fixing",
                "message": f"Fixing error in {err_file} (attempt {fix_attempt + 1}/{MAX_FIX_RETRIES})...",
            })

            broken_file = files_map.get(err_file)
            if not broken_file:
                for path, f in files_map.items():
                    if err_file.split("/")[-1] in path:
                        broken_file = f
                        err_file = path
                        break
            if not broken_file:
                logger.warning(f"[clone:{clone_id}] Could not find {err_file} in generated files, skipping fix")
                break

            try:
                fix_result = await fix_component(
                    file_path=err_file,
                    file_content=broken_file["content"],
                    error_message=err_msg,
                )
                fixed_content = fix_result["content"]
                fix_usage = fix_result["usage"]

                ai_usage["tokens_in"] = ai_usage.get("tokens_in", 0) + fix_usage["tokens_in"]
                ai_usage["tokens_out"] = ai_usage.get("tokens_out", 0) + fix_usage["tokens_out"]
                ai_usage["api_calls"] = ai_usage.get("api_calls", 0) + 1

                broken_file["content"] = fixed_content
                upload_file_to_sandbox(clone_id, err_file, fixed_content)
                logger.info(f"[clone:{clone_id}] Uploaded fix for {err_file}, waiting for hot reload...")

                await status_queue.put({
                    "status": "file_write", "type": "file_write",
                    "file": err_file, "action": "fix",
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

        t_total = time.time() - t0
        total_lines = sum(f["content"].count("\n") + 1 for f in files)
        cost_str = f", cost=${ai_usage.get('total_cost', 0):.4f}" if ai_usage else ""
        logger.info(
            f"[clone:{clone_id}] DONE: {t_total:.1f}s total "
            f"(scrape={t_scrape:.1f}s, ai={t_ai:.1f}s, sandbox_wait={t_sandbox_wait:.1f}s) "
            f"| {len(files)} files, {total_lines} lines{cost_str}"
        )

        # Save to DB — use the same clone_id so storage files match the DB record
        db_record = await save_clone(url=url, preview_url=preview_url, clone_id=clone_id)
        if db_record:
            logger.info(f"[clone:{clone_id}] Saved to database")

        file_list = [
            {"path": f["path"], "content": f["content"], "lines": f["content"].count("\n") + 1}
            for f in files
        ]
        scaffold_paths = sorted(set(tf["path"] for tf in all_template_files))

        await status_queue.put({
            "status": "done",
            "message": "Clone complete!",
            "preview_url": preview_url,
            "clone_id": clone_id,
            "files": file_list,
            "scaffold_paths": scaffold_paths,
            "usage": ai_usage,
        })

    except Exception as e:
        logger.exception(f"[clone:{clone_id}] Unhandled error for {url}")
        await status_queue.put({"status": "error", "message": str(e)})
    finally:
        _clone_semaphore.release()
        _active_clones.pop(client_ip, None)
        await status_queue.put({"_done": True})
        logger.info(f"[clone:{clone_id}] Released slot (slots={_clone_semaphore._value}/{MAX_CONCURRENT_CLONES})")


@router.post("/api/clone")
async def clone_website(request: CloneRequest, raw_request: Request):
    """Clone a website: scrape, screenshot, generate Next.js files, deploy to sandbox."""
    # Auth check
    if SITE_PASSWORD_HASH:
        token = _get_token_from_request(raw_request)
        if not token or not _verify_token(token):
            raise HTTPException(status_code=401, detail="Not authenticated. Please enter the site password.")

    # Daily limit check
    daily_count = await get_daily_clone_count()
    if daily_count >= DAILY_CLONE_LIMIT:
        raise HTTPException(status_code=429, detail=f"Daily clone limit reached ({DAILY_CLONE_LIMIT} clones per 24 hours). Please try again later.")

    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not _is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL: cannot clone internal or private network addresses")

    client_ip = raw_request.client.host if raw_request.client else "unknown"

    # Block if this IP already has an active clone
    if client_ip in _active_clones:
        raise HTTPException(status_code=429, detail="A clone is already in progress. Please wait for it to finish.")

    # Rate limit per IP
    now = time.time()
    last_request = _rate_limit_map.get(client_ip, 0)
    if now - last_request < RATE_LIMIT_SECONDS:
        raise HTTPException(status_code=429, detail="Please wait before starting another clone")
    _rate_limit_map[client_ip] = now

    # Check global concurrency
    if _clone_semaphore._value == 0:
        logger.warning(f"[clone] Rejected request from {client_ip}: all {MAX_CONCURRENT_CLONES} slots busy")
        raise HTTPException(status_code=503, detail=f"Server busy — {MAX_CONCURRENT_CLONES} clones already in progress. Try again shortly.")

    clone_id = str(uuid.uuid4())
    status_queue: asyncio.Queue = asyncio.Queue()

    _active_clones[client_ip] = clone_id
    _clone_queues[clone_id] = status_queue

    # Launch clone as a background task — survives SSE disconnect
    asyncio.create_task(_run_clone(clone_id, url, status_queue, client_ip))

    async def event_stream():
        """SSE reader — forwards events from the background task to the client."""
        try:
            while True:
                try:
                    event = await asyncio.wait_for(status_queue.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    yield sse_event({"status": "error", "message": "Timed out waiting for clone"})
                    break

                if isinstance(event, dict) and event.get("_done"):
                    break

                # Format and forward events
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
        except Exception:
            pass  # Client disconnected — background task continues
        finally:
            _clone_queues.pop(clone_id, None)

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


@router.delete("/api/sandbox/{clone_id}")
async def delete_sandbox(clone_id: str):
    """Delete a Daytona sandbox to free resources."""
    _sandbox_urls.pop(clone_id, None)
    await cleanup_sandbox(clone_id)
    return {"ok": True}


@router.post("/api/sandbox/{clone_id}/end")
async def end_sandbox(clone_id: str):
    """Beacon-compatible endpoint to clean up a sandbox (POST-only for sendBeacon)."""
    _sandbox_urls.pop(clone_id, None)
    await cleanup_sandbox(clone_id)
    return {"ok": True}


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


@router.get("/api/clones/{clone_id}/files")
async def get_clone_files(clone_id: str):
    """Get all generated source files for a clone from Supabase Storage."""
    files = list_storage_files(clone_id)
    if not files:
        raise HTTPException(status_code=404, detail="No files found for this clone")
    return {"files": files}
