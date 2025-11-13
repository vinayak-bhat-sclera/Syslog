# app/routers/profiles.py
import json
import uuid
import logging
from typing import Dict, Optional, Any, List

from fastapi import APIRouter, HTTPException, Path, Query, status, Body

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import clear_cache, clear_profile_devices

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


# ─────────────────────────────
# Helper
# ─────────────────────────────
def _validate_docker_name(docker_name: Optional[str]):
    """Ensure docker_name is provided in the path."""
    if not docker_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="docker_name path param is required",
        )


# ─────────────────────────────
# Create Profile
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/create",
    status_code=status.HTTP_201_CREATED,
)
def create_profile(
    username: str = Path(..., description="User name (not used by DB, for path consistency)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB, for path consistency)"),
    docker_name: str = Path(..., description="Docker name (used as docker_name in DB)"),
    p: profile_models.ProfileIn = Body(...),
) -> Dict[str, str]:
    """Create a new syslog profile with optional device mappings.
    docker_name is taken from the path and stored in DB as docker_name.
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

            # Clear in-memory cache if available
            try:
                clear_cache()
            except Exception:
                logger.debug("clear_cache unavailable or failed; continuing")

            cursor.close()

    except Exception as e:
        logger.exception("create_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "id": pid}


# ─────────────────────────────
# Update Profile
# ─────────────────────────────
@router.put(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/update",
    response_model=Dict[str, str],
)
def update_profile(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name (must match existing profile.docker_name)"),
    profile_id: str = Path(..., description="Profile ID to update"),
    p: profile_models.ProfileUpdate = Body(None),
):
    """Update allowed fields for a profile. docker_name is taken from path and must match stored value."""
    _validate_docker_name(docker_name)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            # Ensure docker_name matches (previously network)
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

            # Replace device mappings only if provided
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

            # Clear cache for this profile (or full cache fallback)
            try:
                clear_profile_devices(profile_id)
            except Exception:
                clear_cache()

            cursor.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": profile_id}


# ─────────────────────────────
# Delete Profile
# ─────────────────────────────
@router.delete(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/delete",
    status_code=status.HTTP_200_OK,
)
def delete_profile(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name"),
    profile_id: str = Path(..., description="Profile ID to delete"),
):
    """Delete a profile and its associated device mappings. docker_name from path must match stored value."""
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

            try:
                clear_profile_devices(profile_id)
            except Exception:
                clear_cache()

            cursor.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "id": profile_id}


# ─────────────────────────────
# Get All Profiles (Paginated, Filtered)
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
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """
    Return paginated profiles for the provided docker_name path value.
    Optional filters: profile_type and search (profile name).
    """
    _validate_docker_name(docker_name)

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
# Get Single Profile
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/get",
    status_code=status.HTTP_200_OK,
)
def get_profile(
    username: str = Path(..., description="User name (not used by DB)"),
    vdmsid: str = Path(..., description="VDMS id (not used by DB)"),
    docker_name: str = Path(..., description="Docker name"),
    profile_id: str = Path(..., description="Profile ID"),
):
    """Fetch a single profile by ID and docker_name (path)."""
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
