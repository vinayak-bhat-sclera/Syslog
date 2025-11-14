# app/routers/profiles.py

import json
import uuid
import logging
import asyncio
from typing import Dict, Optional, Any, List

from fastapi import APIRouter, HTTPException, Path, Query, status, Body

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import (
    clear_cache,
    clear_profile_devices,
    notify_profile_change,
)

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


# ─────────────────────────────
# Helper
# ─────────────────────────────
def _validate_docker_name(docker_name: Optional[str]):
    if not docker_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="docker_name path param is required",
        )


# ─────────────────────────────
# CREATE PROFILE
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/create",
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    username: str = Path(..., description="User name (not used by DB, for path consistency)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB, for path consistency)"),
    docker_name: str = Path(..., description="Docker name (used as docker_name in DB)"),
    p: profile_models.ProfileIn = Body(...),
) -> Dict[str, str]:
    """
    Create a new syslog profile. docker_name taken from path and stored in DB as docker_name.
    Triggers a profile-specific cache refresh asynchronously.
    """
    _validate_docker_name(docker_name)
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
            cursor.execute(
                """
                INSERT INTO syslog_profiles (id, name, `type`, docker_name, priorities, facilities, keywords)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (pid, p.name, p.type, docker_name, prio_json, fac_json, kws_json),
            )
            cnx.commit()

            # Assign device mappings if provided
            if p.device_ids:
                for device_id in p.device_ids:
                    rid = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                        VALUES (%s,%s,%s)
                        """,
                        (rid, pid, device_id.strip()),
                    )
                cnx.commit()

            # best-effort clear full cache (async task)
            try:
                asyncio.create_task(clear_cache())
            except Exception:
                logger.debug("clear_cache unavailable or failed; continuing")

            cursor.close()

        # trigger profile-specific refresh in background
        try:
            asyncio.create_task(notify_profile_change(pid))
        except Exception:
            logger.exception("Failed to schedule notify_profile_change for %s", pid)

    except Exception as e:
        logger.exception("create_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "id": pid}


# ─────────────────────────────
# UPDATE PROFILE
# ─────────────────────────────
@router.put(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/update",
    response_model=Dict[str, str],
)
async def update_profile(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name (must match existing profile.docker_name)"),
    profile_id: str = Path(..., description="Profile ID to update"),
    p: profile_models.ProfileUpdate = Body(None),
):
    """
    Update allowed fields for a profile. docker_name must match path value.
    Triggers profile-specific cache eviction + refresh.
    """
    _validate_docker_name(docker_name)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            # ensure docker_name matches stored value
            if existing.get("docker_name") != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            preserved_type = existing["type"]
            new_name = p.name if p and p.name is not None else existing["name"]

            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = (
                    json.dumps(p.priorities, ensure_ascii=False)
                    if p and p.priorities is not None
                    else existing.get("priorities")
                )
                fac_json = (
                    json.dumps(p.facilities, ensure_ascii=False)
                    if p and p.facilities is not None
                    else existing.get("facilities")
                )
                kws_json = (
                    json.dumps(p.keywords, ensure_ascii=False)
                    if p and p.keywords is not None
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

            # Replace device mappings only if provided in payload
            if p and p.device_ids is not None:
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                for device_id in p.device_ids:
                    rid = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                        VALUES (%s,%s,%s)
                        """,
                        (rid, profile_id, device_id.strip()),
                    )
                cnx.commit()

            cursor.close()

        # schedule cache eviction + profile refresh
        try:
            asyncio.create_task(notify_profile_change(profile_id))
        except Exception:
            logger.exception("Failed to schedule notify_profile_change for %s", profile_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": profile_id}


# ─────────────────────────────
# DELETE PROFILE
# ─────────────────────────────
@router.delete(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/delete",
    status_code=status.HTTP_200_OK,
)
async def delete_profile(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name"),
    profile_id: str = Path(..., description="Profile ID to delete"),
):
    """
    Delete profile and associated device mappings.
    Triggers cache eviction + refresh for that profile (async).
    """
    _validate_docker_name(docker_name)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT docker_name FROM syslog_profiles WHERE id=%s", (profile_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Profile not found")
            if row["docker_name"] != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
            cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (profile_id,))
            cnx.commit()

            cursor.close()

        # schedule cache eviction + profile refresh (will clear profile entries)
        try:
            asyncio.create_task(notify_profile_change(profile_id))
        except Exception:
            logger.exception("Failed to schedule notify_profile_change for %s", profile_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "id": profile_id}


# ─────────────────────────────
# GET ALL PROFILES
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles",
    status_code=status.HTTP_200_OK,
)
def get_all_profiles(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name to filter by"),
    profile_type: Optional[str] = Query(None, description="Filter by profile type (internal|external)"),
    search: Optional[str] = Query(None, description="Case-insensitive substring match on profile name"),
    page: Any = Query(1, description="Page number (int)"),
    limit: Any = Query(50, description="Page size (int)"),
):
    """
    Return paginated profiles for the provided docker_name path value.
    Optional filters: profile_type and search (profile name).
    This endpoint accepts string numbers and converts them to ints.
    """
    _validate_docker_name(docker_name)

    # ensure page/limit are integers (some clients POST them as strings)
    try:
        page = int(page)
        limit = int(limit)
    except Exception:
        raise HTTPException(status_code=422, detail="page and limit must be integers")

    if page < 1 or limit < 1:
        raise HTTPException(status_code=422, detail="page and limit must be >= 1")

    params: List[Any] = []
    where_clauses: List[str] = []

    # docker_name path MUST filter results
    where_clauses.append("docker_name = %s")
    params.append(docker_name)

    if profile_type:
        where_clauses.append("`type` = %s")
        params.append(profile_type)
    if search:
        where_clauses.append("LOWER(name) LIKE %s")
        params.append(f"%{search.lower()}%")

    where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    offset = (page - 1) * limit

    sql = f"""
        SELECT 
            id, 
            name, 
            `type` AS profile_type, 
            docker_name, 
            priorities, 
            facilities, 
            keywords
        FROM syslog_profiles
        {where}
        ORDER BY name ASC
        LIMIT %s OFFSET %s
    """

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


# ─────────────────────────────
# GET SINGLE PROFILE (updated route)
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}",
    status_code=status.HTTP_200_OK,
)
def get_profile(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name"),
    profile_id: str = Path(..., description="Profile ID"),
):
    """
    Fetch a single profile by ID and docker_name (path).
    Note: route uses .../syslog_profiles/{profile_id} (no trailing /get).
    """
    _validate_docker_name(docker_name)

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, `type` AS profile_type, docker_name, priorities, facilities, keywords
            FROM syslog_profiles
            WHERE id=%s
            """,
            (profile_id,),
        )
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    if row["docker_name"] != docker_name:
        raise HTTPException(status_code=403, detail="docker_name mismatch")

    return row
