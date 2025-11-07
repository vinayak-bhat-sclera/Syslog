# app/routers/profile_devices.py
import uuid
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends, status

from app.db import get_db_connection
from app.models import profile_devices as pd_models
from app.services.device_cache import clear_profile_devices

router = APIRouter()
logger = logging.getLogger("app.routers.profile_devices")


@router.post("/syslog_profile_devices", status_code=status.HTTP_201_CREATED)
def assign_profile_devices(payload: pd_models.ProfileDevicesAssign):
    """
    Assign device UUIDs to a profile. Returns created mappings.
    """
    created = []
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            for device_id in payload.device_uuids:
                rid = str(uuid.uuid4())
                cursor.execute(
                    "INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id) VALUES (%s,%s,%s)",
                    (rid, payload.profile_id, device_id.strip()),
                )
                created.append({"mapping_id": rid, "device_id": device_id.strip()})
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("assign_profile_devices failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error assigning devices")

    # clear cache entries related to profile
    import asyncio

    asyncio.create_task(clear_profile_devices(payload.profile_id))

    return {"status": "assigned", "profile_id": payload.profile_id, "mappings": created}


@router.delete("/syslog_profile_devices/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_profile_device(mapping_id: str):
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            # fetch profile_id for cache eviction
            cursor.execute("SELECT profile_id FROM syslog_profile_devices WHERE id=%s", (mapping_id,))
            row = cursor.fetchone()
            profile_id = row[0] if row else None

            cursor.execute("DELETE FROM syslog_profile_devices WHERE id=%s", (mapping_id,))
            cnx.commit()
            cursor.close()

        if profile_id:
            import asyncio

            asyncio.create_task(clear_profile_devices(profile_id))

    except Exception as e:
        logger.exception("unassign_profile_device failed: %s", e)
        raise HTTPException(status_code=400, detail="DB error unassigning mapping")

    return None


@router.get("/syslog_profile_devices")
def get_all_profile_devices(
    network: Optional[str] = Query(None, description="Filter device mappings by profile network"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """
    Paginated list of profile-device mappings. If `network` provided, return mappings only for profiles in that network.
    """
    params = []
    where = ""
    if network:
        where = " WHERE p.network = %s"
        params.append(network)

    offset = (page - 1) * limit
    sql_items = """
        SELECT pd.id AS mapping_id, pd.profile_id, p.name AS profile_name, pd.device_id
        FROM syslog_profile_devices pd
        LEFT JOIN syslog_profiles p ON pd.profile_id = p.id
        """ + where + " ORDER BY p.name ASC LIMIT %s OFFSET %s"
    sql_count = "SELECT COUNT(1) as cnt FROM syslog_profile_devices pd LEFT JOIN syslog_profiles p ON pd.profile_id = p.id " + where

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql_count, tuple(params))
        total = cursor.fetchone()["cnt"]
        cursor.execute(sql_items, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {"total": total, "page": page, "limit": limit, "items": rows}


@router.get("/syslog_profile_device_ids")
def get_device_ids_for_network(network: str = Query(..., description="Network(s) - comma separated or single"),):
    """
    Return array of device_ids present in profile_devices for the given network.
    network can be comma-separated list of networks.
    """
    nets = [n.strip() for n in network.split(",") if n.strip()]
    placeholder = ",".join(["%s"] * len(nets))

    with get_db_connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute(
            f"""
            SELECT DISTINCT pd.device_id
            FROM syslog_profile_devices pd
            JOIN syslog_profiles p ON pd.profile_id = p.id
            WHERE p.network IN ({placeholder})
            """,
            tuple(nets),
        )
        rows = [r[0] for r in cursor.fetchall() or []]
        cursor.close()

    return {"total": len(rows), "device_ids": rows}
