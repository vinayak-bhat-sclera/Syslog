# app/routers/cache_monitor.py
import time
import logging
from fastapi import APIRouter, HTTPException
from app.services.device_cache import DEVICE_CACHE, clear_cache, remove_device_mapping

router = APIRouter()
logger = logging.getLogger("app.routers.cache_monitor")


@router.get("/cache/status")
def cache_status():
    """
    Show all IP → device_id mappings currently in the in-memory cache.
    """
    now = time.time()
    items = [
        {
            "ip": ip,
            "device_id": val[0],
            "expires_in_sec": max(0, int(val[1] - now))
        }
        for ip, val in DEVICE_CACHE.items()
    ]
    return {
        "total_cached": len(items),
        "cache_ttl_sec": 3600,
        "items": items
    }


@router.delete("/cache/{ip}")
def delete_cache_entry(ip: str):
    """
    Remove a specific IP entry from the cache.
    """
    if ip not in DEVICE_CACHE:
        raise HTTPException(status_code=404, detail=f"No cache entry for {ip}")
    remove_device_mapping(ip)
    return {"status": "removed", "ip": ip}


@router.delete("/cache/clear")
def clear_all_cache():
    """
    Clear the entire in-memory cache.
    """
    clear_cache()
    return {"status": "cleared", "total_now": 0}
