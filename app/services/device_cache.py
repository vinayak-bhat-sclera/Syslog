# app/services/device_cache.py
import logging
import time
import requests
from typing import Optional, Dict, Tuple

from app.config import settings

logger = logging.getLogger("app.services.device_cache")

# ─────────────────────────────
# In-memory cache structure
# ─────────────────────────────
# ip_to_device[ip] = (device_id, expiry_timestamp)
# device_to_ip[device_id] = ip
ip_to_device: Dict[str, Tuple[str, float]] = {}
device_to_ip: Dict[str, str] = {}

# Cache expiration (seconds)
CACHE_TTL = 3600  # 1 hour


# ─────────────────────────────
# Core cache functions
# ─────────────────────────────
def get_device_id_for_ip(ip: str) -> Optional[str]:
    """
    Get device_id for a given IP from the in-memory cache.
    If expired or missing, return None.
    """
    entry = ip_to_device.get(ip)
    if not entry:
        return None

    device_id, expiry = entry
    if time.time() > expiry:
        # Expired → remove from cache
        ip_to_device.pop(ip, None)
        device_to_ip.pop(device_id, None)
        logger.debug("Cache expired for IP %s", ip)
        return None

    return device_id


def set_device_id_cache(ip: str, device_id: str):
    """
    Store a mapping in cache for both directions (ip → device_id and device_id → ip).
    """
    expiry = time.time() + CACHE_TTL
    ip_to_device[ip] = (device_id, expiry)
    device_to_ip[device_id] = ip
    logger.debug("Cache updated: %s ↔ %s (expires in %ds)", ip, device_id, CACHE_TTL)


def remove_device_mapping(device_id: str):
    """
    Remove a device's cached entry by device_id.
    Automatically removes both ip → device_id and device_id → ip mappings.
    """
    ip = device_to_ip.pop(device_id, None)
    if ip:
        ip_to_device.pop(ip, None)
        logger.debug("Removed cache mapping for device_id=%s (ip=%s)", device_id, ip)
    else:
        logger.debug("No cached mapping found for device_id=%s", device_id)


def clear_cache():
    """Clear the entire cache (for full reset)."""
    ip_to_device.clear()
    device_to_ip.clear()
    logger.warning("In-memory device cache cleared")


# ─────────────────────────────
# Fallback: Fetch from Spring Boot API if not cached
# ─────────────────────────────
def fetch_device_from_api(ip: str) -> Optional[str]:
    """
    Call Spring Boot to fetch device_id for the given IP.
    """
    url = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}/api/device/by-ip/{ip}"
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            device_id = data.get("device_id")
            if device_id:
                logger.info("[SPRINGBOOT LOOKUP] %s → %s", ip, device_id)
                set_device_id_cache(ip, device_id)
                return device_id
        else:
            logger.warning("Spring Boot lookup failed (%s): %s", resp.status_code, url)
    except Exception as e:
        logger.exception("Spring Boot API error for %s: %s", ip, e)
    return None


# ─────────────────────────────
# Unified resolver used by UDP server
# ─────────────────────────────
def resolve_device_id(ip: str) -> Optional[str]:
    """
    Unified resolver:
    1. Check in-memory cache
    2. If missing or expired → call Spring Boot → update cache
    """
    cached = get_device_id_for_ip(ip)
    if cached:
        logger.debug("[CACHE HIT] %s → %s", ip, cached)
        return cached

    logger.debug("[CACHE MISS] %s → fetching from Spring Boot", ip)
    return fetch_device_from_api(ip)
