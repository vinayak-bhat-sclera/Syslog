# app/routers/integrations.py
import uuid
import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, status, Depends

from app.db import get_db_connection
from app.models import integrations as integ_models
from app.services.device_cache import clear_cache
from app.config import settings

router = APIRouter()
logger = logging.getLogger("app.routers.integrations")


def require_cloud_scope(x_cloud_auth: Optional[str] = None):
    token = getattr(settings, "CLOUD_AUTH_TOKEN", None)
    if token:
        if x_cloud_auth != token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing/invalid cloud auth")
    return None


@router.post("/syslog_integrations", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_cloud_scope)])
def create_integration(i: integ_models.IntegrationIn, network: str = Query(..., description="Network query param")):
    iid = str(uuid.uuid4())
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(
                """
                INSERT INTO syslog_integration (id, profile_id, destination_name, destination_type, ip_address, port, auth_token, network)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (iid, i.profile_id, i.destination_name, i.destination_type, i.ip_address, i.port, i.auth_token, network),
            )
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("create_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    # clear cache (optional)
    import asyncio

    asyncio.create_task(clear_cache())
    return {"status": "created", "id": iid}


@router.put("/syslog_integrations/{integration_id}", dependencies=[Depends(require_cloud_scope)])
def update_integration(integration_id: str, i: integ_models.IntegrationIn, network: str = Query(...)):
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(
                """
                UPDATE syslog_integration
                SET destination_name=%s, destination_type=%s, ip_address=%s, port=%s, auth_token=%s, profile_id=%s
                WHERE id=%s AND network=%s
                """,
                (i.destination_name, i.destination_type, i.ip_address, i.port, i.auth_token, i.profile_id, integration_id, network),
            )
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("update_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    import asyncio

    asyncio.create_task(clear_cache())
    return {"status": "updated", "id": integration_id}


@router.delete("/syslog_integrations/{integration_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_cloud_scope)])
def delete_integration(integration_id: str, network: str = Query(...)):
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_integration WHERE id=%s AND network=%s", (integration_id, network))
            cnx.commit()
            cursor.close()
    except Exception:
        logger.exception("delete_integration failed")
        raise HTTPException(status_code=400, detail="DB error deleting integration")
    import asyncio

    asyncio.create_task(clear_cache())
    return None


@router.get("/syslog_integrations")
def get_all_integrations(
    network: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search by destination_name (case-insensitive)"),
    profile_id: Optional[str] = Query(None, description="Filter by profile_id"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    params = []
    where = " WHERE 1=1 "
    if network:
        where += " AND network = %s"
        params.append(network)
    if q:
        where += " AND LOWER(destination_name) LIKE %s"
        params.append(f"%{q.lower()}%")
    if profile_id:
        where += " AND profile_id = %s"
        params.append(profile_id)

    offset = (page - 1) * limit
    sql_items = "SELECT id, profile_id, destination_name, destination_type, ip_address, port, auth_token, network FROM syslog_integration " + where + " ORDER BY destination_name ASC LIMIT %s OFFSET %s"
    sql_count = "SELECT COUNT(1) as cnt FROM syslog_integration " + where

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql_count, tuple(params))
        total = cursor.fetchone()["cnt"]
        cursor.execute(sql_items, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {"total": total, "page": page, "limit": limit, "items": rows}


@router.get("/syslog_integrations/{integration_id}")
def get_integration(integration_id: str, network: str = Query(...)):
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT id, profile_id, destination_name, destination_type, ip_address, port, auth_token, network FROM syslog_integration WHERE id=%s AND network=%s", (integration_id, network))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    return row
