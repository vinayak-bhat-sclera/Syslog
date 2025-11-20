# app/routers/profiles.py
import json
import uuid
import logging
import asyncio
from typing import Dict, Optional, Any, List
import httpx
from app.config import settings

from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    Query,
    status,
    Body,
)

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
    docker_name: str = Path(..., description="Docker name"),
    profile_id: str = Path(...),
    p: profile_models.ProfileUpdate = Body(None),
):
    """
    Update profile.

    Behavior:
    - docker_name == "all" → update regardless of stored docker_name
    - otherwise → enforce exact docker match
    """
    _validate_docker_name(docker_name)
    update_all = (docker_name.lower().strip() == "all")

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # Fetch existing profile
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()

            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            # Enforce docker matching
            if not update_all and existing["docker_name"] != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            preserved_type = existing["type"]
            new_name = p.name if p and p.name is not None else existing["name"]

            # internal/external logic
            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = (
                    json.dumps(p.priorities)
                    if p and p.priorities is not None
                    else existing.get("priorities")
                )
                fac_json = (
                    json.dumps(p.facilities)
                    if p and p.facilities is not None
                    else existing.get("facilities")
                )
                kws_json = (
                    json.dumps(p.keywords)
                    if p and p.keywords is not None
                    else existing.get("keywords")
                )

            # Update profile
            cursor.execute(
                """
                UPDATE syslog_profiles
                SET name=%s, priorities=%s, facilities=%s, keywords=%s
                WHERE id=%s
                """,
                (new_name, prio_json, fac_json, kws_json, profile_id),
            )
            cnx.commit()

            # Device list update
            if p and p.device_ids is not None:
                cursor.execute(
                    "DELETE FROM syslog_profile_devices WHERE profile_id=%s",
                    (profile_id,),
                )
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

        # Cache refresh
        asyncio.create_task(notify_profile_change(profile_id))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "updated",
        "id": profile_id,
        "mode": "all-dockers" if update_all else "single-docker",
    }


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
    Delete multiple profiles by IDs.

    docker_name = "all" → delete ALL provided profile_ids (no docker filter)
    otherwise → delete only profiles from that docker_name
    """
    if not body.profile_ids:
        raise HTTPException(status_code=422, detail="profile_ids list cannot be empty")

    deleted = []
    skipped = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            delete_all = (docker_name.lower().strip() == "all")

            for pid in body.profile_ids:
                cursor.execute("SELECT docker_name FROM syslog_profiles WHERE id=%s", (pid,))
                row = cursor.fetchone()

                if not row:
                    skipped.append(pid)
                    continue

                if not delete_all and row["docker_name"] != docker_name:
                    skipped.append(pid)
                    continue

                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (pid,))
                cursor.execute("DELETE FROM syslog_integration WHERE profile_id=%s", (pid,))
                cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (pid,))
                deleted.append(pid)

            cnx.commit()
            cursor.close()

        for pid in deleted:
            asyncio.create_task(notify_profile_change(pid))

    except Exception as e:
        logger.exception("Bulk delete profiles failed")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "status": "success",
        "deleted_ids": deleted,
        "skipped_ids": skipped,
        "mode": "all-dockers" if delete_all else "single-docker",
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
    docker_name == "all" → delete EVERYTHING across all dockers.
    specific docker_name → delete only profiles belonging to that docker.
    """
    deleted_ids = []

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            # MODE 1: delete EVERYTHING
            if docker_name.lower().strip() == "all":
                cursor.execute("SELECT id FROM syslog_profiles")
                rows = cursor.fetchall() or []
                profile_ids = [r["id"] for r in rows]

                if profile_ids:
                    cursor.execute("DELETE FROM syslog_integration")
                    cursor.execute("DELETE FROM syslog_profile_devices")
                    cursor.execute("DELETE FROM syslog_profiles")

                cnx.commit()
                cursor.close()

                for pid in profile_ids:
                    asyncio.create_task(notify_profile_change(pid))

                return {
                    "status": "deleted_all",
                    "count": len(profile_ids),
                    "deleted_ids": profile_ids,
                    "docker_name": "all",
                }

            # MODE 2: delete only specific docker
            cursor.execute(
                "SELECT id FROM syslog_profiles WHERE docker_name=%s",
                (docker_name,),
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
        "docker_name": docker_name,
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

    # 1. Docker Filter
    if docker_name.lower() != "all":
        where_clauses.append("docker_name = %s")
        params.append(docker_name)

    # 2. Multi-select Type Filter
    if profile_type:
        type_list = [
            t.strip().lower()
            for t in profile_type.split(",")
            if t.strip()
        ]
        type_filters = " OR ".join(["LOWER(`type`) = %s" for _ in type_list])
        where_clauses.append(f"({type_filters})")
        params.extend(type_list)

    # 3. Search Filter
    if search:
        where_clauses.append("LOWER(name) LIKE %s")
        params.append(f"%{search.lower().strip()}%")

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
async def get_profile(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_id: str = Path(...),
):
    """
    Fetch single profile.
    If docker_name == "all" → ignore docker validation.
    Also return device_details fetched from Spring Boot using device_ids.
    """
    _validate_docker_name(docker_name)

    # 1. Fetch PROFILE
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, type AS profile_type, docker_name,
                   priorities, facilities, keywords
            FROM syslog_profiles
            WHERE id=%s
            """,
            (profile_id,),
        )
        row = cursor.fetchone()

        if not row:
            cursor.close()
            raise HTTPException(status_code=404, detail="Profile not found")

        # 2. Fetch DEVICE IDs
        cursor.execute(
            """
            SELECT device_id 
            FROM syslog_profile_devices 
            WHERE profile_id = %s
            """,
            (profile_id,),
        )
        device_rows = cursor.fetchall()
        cursor.close()

    device_ids = [d["device_id"] for d in device_rows]

    # 3. Docker validation (skip if docker_name == 'all')
    if docker_name.lower().strip() != "all":
        if row["docker_name"] != docker_name:
            raise HTTPException(status_code=403, detail="docker_name mismatch")

    # 4. Call Spring Boot for device details
    device_details = []
    if device_ids:
        try:
            # Correct SpringBoot URL
            spring_url = (
                f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"
                f"/docker/{docker_name}/getdevicedetailsbyids"
            )

            async with httpx.AsyncClient(timeout=10) as client:
                spring_resp = await client.post(
                    spring_url,
                    json=device_ids,
                    params={"pageno": 1, "pagesize": 100},
                )

            # Correct JSON parsing for BOTH mock and real server
            try:
                data = spring_resp.json()

                if isinstance(data, list):
                    device_details = data
                elif isinstance(data, dict) and "data" in data:
                    device_details = data["data"]
                else:
                    device_details = []

            except Exception:
                device_details = []

        except Exception as e:
            logger.error("SpringBoot call failed: %s", e)
            device_details = []

    # 5. Final response
    row["device_ids"] = device_ids
    row["device_details"] = device_details

    return row
