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
    docker_name: str = Path(..., description="Docker instance name"),
    device_id: str = Path(..., description="Device ID whose incidents must be fetched"),

    priority_code: Optional[Any] = Query(None, description="Filter by syslog priority"),
    facility_code: Optional[Any] = Query(None, description="Filter by syslog facility"),

    page: Any = Query(1, description="Page number"),
    limit: Any = Query(10, description="Limit per page"),
) -> Dict[str, Any]:

    # Validate device_id belongs to this docker_name  ❗ NEW
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT p.docker_name
                FROM syslog_profile_devices pd
                JOIN syslog_profiles p ON p.id = pd.profile_id
                WHERE pd.device_id = %s
                """,
                (device_id,),
            )
            row = cursor.fetchone()
            cursor.close()

        if not row:
            raise HTTPException(status_code=404, detail="Device not found in any profile")

        if row["docker_name"] != docker_name:
            raise HTTPException(status_code=403, detail="docker_name mismatch for device_id")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("docker_name validation failed: %s", e)
        raise HTTPException(status_code=500, detail="Error validating docker_name")

    # Convert page, limit to int
    try:
        page = int(page)
        limit = int(limit)
    except Exception:
        raise HTTPException(status_code=422, detail="page and limit must be integers")

    if page < 1 or limit < 1:
        raise HTTPException(status_code=422, detail="page and limit must be >= 1")

    # Convert filters to int
    def convert_optional_int(value):
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        try:
            return int(value)
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="priority_code and facility_code must be integers",
            )

    priority_code = convert_optional_int(priority_code)
    facility_code = convert_optional_int(facility_code)

    try:
        params = [device_id]
        where = " WHERE inc.device_id = %s "

        if priority_code is not None:
            where += " AND inc.priority_code = %s"
            params.append(priority_code)

        if facility_code is not None:
            where += " AND inc.facility_code = %s"
            params.append(facility_code)

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

            cursor.execute(sql_count, tuple(params))
            total = cursor.fetchone()["cnt"]

            cursor.execute(sql_items, tuple(params + [limit, offset]))
            rows = cursor.fetchall() or []
            cursor.close()

        for r in rows:
            r["priority_label"] = PRIORITY_MAP.get(r.get("priority_code"), "unknown")
            r["facility_label"] = FACILITY_MAP.get(r.get("facility_code"), "unknown")

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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_incidents failed: %s", e)
        raise HTTPException(status_code=500, detail="DB error fetching syslog incidents")
