# app/routers/profile_devices.py
import uuid
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.db import get_db_connection
from app.models import profile_devices as pd_models

router = APIRouter()
logger = logging.getLogger("app.routers.profile_devices")


@router.post("/syslog_profile_devices", status_code=status.HTTP_201_CREATED)
def assign_profile_devices(payload: pd_models.ProfileDevicesAssign):
    """
    Assign one or more device IDs to a syslog profile.
    payload.devices: list of device UUIDs
    """
    created = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            for device_id in payload.devices:
                rid = str(uuid.uuid4())
                cursor.execute("""
                    INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                    VALUES (%s, %s, %s)
                """, (rid, payload.profile_id, device_id.strip()))
                created.append({"mapping_id": rid, "device_id": device_id.strip()})
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("assign_profile_devices failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error assigning devices")
    return {"status": "assigned", "profile_id": payload.profile_id, "mappings": created}


@router.delete("/syslog_profile_devices/{mapping_id}", status_code=status.HTTP_200_OK)
def unassign_profile_device(mapping_id: str):
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_profile_devices WHERE id=%s", (mapping_id,))
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("unassign_profile_device failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error unassigning mapping")
    return {"status": "unassigned", "mapping_id": mapping_id}


@router.get("/syslog_profile_devices", status_code=status.HTTP_200_OK)
def get_all_profile_devices(page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=1000)):
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            offset = (page - 1) * limit
            cursor.execute("""
                SELECT pd.id AS mapping_id, pd.profile_id, p.name AS profile_name,
                       pd.device_id
                FROM syslog_profile_devices pd
                LEFT JOIN syslog_profiles p ON pd.profile_id = p.id
                ORDER BY p.name ASC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = cursor.fetchall() or []
        finally:
            cursor.close()
    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


@router.get("/syslog_profile_devices/device_ids", status_code=status.HTTP_200_OK)
def get_device_ids_for_network(
    network: str = Query(..., description="Network to filter profiles by"),
    profile_type: Optional[str] = Query(None, description="Filter by profile type (internal|external)"),
):
    """
    Return all device_ids present in syslog_profile_devices for profiles that belong to the provided network.
    Optional: filter by profile type (internal/external).
    Returns list of strings (device_ids).
    """
    params = [network]
    q = """
        SELECT DISTINCT pd.device_id
        FROM syslog_profile_devices pd
        JOIN syslog_profiles p ON pd.profile_id = p.id
        WHERE p.network = %s
    """
    if profile_type:
        q += " AND p.type = %s"
        params.append(profile_type)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(q, tuple(params))
            rows = [r[0] for r in cursor.fetchall() or []]
            cursor.close()
    except Exception as e:
        logger.exception("get_device_ids_for_network failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error fetching device ids")
    return {"total": len(rows), "device_ids": rows}
