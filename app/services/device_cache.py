# app/services/device_cache.py
import logging
import requests
import redis
from typing import Optional
from app.config import settings

logger = logging.getLogger("app.services.device_cache")

try:
    redis_client = redis.StrictRedis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=0,
        decode_responses=True
    )
    logger.info("Connected to Redis successfully.")
except Exception as e:
    logger.exception("Failed to connect to Redis: %s", e)
    redis_client = None

CACHE_TTL = 3600  # 1 hour


def get_device_id(ip: str) -> Optional[str]:
    """
    Get device_id for an IP.
    Checks Redis first, then Spring Boot, then caches.
    """
    if not redis_client:
        return fetch_device_from_api(ip)

    device_id = redis_client.get(ip)
    if device_id:
        logger.debug("Cache hit: %s → %s", ip, device_id)
        return device_id

    device_id = fetch_device_from_api(ip)
    if device_id:
        redis_client.setex(ip, CACHE_TTL, device_id)
        logger.info("Cached mapping %s → %s", ip, device_id)
    return device_id


def fetch_device_from_api(ip: str) -> Optional[str]:
    """
    Call Spring Boot for IP → device_id mapping.
    Adjust the endpoint as per your Spring Boot service.
    """
    url = f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}/api/device/by-ip/{ip}"
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            device_id = data.get("device_id")
            if device_id:
                logger.debug("API: %s → %s", ip, device_id)
                return device_id
        logger.warning("API failed (%s): %s", resp.status_code, url)
    except Exception as e:
        logger.exception("API error for %s: %s", ip, e)
    return None


def remove_device_mapping(ip: str):
    """Remove a cached mapping."""
    if redis_client:
        redis_client.delete(ip)
        logger.info("Removed cache for %s", ip)


def clear_cache():
    """Clear all mappings."""
    if redis_client:
        redis_client.flushdb()
        logger.warning("Redis cache cleared")
