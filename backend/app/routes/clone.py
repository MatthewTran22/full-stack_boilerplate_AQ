import json
import uuid
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from app.services.scraper import scrape_website, screenshot_website
from app.services.ai_generator import generate_clone
from app.services.sandbox import create_sandbox, get_preview_html
from app.database import save_clone, get_clones, get_clone

logger = logging.getLogger(__name__)

router = APIRouter()


class CloneRequest(BaseModel):
    url: str


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/api/clone")
async def clone_website(request: CloneRequest):
    """Clone a website: scrape, screenshot, generate with AI, deploy to sandbox."""
    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    async def event_stream():
        clone_id = str(uuid.uuid4())

        try:
            # Step 1: Scrape
            yield sse_event({"status": "scraping", "message": "Fetching website HTML..."})
            scraped = await scrape_website(url)

            # Step 2: Screenshot
            yield sse_event({"status": "scraping", "message": "Taking screenshot..."})
            try:
                screenshot_b64 = await screenshot_website(url)
            except Exception as e:
                logger.warning(f"Screenshot failed: {e}, proceeding without it")
                screenshot_b64 = ""

            # Step 3: Generate clone with AI
            yield sse_event({"status": "generating", "message": "AI is generating the clone..."})
            generated_html = await generate_clone(
                html=scraped["html"],
                screenshot_b64=screenshot_b64,
                url=url,
            )

            if not generated_html.strip():
                yield sse_event({"status": "error", "message": "AI returned empty result"})
                return

            # Step 4: Deploy to sandbox
            yield sse_event({"status": "deploying", "message": "Deploying to sandbox..."})
            sandbox_url = await create_sandbox(generated_html, clone_id)

            # Step 5: Save to database
            preview_url = sandbox_url or f"/api/preview/{clone_id}"
            db_record = await save_clone(
                url=url,
                generated_html=generated_html,
                sandbox_url=sandbox_url,
            )

            # Use DB-generated ID if available
            if db_record and "id" in db_record:
                clone_id = db_record["id"]
                if not sandbox_url:
                    from app.services.sandbox import _preview_store
                    _preview_store[clone_id] = generated_html
                    preview_url = f"/api/preview/{clone_id}"

            yield sse_event({
                "status": "done",
                "message": "Clone complete!",
                "html": generated_html,
                "preview_url": preview_url,
                "clone_id": clone_id,
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
    """Serve the generated HTML as a preview."""
    html = get_preview_html(clone_id)

    if html is None:
        record = await get_clone(clone_id)
        if record:
            html = record.get("generated_html")

    if html is None:
        raise HTTPException(status_code=404, detail="Clone not found")

    return HTMLResponse(content=html)


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
