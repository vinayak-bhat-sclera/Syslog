import asyncio
import time
import logging
from typing import Optional, Dict, Tuple, List, Set

import aiohttp

from app.config import settings
from app.db import get_db_connection

logger = logging.getLogger("app.services.device_cache")

# in-memory cache: ip -> (device_id, expiry_ts)
_DEVICE_CACHE: Dict[str, Tuple[str, int]] = {}
_CACHE_LOCK = asyncio.Lock()
CACHE_TTL = getattr(settings, "CACHE_TTL", 3600)  # seconds

# Track last-known device_ids per profile to detect removed device_ids on update/delete
_PROFILE_DEVICE_MAP: Dict[str, Set[str]] = {}

# Background control
_BACKGROUND_TASK: Optional[asyncio.Task] = None
_REFRESH_INTERVAL_SECONDS = getattr(settings, "CACHE_REFRESH_INTERVAL_SECONDS", 3600)
_SNAPSHOT_INTERVAL_SECONDS = getattr(settings, "CACHE_SNAPSHOT_INTERVAL_SECONDS", 300)


# ---------------------------------------------------------------------
# ✔ UPDATED — REMOVED OLD WRONG IP LOOKUP FUNCTION
# OLD `_fetch_from_springboot(ip)` REMOVED SAFELY
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# SpringBoot mapping fetchers (USED BY REFRESH)
# ---------------------------------------------------------------------
async def _fetch_mappings_from_springboot_for_device_ids(
    device_ids: List[str], docker_name: Optional[str] = None
) -> Dict[str, str]:
    if not device_ids:
        return {}

    base = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"

    if docker_name:
        docker_name = str(docker_name)  # preserve exact case
        url = f"{base}/docker/{docker_name}/getdevicedetailsbyids?pageno=0&pagesize=0"
    else:
        url = f"{base}/api/device/mappings"

    result = {}

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=device_ids) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mappings_raw = []

                    if isinstance(data, list):
                        mappings_raw = data
                    elif isinstance(data, dict):
                        mappings_raw = (
                            data.get("devices")
                            or data.get("mappings")
                            or data.get("data")
                            or []
                        )

                    for m in mappings_raw:
                        device_id = (
                            m.get("id")
                            or m.get("device_id")
                            or m.get("deviceId")
                        )
                        ip = (
                            m.get("ip_address")
                            or m.get("ip")
                            or m.get("ipAddress")
                        )
                        if device_id and ip:
                            result[ip] = device_id

                else:
                    text = await resp.text()
                    logger.warning(
                        "SpringBoot mapping POST failed (%s): %s -> %s",
                        resp.status,
                        url,
                        text[:400],
                    )

    except Exception as e:
        logger.exception("SpringBoot mapping fetch error: %s", e)

    return result


# ---------------------------------------------------------------------
# Device Cache Lookup
# ---------------------------------------------------------------------
async def get_device_id(ip: str) -> Optional[str]:
    now = int(time.time())

    # Check cache
    async with _CACHE_LOCK:
        entry = _DEVICE_CACHE.get(ip)
        if entry:
            device_id, expiry = entry
            if expiry > now:
                logger.debug("[DEVICE_CACHE HIT] %s -> %s", ip, device_id)
                return device_id

            _DEVICE_CACHE.pop(ip, None)

    # ❌ REMOVED fallback SpringBoot IP lookup (wrong API)
    logger.debug("[DEVICE_CACHE MISS] no mapping for %s", ip)
    return None


async def set_device_cache(ip: str, device_id: str, ttl: Optional[int] = None) -> None:
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
    async with _CACHE_LOCK:
        _DEVICE_CACHE.clear()
    logger.warning("Device cache flushed (in-memory).")


async def _evict_by_device_ids(device_ids: List[str]) -> None:
    if not device_ids:
        return

    ids_set = set(device_ids)

    async with _CACHE_LOCK:
        to_remove = [
            ip for ip, (did, _) in _DEVICE_CACHE.items()
            if did in ids_set
        ]
        for ip in to_remove:
            _DEVICE_CACHE.pop(ip, None)
            logger.debug("[DEVICE_CACHE EVICT - BY_DEVICE_IDS] evicted %s", ip)


async def clear_profile_devices(profile_id: str) -> None:
    device_ids = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
                (profile_id,),
            )
            rows = cursor.fetchall() or []
            cursor.close()

            device_ids = [
                r["device_id"]
                for r in rows
                if r.get("device_id")
            ]

    except Exception:
        logger.exception("Error loading profile device_ids for cache eviction")

    await _evict_by_device_ids(device_ids)
    _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)


# ---------------------------------------------------------------------
# Profile cache refresh using correct SpringBoot API
# ---------------------------------------------------------------------
async def refresh_cache_for_profile(profile_id: str) -> None:
    device_ids = []
    docker_name = None

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)

            cursor.execute(
                "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
                (profile_id,),
            )
            rows = cursor.fetchall() or []
            device_ids = [
                r["device_id"] for r in rows if r.get("device_id")
            ]

            cursor.execute(
                "SELECT docker_name FROM syslog_profiles WHERE id=%s",
                (profile_id,),
            )
            row = cursor.fetchone()
            cursor.close()

            if row:
                docker_name = row.get("docker_name")

    except Exception:
        logger.exception(
            "Error fetching profile devices or docker_name for %s",
            profile_id,
        )
        return

    prev_set = _PROFILE_DEVICE_MAP.get(profile_id, set())
    _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)

    if not device_ids:
        logger.info(
            "No device_ids found for profile %s; nothing to refresh",
            profile_id,
        )
        return

    mappings = await _fetch_mappings_from_springboot_for_device_ids(
        device_ids, docker_name=docker_name
    )

    if not mappings:
        logger.info(
            "No mappings returned from springboot for profile %s",
            profile_id,
        )
        return

    now = int(time.time())

    async with _CACHE_LOCK:
        for ip, did in mappings.items():
            _DEVICE_CACHE[ip] = (did, now + CACHE_TTL)
            logger.info(
                "[DEVICE_CACHE REFRESH - PROFILE] %s -> %s (profile=%s)",
                ip,
                did,
                profile_id,
            )


# ---------------------------------------------------------------------
# Full refresh for all profiles
# ---------------------------------------------------------------------
async def refresh_all_profiles_cache() -> None:
    profile_to_device_ids = {}
    profile_to_docker = {}

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT profile_id, device_id
                FROM syslog_profile_devices
                """
            )
            rows = cursor.fetchall() or []

            for r in rows:
                pid, did = r.get("profile_id"), r.get("device_id")
                if pid and did:
                    profile_to_device_ids.setdefault(pid, []).append(did)

            cursor.execute("SELECT id, docker_name FROM syslog_profiles")
            rows = cursor.fetchall() or []

            for r in rows:
                profile_to_docker[r["id"]] = r.get("docker_name")

            cursor.close()

    except Exception:
        logger.exception("Error fetching profile list for full refresh")
        return

    if not profile_to_device_ids:
        logger.info("No device_ids in DB; skipping full refresh")
        return

    now = int(time.time())

    try:
        for profile_id, device_ids in profile_to_device_ids.items():
            docker_name = profile_to_docker.get(profile_id)
            _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)

            if not device_ids:
                continue

            mappings = await _fetch_mappings_from_springboot_for_device_ids(
                device_ids, docker_name=docker_name
            )

            if not mappings:
                continue

            async with _CACHE_LOCK:
                for ip, did in mappings.items():
                    _DEVICE_CACHE[ip] = (did, now + CACHE_TTL)
                    logger.info(
                        "[DEVICE_CACHE REFRESH - ALL] %s -> %s (profile=%s)",
                        ip,
                        did,
                        profile_id,
                    )

    except Exception:
        logger.exception("Full refresh failed")


# ---------------------------------------------------------------------
# Cache snapshot utilities
# ---------------------------------------------------------------------
def get_cache_snapshot() -> Dict[str, str]:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        return {}

    async def _snap():
        async with _CACHE_LOCK:
            return {ip: did for ip, (did, _) in _DEVICE_CACHE.items()}

    return asyncio.get_event_loop().run_until_complete(_snap())


async def get_cache_snapshot_async() -> Dict[str, str]:
    async with _CACHE_LOCK:
        return {
            ip: did
            for ip, (did, _) in _DEVICE_CACHE.items()
        }


# ---------------------------------------------------------------------
# Notify when profile changes (add/remove devices)
# ---------------------------------------------------------------------
async def notify_profile_change(profile_id: str) -> None:
    try:
        logger.info("Profile %s changed — refreshing cache", profile_id)

        current_ids = set()

        try:
            with get_db_connection() as cnx:
                cursor = cnx.cursor(dictionary=True)
                cursor.execute(
                    "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
                    (profile_id,),
                )
                rows = cursor.fetchall() or []
                cursor.close()

                current_ids = {
                    r["device_id"]
                    for r in rows
                    if r.get("device_id")
                }

        except Exception:
            logger.exception("Error reading device_ids for %s", profile_id)

        prev_ids = _PROFILE_DEVICE_MAP.get(profile_id, set())
        removed = list(prev_ids - current_ids)

        if removed:
            await _evict_by_device_ids(removed)

        await clear_profile_devices(profile_id)
        await refresh_cache_for_profile(profile_id)

    except Exception:
        logger.exception("notify_profile_change failed for %s", profile_id)


# ---------------------------------------------------------------------
# Background refresh loop
# ---------------------------------------------------------------------
async def _background_refresh_loop():
    try:
        logger.info(
            "Device cache background task starting (refresh=%ds snapshot=%ds)",
            _REFRESH_INTERVAL_SECONDS,
            _SNAPSHOT_INTERVAL_SECONDS,
        )

        last_full = 0

        while True:
            now = int(time.time())

            try:
                await get_cache_snapshot_async()
            except Exception:
                logger.exception("Error taking snapshot")

            if now - last_full >= _REFRESH_INTERVAL_SECONDS:
                try:
                    await refresh_all_profiles_cache()
                    last_full = now
                except Exception:
                    logger.exception("Scheduled full refresh failed")

            await asyncio.sleep(_SNAPSHOT_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        logger.info("Device cache task cancelled")
        raise
    except Exception:
        logger.exception("Device cache background loop crashed")


def start_background_tasks(loop: Optional[asyncio.AbstractEventLoop] = None):
    global _BACKGROUND_TASK
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.done():
        return _BACKGROUND_TASK

    if loop is None:
        loop = asyncio.get_event_loop()

    _BACKGROUND_TASK = loop.create_task(_background_refresh_loop())
    return _BACKGROUND_TASK


def stop_background_tasks(task: Optional[asyncio.Task]):
    global _BACKGROUND_TASK
    if task:
        task.cancel()
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.cancelled():
        _BACKGROUND_TASK.cancel()
