# app/routers/integrations.py
import uuid
import logging
from typing import Dict

from fastapi import APIRouter, HTTPException

from app.db import get_db_connection
from app.models import integrations as integ_models

router = APIRouter()
logger = logging.getLogger("app.routers.integrations")

@router.post("/syslog_integrations")
def create_integration(i: integ_models.IntegrationIn) -> Dict[str, str]:
    iid = str(uuid.uuid4())
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            try:
                cursor.execute("""
                    INSERT INTO syslog_integration (id, profile_id, destination_name, destination_type, ip_address, port, auth_token)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (iid, i.profile_id, i.destination_name, i.destination_type, i.ip_address, i.port, i.auth_token))
                cnx.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.exception("create_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "created", "id": iid}

@router.put("/syslog_integrations/{integration_id}")
def update_integration(integration_id: str, i: integ_models.IntegrationIn):
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            try:
                cursor.execute("""
                    UPDATE syslog_integration SET destination_name=%s, destination_type=%s, ip_address=%s, port=%s, auth_token=%s
                    WHERE id=%s
                """, (i.destination_name, i.destination_type, i.ip_address, i.port, i.auth_token, integration_id))
                cnx.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.exception("update_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "updated", "id": integration_id}

@router.delete("/syslog_integrations/{integration_id}")
def delete_integration(integration_id: str):
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            try:
                cursor.execute("DELETE FROM syslog_integration WHERE id=%s", (integration_id,))
                cnx.commit()
            finally:
                cursor.close()
    except Exception:
        logger.exception("delete_integration failed")
        raise HTTPException(status_code=400, detail="DB error deleting integration")
    return {"status": "deleted", "id": integration_id}

@router.get("/syslog_integrations")
def get_all_integrations():
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM syslog_integration ORDER BY created_at DESC")
            rows = cursor.fetchall() or []
        finally:
            cursor.close()
    return {"total": len(rows), "items": rows}

@router.get("/syslog_integrations/{integration_id}")
def get_integration(integration_id: str):
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM syslog_integration WHERE id=%s", (integration_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    return row
