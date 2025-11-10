# app/routers/cache_monitor.py
import logging
from fastapi import APIRouter
from app.services.device_cache import clear_cache

router = APIRouter()
logger = logging.getLogger("app.routers.cache_monitor")


@router.get("/cache/status")
async def get_cache_status():
    """
    Return basic in-memory cache stats (size, keys).
    """
    from app.services.device_cache import _DEVICE_CACHE  # internal import, read-only
    total = len(_DEVICE_CACHE)
    keys_preview = list(_DEVICE_CACHE.keys())[:10]
    return {"total_entries": total, "sample_keys": keys_preview}


@router.post("/cache/clear")
async def clear_entire_cache():
    """
    Clear all in-memory cache entries.
    """
    await clear_cache()
    logger.warning("Cache cleared via API.")
    return {"status": "cleared"}
