# app/routers/profile_devices.py
import uuid
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Path, status

from app.db import get_db_connection

router = APIRouter()
logger = logging.getLogger("app.routers.profile_devices")


# ───────────────────────────────────────────────────────────────
# Get all device IDs for a docker_name
# ───────────────────────────────────────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_devices",
    status_code=status.HTTP_200_OK,
)
def get_all_device_ids(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    page: Any = Query(1, description="Page number (int or str)"),
    limit: Any = Query(100, description="Limit (int or str)"),
):
    """
    Fetch all device_ids associated with ANY profile inside a given docker_name.
    NOTE: This API does NOT use profile_type filter anymore.
    Accepts page & limit as strings and converts them to integers.
    """

    # Ensure page & limit are integers
    try:
        page = int(page)
        limit = int(limit)
    except Exception:
        raise HTTPException(status_code=422, detail="page and limit must be integers")

    if page < 1 or limit < 1:
        raise HTTPException(status_code=422, detail="page and limit must be >= 1")

    offset = (page - 1) * limit

    sql = """
        SELECT DISTINCT pd.device_id
        FROM syslog_profile_devices pd
        JOIN syslog_profiles p ON pd.profile_id = p.id
        WHERE p.docker_name = %s
        ORDER BY pd.device_id ASC
        LIMIT %s OFFSET %s
    """

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(sql, (docker_name, limit, offset))
            rows = [r[0] for r in cursor.fetchall() or []]
            cursor.close()
    except Exception as e:
        logger.exception("get_all_device_ids failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error fetching device IDs")

    return {
        "total": len(rows),
        "page": page,
        "limit": limit,
        "docker_name": docker_name,
        "profile_type": None,
        "device_ids": rows,
    }


# ───────────────────────────────────────────────────────────────
# Get Devices for a Specific Profile
# ───────────────────────────────────────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profile_devices/{profile_id}",
    status_code=status.HTTP_200_OK,
)
def get_devices_by_profile(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_id: str = Path(...),
):
    """
    Retrieve all devices assigned to a specific profile.
    Ensures profile belongs to provided docker_name.
    """

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # Validate docker_name matches the profile
            cursor.execute(
                "SELECT docker_name FROM syslog_profiles WHERE id=%s",
                (profile_id,),
            )
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Profile not found")

            if row["docker_name"] != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            # Fetch devices under this profile
            cursor.execute(
                "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
                (profile_id,),
            )
            devices = [r["device_id"] for r in cursor.fetchall() or []]

            cursor.close()

    except HTTPException:
        raise

    except Exception as e:
        logger.exception("get_devices_by_profile failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error fetching devices")

    return {
        "status": "success",
        "profile_id": profile_id,
        "docker_name": docker_name,
        "count": len(devices),
        "device_ids": devices,
    }

