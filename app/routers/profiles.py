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
    docker_name: str = Path(..., description="Docker name (stored EXACTLY as provided)"),
    p: profile_models.ProfileIn = Body(...),
) -> Dict[str, str]:
    """
    Create new profile.
    docker_name is saved EXACTLY as sent in URL.
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

            # Insert device_ids if provided
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

            cursor.close()

        # Clear all cache (async)
        try:
            asyncio.create_task(clear_cache())
        except:
            pass

        # Refresh cache for this profile
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
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(..., description="Docker name (MUST match DB EXACTLY)"),
    profile_id: str = Path(...),
    p: profile_models.ProfileUpdate = Body(None),
):
    """
    Update profile.
    docker_name is compared EXACTLY (case-sensitive).
    """
    _validate_docker_name(docker_name)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            # IMPORTANT → must match exactly, no lower(), upper(), strip()
            if existing["docker_name"] != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            preserved_type = existing["type"]
            new_name = p.name if p and p.name is not None else existing["name"]

            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = json.dumps(p.priorities) if p and p.priorities is not None else existing.get("priorities")
                fac_json = json.dumps(p.facilities) if p and p.facilities is not None else existing.get("facilities")
                kws_json = json.dumps(p.keywords) if p and p.keywords is not None else existing.get("keywords")

            cursor.execute(
                """
                UPDATE syslog_profiles
                SET name=%s, priorities=%s, facilities=%s, keywords=%s
                WHERE id=%s
                """,
                (new_name, prio_json, fac_json, kws_json, profile_id),
            )
            cnx.commit()

            # Update device list if provided
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

        # Refresh profile cache
        asyncio.create_task(notify_profile_change(profile_id))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": profile_id}


# # ─────────────────────────────
# # DELETE PROFILE
# # ─────────────────────────────
# @router.delete(
#     "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/delete",
#     status_code=status.HTTP_200_OK,
# )
# async def delete_profile(
#     username: str = Path(...),
#     vdmsid: str = Path(...),
#     docker_name: str = Path(...),
#     profile_id: str = Path(...),
# ):
#     """
#     Delete profile. docker_name compared EXACTLY.
#     """
#     _validate_docker_name(docker_name)

#     try:
#         with get_db_connection() as cnx:
#             cursor = cnx.cursor(dictionary=True)

#             cursor.execute("SELECT docker_name FROM syslog_profiles WHERE id=%s", (profile_id,))
#             row = cursor.fetchone()

#             if not row:
#                 raise HTTPException(status_code=404, detail="Profile not found")

#             # Exact match only
#             if row["docker_name"] != docker_name:
#                 raise HTTPException(status_code=403, detail="docker_name mismatch")

#             cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
#             cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (profile_id,))
#             cnx.commit()

#             cursor.close()

#         asyncio.create_task(notify_profile_change(profile_id))

#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.exception("delete_profile failed")
#         raise HTTPException(status_code=400, detail=str(e))

#     return {"status": "deleted", "id": profile_id}


# ─────────────────────────────
# DELETE MULTIPLE / SINGLE PROFILES
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/delete",
    status_code=status.HTTP_200_OK,
)
async def delete_profiles(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    body: Dict[str, List[str]] = Body(..., example={"ids": ["id1", "id2"]}),
):
    """
    Delete one OR multiple profiles.
    Body: { "ids": ["id1", "id2", ...] }
    Ensures:
        - docker_name must match
        - integrations linked to the profile are deleted
        - profile_devices are deleted
        - profile is deleted
        - cache refresh is triggered
    """
    ids = body.get("ids")
    if not ids or not isinstance(ids, list):
        raise HTTPException(status_code=422, detail="Body must contain 'ids' list")

    deleted = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            for pid in ids:
                # validate profile exists
                cursor.execute("SELECT docker_name FROM syslog_profiles WHERE id=%s", (pid,))
                row = cursor.fetchone()
                if not row:
                    continue  # silently skip invalid IDs

                # check docker_name matches EXACTLY
                if row["docker_name"] != docker_name:
                    continue  # skip IDs not belonging to this docker

                # Delete integrations
                cursor.execute("DELETE FROM syslog_integration WHERE profile_id=%s", (pid,))

                # Delete devices
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (pid,))

                # Delete profile
                cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (pid,))
                deleted.append(pid)

            cnx.commit()
            cursor.close()

        # trigger cache updates for each profile
        for pid in deleted:
            asyncio.create_task(notify_profile_change(pid))

    except Exception as e:
        logger.exception("delete_profiles failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "deleted_ids": deleted, "count": len(deleted)}

# ─────────────────────────────
# DELETE ALL PROFILES FOR A DOCKER
# ─────────────────────────────
@router.delete(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/delete/all",
    status_code=status.HTTP_200_OK,
)
async def delete_all_profiles(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
):
    """
    Deletes ALL profiles (and their devices + integrations) belonging to the given docker_name.
    """
    deleted_ids = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # Get all profiles under docker
            cursor.execute("SELECT id FROM syslog_profiles WHERE docker_name=%s", (docker_name,))
            rows = cursor.fetchall() or []

            profile_ids = [r["id"] for r in rows]

            for pid in profile_ids:
                # Delete integrations
                cursor.execute("DELETE FROM syslog_integration WHERE profile_id=%s", (pid,))

                # Delete device links
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (pid,))

                # Delete profile
                cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (pid,))

                deleted_ids.append(pid)

            cnx.commit()
            cursor.close()

        # notify cache for each deleted id
        for pid in deleted_ids:
            asyncio.create_task(notify_profile_change(pid))

    except Exception as e:
        logger.exception("delete_all_profiles failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted_all", "count": len(deleted_ids), "deleted_ids": deleted_ids}



# ─────────────────────────────
# GET ALL PROFILES
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles",
    status_code=status.HTTP_200_OK,
)
def get_all_profiles(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: Any = Query(1),
    limit: Any = Query(50),
):
    """
    List profiles for EXACT docker_name.
    """
    _validate_docker_name(docker_name)

    try:
        page = int(page)
        limit = int(limit)
    except:
        raise HTTPException(status_code=422, detail="page and limit must be integers")

    if page < 1 or limit < 1:
        raise HTTPException(status_code=422, detail="page and limit must be >= 1")

    params: List[Any] = [docker_name]
    where_clauses = ["docker_name = %s"]

    if profile_type:
        where_clauses.append("`type` = %s")
        params.append(profile_type)

    if search:
        where_clauses.append("LOWER(name) LIKE %s")
        params.append(f"%{search.lower()}%")

    where = " WHERE " + " AND ".join(where_clauses)
    offset = (page - 1) * limit

    sql = f"""
        SELECT 
            id, name, `type` AS profile_type, docker_name,
            priorities, facilities, keywords
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
# GET SINGLE PROFILE
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}",
    status_code=status.HTTP_200_OK,
)
def get_profile(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_id: str = Path(...),
):
    """
    Fetch one profile. docker_name compared EXACTLY.
    """
    _validate_docker_name(docker_name)

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, `type` AS profile_type, docker_name,
                   priorities, facilities, keywords
            FROM syslog_profiles
            WHERE id=%s
            """,
            (profile_id,),
        )
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Must match exactly — no normalization
    if row["docker_name"] != docker_name:
        raise HTTPException(status_code=403, detail="docker_name mismatch")

    return row
