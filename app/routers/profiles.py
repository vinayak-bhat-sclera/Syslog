# app/routers/profiles.py
import json
import uuid
import logging
from typing import Dict, Optional, Any, List

from fastapi import APIRouter, HTTPException, Query, status, Body  # ✅ Added Body

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import clear_cache, clear_profile_devices

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


# ─────────────────────────────
# Helper
# ─────────────────────────────
def _validate_network(network: Optional[str]):
    """Ensure network param is always provided."""
    if not network:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="network query param is required",
        )


# ─────────────────────────────
# Create Profile
# ─────────────────────────────
@router.post("/syslog_profiles", status_code=status.HTTP_201_CREATED)
def create_profile(
    p: profile_models.ProfileIn = Body(...),  # ✅ Explicitly parse from JSON body
    network: str = Query(..., description="Network for the profile"),
) -> Dict[str, str]:
    """Create a new syslog profile with optional device mappings."""
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
            cursor.execute(
                """
                INSERT INTO syslog_profiles (id, name, type, network, priorities, facilities, keywords)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (pid, p.name, p.type, network, prio_json, fac_json, kws_json),
            )
            cnx.commit()

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
# Update Profile (query-param style)
# ─────────────────────────────
@router.put("/syslog_profiles", response_model=Dict[str, str])
def update_profile(
    profile_id: str = Query(..., description="Profile ID to update"),
    network: str = Query(..., description="Network of the profile (immutable but required)"),
    p: profile_models.ProfileUpdate = Body(None),  # ✅ parse from body if present
):
    """Update a syslog profile (query-param style)."""
    _validate_network(network)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")
            if existing["network"] != network:
                raise HTTPException(status_code=403, detail="Network mismatch")

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
# Delete Profile (query-param style)
# ─────────────────────────────
@router.delete("/syslog_profiles", status_code=status.HTTP_200_OK)
def delete_profile(
    profile_id: str = Query(..., description="Profile ID to delete"),
    network: str = Query(..., description="Network must match existing profile"),
):
    """Delete a profile and its associated device mappings (query-param style)."""
    _validate_network(network)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
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
@router.get("/syslog_profiles/all", status_code=status.HTTP_200_OK)
def get_all_profiles(
    network: Optional[str] = Query(None, description="Filter by network"),
    profile_type: Optional[str] = Query(None, description="Filter by profile type (internal|external)"),
    search: Optional[str] = Query(None, description="Substring match on profile name"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """Return paginated profiles sorted alphabetically by name."""
    params: List[Any] = []
    where_clauses: List[str] = []

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
        cursor.execute(sql, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


# ─────────────────────────────
# Get Single Profile (query-param style)
# ─────────────────────────────
@router.get("/syslog_profiles/single", status_code=status.HTTP_200_OK)
def get_profile(
    profile_id: str = Query(..., description="Profile ID"),
    network: str = Query(..., description="Network of the profile"),
):
    """Fetch a single profile by ID and network (query-param style)."""
    _validate_network(network)
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    if row["network"] != network:
        raise HTTPException(status_code=403, detail="Network mismatch")

    return row
