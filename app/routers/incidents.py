# app/routers/incidents.py
import logging
from typing import Optional, Any, Dict, List

from fastapi import APIRouter, Query, HTTPException, Path
from app.db import get_db_connection
from app.utils.constants import PRIORITY_MAP, FACILITY_MAP

router = APIRouter()
logger = logging.getLogger("app.routers.incidents")


# ───────────────────────────────────────────────────────────────
# Get Syslog Incidents for a device_id under a docker_name
# ───────────────────────────────────────────────────────────────
from typing import Optional, Any, List

@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_incidents/{device_id}/incidents",
    status_code=200,
)
def list_incidents(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    device_id: str = Path(...),

    priority_code: Optional[Any] = Query(None),
    facility_code: Optional[Any] = Query(None),

    page_no: Any = Query(1),
    page_size: Any = Query(10),
) -> Dict[str, Any]:

    docker_clean = docker_name.lower().strip()

    # ---------------------------------------------------------
    # DEVICE VALIDATION
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # Pagination validation
    # ---------------------------------------------------------
    try:
        page_no = int(page_no)
        page_size = int(page_size)
    except:
        raise HTTPException(status_code=422, detail="page_no and page_size must be integers")

    if page_no < 1 or page_size < 1:
        raise HTTPException(status_code=422, detail="page_no and page_size must be >= 1")

    # ---------------------------------------------------------
    # Robust multi-value parser
    # ---------------------------------------------------------
    def parse_multi_int(value: Optional[Any], field: str) -> Optional[List[int]]:
        """
        Accepts ANY of the following:
            "all"
            ["all"]
            "%22all%22"
            ["1","2","3"]
            "1,2,3"
            ["all","5"]
        Behavior:
            If "all" appears → DISABLE THAT FILTER → return None
        """

        if value is None:
            return None

        # Normalize everything into a list
        if isinstance(value, list):
            values = value
        else:
            values = [value]

        cleaned = []

        for v in values:
            if v is None:
                continue

            v = str(v).strip()

            # Remove brackets and quotes
            v = v.strip("[]").strip().strip('"').strip("'")

            # If comma separated → split
            parts = [p.strip() for p in v.split(",") if p.strip()]

            cleaned.extend(parts)

        # If the ONLY meaningful value is "all" → disable filter
        only_vals = [c.lower() for c in cleaned if c]
        if "all" in only_vals:
            return None

        # Convert to integers
        result = []
        for c in cleaned:
            try:
                result.append(int(c))
            except:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} contains invalid integer value '{c}'"
                )

        return result if result else None

    # Apply parsing
    priority_list = parse_multi_int(priority_code, "priority_code")
    facility_list = parse_multi_int(facility_code, "facility_code")

    # ---------------------------------------------------------
    # Build dynamic SQL
    # ---------------------------------------------------------
    params = [device_id]
    where = " WHERE inc.device_id = %s "

    if priority_list is not None:  # add filter only if not "all"
        placeholders = ",".join(["%s"] * len(priority_list))
        where += f" AND inc.priority_code IN ({placeholders}) "
        params.extend(priority_list)

    if facility_list is not None:
        placeholders = ",".join(["%s"] * len(facility_list))
        where += f" AND inc.facility_code IN ({placeholders}) "
        params.extend(facility_list)

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

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)

            cursor.execute(sql_count, tuple(params))
            total = cursor.fetchone()["cnt"]

            cursor.execute(sql_items, tuple(params + [page_size, offset]))
            rows = cursor.fetchall() or []
            cursor.close()

        for r in rows:
            r["priority_label"] = PRIORITY_MAP.get(r.get("priority_code"), "unknown")
            r["facility_label"] = FACILITY_MAP.get(r.get("facility_code"), "unknown")

        return {
            "status": "success",
            "total": total,
            "page": page_no,
            "limit": page_size,
            "count": len(rows),
            "filters": {
                "docker_name": docker_name,
                "device_id": device_id,
                "priority_code": priority_list,
                "facility_code": facility_list,
            },
            "items": rows,
        }

    except Exception as e:
        logger.exception("list_incidents failed: %s", e)
        raise HTTPException(status_code=500, detail="DB error fetching syslog incidents")
