# app/routers/incidents.py
from typing import Optional, Any, Dict

from fastapi import APIRouter, Query
from app.db import get_db_connection
from app.utils.constants import PRIORITY_MAP, FACILITY_MAP

router = APIRouter()


@router.get("/syslog_incidents")
def list_incidents(
    network: Optional[str] = Query(None, description="Filter incidents by profile network"),
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
        where += " AND inc.device_id=%s"
        params.append(device_id)
    if profile_id:
        where += " AND inc.profile_id=%s"
        params.append(profile_id)
    if priority_code is not None:
        where += " AND inc.priority_code=%s"
        params.append(priority_code)
    if facility_code is not None:
        where += " AND inc.facility_code=%s"
        params.append(facility_code)
    if since_ts:
        where += " AND inc.timestamp>=%s"
        params.append(since_ts)
    if until_ts:
        where += " AND inc.timestamp<=%s"
        params.append(until_ts)
    if network:
        where += " AND p.network=%s"
        params.append(network)

    offset = (page - 1) * limit

    sql_items = (
        "SELECT inc.id, inc.device_id, inc.profile_id, inc.priority_code, inc.facility_code, inc.message, inc.timestamp "
        "FROM syslog_incidents inc LEFT JOIN syslog_profiles p ON inc.profile_id = p.id "
        + where
        + " ORDER BY inc.timestamp DESC LIMIT %s OFFSET %s"
    )
    sql_count = "SELECT COUNT(1) as cnt FROM syslog_incidents inc LEFT JOIN syslog_profiles p ON inc.profile_id = p.id " + where

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql_count, tuple(params))
        total = cursor.fetchone()["cnt"]
        cursor.execute(sql_items, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    for r in rows:
        r["priority_label"] = PRIORITY_MAP.get(r.get("priority_code"), "unknown") if r.get("priority_code") is not None else None
        r["facility_label"] = FACILITY_MAP.get(r.get("facility_code"), "unknown") if r.get("facility_code") is not None else None

    return {"total": total, "page": page, "limit": limit, "items": rows}
