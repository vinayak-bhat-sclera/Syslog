# app/services/syslog_parser.py
from typing import Optional, Tuple, Dict, Any

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
    data = data.strip()
    pri_ex = extract_pri_from_message(data)
    facility_code = None
    priority_code = None
    message = data
    if pri_ex:
        facility_code, priority_code = pri_ex
        try:
            end = data.index(">", data.index("<") + 1)
            message = data[end + 1:].strip()
        except Exception:
            message = data
    else:
        priority_code = 6
        facility_code = 1
    return {
        "host": host,
        "message": message,
        "priority_code": int(priority_code),
        "facility_code": int(facility_code),
        "raw": data
    }
