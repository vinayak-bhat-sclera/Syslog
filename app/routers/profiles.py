# app/routers/profiles.py
import json
import uuid
import logging
from typing import Dict

from fastapi import APIRouter, HTTPException

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import clear_cache  

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


# ─────────────────────────────
# Create Profile
# ─────────────────────────────
@router.post("/syslog_profiles")
def create_profile(p: profile_models.ProfileIn) -> Dict[str, str]:
    """
    Create a new syslog profile.
    """
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
                # Insert profile
                cursor.execute("""
                    INSERT INTO syslog_profiles (id, name, type, network, priorities, facilities, keywords)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (pid, p.name, p.type, p.network, prio_json, fac_json, kws_json))
                cnx.commit()

                # Insert device mappings (if provided)
                if getattr(p, "devices", None):
                    for d in p.devices:
                        rid = str(uuid.uuid4())
                        cursor.execute("""
                            INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                            VALUES (%s,%s,%s)
                        """, (rid, pid, d.device_id))
                    cnx.commit()

                # Optional: clear cache since mappings changed
                clear_cache()

            finally:
                cursor.close()
    except Exception as e:
        logger.exception("create_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "id": pid}


# ─────────────────────────────
# Update Profile
# ─────────────────────────────
@router.put("/syslog_profiles/{profile_id}")
def update_profile(profile_id: str, p: profile_models.ProfileUpdate):
    """Update profile and device mappings."""
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
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

                cursor.execute("""
                    UPDATE syslog_profiles
                    SET name=%s, type=%s, network=%s, priorities=%s, facilities=%s, keywords=%s
                    WHERE id=%s
                """, (new_name, preserved_type, preserved_network, prio_json, fac_json, kws_json, profile_id))
                cnx.commit()

                # Update device mappings if provided
                if getattr(p, "devices", None) is not None:
                    cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                    for d in p.devices:
                        rid = str(uuid.uuid4())
                        cursor.execute("""
                            INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                            VALUES (%s,%s,%s)
                        """, (rid, profile_id, d.device_id))
                    cnx.commit()

                # Optional: clear cache since mappings changed
                clear_cache()

            finally:
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
@router.delete("/syslog_profiles/{profile_id}")
def delete_profile(profile_id: str):
    """Delete a profile and associated device mappings."""
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            try:
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (profile_id,))
                cnx.commit()

                # Clear cache since mappings changed
                clear_cache()

            finally:
                cursor.close()
    except Exception:
        logger.exception("delete_profile failed")
        raise HTTPException(status_code=400, detail="DB error deleting profile")

    return {"status": "deleted", "id": profile_id}


# ─────────────────────────────
# Get All Profiles
# ─────────────────────────────
@router.get("/syslog_profiles")
def get_all_profiles():
    """Return all syslog profiles."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM syslog_profiles ORDER BY name ASC")
            rows = cursor.fetchall() or []
        finally:
            cursor.close()
    return {"total": len(rows), "items": rows}


# ─────────────────────────────
# Get One Profile
# ─────────────────────────────
@router.get("/syslog_profiles/{profile_id}")
def get_profile(profile_id: str):
    """Fetch a single profile by ID."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")

    return row
