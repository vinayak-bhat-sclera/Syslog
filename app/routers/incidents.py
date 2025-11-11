# app/routers/incidents.py
import logging
from typing import Optional, Any, Dict

from fastapi import APIRouter, Query, HTTPException  # Depends  # Uncomment Depends when JWT auth is added
from app.db import get_db_connection
from app.utils.constants import PRIORITY_MAP, FACILITY_MAP
# from app.utils.token_validator import get_current_user  # Uncomment when enabling JWT auth

router = APIRouter()
logger = logging.getLogger("app.routers.incidents")


# ─────────────────────────────
# List / Filter Syslog Incidents
# ─────────────────────────────
@router.get("/syslog_incidents", status_code=200)
def list_incidents(
    network: Optional[str] = Query(None, description="Filter by network (profile network association)"),
    device_id: Optional[str] = Query(None, description="Filter by device_id"),
    profile_id: Optional[str] = Query(None, description="Filter by profile_id"),
    priority_code: Optional[int] = Query(None, description="Filter by syslog priority code"),
    facility_code: Optional[int] = Query(None, description="Filter by syslog facility code"),
    since_ts: Optional[str] = Query(None, description="Filter incidents since timestamp (inclusive, ISO8601)"),
    until_ts: Optional[str] = Query(None, description="Filter incidents until timestamp (inclusive, ISO8601)"),
    page: int = Query(1, ge=1, description="Pagination page number"),
    limit: int = Query(50, ge=1, le=1000, description="Number of incidents per page"),
    # current_user: Any = Depends(get_current_user),  # Uncomment when auth is enabled
) -> Dict[str, Any]:
    """
    Retrieve syslog incidents with advanced filtering and pagination.

    Filters:
    - network, device_id, profile_id, priority_code, facility_code
    - since_ts, until_ts (timestamps)
    """

    try:
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
            where += " AND inc.timestamp >= %s"
            params.append(since_ts)
        if until_ts:
            where += " AND inc.timestamp <= %s"
            params.append(until_ts)
        if network:
            where += " AND p.network = %s"
            params.append(network)

        offset = (page - 1) * limit

        sql_items = f"""
            SELECT 
                inc.id,
                inc.device_id,
                inc.profile_id,
                inc.priority_code,
                inc.facility_code,
                inc.message,
                inc.timestamp
            FROM syslog_incidents inc
            LEFT JOIN syslog_profiles p ON inc.profile_id = p.id
            {where}
            ORDER BY inc.timestamp DESC
            LIMIT %s OFFSET %s
        """

        sql_count = f"""
            SELECT COUNT(1) as cnt
            FROM syslog_incidents inc
            LEFT JOIN syslog_profiles p ON inc.profile_id = p.id
            {where}
        """

        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # Count first for total pages
            cursor.execute(sql_count, tuple(params))
            total = cursor.fetchone()["cnt"]

            # Fetch paginated data
            cursor.execute(sql_items, tuple(params + [limit, offset]))
            rows = cursor.fetchall() or []
            cursor.close()

        # Add readable labels for priority and facility codes
        for r in rows:
            r["priority_label"] = (
                PRIORITY_MAP.get(r.get("priority_code"), "unknown")
                if r.get("priority_code") is not None else None
            )
            r["facility_label"] = (
                FACILITY_MAP.get(r.get("facility_code"), "unknown")
                if r.get("facility_code") is not None else None
            )

        return {
            "status": "success",
            "total": total,
            "page": page,
            "limit": limit,
            "count": len(rows),
            "filters": {
                "network": network,
                "device_id": device_id,
                "profile_id": profile_id,
                "priority_code": priority_code,
                "facility_code": facility_code,
                "since_ts": since_ts,
                "until_ts": until_ts,
            },
            "items": rows,
        }

    except Exception as e:
        logger.exception("list_incidents failed: %s", e)
        raise HTTPException(status_code=500, detail="DB error fetching syslog incidents")
