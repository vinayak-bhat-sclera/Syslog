# app/services/udp_server.py
import asyncio
import logging
import json
import uuid
import aiohttp
from typing import Any, Dict, List, Tuple, Optional

from app.services.syslog_parser import parse_syslog_line
from app.services.semantic_matcher import find_matching_keyword
from app.services.forwarding import forward_to_integration
from app.db import get_db_connection
from app.config import settings
from app.services.device_cache import get_device_id_for_ip, set_device_id_cache

logger = logging.getLogger("app.services.udp_server")


# ─────────────────────────────
# DB Helpers
# ─────────────────────────────
def get_profiles_for_device(device_id: str) -> List[Dict[str, Any]]:
    """Return all profiles mapped to a given device ID."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT p.*
                FROM syslog_profile_devices pd
                JOIN syslog_profiles p ON pd.profile_id = p.id
                WHERE pd.device_id = %s
            """, (device_id,))
            rows = cursor.fetchall() or []
            return rows
        finally:
            cursor.close()


def get_integrations_for_profile(profile_id: str) -> List[Dict[str, Any]]:
    """Fetch all integrations attached to a given profile."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM syslog_integration WHERE profile_id = %s", (profile_id,))
            return cursor.fetchall() or []
        finally:
            cursor.close()


# ─────────────────────────────
# Device Resolver
# ─────────────────────────────
async def resolve_device_id(src_ip: str) -> Optional[str]:
    """
    Resolve device_id for a given IP:
    1. Try from Redis cache
    2. If missing, call Spring Boot API, then cache result
    """
    # Step 1: Try Redis cache
    cached = await get_device_id_for_ip(src_ip)
    if cached:
        logger.debug("[CACHE HIT] %s → %s", src_ip, cached)
        return cached

    # Step 2: Ask Spring Boot
    url = f"http://springboot-server:8080/api/device/by-ip/{src_ip}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    device_id = data.get("device_id")
                    if device_id:
                        await set_device_id_cache(src_ip, device_id)
                        logger.info("[SPRINGBOOT LOOKUP] %s → %s", src_ip, device_id)
                        return device_id
                else:
                    logger.warning("SpringBoot lookup failed (%s): %s", resp.status, await resp.text())
    except Exception as e:
        logger.warning("SpringBoot lookup error for %s: %s", src_ip, e)

    return None


# ─────────────────────────────
# UDP Syslog Server
# ─────────────────────────────
async def start_udp_server():
    loop = asyncio.get_running_loop()

    class SyslogProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: Tuple[str, int]):
            asyncio.create_task(self.handle_message(data, addr))

        async def handle_message(self, data: bytes, addr: Tuple[str, int]):
            try:
                raw = data.decode("utf-8", errors="ignore").strip()
                src_ip = addr[0]
                logger.info("[SYSLOG RECEIVED] %s %s", src_ip, raw[:300])

                parsed = parse_syslog_line(raw, src_ip)
                if not parsed:
                    logger.warning("Failed to parse syslog from %s", src_ip)
                    return

                # Resolve device ID from IP (cache + springboot)
                device_id = await resolve_device_id(src_ip)
                if not device_id:
                    logger.warning("No device_id found for IP %s", src_ip)
                    return

                logger.debug("[DEVICE RESOLVED] %s → %s", src_ip, device_id)

                # Get profiles linked to this device
                profiles = get_profiles_for_device(device_id)
                if not profiles:
                    logger.info("No profiles linked to device %s", device_id)
                    return

                stored, forwarded = False, False

                for prof in profiles:
                    prof_id = prof["id"]
                    prof_type = prof["type"]

                    prios = json.loads(prof["priorities"]) if prof.get("priorities") else None
                    facs = json.loads(prof["facilities"]) if prof.get("facilities") else None
                    kws = json.loads(prof["keywords"]) if prof.get("keywords") else []

                    matched_kw, kw_score = None, 0.0
                    if kws:
                        try:
                            matched_kw, kw_score = find_matching_keyword(parsed["message"], kws)
                        except Exception:
                            logger.exception("Keyword match failed for profile %s", prof_id)

                    p_match = (not prios) or parsed["priority_code"] in prios
                    f_match = (not facs) or parsed["facility_code"] in facs
                    accept = bool(matched_kw or (p_match and f_match))

                    if not accept:
                        continue

                    if prof_type == "internal":
                        try:
                            with get_db_connection() as cnx:
                                cursor = cnx.cursor()
                                iid = str(uuid.uuid4())
                                cursor.execute("""
                                    INSERT INTO syslog_incidents
                                    (id, device_id, profile_id, priority_code, facility_code, message, timestamp)
                                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                                """, (
                                    iid, device_id, prof_id,
                                    parsed["priority_code"], parsed["facility_code"], parsed["message"]
                                ))
                                cnx.commit()
                                stored = True
                                logger.info("[INCIDENT STORED] %s (%s)", iid, device_id)
                        except Exception:
                            logger.exception("Failed to insert incident for profile %s", prof_id)

                    else:  # external
                        integrations = get_integrations_for_profile(prof_id)
                        if not integrations:
                            continue
                        for integ in integrations:
                            try:
                                forward_to_integration(integ, parsed)
                                forwarded = True
                            except Exception:
                                logger.exception("Forwarding failed for profile %s", prof_id)

                if not stored:
                    logger.debug("No incidents stored for %s", device_id)
                if not forwarded:
                    logger.debug("No external forwards triggered for %s", device_id)

            except Exception:
                logger.exception("UDP message handling failed.")

    # Start UDP listener
    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(),
        local_addr=("0.0.0.0", settings.SYSLOG_PORT)
    )
    logger.info("UDP syslog server listening on port %d", settings.SYSLOG_PORT)

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        transport.close()
        raise
