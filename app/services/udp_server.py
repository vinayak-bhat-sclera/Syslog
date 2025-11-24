# app/services/udp_server.py
import asyncio
import logging
import json
import uuid
import inspect
from typing import Any, Dict, List, Tuple, Optional

from app.services.syslog_parser import parse_syslog_line
from app.services.semantic_matcher import find_matching_keyword
from app.services.forwarding import forward_to_integration
from app.db import get_db_connection
from app.config import settings
from app.services.device_cache import get_device_id, set_device_cache

logger = logging.getLogger("app.services.udp_server")


# ─────────────────────────────
# DB Helpers
# ─────────────────────────────
def get_profiles_for_device(device_id: str) -> List[Dict[str, Any]]:
    """Return all profiles mapped to a given device ID."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT p.* 
                FROM syslog_profile_devices pd
                JOIN syslog_profiles p ON pd.profile_id = p.id
                WHERE pd.device_id = %s
                """,
                (device_id,),
            )
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
# UDP Syslog Server (async)
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
                logger.info("[SYSLOG RECEIVED] %s %s", src_ip, raw[:400])

                parsed = parse_syslog_line(raw, src_ip)
                if not parsed:
                    logger.warning("Failed to parse syslog from %s", src_ip)
                    return

                # Resolve device_id via cache
                device_id = await get_device_id(src_ip)
                if not device_id:
                    logger.warning("No device_id found for IP %s", src_ip)
                    return

                profiles = get_profiles_for_device(device_id)
                if not profiles:
                    logger.info("No profiles linked to device %s", device_id)
                    return

                did_store = False
                did_forward = False

                for prof in profiles:
                    prof_id = prof["id"]
                    prof_type = prof["type"]

                    # parse profile criteria
                    prios = json.loads(prof["priorities"]) if prof.get("priorities") else None
                    facs = json.loads(prof["facilities"]) if prof.get("facilities") else None
                    kws = json.loads(prof["keywords"]) if prof.get("keywords") else []

                    # keyword match (highest priority)
                    matched_kw = None
                    kw_score = 0.0
                    if kws:
                        try:
                            matched_kw, kw_score = find_matching_keyword(parsed["message"], kws)
                        except Exception:
                            logger.exception("Keyword matching error for profile %s", prof_id)

                    # priority match (OR logic)
                    p_match = (not prios) or parsed["priority_code"] in prios

                    # facility match (OR logic)
                    f_match = (not facs) or parsed["facility_code"] in facs

                    # NEW ACCEPTANCE RULE
                    accept = False
                    if matched_kw:
                        accept = True
                    elif p_match or f_match:
                        accept = True

                    if not accept:
                        logger.debug("Profile %s did not accept message", prof_id)
                        continue

                    # INTERNAL → store incident
                    if prof_type == "internal":
                        try:
                            with get_db_connection() as cnx:
                                cursor = cnx.cursor()
                                iid = str(uuid.uuid4())
                                cursor.execute(
                                    """
                                    INSERT INTO syslog_incidents
                                    (id, device_id, profile_id, priority_code, facility_code, message, timestamp)
                                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                                    """,
                                    (
                                        iid,
                                        device_id,
                                        prof_id,
                                        parsed["priority_code"],
                                        parsed["facility_code"],
                                        parsed["message"],
                                    ),
                                )
                                cnx.commit()
                                did_store = True
                                logger.info("Stored incident %s for profile %s device %s", iid, prof_id, device_id)
                        except Exception:
                            logger.exception("DB insert error for profile %s", prof_id)

                    else:
                        # EXTERNAL → forward ALWAYS (unchanged), but schedule safely
                        integrations = get_integrations_for_profile(prof_id)
                        if not integrations:
                            logger.info("External profile %s has no integrations", prof_id)

                        for integ in integrations:
                            try:
                                # If forward_to_integration is async, schedule it as a task.
                                # If it's sync/blocking, run it in executor to avoid blocking the loop.
                                if inspect.iscoroutinefunction(forward_to_integration):
                                    # schedule coroutine, do not await here
                                    asyncio.create_task(forward_to_integration(integ, parsed))
                                    did_forward = True
                                    logger.info(
                                        "Scheduled async forward for profile %s -> integration %s (%s:%s)",
                                        prof_id,
                                        integ.get("id"),
                                        integ.get("ip_address"),
                                        integ.get("port"),
                                    )
                                else:
                                    # schedule sync function in threadpool
                                    loop.run_in_executor(None, forward_to_integration, integ, parsed)
                                    did_forward = True
                                    logger.info(
                                        "Scheduled sync forward for profile %s -> integration %s (%s:%s) in executor",
                                        prof_id,
                                        integ.get("id"),
                                        integ.get("ip_address"),
                                        integ.get("port"),
                                    )
                            except Exception:
                                logger.exception(
                                    "Forwarding scheduling failed for profile %s integration %s",
                                    prof_id,
                                    integ.get("id"),
                                )

                if not did_store:
                    logger.debug("No internal incidents stored for device %s", device_id)
                if not did_forward:
                    logger.debug("No external forwards triggered for device %s", device_id)

            except Exception:
                logger.exception("UDP message processing failed")

    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(),
        local_addr=("0.0.0.0", settings.SYSLOG_PORT),
    )
    logger.info("UDP syslog server listening on %d", settings.SYSLOG_PORT)
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        transport.close()
        raise
