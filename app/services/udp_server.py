# app/services/udp_server.py
import asyncio
import logging
import json
import uuid
from typing import Any, Dict, List, Tuple, Optional
from app.services.syslog_parser import parse_syslog_line
from app.services.semantic_matcher import find_matching_keyword
from app.services.forwarding import forward_to_integration
from app.services.device_cache import resolve_device_id
from app.db import get_db_connection
from app.config import settings

logger = logging.getLogger("app.services.udp_server")


# ─────────────────────────────
# DB Helpers
# ─────────────────────────────
def get_profiles_for_device(device_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.* 
            FROM syslog_profile_devices pd
            JOIN syslog_profiles p ON pd.profile_id = p.id
            WHERE pd.device_id = %s
        """, (device_id,))
        rows = cursor.fetchall() or []
        cursor.close()
        return rows


def get_integrations_for_profile(profile_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT * FROM syslog_integration WHERE profile_id = %s", (profile_id,))
        rows = cursor.fetchall() or []
        cursor.close()
        return rows


# ─────────────────────────────
# Async UDP Syslog Server
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
                logger.info("[SYSLOG RECEIVED] from %s: %s", src_ip, raw[:300])

                parsed = parse_syslog_line(raw, src_ip)
                if not parsed:
                    return

                # Async device ID resolution
                device_id = await resolve_device_id(src_ip)
                if not device_id:
                    logger.warning("Unrecognized device IP %s", src_ip)
                    return

                profiles = get_profiles_for_device(device_id)
                if not profiles:
                    logger.info("No profiles for device %s", device_id)
                    return

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
                                    VALUES (%s,%s,%s,%s,%s,%s,NOW())
                                """, (iid, device_id, prof_id,
                                      parsed["priority_code"], parsed["facility_code"], parsed["message"]))
                                cnx.commit()
                                cursor.close()
                                logger.info("[INCIDENT STORED] %s for %s", iid, device_id)
                        except Exception:
                            logger.exception("DB insert failed for profile %s", prof_id)
                    else:
                        integrations = get_integrations_for_profile(prof_id)
                        for integ in integrations:
                            try:
                                forward_to_integration(integ, parsed)
                            except Exception:
                                logger.exception("Forward failed for %s", prof_id)

            except Exception:
                logger.exception("Error handling UDP syslog packet.")

    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(),
        local_addr=("0.0.0.0", settings.SYSLOG_PORT)
    )
    logger.info("UDP syslog server listening on port %d", settings.SYSLOG_PORT)

    try:
        await asyncio.Future()  # Run forever
    except asyncio.CancelledError:
        transport.close()
        raise
