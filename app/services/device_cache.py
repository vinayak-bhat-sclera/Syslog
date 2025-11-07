# app/services/device_cache.py
import asyncio
import time
import logging
from typing import Optional, Dict

import aiohttp

from app.config import settings

logger = logging.getLogger("app.services.device_cache")

# in-memory cache: ip -> (device_id, expiry_ts)
_DEVICE_CACHE: Dict[str, tuple] = {}
_CACHE_LOCK = asyncio.Lock()
CACHE_TTL = getattr(settings, "CACHE_TTL", 3600)  # seconds


async def _fetch_from_springboot(ip: str) -> Optional[str]:
    """
    Async request to Spring Boot to resolve ip -> device_id.
    Expects Spring Boot endpoint that returns JSON { "device_id": "..." }.
    """
    url = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}/api/device/by-ip/{ip}"
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    device_id = data.get("device_id") or data.get("deviceId") or data.get("id")
                    if device_id:
                        return device_id
                else:
                    text = await resp.text()
                    logger.warning("SpringBoot lookup failed (%s): %s -> %s", resp.status, url, text[:200])
    except Exception as e:
        logger.exception("SpringBoot lookup error for %s: %s", ip, e)
    return None


async def get_device_id(ip: str) -> Optional[str]:
    """
    Async lookup: check local cache, if miss call SpringBoot and cache result.
    """
    now = int(time.time())
    async with _CACHE_LOCK:
        entry = _DEVICE_CACHE.get(ip)
        if entry:
            device_id, expiry = entry
            if expiry > now:
                logger.debug("[DEVICE_CACHE HIT] %s -> %s", ip, device_id)
                return device_id
            else:
                # expired
                _DEVICE_CACHE.pop(ip, None)

    # cache miss -> call springboot
    device_id = await _fetch_from_springboot(ip)
    if device_id:
        async with _CACHE_LOCK:
            _DEVICE_CACHE[ip] = (device_id, int(time.time()) + CACHE_TTL)
            logger.info("[DEVICE_CACHE SET] %s -> %s (ttl=%ds)", ip, device_id, CACHE_TTL)
    else:
        logger.debug("[DEVICE_CACHE MISS] no mapping for %s", ip)
    return device_id


async def set_device_cache(ip: str, device_id: str, ttl: Optional[int] = None) -> None:
    """Manually set ip->device_id mapping in cache."""
    if ttl is None:
        ttl = CACHE_TTL
    async with _CACHE_LOCK:
        _DEVICE_CACHE[ip] = (device_id, int(time.time()) + ttl)
        logger.info("[DEVICE_CACHE MANUAL SET] %s -> %s (ttl=%ds)", ip, device_id, ttl)


async def remove_device_cache_for_ip(ip: str) -> None:
    async with _CACHE_LOCK:
        _DEVICE_CACHE.pop(ip, None)
        logger.debug("[DEVICE_CACHE EVICT] %s", ip)


async def clear_cache() -> None:
    """Clear entire in-memory cache."""
    async with _CACHE_LOCK:
        _DEVICE_CACHE.clear()
    logger.warning("Device cache flushed (in-memory).")


async def clear_profile_devices(profile_id: str) -> None:
    """
    Remove cached entries that belong to mappings in profile_id.
    This function is used after profile update/delete to evict any ip entries associated with mappings.
    It checks DB to find IPs for that profile and evicts them.
    """
    # Lazy import to avoid cycle
    from app.db import get_db_connection

    ips = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                "SELECT pd.device_id AS device_id FROM syslog_profile_devices pd WHERE pd.profile_id=%s",
                (profile_id,),
            )
            rows = cursor.fetchall() or []
            cursor.close()
            for r in rows:
                # We don't have ip stored in profile_devices (design), so only evict entries where device_id maps.
                # Try to evict cached entries with that device_id.
                device_id = r.get("device_id")
                ips.append(device_id)
    except Exception:
        logger.exception("Error loading profile device_ids for cache eviction")

    # Evict any cache entries where value == device_id
    async with _CACHE_LOCK:
        to_remove = []
        for ip, (did, _) in _DEVICE_CACHE.items():
            if did in ips:
                to_remove.append(ip)
        for ip in to_remove:
            _DEVICE_CACHE.pop(ip, None)
            logger.debug("Evicted cached ip %s for profile %s", ip, profile_id)
