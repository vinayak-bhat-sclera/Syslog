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
_REFRESH_INTERVAL_SECONDS = getattr(settings, "CACHE_REFRESH_INTERVAL_SECONDS", 3600)  # hourly
_SNAPSHOT_INTERVAL_SECONDS = getattr(settings, "CACHE_SNAPSHOT_INTERVAL_SECONDS", 300)  # 5 minutes


async def _fetch_from_springboot(ip: str) -> Optional[str]:
    """
    Correct lookup for device_id by IP.
    Calls the real SpringBoot endpoint:
    /docker/{docker_name}/getalldeviceids
    """

    # STEP 1 — determine docker_name for this IP
    docker_name = None
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("""
                SELECT p.docker_name 
                FROM syslog_profiles p
                JOIN syslog_profile_devices pd ON p.id = pd.profile_id
                WHERE pd.ip_address = %s
            """, (ip,))
            row = cursor.fetchone()
            cursor.close()

            if row:
                docker_name = row["docker_name"]
    except Exception:
        logger.exception("Failed to determine docker_name for IP %s", ip)

    if not docker_name:
        logger.warning("No docker_name found for IP %s", ip)
        return None

    url = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}/docker/{docker_name}/getalldeviceids"

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("SpringBoot GET failed (%s): %s -> %s",
                                   resp.status, url, text[:300])
                    return None

                data = await resp.json()

                # STEP 2 — find our IP
                for item in data:
                    ip_addr = item.get("ip_address")
                    if ip_addr == ip:
                        return item.get("id")

    except Exception as e:
        logger.exception("SpringBoot lookup error for %s: %s", ip, e)

    return None


async def get_device_id(ip: str) -> Optional[str]:
    now = int(time.time())
    async with _CACHE_LOCK:
        entry = _DEVICE_CACHE.get(ip)
        if entry:
            device_id, expiry = entry
            if expiry > now:
                logger.debug("[DEVICE_CACHE HIT] %s -> %s", ip, device_id)
                return device_id
            else:
                _DEVICE_CACHE.pop(ip, None)

    device_id = await _fetch_from_springboot(ip)
    if device_id:
        async with _CACHE_LOCK:
            _DEVICE_CACHE[ip] = (device_id, int(time.time()) + CACHE_TTL)
            logger.info("[DEVICE_CACHE SET] %s -> %s (ttl=%ds)", ip, device_id, CACHE_TTL)
    else:
        logger.debug("[DEVICE_CACHE MISS] no mapping for %s", ip)
    return device_id


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
        to_remove = [ip for ip, (did, _) in _DEVICE_CACHE.items() if did in ids_set]
        for ip in to_remove:
            _DEVICE_CACHE.pop(ip, None)
            logger.debug("[DEVICE_CACHE EVICT - BY_DEVICE_IDS] evicted %s", ip)


async def clear_profile_devices(profile_id: str) -> None:
    #from app.db import get_db_connection

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
                if r.get("device_id"):
                    device_ids.append(r["device_id"])
    except Exception:
        logger.exception("Error loading profile device_ids for cache eviction")

    await _evict_by_device_ids(device_ids)
    _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)


# ----------------------------
# SpringBoot mapping fetchers
# ----------------------------
async def _fetch_mappings_from_springboot_for_device_ids(device_ids: List[str], docker_name: Optional[str] = None) -> Dict[str, str]:
    if not device_ids:
        return {}

    base = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"

    # ❗ Important fix: use docker_name EXACTLY AS STORED IN DATABASE (preserve case)
    if docker_name:
        docker_name = str(docker_name)  # ensure string, no .lower(), .upper(), nothing altered
        url = f"{base}/docker/{docker_name}/getdevicedetailsbyids?pageno=1&pagesize=100"
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
                        mappings_raw = data.get("devices") or data.get("mappings") or data.get("data") or []
                    for m in mappings_raw:
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

#fetchone()changes have been added here 

async def refresh_cache_for_profile(profile_id: str) -> None:

    device_ids = []
    docker_name = None
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED

            cursor.execute(
                "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
                (profile_id,),
            )
            rows = cursor.fetchall() or []
            device_ids = [r["device_id"] for r in rows if r.get("device_id")]

            cursor.execute(
                "SELECT docker_name FROM syslog_profiles WHERE id=%s",
                (profile_id,),
            )
            p = cursor.fetchone()
            cursor.close()

            if p:
                docker_name = p.get("docker_name")  # preserve exact casing
    except Exception:
        logger.exception(
            "Error fetching profile devices or docker_name for refresh for profile %s",
            profile_id
        )
        return

    prev_set = _PROFILE_DEVICE_MAP.get(profile_id, set())
    _PROFILE_DEVICE_MAP[profile_id] = set(device_ids)

    if not device_ids:
        logger.info("No device_ids found for profile %s; nothing to refresh", profile_id)
        return

    mappings = await _fetch_mappings_from_springboot_for_device_ids(
        device_ids, docker_name=docker_name
    )

    if not mappings:
        logger.info("No mappings returned from springboot for profile %s", profile_id)
        return

    now = int(time.time())
    async with _CACHE_LOCK:
        for ip, did in mappings.items():
            _DEVICE_CACHE[ip] = (did, now + CACHE_TTL)
            logger.info(
                "[DEVICE_CACHE REFRESH - PROFILE] %s -> %s (profile=%s ttl=%ds)",
                ip, did, profile_id, CACHE_TTL
            )

async def refresh_all_profiles_cache() -> None:
    #from app.db import get_db_connection

    profile_to_device_ids = {}
    profile_to_docker = {}

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            cursor.execute("""
                SELECT pd.profile_id AS profile_id, pd.device_id AS device_id
                FROM syslog_profile_devices pd
            """)
            rows = cursor.fetchall() or []
            for r in rows:
                pid, did = r.get("profile_id"), r.get("device_id")
                if pid and did:
                    profile_to_device_ids.setdefault(pid, []).append(did)

            cursor.execute("SELECT id, docker_name FROM syslog_profiles")
            rows2 = cursor.fetchall() or []
            for r in rows2:
                profile_to_docker[r["id"]] = r.get("docker_name")  # EXACT casing preserved

            cursor.close()
    except Exception:
        logger.exception("Error fetching profile device list for full refresh")
        return

    if not profile_to_device_ids:
        logger.info("No device_ids present in DB; skipping full cache refresh")
        return

    now = int(time.time())
    try:
        for profile_id, device_ids in profile_to_device_ids.items():
            docker_name = profile_to_docker.get(profile_id)
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
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        logger.debug("get_cache_snapshot called from running loop — use get_cache_snapshot_async instead")
        return {}
    else:
        async def _snap2():
            async with _CACHE_LOCK:
                return {ip: did for ip, (did, _) in _DEVICE_CACHE.items()}

        return asyncio.get_event_loop().run_until_complete(_snap2())


async def get_cache_snapshot_async() -> Dict[str, str]:
    async with _CACHE_LOCK:
        return {ip: did for ip, (did, _) in _DEVICE_CACHE.items()}


async def notify_profile_change(profile_id: str) -> None:
    try:
        logger.info("Profile change notified for %s: evicting & refreshing cache entries", profile_id)

        #from app.db import get_db_connection
        current_device_ids = set()
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
        removed = list(prev_device_ids - current_device_ids)
        if removed:
            logger.info("Evicting %d device_ids removed from profile %s", len(removed), profile_id)
            await _evict_by_device_ids(removed)

        await clear_profile_devices(profile_id)

        await refresh_cache_for_profile(profile_id)

    except Exception:
        logger.exception("notify_profile_change failed for profile %s", profile_id)


async def _background_refresh_loop():
    try:
        logger.info(
            "Device cache background task starting (refresh every %ds, snapshot every %ds)",
            _REFRESH_INTERVAL_SECONDS, _SNAPSHOT_INTERVAL_SECONDS
        )
        last_full_refresh = 0

        while True:
            now = int(time.time())

            try:
                snapshot = await get_cache_snapshot_async()
                if snapshot:
                    sample_items = list(snapshot.items())[:50]
                    logger.debug("[DEVICE_CACHE SNAPSHOT] total=%d sample=%s", len(snapshot), sample_items)
                else:
                    logger.debug("[DEVICE_CACHE SNAPSHOT] cache empty")
            except Exception:
                logger.exception("Error taking cache snapshot")

            if now - last_full_refresh >= _REFRESH_INTERVAL_SECONDS:
                try:
                    logger.info("Starting scheduled full cache refresh")
                    await refresh_all_profiles_cache()
                    last_full_refresh = now
                except Exception:
                    logger.exception("Scheduled full cache refresh failed")

            await asyncio.sleep(_SNAPSHOT_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Device cache background task cancelled")
        raise
    except Exception:
        logger.exception("Device cache background loop crashed")


def start_background_tasks(loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
    global _BACKGROUND_TASK
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.done():
        return _BACKGROUND_TASK
    if loop is None:
        loop = asyncio.get_event_loop()
    _BACKGROUND_TASK = loop.create_task(_background_refresh_loop())
    return _BACKGROUND_TASK


def stop_background_tasks(task: Optional[asyncio.Task]) -> None:
    global _BACKGROUND_TASK
    if task:
        task.cancel()
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.cancelled():
        _BACKGROUND_TASK.cancel()
