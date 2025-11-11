# app/routers/profile_devices.py
import uuid
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.db import get_db_connection
from app.models import profile_devices as pd_models

router = APIRouter()
logger = logging.getLogger("app.routers.profile_devices")


# ─────────────────────────────
# Assign Devices to Profile
# ─────────────────────────────
@router.post("/syslog_profile_devices", status_code=status.HTTP_201_CREATED)
def assign_profile_devices(payload: pd_models.ProfileDevicesAssign) -> Dict[str, Any]:
    """
    Assign one or more device IDs to a syslog profile.
    """
    created = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            for device_id in payload.devices:
                rid = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                    VALUES (%s, %s, %s)
                    """,
                    (rid, payload.profile_id, device_id.strip()),
                )
                created.append({"mapping_id": rid, "device_id": device_id.strip()})
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("assign_profile_devices failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error assigning devices")

    return {
        "status": "assigned",
        "profile_id": payload.profile_id,
        "mappings": created,
        "count": len(created),
    }


# ─────────────────────────────
# Unassign Device from Profile (query-param style)
# ─────────────────────────────
@router.delete("/syslog_profile_devices", status_code=status.HTTP_200_OK)
def unassign_profile_device(mapping_id: str = Query(..., description="Mapping ID to delete")) -> Dict[str, str]:
    """
    Remove a specific device-profile mapping (query-param style).
    """
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_profile_devices WHERE id=%s", (mapping_id,))
            affected = cursor.rowcount
            cnx.commit()
            cursor.close()

        if affected == 0:
            raise HTTPException(status_code=404, detail="Mapping not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unassign_profile_device failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error unassigning mapping")

    return {"status": "unassigned", "mapping_id": mapping_id}


# ─────────────────────────────
# List All Profile–Device Mappings (Paginated)
# ─────────────────────────────
@router.get("/syslog_profile_devices", status_code=status.HTTP_200_OK)
def get_all_profile_devices(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
) -> Dict[str, Any]:
    """
    Get all profile-device mappings (paginated).
    Includes profile name for convenience.
    """
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            offset = (page - 1) * limit
            cursor.execute(
                """
                SELECT pd.id AS mapping_id, pd.profile_id, p.name AS profile_name,
                       pd.device_id
                FROM syslog_profile_devices pd
                LEFT JOIN syslog_profiles p ON pd.profile_id = p.id
                ORDER BY p.name ASC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cursor.fetchall() or []
            cursor.close()
        except Exception as e:
            logger.exception("get_all_profile_devices failed: %s", e)
            raise HTTPException(status_code=400, detail="DB error fetching mappings")

    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


# ─────────────────────────────
# Get Device IDs for a Network (Paginated)
# ─────────────────────────────
@router.get("/syslog_profile_devices/device_ids", status_code=status.HTTP_200_OK)
def get_device_ids_for_network(
    network: str = Query(..., description="Network to filter profiles by"),
    profile_type: Optional[str] = Query(None, description="Filter by profile type (internal|external)"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=5000),
) -> Dict[str, Any]:
    """
    Get all device IDs associated with profiles belonging to the specified network.
    Optionally filter by profile type (internal/external).
    Returns a paginated list of device IDs.
    """
    if not network:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="network query param is required",
        )

    params = [network]
    where_clause = "WHERE p.network = %s"
    if profile_type:
        where_clause += " AND p.type = %s"
        params.append(profile_type)

    offset = (page - 1) * limit
    sql = f"""
        SELECT DISTINCT pd.device_id
        FROM syslog_profile_devices pd
        JOIN syslog_profiles p ON pd.profile_id = p.id
        {where_clause}
        ORDER BY pd.device_id ASC
        LIMIT %s OFFSET %s
    """

    params.extend([limit, offset])

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(sql, tuple(params))
            rows = [r[0] for r in cursor.fetchall() or []]
            cursor.close()
    except Exception as e:
        logger.exception("get_device_ids_for_network failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error fetching device IDs")

    return {
        "total": len(rows),
        "page": page,
        "limit": limit,
        "network": network,
        "profile_type": profile_type,
        "device_ids": rows,
    }


# ─────────────────────────────
# Get Devices for a Specific Profile (query-param style)
# ─────────────────────────────
@router.get("/syslog_profile_devices/by_profile", status_code=status.HTTP_200_OK)
def get_devices_by_profile(
    profile_id: str = Query(..., description="Profile ID"),
    network: Optional[str] = Query(None, description="Optional network filter"),
) -> Dict[str, Any]:
    """
    Retrieve all devices assigned to a specific profile.
    Optionally verify the profile belongs to a given network.
    """
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            if network:
                cursor.execute(
                    """
                    SELECT pd.device_id 
                    FROM syslog_profile_devices pd
                    JOIN syslog_profiles p ON pd.profile_id = p.id
                    WHERE pd.profile_id=%s AND p.network=%s
                    """,
                    (profile_id, network),
                )
            else:
                cursor.execute(
                    "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
                    (profile_id,),
                )

            rows = [r["device_id"] for r in cursor.fetchall() or []]
            cursor.close()
    except Exception as e:
        logger.exception("get_devices_by_profile failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error fetching devices for profile")

    return {
        "status": "success",
        "profile_id": profile_id,
        "network": network,
        "count": len(rows),
        "device_ids": rows,
    }
