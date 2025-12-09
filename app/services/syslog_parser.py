# app/services/syslog_parser.py
import re
from typing import Optional, Tuple, Dict, Any

PRIORITY_KEYWORDS = {
    "emerg": 0,
    "alert": 1,
    "critical": 2,
    "crit": 2,
    "error": 3,
    "err": 3,
    "warning": 4,
    "warn": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}

FACILITY_KEYWORDS = {
    "kern": 0,
    "user": 1,
    "mail": 2,
    "daemon": 3,
    "auth": 4,
    "syslog": 5,
    "lpr": 6,
    "news": 7,
    "uucp": 8,
    "cron": 9,
    "authpriv": 10,
}

# Add dynamic entries local0 → local7
for i in range(8):
    FACILITY_KEYWORDS[f"local{i}"] = 11 + i



def extract_pri_from_message(data: str) -> Optional[Tuple[int, int]]:
    try:
        s = data.find("<")
        e = data.find(">", s + 1)
        if s != -1 and e != -1:
            pri_val = int(data[s + 1:e])
            facility = pri_val // 8
            priority = pri_val % 8
            return facility, priority
    except Exception:
        return None
    return None


def parse_syslog_line(data: str, host: str) -> Dict[str, Any]:
    """
    Updated logic:
    - Extract PRI (<14>)
    - OR detect priority/facility from message content
    - OR fallback to defaults (facility=user(1), priority=info(6))
    """
    data = data.strip()
    message_lower = data.lower()

    facility_code = None
    priority_code = None
    message = data

    # ───────────────────────────────────────────────
    # 1. Try extracting <PRI> from syslog header
    # ───────────────────────────────────────────────
    pri_ex = extract_pri_from_message(data)
    if pri_ex:
        facility_code, priority_code = pri_ex
        try:
            end = data.index(">", data.index("<") + 1)
            message = data[end + 1:].strip()
            message_lower = message.lower()
        except Exception:
            pass

    # ───────────────────────────────────────────────
    # 2. Override using message keyword detection
    #    Example: “critical login failure” → priority=2 (crit)
    # ───────────────────────────────────────────────
    for kw, code in PRIORITY_KEYWORDS.items():
        if kw in message_lower:
            priority_code = code
            break

    # ───────────────────────────────────────────────
    # 3. Facility detection from message keywords
    # ───────────────────────────────────────────────
    for fkw, fcode in FACILITY_KEYWORDS.items():
        if fkw in message_lower:
            facility_code = fcode
            break

    # ───────────────────────────────────────────────
    # 4. Fallback defaults if missing
    # ───────────────────────────────────────────────
    if priority_code is None:
        priority_code = 6   # info
    if facility_code is None:
        facility_code = 1   # user

    return {
        "host": host,
        "message": message,
        "priority_code": int(priority_code),
        "facility_code": int(facility_code),
        "raw": data
    }
