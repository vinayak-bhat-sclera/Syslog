# app/routers/incidents.py
import logging
import datetime
from typing import Optional, Any, Dict, List

from fastapi import APIRouter, Query, HTTPException, Path
from app.db import get_db_connection
from app.utils.constants import PRIORITY_MAP, FACILITY_MAP

router = APIRouter()
logger = logging.getLogger("app.routers.incidents")


# ───────────────────────────────────────────────────────────────
# Get Syslog Incidents for a device_id (docker validation removed)
# ───────────────────────────────────────────────────────────────


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

    # NOTE: docker validation removed — endpoint returns incidents for device_id regardless of docker_name

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

        # ----------------------------
        # Convert timestamp to server local timezone (Python-side)
        # - Assumes stored timestamps are UTC (common setup)
        # - Uses the Python process local timezone (datetime.now().astimezone().tzinfo)
        # - Formats as "YYYY-MM-DD HH:MM:SS" to match existing DB format
        # ----------------------------
        try:
            local_tz = datetime.datetime.now().astimezone().tzinfo
        except Exception:
            local_tz = datetime.timezone.utc

        for r in rows:
            # Add labels as before
            r["priority_label"] = PRIORITY_MAP.get(r.get("priority_code"), "unknown")
            r["facility_label"] = FACILITY_MAP.get(r.get("facility_code"), "unknown")

            # Convert timestamp if present
            ts = r.get("timestamp")
            if isinstance(ts, datetime.datetime):
                # If timestamp is naive, assume UTC
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
                try:
                    ts_local = ts.astimezone(local_tz)
                    # Keep same string format as before (no timezone suffix)
                    r["timestamp"] = ts_local.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    # On any failure, fall back to original value (stringify)
                    r["timestamp"] = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime.datetime) else str(ts)
            else:
                # Not a datetime (could be string) — leave as-is
                r["timestamp"] = r.get("timestamp")

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
