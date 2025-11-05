# app/routers/incidents.py
from typing import Optional, Any, Dict, List

from fastapi import APIRouter, Query
from app.db import get_db_connection
from app.utils.constants import PRIORITY_MAP, FACILITY_MAP

router = APIRouter()

@router.get("/syslog_incidents")
def list_incidents(
    device_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    priority_code: Optional[int] = Query(None),
    facility_code: Optional[int] = Query(None),
    since_ts: Optional[str] = Query(None),
    until_ts: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
) -> Dict[str, Any]:
    params = []
    where = " WHERE 1=1 "
    if device_id:
        where += " AND device_id=%s"; params.append(device_id)
    if profile_id:
        where += " AND profile_id=%s"; params.append(profile_id)
    if priority_code is not None:
        where += " AND priority_code=%s"; params.append(priority_code)
    if facility_code is not None:
        where += " AND facility_code=%s"; params.append(facility_code)
    if since_ts:
        where += " AND timestamp>=%s"; params.append(since_ts)
    if until_ts:
        where += " AND timestamp<=%s"; params.append(until_ts)

    offset = (page - 1) * limit
    sql = "SELECT id, device_id, profile_id, priority_code, facility_code, message, timestamp FROM syslog_incidents " + where + " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute(sql, tuple(params + [limit, offset]))
            rows = cursor.fetchall() or []
        finally:
            cursor.close()

    for r in rows:
        r["priority_label"] = PRIORITY_MAP.get(r.get("priority_code"), "unknown") if r.get("priority_code") is not None else None
        r["facility_label"] = FACILITY_MAP.get(r.get("facility_code"), "unknown") if r.get("facility_code") is not None else None
    return {"total": len(rows), "page": page, "limit": limit, "items": rows}
