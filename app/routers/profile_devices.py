# app/routers/profile_devices.py
import uuid
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Path, status

from app.db import get_db_connection

router = APIRouter()
logger = logging.getLogger("app.routers.profile_devices")


# ───────────────────────────────────────────────────────────────
# Get all device IDs for a docker_name and profile_type
# ───────────────────────────────────────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/type/{profile_type}/syslog_devices",
    status_code=status.HTTP_200_OK,
)
def get_devices_by_type(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(..., description="Must match an existing docker_name EXACTLY"),
    profile_type: str = Path(..., description="internal | external"),
):
    """
    STRICT device fetch by docker_name + profile_type.
    NEW: return ONLY device_ids without pagination.
    """

    # STRICT VALIDATION: profile_type
    profile_type = profile_type.lower().strip()
    if profile_type not in ("internal", "external"):
        raise HTTPException(
            status_code=422,
            detail="profile_type must be exactly 'internal' or 'external'",
        )

    # STRICT VALIDATION: docker_name (NO 'all')
    docker_clean = docker_name.strip()

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(
                "SELECT 1 FROM syslog_profiles WHERE docker_name = %s LIMIT 1",
                (docker_clean,),
            )
            exists = cursor.fetchone()
            cursor.close()
    except Exception as e:
        logger.exception("Docker validation failed: %s", e)
        raise HTTPException(status_code=500, detail="Database error during validation")

    if not exists:
        raise HTTPException(
            status_code=404,
            detail=f"Docker name '{docker_clean}' does not exist",
        )

    # ---------------------------------
    # Fetch device_ids (NO pagination)
    # ---------------------------------
    sql = """
        SELECT DISTINCT pd.device_id
        FROM syslog_profile_devices pd
        JOIN syslog_profiles p ON pd.profile_id = p.id
        WHERE p.type = %s
          AND p.docker_name = %s
        ORDER BY pd.device_id ASC
    """

    params = (profile_type, docker_clean)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(sql, params)
            rows = [r[0] for r in cursor.fetchall() or []]
            cursor.close()
    except Exception as e:
        logger.exception("get_devices_by_type failed: %s", e)
        raise HTTPException(status_code=500, detail="DB error fetching devices")

    # FINAL MINIMAL RESPONSE
    return {
        "device_ids": rows
    }


# # ───────────────────────────────────────────────────────────────
# # Get Devices for a Specific Profile
# # ───────────────────────────────────────────────────────────────
# @router.get(
#     "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profile_devices/{profile_id}",
#     status_code=status.HTTP_200_OK,
# )
# def get_devices_by_profile(
#     username: str = Path(...),
#     vdmsid: str = Path(...),
#     docker_name: str = Path(...),
#     profile_id: str = Path(...),
# ):
#     """
#     Retrieve all devices assigned to a specific profile.
#     Ensures profile belongs to provided docker_name.
#     """

#     try:
#         with get_db_connection() as cnx:
#             cursor = cnx.cursor(dictionary=True)

#             # Validate docker_name matches the profile
#             cursor.execute(
#                 "SELECT docker_name FROM syslog_profiles WHERE id=%s",
#                 (profile_id,),
#             )
#             row = cursor.fetchone()

#             if not row:
#                 raise HTTPException(status_code=404, detail="Profile not found")

#             if row["docker_name"] != docker_name:
#                 raise HTTPException(status_code=403, detail="docker_name mismatch")

#             # Fetch devices under this profile
#             cursor.execute(
#                 "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
#                 (profile_id,),
#             )
#             devices = [r["device_id"] for r in cursor.fetchall() or []]

#             cursor.close()

#     except HTTPException:
#         raise

#     except Exception as e:
#         logger.exception("get_devices_by_profile failed: %s", e)
#         raise HTTPException(status_code=400, detail="DB error fetching devices")

#     return {
#         "status": "success",
#         "profile_id": profile_id,
#         "docker_name": docker_name,
#         "count": len(devices),
#         "device_ids": devices,
#     }

    
# # ───────────────────────────────────────────────────────────────
# # NEW: Get device_ids with profile_type mapping
# # ───────────────────────────────────────────────────────────────
# @router.get(
#     "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_device_idtypes",
#     status_code=status.HTTP_200_OK,
# )
# def get_device_ids_with_profile_types(
#     username: str = Path(...),
#     vdmsid: str = Path(...),
#     docker_name: str = Path(...),
#     page: Any = Query(1, description="Page number (int or str)"),
#     limit: Any = Query(100, description="Limit (int or str)"),
# ):
#     """
#     Returns device_ids along with corresponding profile_type.
#     Example:
#         [
#             {"device_id": "...", "profile_type": "internal"},
#             {"device_id": "...", "profile_type": "external"}
#         ]
#     """

#     # Convert pagination safely
#     try:
#         page = int(page)
#         limit = int(limit)
#     except:
#         raise HTTPException(status_code=422, detail="page and limit must be integers")

#     if page < 1 or limit < 1:
#         raise HTTPException(status_code=422, detail="page and limit must be >= 1")

#     offset = (page - 1) * limit

#     sql = """
#         SELECT DISTINCT 
#             pd.device_id,
#             p.type AS profile_type
#         FROM syslog_profile_devices pd
#         JOIN syslog_profiles p ON pd.profile_id = p.id
#         WHERE p.docker_name = %s
#         ORDER BY pd.device_id ASC
#         LIMIT %s OFFSET %s
#     """

#     try:
#         with get_db_connection() as cnx:
#             cursor = cnx.cursor(dictionary=True)
#             cursor.execute(sql, (docker_name, limit, offset))
#             rows = cursor.fetchall() or []
#             cursor.close()

#     except Exception as e:
#         logger.exception("get_device_ids_with_profile_types failed: %s", e)
#         raise HTTPException(status_code=400, detail="DB error fetching device ID types")

#     return {
#         "total": len(rows),
#         "page": page,
#         "limit": limit,
#         "docker_name": docker_name,
#         "items": rows,
#     }
