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
    notify_profile_change
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
    username: str,
    vdmsid: str,
    docker_name: str,
    p: profile_models.ProfileIn = Body(...),
) -> Dict[str, str]:

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
                INSERT INTO syslog_profiles 
                (id, name, `type`, docker_name, priorities, facilities, keywords)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (pid, p.name, p.type, docker_name, prio_json, fac_json, kws_json),
            )
            cnx.commit()

            # Device mappings
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

        # 🔥 Immediately refresh cache for this profile
        asyncio.create_task(notify_profile_change(pid))

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
    username: str,
    vdmsid: str,
    docker_name: str,
    profile_id: str,
    p: profile_models.ProfileUpdate = Body(None),
):

    _validate_docker_name(docker_name)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()

            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            if existing.get("docker_name") != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            preserved_type = existing["type"]
            new_name = p.name if p and p.name is not None else existing["name"]

            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = json.dumps(p.priorities) if (p and p.priorities is not None) else existing["priorities"]
                fac_json = json.dumps(p.facilities) if (p and p.facilities is not None) else existing["facilities"]
                kws_json = json.dumps(p.keywords) if (p and p.keywords is not None) else existing["keywords"]

            cursor.execute(
                """
                UPDATE syslog_profiles
                SET name=%s, priorities=%s, facilities=%s, keywords=%s
                WHERE id=%s
                """,
                (new_name, prio_json, fac_json, kws_json, profile_id),
            )
            cnx.commit()

            # Replace device mappings ONLY IF INCLUDED
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

        # 🔥 Immediate refresh
        asyncio.create_task(notify_profile_change(profile_id))

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
    username: str,
    vdmsid: str,
    docker_name: str,
    profile_id: str,
):

    _validate_docker_name(docker_name)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                "SELECT docker_name FROM syslog_profiles WHERE id=%s", (profile_id,)
            )
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Profile not found")

            if row["docker_name"] != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
            cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (profile_id,))
            cnx.commit()
            cursor.close()

        # 🔥 Trigger cache eviction + refresh
        asyncio.create_task(notify_profile_change(profile_id))

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
    username: str,
    vdmsid: str,
    docker_name: str,
    profile_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):

    _validate_docker_name(docker_name)

    params = [docker_name]
    where = ["docker_name = %s"]

    if profile_type:
        where.append("`type` = %s")
        params.append(profile_type)

    if search:
        where.append("LOWER(name) LIKE %s")
        params.append(f"%{search.lower()}%")

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
        WHERE {" AND ".join(where)}
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
# GET SINGLE PROFILE (UPDATED ROUTE)
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}",
    status_code=status.HTTP_200_OK,
)
def get_profile(
    username: str,
    vdmsid: str,
    docker_name: str,
    profile_id: str,
):

    _validate_docker_name(docker_name)

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT 
                id,
                name,
                `type` AS profile_type,
                docker_name,
                priorities,
                facilities,
                keywords
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
