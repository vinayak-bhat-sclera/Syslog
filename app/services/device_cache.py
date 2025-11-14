import asyncio
import time
import logging
from typing import Optional, Dict, Tuple, List, Any, Set

import aiohttp

from app.config import settings

logger = logging.getLogger("app.services.device_cache")

# in-memory cache: ip -> (device_id, expiry_ts)
_DEVICE_CACHE: Dict[str, Tuple[str, int]] = {}
_CACHE_LOCK = asyncio.Lock()
CACHE_TTL = getattr(settings, "CACHE_TTL", 3600)  # seconds

# Track last-known device_ids per profile to detect removed device_ids on update/delete
_PROFILE_DEVICE_MAP: Dict[str, Set[str]] = {}

# Background control
_BACKGROUND_TASK: Optional[asyncio.Task] = None
_REFRESH_INTERVAL_SECONDS = getattr(settings, "CACHE_REFRESH_INTERVAL_SECONDS", 3600)  # hourly
_SNAPSHOT_INTERVAL_SECONDS = getattr(settings, "CACHE_SNAPSHOT_INTERVAL_SECONDS", 300)  # 5 minutes


async def _fetch_from_springboot(ip: str) -> Optional[str]:
    """
    Async request to Spring Boot to resolve ip -> device_id.
    Expects Spring Boot endpoint that returns JSON { "device_id": "..." }.
    (legacy fallback behavior — still present)
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

    # cache miss -> call springboot by-ip (legacy fallback)
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


async def _evict_by_device_ids(device_ids: List[str]) -> None:
    """Evict any cache entries whose device_id is in device_ids (internal helper)."""
    if not device_ids:
        return
    ids_set = set(device_ids)
    async with _CACHE_LOCK:
        to_remove: List[str] = []
        for ip, (did, _) in list(_DEVICE_CACHE.items()):
            if did in ids_set:
                to_remove.append(ip)
        for ip in to_remove:
            _DEVICE_CACHE.pop(ip, None)
            logger.debug("[DEVICE_CACHE EVICT - BY_DEVICE_IDS] evicted %s (device_id in provided list)", ip)


async def clear_profile_devices(profile_id: str) -> None:
    """
    Remove cached entries that belong to mappings in profile_id.
    It looks up device_ids for that profile in the DB and evicts any cache entries where device_id matches.
    Also updates the in-memory profile->device_id map.
    """
    # Lazy import to avoid cycle
    from app.db import get_db_connection

    device_ids: List[str] = []
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

    # Evict entries whose device_id is in device_ids
    await _evict_by_device_ids(device_ids)

    # Update the profile-device map to the current set (may be empty)
    _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)


# ----------------------------
# SpringBoot mapping fetchers
# ----------------------------
async def _fetch_mappings_from_springboot_for_device_ids(device_ids: List[str], docker_name: Optional[str] = None) -> Dict[str, str]:
    """
    Primary method to fetch device_id -> ip mappings for a list of device_ids from Spring Boot.

    Expected request (real service):
      POST /docker/{docker_name}/getdevicedetailsbyids?pageno=1&pagesize=100
      BODY: ["id1","id2",...]

    Expected response (real service returns list of objects):
      [ {"id":"<device_id>", "ip_address":"<ip>", ...}, ... ]

    This function returns dict: ip -> device_id
    """
    if not device_ids:
        return {}

    base = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"
    # Use the docker_name path if provided; otherwise fall back to generic mapping endpoint
    if docker_name:
        url = f"{base}/docker/{docker_name}/getdevicedetailsbyids?pageno=1&pagesize=100"
    else:
        # fallback if caller didn't supply docker_name (defensive)
        url = f"{base}/api/device/mappings"

    result: Dict[str, str] = {}

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # The real service expects a JSON array body (as in your curl) — send the array.
            async with session.post(url, json=device_ids) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # real service returns a list (not wrapped), or sometimes a wrapper -> handle both
                    mappings_raw = []
                    if isinstance(data, list):
                        mappings_raw = data
                    elif isinstance(data, dict):
                        # try common keys
                        mappings_raw = data.get("devices") or data.get("mappings") or data.get("data") or []
                    else:
                        mappings_raw = []

                    for m in mappings_raw:
                        # real response uses "id" and "ip_address"
                        device_id = m.get("id") or m.get("device_id") or m.get("deviceId")
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
    - ask Spring Boot for device_id->ip pairs for that profile's docker_name
    - populate cache entries and update profile->device map
    """
    from app.db import get_db_connection

    # collect device_ids and docker_name for this profile
    device_ids: List[str] = []
    docker_name: Optional[str] = None
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
            rows = cursor.fetchall() or []
            device_ids = [r["device_id"] for r in rows if r.get("device_id")]
            cursor.execute("SELECT docker_name FROM syslog_profiles WHERE id=%s", (profile_id,))
            p = cursor.fetchone()
            cursor.close()
            if p:
                docker_name = p.get("docker_name")
    except Exception:
        logger.exception("Error fetching profile devices or docker_name for refresh for profile %s", profile_id)
        return

    # Update the profile-device map before doing external calls (so notify/evict logic has previous state)
    prev_set = _PROFILE_DEVICE_MAP.get(profile_id, set())
    _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)

    if not device_ids:
        logger.info("No device_ids found for profile %s; nothing to refresh", profile_id)
        return

    # fetch mappings
    mappings = await _fetch_mappings_from_springboot_for_device_ids(device_ids, docker_name=docker_name)
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
    - load profile->device_ids mapping from DB
    - for each profile call Spring Boot and update cache and profile map
    """
    from app.db import get_db_connection

    # Build map: profile_id -> list(device_id)
    profile_to_device_ids: Dict[str, List[str]] = {}
    profile_to_docker: Dict[str, Optional[str]] = {}
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT pd.profile_id AS profile_id, pd.device_id AS device_id
                FROM syslog_profile_devices pd
                """
            )
            rows = cursor.fetchall() or []
            for r in rows:
                pid = r.get("profile_id")
                did = r.get("device_id")
                if pid and did:
                    profile_to_device_ids.setdefault(pid, []).append(did)
            # fetch docker_name per profile
            cursor.execute("SELECT id, docker_name FROM syslog_profiles")
            rows2 = cursor.fetchall() or []
            for r in rows2:
                pid = r.get("id")
                docker_name = r.get("docker_name")
                profile_to_docker[pid] = docker_name
            cursor.close()
    except Exception:
        logger.exception("Error fetching profile device list for full refresh")
        return

    if not profile_to_device_ids:
        logger.info("No device_ids present in DB; skipping full cache refresh")
        return

    # iterate each profile and refresh its mappings
    now = int(time.time())
    try:
        for profile_id, device_ids in profile_to_device_ids.items():
            docker_name = profile_to_docker.get(profile_id)
            # update profile map
            _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)
            if not device_ids:
                continue
            mappings = await _fetch_mappings_from_springboot_for_device_ids(device_ids, docker_name=docker_name)
            if not mappings:
                logger.debug("No mappings returned for profile %s during full refresh", profile_id)
                continue
            async with _CACHE_LOCK:
                for ip, did in mappings.items():
                    _DEVICE_CACHE[ip] = (did, now + CACHE_TTL)
                    logger.info("[DEVICE_CACHE REFRESH - ALL] %s -> %s (profile=%s ttl=%ds)", ip, did, profile_id, CACHE_TTL)
    except Exception:
        logger.exception("Full cache refresh failed")


def get_cache_snapshot() -> Dict[str, str]:
    """
    Return a copy of the current cache mapping ip -> device_id (ignores expiry).
    This is synchronous and intended for debug logging (safe copy done under lock with asyncio.run if needed).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # if called from running loop, create coroutine and run it appropriately
        # NOTE: run_until_complete cannot be called when loop is running; callers inside async should use get_cache_snapshot_async
        # For safety here, return an empty dict if called incorrectly.
        logger.debug("get_cache_snapshot called from running loop — use get_cache_snapshot_async instead")
        return {}
    else:
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
    This will:
     - determine device_ids removed since last-known state and evict any cache entries for them
     - evict and refresh cache entries for the profile (fresh fetch)
    """
    try:
        logger.info("Profile change notified for %s: evicting & refreshing cache entries", profile_id)

        # fetch current device_ids from DB
        from app.db import get_db_connection
        current_device_ids: Set[str] = set()
        try:
            with get_db_connection() as cnx:
                cursor = cnx.cursor(dictionary=True)
                cursor.execute("SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                rows = cursor.fetchall() or []
                cursor.close()
                current_device_ids = {r["device_id"] for r in rows if r.get("device_id")}
        except Exception:
            logger.exception("Error fetching current device_ids for notify_profile_change %s", profile_id)

        prev_device_ids = _PROFILE_DEVICE_MAP.get(profile_id, set())

        # device_ids removed by the update/delete = prev - current
        removed = list(prev_device_ids - current_device_ids)
        if removed:
            logger.info("Evicting %d device_ids removed from profile %s", len(removed), profile_id)
            await _evict_by_device_ids(removed)

        # now clear any profile-specific entries (and update the profile map)
        await clear_profile_devices(profile_id)

        # refresh profile mappings
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

            # sleep for the snapshot interval (small increments)
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
