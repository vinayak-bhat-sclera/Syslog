# app/services/forwarding.py
import socket
import datetime
import logging
from typing import Dict, Any

logger = logging.getLogger("app.services.forwarding")

def forward_to_integration(integration_row: Dict[str, Any], parsed: Dict[str, Any]) -> None:
    target_ip = integration_row.get("ip_address")
    target_port = int(integration_row.get("port", 514))
    try:
        pri = parsed["facility_code"] * 8 + parsed["priority_code"]
        timestamp = datetime.datetime.now().strftime("%b %d %H:%M:%S")
        host = parsed.get("host", "-")
        message = parsed.get("message", "")
        syslog_msg = f"<{pri}>{timestamp} {host} {message}".encode("utf-8")

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(syslog_msg, (target_ip, target_port))
        logger.info("Forwarded RFC3164 syslog to %s:%d", target_ip, target_port)
    except Exception:
        logger.exception("Failed forwarding to %s:%s", target_ip, target_port)
