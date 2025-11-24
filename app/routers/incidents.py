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

    priority_code: Optional[Any] = Query(None),
    facility_code: Optional[Any] = Query(None),

    page_no: Any = Query(1),
    page_size: Any = Query(10),
) -> Dict[str, Any]:

    # -----------------------------------------------------------------
    # DEVICE VALIDATION LOGIC (UPDATED)
    #
    # ✔ If docker_name == "all" → ALWAYS ALLOW (ZERO validation)
    # ✔ If docker_name != "all":
    #       - If device assigned → enforce docker match
    #       - If device NOT assigned → ALLOW
    # -----------------------------------------------------------------
    docker_clean = docker_name.lower().strip()

    if docker_clean != "all":
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)
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

        if row and row["docker_name"] != docker_name:
            raise HTTPException(status_code=403, detail="docker_name mismatch for device_id")

        # If row is None → device not assigned → STILL allowed

    # -----------------------------------------------------------------
    # Pagination validation (UPDATED to use page_no & page_size)
    # -----------------------------------------------------------------
    try:
        page_no = int(page_no)
        page_size = int(page_size)
    except:
        raise HTTPException(status_code=422, detail="page_no and page_size must be integers")

    if page_no < 1 or page_size < 1:
        raise HTTPException(status_code=422, detail="page_no and page_size must be >= 1")

    # -----------------------------------------------------------------
    # Convert filters
    # -----------------------------------------------------------------
    def convert_optional_int(v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        try:
            return int(v)
        except:
            raise HTTPException(
                status_code=422,
                detail="priority_code and facility_code must be integers"
            )

    priority_code = convert_optional_int(priority_code)
    facility_code = convert_optional_int(facility_code)

    # -----------------------------------------------------------------
    # Build SQL query
    # -----------------------------------------------------------------
    try:
        params = [device_id]
        where = " WHERE inc.device_id = %s "

        if priority_code is not None:
            where += " AND inc.priority_code = %s"
            params.append(priority_code)

        if facility_code is not None:
            where += " AND inc.facility_code = %s"
            params.append(facility_code)

        offset = (page_no - 1) * page_size

        sql_items = f"""
            SELECT inc.id, inc.device_id, inc.profile_id,
                   inc.priority_code, inc.facility_code,
                   inc.message, inc.timestamp
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
            cursor = cnx.cursor(buffered=True, dictionary=True)

            # Count
            cursor.execute(sql_count, tuple(params))
            total = cursor.fetchone()["cnt"]

            # Paginated items
            cursor.execute(sql_items, tuple(params + [page_size, offset]))
            rows = cursor.fetchall() or []
            cursor.close()

        # Add human-readable labels
        for r in rows:
            r["priority_label"] = PRIORITY_MAP.get(r.get("priority_code"), "unknown")
            r["facility_label"] = FACILITY_MAP.get(r.get("facility_code"), "unknown")

        # -----------------------------------------------------------------
        # Final response (unchanged except param name updates)
        # -----------------------------------------------------------------
        return {
            "status": "success",
            "total": total,
            "page": page_no,
            "limit": page_size,
            "count": len(rows),
            "filters": {
                "docker_name": docker_name,
                "device_id": device_id,
                "priority_code": priority_code,
                "facility_code": facility_code,
            },
            "items": rows,
        }

    except Exception as e:
        logger.exception("list_incidents failed: %s", e)
        raise HTTPException(status_code=500, detail="DB error fetching syslog incidents")
