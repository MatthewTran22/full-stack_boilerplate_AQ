import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

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
    generated_html: str,
    screenshot_url: Optional[str] = None,
    sandbox_url: Optional[str] = None,
) -> Optional[dict]:
    """Save a clone record to the database."""
    client = get_supabase()
    if client is None:
        return None

    try:
        result = (
            client.table("clones")
            .insert(
                {
                    "url": url,
                    "generated_html": generated_html,
                    "screenshot_url": screenshot_url,
                    "sandbox_url": sandbox_url,
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to save clone: {e}")
        return None


async def get_clones() -> list:
    """Get all clones, ordered by most recent."""
    client = get_supabase()
    if client is None:
        return []

    try:
        result = (
            client.table("clones")
            .select("id, url, sandbox_url, created_at")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to get clones: {e}")
        return []


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
