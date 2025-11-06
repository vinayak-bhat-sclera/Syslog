# app/routers/profiles.py
import json
import uuid
import logging
from typing import Dict
from fastapi import APIRouter, HTTPException
from app.db import get_db_connection
from app.models import profiles as profile_models
from app.services.device_cache import clear_profile_devices, clear_cache

router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


@router.post("/syslog_profiles")
def create_profile(p: profile_models.ProfileIn) -> Dict[str, str]:
    """Create a new syslog profile and assign devices."""
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

            cursor.execute("""
                INSERT INTO syslog_profiles (id, name, type, network, priorities, facilities, keywords)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (pid, p.name, p.type, p.network, prio_json, fac_json, kws_json))
            cnx.commit()

            if getattr(p, "device_uuids", None):
                for du in p.device_uuids:
                    rid = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                        VALUES (%s,%s,%s)
                    """, (rid, pid, du))
                cnx.commit()

            # Refresh cache
            clear_profile_devices(pid)

    except Exception as e:
        logger.exception("create_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "id": pid}


@router.put("/syslog_profiles/{profile_id}")
def update_profile(profile_id: str, p: profile_models.ProfileUpdate):
    """Update profile details and associated devices."""
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)

            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            new_name = p.name or existing["name"]
            preserved_type = existing["type"]
            preserved_network = existing["network"]

            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = json.dumps(p.priorities) if p.priorities else existing.get("priorities")
                fac_json = json.dumps(p.facilities) if p.facilities else existing.get("facilities")
                kws_json = json.dumps(p.keywords) if p.keywords else existing.get("keywords")

            cursor.execute("""
                UPDATE syslog_profiles
                SET name=%s, priorities=%s, facilities=%s, keywords=%s
                WHERE id=%s
            """, (new_name, prio_json, fac_json, kws_json, profile_id))
            cnx.commit()

            if getattr(p, "device_uuids", None):
                cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
                for du in p.device_uuids:
                    rid = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                        VALUES (%s,%s,%s)
                    """, (rid, profile_id, du))
                cnx.commit()

            clear_profile_devices(profile_id)

    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": profile_id}


@router.delete("/syslog_profiles/{profile_id}")
def delete_profile(profile_id: str):
    """Delete a profile and clear related cache."""
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (profile_id,))
            cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (profile_id,))
            cnx.commit()
            clear_profile_devices(profile_id)
    except Exception as e:
        logger.exception("delete_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "id": profile_id}
