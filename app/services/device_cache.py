# app/services/device_cache.py
import asyncio
import time
import logging
from typing import Optional, Dict, Tuple, List, Any

import aiohttp

from app.config import settings

logger = logging.getLogger("app.services.device_cache")

# in-memory cache: ip -> (device_id, expiry_ts)
_DEVICE_CACHE: Dict[str, Tuple[str, int]] = {}
_CACHE_LOCK = asyncio.Lock()
CACHE_TTL = getattr(settings, "CACHE_TTL", 3600)  # seconds

# Background control
_BACKGROUND_TASK: Optional[asyncio.Task] = None
_REFRESH_INTERVAL_SECONDS = getattr(settings, "CACHE_REFRESH_INTERVAL_SECONDS", 3600)  # hourly
_SNAPSHOT_INTERVAL_SECONDS = getattr(settings, "CACHE_SNAPSHOT_INTERVAL_SECONDS", 300)  # 5 minutes


async def _fetch_from_springboot(ip: str) -> Optional[str]:
    """
    Async request to Spring Boot to resolve ip -> device_id.
    Expects Spring Boot endpoint that returns JSON { "device_id": "..." }.
    (unchanged legacy behavior)
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
    Behavior preserved: legacy fallback to by-ip lookup.
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

    # cache miss -> call springboot by-ip (legacy)
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
    It checks DB to find device_ids for that profile and evicts entries whose device_id matches.
    """
    # Lazy import to avoid cycle
    from app.db import get_db_connection

    device_ids = []
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
                did = r.get("device_id")
                if did:
                    device_ids.append(did)
    except Exception:
        logger.exception("Error loading profile device_ids for cache eviction")

    # Evict any cache entries where value == device_id
    async with _CACHE_LOCK:
        to_remove: List[str] = []
        for ip, (did, _) in list(_DEVICE_CACHE.items()):
            if did in device_ids:
                to_remove.append(ip)
        for ip in to_remove:
            _DEVICE_CACHE.pop(ip, None)
            logger.debug("Evicted cached ip %s for profile %s", ip, profile_id)


# ----------------------------
# SpringBoot mapping fetchers
# ----------------------------
async def _fetch_mappings_from_springboot_for_device_ids(device_ids: List[str]) -> Dict[str, str]:
    """
    Primary method to fetch device_id -> ip mappings for a list of device_ids from Spring Boot.

    Expected POST:
      POST /api/device/mappings
      { "device_ids": ["id1","id2", ...] }

    Expected response:
      { "mappings": [ {"device_id": "...", "ip_address": "..." }, ... ] }

    Returns dict: ip -> device_id
    """
    if not device_ids:
        return {}

    base = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"
    url = f"{base}/api/device/mappings"
    result: Dict[str, str] = {}

    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json={"device_ids": device_ids}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mappings = data.get("mappings") or data.get("data") or []
                    for m in mappings:
                        device_id = m.get("device_id") or m.get("deviceId") or m.get("id")
                        ip = m.get("ip_address") or m.get("ip") or m.get("ipAddress")
                        if device_id and ip:
                            result[ip] = device_id
                else:
                    text = await resp.text()
                    logger.warning("SpringBoot mapping POST failed (%s): %s -> %s", resp.status, url, text[:400])
    except Exception as e:
        logger.exception("SpringBoot mapping fetch error: %s", e)

    return result


async def refresh_cache_for_profile(profile_id: str) -> None:
    """
    Refresh cache for a single profile:
    - read device_ids from DB for profile
    - ask Spring Boot for device_id->ip pairs (single POST)
    - populate cache entries
    """
    from app.db import get_db_connection

    # Clear previous entries for that profile first
    await clear_profile_devices(profile_id)

    # collect device_ids for this profile
    device_ids: List[str] = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
            rows = cursor.fetchall() or []
            cursor.close()
            device_ids = [r["device_id"] for r in rows if r.get("device_id")]
    except Exception:
        logger.exception("Error fetching profile devices for refresh for profile %s", profile_id)
        return

    if not device_ids:
        logger.info("No device_ids found for profile %s; nothing to refresh", profile_id)
        return

    # fetch mappings
    mappings = await _fetch_mappings_from_springboot_for_device_ids(device_ids)
    if not mappings:
        logger.info("No mappings returned from springboot for profile %s", profile_id)
        return

    # set mappings in cache
    now = int(time.time())
    async with _CACHE_LOCK:
        for ip, did in mappings.items():
            _DEVICE_CACHE[ip] = (did, now + CACHE_TTL)
            logger.info("[DEVICE_CACHE REFRESH - PROFILE] %s -> %s (profile=%s ttl=%ds)", ip, did, profile_id, CACHE_TTL)


async def refresh_all_profiles_cache() -> None:
    """
    Refresh mappings for all profiles:
    - load all device_ids from DB (grouped)
    - call Spring Boot in batches (to limit request sizes)
    - populate cache
    """
    from app.db import get_db_connection

    # Build full device_id set from DB
    all_device_ids: List[str] = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT DISTINCT pd.device_id AS device_id FROM syslog_profile_devices pd")
            rows = cursor.fetchall() or []
            cursor.close()
            all_device_ids = [r["device_id"] for r in rows if r.get("device_id")]
    except Exception:
        logger.exception("Error fetching all profile device_ids for full refresh")
        return

    if not all_device_ids:
        logger.info("No device_ids present in DB; skipping full cache refresh")
        return

    # call Spring Boot in reasonable batches (e.g. 200 ids per call) to avoid huge payloads
    batch_size = 200
    now = int(time.time())
    try:
        for i in range(0, len(all_device_ids), batch_size):
            batch = all_device_ids[i : i + batch_size]
            mappings = await _fetch_mappings_from_springboot_for_device_ids(batch)
            if not mappings:
                logger.debug("No mappings returned for batch starting at %d", i)
                continue
            async with _CACHE_LOCK:
                for ip, did in mappings.items():
                    _DEVICE_CACHE[ip] = (did, now + CACHE_TTL)
                    logger.info("[DEVICE_CACHE REFRESH - ALL] %s -> %s (ttl=%ds)", ip, did, CACHE_TTL)
    except Exception:
        logger.exception("Full cache refresh failed")


def get_cache_snapshot() -> Dict[str, str]:
    """
    Return a copy of the current cache mapping ip -> device_id (ignores expiry).
    This is synchronous and intended for debug logging (safe copy done under lock with asyncio.run if needed).
    """
    # This function is synchronous to allow quick call from sync contexts.
    # We'll create an event loop to acquire the lock safely in most runtime contexts.
    # But if called from within an async context, callers should use the async version below.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # we're in async context; create coro to return snapshot
        async def _snap():
            async with _CACHE_LOCK:
                return {ip: did for ip, (did, _) in _DEVICE_CACHE.items()}
        return loop.run_until_complete(_snap())  # safe when loop running? run_until_complete would fail in running loop
    else:
        # not running loop: create temporary loop
        async def _snap2():
            async with _CACHE_LOCK:
                return {ip: did for ip, (did, _) in _DEVICE_CACHE.items()}
        return asyncio.get_event_loop().run_until_complete(_snap2())


async def get_cache_snapshot_async() -> Dict[str, str]:
    """Async version to be used inside async tasks."""
    async with _CACHE_LOCK:
        return {ip: did for ip, (did, _) in _DEVICE_CACHE.items()}


# ----------------------------
# Profile change notifier
# ----------------------------
async def notify_profile_change(profile_id: str) -> None:
    """
    Called when profile is created/updated/deleted.
    This will evict previous cache entries for that profile and fetch fresh mappings from Spring Boot.
    """
    try:
        logger.info("Profile change notified for %s: evicting & refreshing cache entries", profile_id)
        # evict cached entries for profile
        await clear_profile_devices(profile_id)
        # refresh only that profile mappings
        await refresh_cache_for_profile(profile_id)
    except Exception:
        logger.exception("notify_profile_change failed for profile %s", profile_id)


# ----------------------------
# Background periodic refresh + snapshot logger
# ----------------------------
async def _background_refresh_loop():
    """
    Background loop:
     - every _SNAPSHOT_INTERVAL_SECONDS: log a snapshot of the cache
     - every _REFRESH_INTERVAL_SECONDS: full refresh
    """
    try:
        logger.info("Device cache background task starting (refresh every %ds, snapshot every %ds)",
                    _REFRESH_INTERVAL_SECONDS, _SNAPSHOT_INTERVAL_SECONDS)
        last_full_refresh = 0
        while True:
            now = int(time.time())
            # snapshot logging every SNAPSHOT_INTERVAL_SECONDS
            try:
                snapshot = await get_cache_snapshot_async()
                if snapshot:
                    # show up to 50 entries in logs to avoid huge output
                    sample_items = list(snapshot.items())[:50]
                    logger.debug("[DEVICE_CACHE SNAPSHOT] total=%d sample=%s", len(snapshot), sample_items)
                else:
                    logger.debug("[DEVICE_CACHE SNAPSHOT] cache empty")
            except Exception:
                logger.exception("Error taking cache snapshot")

            # full refresh hourly if time elapsed
            if now - last_full_refresh >= _REFRESH_INTERVAL_SECONDS:
                try:
                    logger.info("Starting scheduled full cache refresh")
                    await refresh_all_profiles_cache()
                    last_full_refresh = now
                except Exception:
                    logger.exception("Scheduled full cache refresh failed")

            # sleep small increments to allow graceful cancellation
            # sleep for SNAPSHOT_INTERVAL_SECONDS (we already did snapshot work)
            await asyncio.sleep(_SNAPSHOT_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Device cache background task cancelled")
        raise
    except Exception:
        logger.exception("Device cache background loop crashed")


def start_background_tasks(loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
    """
    Start the background refresh/snapshot task and return the Task handle.
    Call this from your application startup (e.g. app.main:on_startup).
    """
    global _BACKGROUND_TASK
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.done():
        return _BACKGROUND_TASK
    if loop is None:
        loop = asyncio.get_event_loop()
    _BACKGROUND_TASK = loop.create_task(_background_refresh_loop())
    return _BACKGROUND_TASK


def stop_background_tasks(task: Optional[asyncio.Task]) -> None:
    """
    Cancel background task started by start_background_tasks (if provided).
    Call this on shutdown to cancel gracefully.
    """
    global _BACKGROUND_TASK
    if task:
        task.cancel()
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.cancelled():
        _BACKGROUND_TASK.cancel()
