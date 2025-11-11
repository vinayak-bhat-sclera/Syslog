# app/routers/integrations.py
import uuid
import logging
from typing import Dict, Optional, Any, List

from fastapi import APIRouter, HTTPException, Query, status

from app.db import get_db_connection
from app.models import integrations as integ_models

router = APIRouter()
logger = logging.getLogger("app.routers.integrations")


def _validate_network_for_profile(profile_id: str, network: str):
    with get_db_connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute("SELECT network FROM syslog_profiles WHERE id=%s", (profile_id,))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found for provided profile_id")
    if row[0] != network:
        raise HTTPException(status_code=403, detail="Network mismatch for profile_id")


@router.post("/syslog_integrations", status_code=status.HTTP_201_CREATED)
def create_integration(
    i: integ_models.IntegrationIn,
    network: str = Query(..., description="Network this integration belongs to")
) -> Dict[str, str]:
    """Create a new syslog integration destination. network required."""
    # validate profile belongs to same network
    _validate_network_for_profile(i.profile_id, network)

    iid = str(uuid.uuid4())
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("""
                INSERT INTO syslog_integration (id, profile_id, destination_name, destination_type, ip_address, port, auth_token)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (iid, i.profile_id, i.destination_name, i.destination_type, i.ip_address, i.port, i.auth_token))
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("create_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "created", "id": iid}


@router.put("/syslog_integrations/{integration_id}", status_code=status.HTTP_200_OK)
def update_integration(
    integration_id: str,
    i: integ_models.IntegrationIn,
    network: str = Query(..., description="Network must match profile's network")
):
    # ensure integration exists and belongs to profile with the network
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    _validate_network_for_profile(i.profile_id, network)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("""
                UPDATE syslog_integration
                SET destination_name=%s, destination_type=%s, ip_address=%s, port=%s, auth_token=%s
                WHERE id=%s
            """, (i.destination_name, i.destination_type, i.ip_address, i.port, i.auth_token, integration_id))
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("update_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "updated", "id": integration_id}


@router.delete("/syslog_integrations/{integration_id}", status_code=status.HTTP_200_OK)
def delete_integration(integration_id: str, network: str = Query(...)):
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    # validate network (profile -> network)
    _validate_network_for_profile(row["profile_id"], network)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_integration WHERE id=%s", (integration_id,))
            cnx.commit()
            cursor.close()
    except Exception:
        logger.exception("delete_integration failed")
        raise HTTPException(status_code=400, detail="DB error deleting integration")
    return {"status": "deleted", "id": integration_id}


@router.get("/syslog_integrations", status_code=status.HTTP_200_OK)
def get_all_integrations(
    network: Optional[str] = Query(None, description="Filter by network"),
    profile_id: Optional[str] = Query(None, description="Filter by profile_id"),
    search: Optional[str] = Query(None, description="Case-insensitive name search"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    params: List[Any] = []
    where_clauses: List[str] = []

    if profile_id:
        where_clauses.append("profile_id = %s"); params.append(profile_id)
    if search:
        where_clauses.append("LOWER(destination_name) LIKE %s"); params.append(f"%{search.lower()}%")
    if network:
        # join with profiles to filter by network
        where_clauses.append("p.network = %s"); params.append(network)
        join_profiles = True
    else:
        join_profiles = False

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    offset = (page - 1) * limit

    sql = f"""
        SELECT i.* FROM syslog_integration i
        {"JOIN syslog_profiles p ON i.profile_id = p.id" if join_profiles else ""}
        {where}
        ORDER BY i.destination_name ASC
        LIMIT %s OFFSET %s
    """
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        try:
            cursor.execute(sql, tuple(params + [limit, offset]))
            rows = cursor.fetchall() or []
        finally:
            cursor.close()
    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


@router.get("/syslog_integrations/{integration_id}", status_code=status.HTTP_200_OK)
def get_integration(integration_id: str, network: str = Query(...)):
    # ensure integration exists
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT * FROM syslog_integration WHERE id=%s", (integration_id,))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    _validate_network_for_profile(row["profile_id"], network)
    return row
