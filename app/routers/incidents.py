# app/routers/incidents.py
import logging
from typing import Optional, Any, Dict

from fastapi import APIRouter, Query, HTTPException, Path
from app.db import get_db_connection
from app.utils.constants import PRIORITY_MAP, FACILITY_MAP

router = APIRouter()
logger = logging.getLogger("app.routers.incidents")


# ───────────────────────────────────────────────────────────────
# Get Syslog Incidents for a device_id under a docker_name
# ───────────────────────────────────────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_incidents/{device_id}/incidents",
    status_code=200,
)
def list_incidents(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(..., description="Docker instance name (replaces network)"),
    device_id: str = Path(..., description="Device ID whose incidents must be fetched"),

    # Filters
    priority_code: Optional[int] = Query(None, description="Filter by syslog priority code"),
    facility_code: Optional[int] = Query(None, description="Filter by syslog facility code"),

    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=1000, description="Results per page"),
) -> Dict[str, Any]:
    """
    Fetch syslog incidents for a device inside the provided docker instance.

    NOTE:
    - syslog_incidents table has NO docker_name column (by requirement).
    - Filtering is done by device_id only.
    """

    try:
        params = [device_id]
        where = " WHERE inc.device_id = %s "

        # Optional filters
        if priority_code is not None:
            where += " AND inc.priority_code = %s"
            params.append(priority_code)

        if facility_code is not None:
            where += " AND inc.facility_code = %s"
            params.append(facility_code)

        # Pagination
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
            {where}
            ORDER BY inc.timestamp DESC
            LIMIT %s OFFSET %s
        """

        sql_count = f"""
            SELECT COUNT(1) AS cnt
            FROM syslog_incidents inc
            {where}
        """

        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # Count total results
            cursor.execute(sql_count, tuple(params))
            total = cursor.fetchone()["cnt"]

            # Get paginated incidents
            cursor.execute(sql_items, tuple(params + [limit, offset]))
            rows = cursor.fetchall() or []
            cursor.close()

        # Add readable labels
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
                "docker_name": docker_name,
                "device_id": device_id,
                "priority_code": priority_code,
                "facility_code": facility_code,
                "since_ts": None,
                "until_ts": None,
            },
            "items": rows,
        }

    except Exception as e:
        logger.exception("list_incidents failed: %s", e)
        raise HTTPException(status_code=500, detail="DB error fetching syslog incidents")
