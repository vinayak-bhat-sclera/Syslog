# app/routers/profile_devices.py
import uuid
import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException

from app.db import get_db_connection
from app.models import profile_devices as pd_models

router = APIRouter()
logger = logging.getLogger("app.routers.profile_devices")


@router.post("/syslog_profile_devices")
def assign_profile_devices(payload: pd_models.ProfileDevicesAssign) -> Dict[str, Any]:
    """
    Assign one or more device IDs to a syslog profile.
    """
    created = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            try:
                for device_id in payload.devices:
                    rid = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                        VALUES (%s, %s, %s)
                    """, (rid, payload.profile_id, device_id.strip()))
                    created.append({"mapping_id": rid, "device_id": device_id.strip()})
                cnx.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.exception("assign_profile_devices failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error assigning devices")

    return {"status": "assigned", "profile_id": payload.profile_id, "mappings": created}


@router.delete("/syslog_profile_devices/{mapping_id}")
def unassign_profile_device(mapping_id: str):
    """
    Unassign (delete) a specific profile-device mapping by mapping_id.
    """
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_profile_devices WHERE id=%s", (mapping_id,))
            cnx.commit()
    except Exception as e:
        logger.exception("unassign_profile_device failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error unassigning mapping")

    return {"status": "unassigned", "mapping_id": mapping_id}


@router.get("/syslog_profile_devices")
def get_all_profile_devices():
    """
    Return all profile-device mappings with profile names.
    """
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("""
            SELECT pd.id AS mapping_id, pd.profile_id, p.name AS profile_name,
                   pd.device_id
            FROM syslog_profile_devices pd
            LEFT JOIN syslog_profiles p ON pd.profile_id = p.id
            ORDER BY p.name ASC
        """)
        rows = cursor.fetchall() or []
    return {"total": len(rows), "items": rows}
