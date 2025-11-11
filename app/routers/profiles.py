# app/routers/profiles.py
import json
import uuid
import logging
from typing import Dict, Optional, Any, List

from fastapi import APIRouter, HTTPException, Query, status

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import clear_cache, clear_profile_devices  # expected functions in your cache service

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


def _validate_network(network: Optional[str]):
    if not network:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="network query param is required")


@router.post("/syslog_profiles", status_code=status.HTTP_201_CREATED)
def create_profile(
    p: profile_models.ProfileIn,
    network: str = Query(..., description="Network for the profile")
) -> Dict[str, str]:
    """
    Create a new syslog profile.
    network: must be passed as query param.
    """
    _validate_network(network)
    pid = str(uuid.uuid4())

    if p.type == "external":
        prio_json = fac_json = kws_json = None
    else:
        prio_json = json.dumps(p.priorities) if p.priorities else None
        fac_json = json.dumps(p.facilities) if p.facilities else None
        kws_json = json.dumps(p.keywords) if p.keywords else None

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO syslog_profiles (id, name, type, network, priorities, facilities, keywords)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (pid, p.name, p.type, network, prio_json, fac_json, kws_json),
                )
                cnx.commit()

                # Assign device mappings if any
                if p.device_uuids:
                    for du in p.device_uuids:
                        rid = str(uuid.uuid4())
                        cursor.execute(
                            "INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id) VALUES (%s,%s,%s)",
                            (rid, pid, du.strip()),
                        )
                    cnx.commit()

                # invalidate in-memory cache
                try:
                    clear_cache()
                except Exception:
                    logger.debug("clear_cache not available or failed; continuing")

            finally:
                cursor.close()
    except Exception as e:
        logger.exception("create_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "id": pid}


@router.put("/syslog_profiles/{profile_id}", response_model=Dict[str, str])
def update_profile(
    profile_id: str,
    p: profile_models.ProfileUpdate,
    network: str = Query(..., description="Network of the profile (immutable but required to scope update)")
):
    """
    Update allowed fields for a profile.
    network must match existing profile.network (immutable).
    If device_uuids provided, replaces existing mappings.
    """
    _validate_network(network)
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
                existing = cursor.fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Profile not found")
                if existing["network"] != network:
                    raise HTTPException(status_code=403, detail="Network mismatch")

                preserved_type = existing["type"]

                new_name = p.name if p.name is not None else existing["name"]

                if preserved_type == "external":
                    prio_json = fac_json = kws_json = None
                else:
                    prio_json = (
                        json.dumps(p.priorities, ensure_ascii=False)
                        if p.priorities is not None
                        else existing.get("priorities")
                    )
                    fac_json = (
                        json.dumps(p.facilities, ensure_ascii=False)
                        if p.facilities is not None
                        else existing.get("facilities")
                    )
                    kws_json = (
                        json.dumps(p.keywords, ensure_ascii=False)
                        if p.keywords is not None
                        else existing.get("keywords")
                    )

                cursor.execute(
                    """
                    UPDATE syslog_profiles
                    SET name=%s, priorities=%s, facilities=%s, keywords=%s
                    WHERE id=%s
                    """,
                    (new_name, prio_json, fac_json, kws_json, profile_id),
                )
                cnx.commit()

                # Update device mappings only if explicitly provided
                if p.device_uuids is not None:
                    cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                    for du in p.device_uuids:
                        rid = str(uuid.uuid4())
                        cursor.execute(
                            "INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id) VALUES (%s,%s,%s)",
                            (rid, profile_id, du.strip()),
                        )
                    cnx.commit()

                # clear cache for profile
                try:
                    clear_profile_devices(profile_id)
                except Exception:
                    clear_cache()

            finally:
                cursor.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": profile_id}


@router.delete("/syslog_profiles/{profile_id}", status_code=status.HTTP_200_OK)
def delete_profile(profile_id: str, network: str = Query(...)):
    """Delete a profile and its associated device mappings. Network must match."""
    _validate_network(network)
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            try:
                cursor.execute("SELECT network FROM syslog_profiles WHERE id=%s", (profile_id,))
                row = cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Profile not found")
                if row["network"] != network:
                    raise HTTPException(status_code=403, detail="Network mismatch")

                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (profile_id,))
                cnx.commit()

                try:
                    clear_profile_devices(profile_id)
                except Exception:
                    clear_cache()
            finally:
                cursor.close()
    except HTTPException:
        raise
    except Exception:
        logger.exception("delete_profile failed")
        raise HTTPException(status_code=400, detail="DB error deleting profile")

    return {"status": "deleted", "id": profile_id}


@router.get("/syslog_profiles", status_code=status.HTTP_200_OK)
def get_all_profiles(
    network: Optional[str] = Query(None, description="Filter by network"),
    profile_type: Optional[str] = Query(None, description="Filter by profile type (internal|external)"),
    search: Optional[str] = Query(None, description="Case-insensitive substring match on profile name"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """
    Return paginated profiles. Sort: name ASC.
    """
    params: List[Any] = []
    where_clauses = []
    if network:
        where_clauses.append("network = %s")
        params.append(network)
    if profile_type:
        where_clauses.append("type = %s")
        params.append(profile_type)
    if search:
        where_clauses.append("LOWER(name) LIKE %s")
        params.append(f"%{search.lower()}%")

    where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    offset = (page - 1) * limit
    sql = f"SELECT * FROM syslog_profiles {where} ORDER BY name ASC LIMIT %s OFFSET %s"

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute(sql, tuple(params + [limit, offset]))
            rows = cursor.fetchall() or []
        finally:
            cursor.close()

    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


@router.get("/syslog_profiles/{profile_id}", status_code=status.HTTP_200_OK)
def get_profile(profile_id: str, network: str = Query(...)):
    """Fetch a single profile by ID and network."""
    _validate_network(network)
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    if row["network"] != network:
        raise HTTPException(status_code=403, detail="Network mismatch")
    return row
