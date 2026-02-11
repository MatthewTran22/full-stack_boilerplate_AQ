import math
import os
import logging
import mimetypes
from typing import Optional

logger = logging.getLogger(__name__)

STORAGE_BUCKET = "clone_exports"

_supabase_client = None


def get_supabase():
    """Get or create the Supabase client."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        logger.warning("SUPABASE_URL or SUPABASE_KEY not set, database disabled")
        return None

    from supabase import create_client

    _supabase_client = create_client(url, key)
    return _supabase_client


async def save_clone(
    url: str,
    screenshot_url: Optional[str] = None,
    sandbox_url: Optional[str] = None,
    preview_url: Optional[str] = None,
) -> Optional[dict]:
    """Save a clone record to the database."""
    client = get_supabase()
    if client is None:
        return None

    try:
        row = {
            "url": url,
            "screenshot_url": screenshot_url,
            "sandbox_url": sandbox_url,
        }
        if preview_url is not None:
            row["preview_url"] = preview_url
        result = (
            client.table("clones")
            .insert(row)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to save clone: {e}")
        return None


async def get_clones(page: int = 1, per_page: int = 30) -> dict:
    """Get clones with pagination, ordered by most recent."""
    client = get_supabase()
    if client is None:
        return {"items": [], "total": 0, "page": page, "pages": 0}

    try:
        # Get total count
        count_result = (
            client.table("clones")
            .select("id", count="exact")
            .execute()
        )
        total = count_result.count if count_result.count is not None else 0

        # Fetch page
        offset = (page - 1) * per_page
        result = (
            client.table("clones")
            .select("id, url, sandbox_url, preview_url, created_at")
            .order("created_at", desc=True)
            .range(offset, offset + per_page - 1)
            .execute()
        )
        pages = math.ceil(total / per_page) if per_page > 0 else 0
        return {
            "items": result.data or [],
            "total": total,
            "page": page,
            "pages": pages,
        }
    except Exception as e:
        logger.error(f"Failed to get clones: {e}")
        return {"items": [], "total": 0, "page": page, "pages": 0}


async def get_clone(clone_id: str) -> Optional[dict]:
    """Get a specific clone by ID."""
    client = get_supabase()
    if client is None:
        return None

    try:
        result = (
            client.table("clones")
            .select("*")
            .eq("id", clone_id)
            .single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(f"Failed to get clone {clone_id}: {e}")
        return None


# ── Supabase Storage helpers ──


def _guess_content_type(path: str) -> str:
    """Guess MIME type from file extension."""
    ct, _ = mimetypes.guess_type(path)
    if ct:
        return ct
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "js": "application/javascript",
        "mjs": "application/javascript",
        "css": "text/css",
        "html": "text/html",
        "json": "application/json",
        "svg": "image/svg+xml",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "ico": "image/x-icon",
        "woff": "font/woff",
        "woff2": "font/woff2",
        "ttf": "font/ttf",
        "txt": "text/plain",
    }.get(ext, "application/octet-stream")


def upload_static_files(clone_id: str, files: dict[str, bytes]) -> bool:
    """Upload all static export files to Supabase Storage.

    Args:
        clone_id: The clone identifier (used as folder prefix)
        files: Dict mapping relative paths to file content bytes
               e.g. {"index.html": b"<html>...", "_next/static/css/foo.css": b"..."}

    Returns True if all uploads succeeded, False otherwise.
    """
    client = get_supabase()
    if client is None:
        logger.warning("Supabase not configured, cannot upload static files")
        return False

    bucket = client.storage.from_(STORAGE_BUCKET)
    failed = 0

    for rel_path, data in files.items():
        storage_path = f"{clone_id}/{rel_path}"
        content_type = _guess_content_type(rel_path)
        try:
            bucket.upload(
                storage_path,
                data,
                file_options={"content-type": content_type, "upsert": "true"},
            )
        except Exception as e:
            err_str = str(e)
            # "Duplicate" or "already exists" means upsert isn't supported — try remove + upload
            if "Duplicate" in err_str or "already exists" in err_str:
                try:
                    bucket.remove([storage_path])
                    bucket.upload(
                        storage_path,
                        data,
                        file_options={"content-type": content_type},
                    )
                except Exception as e2:
                    logger.error(f"Storage re-upload failed for {storage_path}: {e2}")
                    failed += 1
            else:
                logger.error(f"Storage upload failed for {storage_path}: {e}")
                failed += 1

    total = len(files)
    logger.info(f"[storage:{clone_id}] Uploaded {total - failed}/{total} files to Supabase Storage")
    return failed == 0


def download_static_file(clone_id: str, path: str) -> Optional[bytes]:
    """Download a single file from Supabase Storage.

    Args:
        clone_id: The clone identifier
        path: Relative path within the clone's folder (e.g. "index.html")

    Returns file bytes or None if not found.
    """
    client = get_supabase()
    if client is None:
        return None

    storage_path = f"{clone_id}/{path}"
    try:
        data = client.storage.from_(STORAGE_BUCKET).download(storage_path)
        return data
    except Exception as e:
        logger.debug(f"Storage download failed for {storage_path}: {e}")
        return None


def get_public_storage_url(clone_id: str, path: str) -> str:
    """Get the public URL for a file in Supabase Storage."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    return f"{supabase_url}/storage/v1/object/public/{STORAGE_BUCKET}/{clone_id}/{path}"
