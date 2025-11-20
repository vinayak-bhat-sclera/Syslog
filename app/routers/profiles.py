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
from pydantic import BaseModel

class ProfileDeleteRequest(BaseModel):
    profile_ids: List[str]


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
    status_code=200,
)
async def delete_multiple_profiles(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    body: ProfileDeleteRequest = Body(...),
):
    """
    Delete multiple profiles by IDs (POST request with body).
    Also deletes devices + integrations tied to those profiles.
    """

    if not body.profile_ids:
        raise HTTPException(status_code=422, detail="profile_ids list cannot be empty")

    deleted = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            for pid in body.profile_ids:

                # Verify profile exists
                cursor.execute(
                    "SELECT docker_name FROM syslog_profiles WHERE id=%s",
                    (pid,)
                )
                row = cursor.fetchone()

                if not row:
                    continue  # skip missing profiles

                if row["docker_name"] != docker_name:
                    continue  # skip mismatched docker_name

                # Delete profile devices
                cursor.execute(
                    "DELETE FROM syslog_profile_devices WHERE profile_id=%s",
                    (pid,)
                )

                # Delete integrations
                cursor.execute(
                    "DELETE FROM syslog_integration WHERE profile_id=%s",
                    (pid,)
                )

                # Delete profile itself
                cursor.execute(
                    "DELETE FROM syslog_profiles WHERE id=%s",
                    (pid,)
                )

                deleted.append(pid)

            cnx.commit()
            cursor.close()

    except Exception as e:
        logger.exception("Bulk delete profiles failed")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "status": "success",
        "deleted_ids": deleted,
        "skipped_ids": list(set(body.profile_ids) - set(deleted)),
    }

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
    Deletes ALL profiles (and their devices + integrations).

    Modes:
    1️⃣ docker_name == "all" → delete EVERYTHING across all dockers.
    2️⃣ specific docker_name → delete only profiles belonging to that docker.
    """
    deleted_ids = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # ─────────────────────────────────────────
            # MODE 1: delete EVERYTHING
            # ─────────────────────────────────────────
            if docker_name.lower().strip() == "all":
                # Get all profile_ids
                cursor.execute("SELECT id FROM syslog_profiles")
                rows = cursor.fetchall() or []
                profile_ids = [r["id"] for r in rows]

                if profile_ids:
                    # Delete integrations
                    cursor.execute("DELETE FROM syslog_integration")
                    # Delete devices
                    cursor.execute("DELETE FROM syslog_profile_devices")
                    # Delete profiles
                    cursor.execute("DELETE FROM syslog_profiles")

                cnx.commit()
                cursor.close()

                # send notifications
                for pid in profile_ids:
                    asyncio.create_task(notify_profile_change(pid))

                return {
                    "status": "deleted_all",
                    "count": len(profile_ids),
                    "deleted_ids": profile_ids,
                    "docker_name": "all"
                }

            # ─────────────────────────────────────────
            # MODE 2: delete ONLY specific docker
            # ─────────────────────────────────────────
            cursor.execute(
                "SELECT id FROM syslog_profiles WHERE docker_name=%s",
                (docker_name,)
            )
            rows = cursor.fetchall() or []
            profile_ids = [r["id"] for r in rows]

            for pid in profile_ids:
                cursor.execute("DELETE FROM syslog_integration WHERE profile_id=%s", (pid,))
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (pid,))
                cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (pid,))
                deleted_ids.append(pid)

            cnx.commit()
            cursor.close()

        for pid in deleted_ids:
            asyncio.create_task(notify_profile_change(pid))

    except Exception as e:
        logger.exception("delete_all_profiles failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "deleted_all",
        "count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "docker_name": docker_name
    }


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
    profile_type: Optional[str] = Query(None, description="Single or comma-separated values"),
    search: Optional[str] = Query(None),
    page: Any = Query(1),
    limit: Any = Query(50),
):
    """
    List profiles.
    - docker_name = specific name → filter normally
    - docker_name = 'all' → return all profiles (ignore docker filter)
    - profile_type supports multi-select: internal,external
    """

    # Validate docker only if not 'all'
    if docker_name.lower() != "all":
        _validate_docker_name(docker_name)

    # Ensure int pagination
    try:
        page = int(page)
        limit = int(limit)
    except:
        raise HTTPException(status_code=422, detail="page and limit must be integers")

    if page < 1 or limit < 1:
        raise HTTPException(status_code=422, detail="page and limit must be >= 1")

    where_clauses = []
    params: List[Any] = []

    # ------------------------------------------
    # 1️ Docker Filter (SKIPPED when 'all')
    # ------------------------------------------
    if docker_name.lower() != "all":
        where_clauses.append("docker_name = %s")
        params.append(docker_name)

    # ------------------------------------------
    # 2️ Multi-select Profile Type Filter
    #    Example: profile_type=internal,external
    # ------------------------------------------
    if profile_type:
        type_list = [
            t.strip().lower()
            for t in profile_type.split(",")
            if t.strip()
        ]

        type_filters = " OR ".join(["LOWER(`type`) = %s" for _ in type_list])
        where_clauses.append(f"({type_filters})")
        params.extend(type_list)

    # ------------------------------------------
    # 3️ Search Filter (partial match)
    # ------------------------------------------
    if search:
        where_clauses.append("LOWER(name) LIKE %s")
        params.append(f"%{search.lower().strip()}%")

    # Build final WHERE
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

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
        {where_sql}
        ORDER BY name ASC
        LIMIT %s OFFSET %s
    """

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {
        "total": len(rows),
        "page": page,
        "limit": limit,
        "items": rows,
    }


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
