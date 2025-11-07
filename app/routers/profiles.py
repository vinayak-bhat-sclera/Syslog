# app/routers/profiles.py
import json
import uuid
import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Depends, status

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import clear_cache, clear_profile_devices
from app.config import settings

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


# Simple header-based auth dependency (scope placeholder)
def require_cloud_scope(x_cloud_auth: Optional[str] = None):
    """
    Simple check: if CLOUD_AUTH_TOKEN is set in settings, incoming header 'X-Cloud-Auth' must match.
    Returns None on success, raises HTTPException on failure.
    """
    token = getattr(settings, "CLOUD_AUTH_TOKEN", None)
    if token:
        if x_cloud_auth != token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing/invalid cloud auth")
    return None


@router.post("/syslog_profiles", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_cloud_scope)])
def create_profile(
    p: profile_models.ProfileIn,
    network: str = Query(..., description="Network query param"),
) -> Dict[str, str]:
    """
    Create profile. Network must be provided as query param (overrides p.network).
    """
    pid = str(uuid.uuid4())
    # override
    p.network = network

    prio_json = fac_json = kws_json = None
    if p.type != "external":
        prio_json = json.dumps(p.priorities) if p.priorities else None
        fac_json = json.dumps(p.facilities) if p.facilities else None
        kws_json = json.dumps(p.keywords) if p.keywords else None

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(
                """
                INSERT INTO syslog_profiles (id, name, type, network, priorities, facilities, keywords)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (pid, p.name, p.type, p.network, prio_json, fac_json, kws_json),
            )
            cnx.commit()

            # assign devices
            if p.device_uuids:
                for du in p.device_uuids:
                    rid = str(uuid.uuid4())
                    cursor.execute(
                        "INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id) VALUES (%s,%s,%s)",
                        (rid, pid, du.strip()),
                    )
                cnx.commit()

            # Clear cache so next UDP resolution hits springboot/cache refresh
            import asyncio

            asyncio.create_task(clear_cache())

    except Exception as e:
        logger.exception("create_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "id": pid}


@router.put("/syslog_profiles/{profile_id}", dependencies=[Depends(require_cloud_scope)])
def update_profile(
    profile_id: str,
    p: profile_models.ProfileUpdate,
    network: str = Query(..., description="Network query param"),
) -> Dict[str, str]:
    """
    Update profile (type/network are immutable). device_uuids optional (if not provided untouched).
    """
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s AND network=%s", (profile_id, network))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            preserved_type = existing["type"]
            preserved_network = existing["network"]
            new_name = p.name if p.name is not None else existing["name"]

            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = json.dumps(p.priorities) if p.priorities is not None else existing.get("priorities")
                fac_json = json.dumps(p.facilities) if p.facilities is not None else existing.get("facilities")
                kws_json = json.dumps(p.keywords) if p.keywords is not None else existing.get("keywords")

            cursor.execute(
                """
                UPDATE syslog_profiles
                SET name=%s, priorities=%s, facilities=%s, keywords=%s
                WHERE id=%s
                """,
                (new_name, prio_json, fac_json, kws_json, profile_id),
            )
            cnx.commit()

            if p.device_uuids is not None:
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                for du in p.device_uuids:
                    rid = str(uuid.uuid4())
                    cursor.execute(
                        "INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id) VALUES (%s,%s,%s)",
                        (rid, profile_id, du.strip()),
                    )
                cnx.commit()

            # Evict cache entries related to this profile
            import asyncio

            asyncio.create_task(clear_profile_devices(profile_id))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": profile_id}


@router.delete("/syslog_profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_cloud_scope)])
def delete_profile(profile_id: str, network: str = Query(..., description="Network query param")):
    """Delete profile and its mappings. Uses network param to prevent accidental deletes across networks."""
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            # double-check network
            cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id IN (SELECT id FROM syslog_profiles WHERE id=%s AND network=%s)", (profile_id, network))
            cursor.execute("DELETE FROM syslog_profiles WHERE id=%s AND network=%s", (profile_id, network))
            cnx.commit()

            # Evict cache
            import asyncio

            asyncio.create_task(clear_profile_devices(profile_id))
    except Exception as e:
        logger.exception("delete_profile failed")
        raise HTTPException(status_code=400, detail=str(e))
    return None


@router.get("/syslog_profiles")
def get_all_profiles(
    network: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search by profile name (case-insensitive)"),
    ptype: Optional[str] = Query(None, description="Filter by type: internal|external"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """
    Paginated list of profiles. Sort name ASC. Optional network filter, search q (name), and type filter.
    """
    params = []
    where = " WHERE 1=1 "
    if network:
        where += " AND network = %s"
        params.append(network)
    if q:
        where += " AND LOWER(name) LIKE %s"
        params.append(f"%{q.lower()}%")
    if ptype:
        where += " AND type = %s"
        params.append(ptype)

    offset = (page - 1) * limit

    sql_items = "SELECT id, name, type, network, priorities, facilities, keywords FROM syslog_profiles " + where + " ORDER BY name ASC LIMIT %s OFFSET %s"
    sql_count = "SELECT COUNT(1) as cnt FROM syslog_profiles " + where

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql_count, tuple(params))
        total = cursor.fetchone()["cnt"]
        cursor.execute(sql_items, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {"total": total, "page": page, "limit": limit, "items": rows}


@router.get("/syslog_profiles/{profile_id}")
def get_profile(profile_id: str, network: str = Query(...)):
    """Fetch a single profile by id and network."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT id, name, type, network, priorities, facilities, keywords FROM syslog_profiles WHERE id=%s AND network=%s", (profile_id, network))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    return row
