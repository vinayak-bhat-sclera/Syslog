import uuid
import logging
from typing import Dict, Optional, Any, List
from fastapi import APIRouter, HTTPException, Query, status
from app.db import get_db_connection
from app.models import integrations as integ_models

router = APIRouter()
logger = logging.getLogger("app.routers.integrations")


# ─────────────────────────────
# Helpers
# ─────────────────────────────
def _validate_network_for_profile(profile_id: str, network: str):
    """Ensure the given profile_id belongs to the specified network."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute("SELECT network FROM syslog_profiles WHERE id=%s", (profile_id,))
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found for provided profile_id")
    if row[0] != network:
        raise HTTPException(status_code=403, detail="Network mismatch for provided profile_id")


# ─────────────────────────────
# Create Integration
# ─────────────────────────────
@router.post("/syslog_integrations", status_code=status.HTTP_201_CREATED)
def create_integration(
    i: integ_models.IntegrationIn,
    network: str = Query(..., description="Network this integration belongs to"),
) -> Dict[str, str]:
    """Create a new syslog integration destination."""
    _validate_network_for_profile(i.profile_id, network)
    iid = str(uuid.uuid4())

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(
                """
                INSERT INTO syslog_integration 
                (id, profile_id, destination_name, ip_address, port, auth_token, network)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    iid,
                    i.profile_id,
                    i.destination_name,
                    i.ip_address,
                    i.port,
                    i.auth_token,
                    network,
                ),
            )
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("create_integration failed")
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "created", "id": iid}


# ─────────────────────────────
# Update Integration
# ─────────────────────────────
@router.put("/syslog_integrations", status_code=status.HTTP_200_OK)
def update_integration(
    integration_id: str = Query(..., description="Integration ID to update"),
    i: integ_models.IntegrationIn = None,
    network: str = Query(..., description="Network must match the profile's network"),
):
    """Update an existing integration (query-param style)."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
        existing = cursor.fetchone()
        cursor.close()

    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")

    _validate_network_for_profile(existing["profile_id"], network)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute(
                """
                UPDATE syslog_integration
                SET destination_name=%s, ip_address=%s, port=%s, auth_token=%s, profile_id=%s
                WHERE id=%s
                """,
                (
                    i.destination_name,
                    i.ip_address,
                    i.port,
                    i.auth_token,
                    i.profile_id,
                    integration_id,
                ),
            )
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("update_integration failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "updated", "id": integration_id}


# ─────────────────────────────
# Delete Integration
# ─────────────────────────────
@router.delete("/syslog_integrations", status_code=status.HTTP_200_OK)
def delete_integration(
    integration_id: str = Query(..., description="Integration ID to delete"),
    network: str = Query(..., description="Network this integration belongs to"),
):
    """Delete an integration (query-param style)."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")

    _validate_network_for_profile(row["profile_id"], network)

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()
            cursor.execute("DELETE FROM syslog_integration WHERE id=%s", (integration_id,))
            cnx.commit()
            cursor.close()
    except Exception as e:
        logger.exception("delete_integration failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "id": integration_id}


# ─────────────────────────────
# Get All Integrations (Paginated with filters)
# ─────────────────────────────
@router.get("/syslog_integrations", status_code=status.HTTP_200_OK)
def get_all_integrations(
    network: Optional[str] = Query(None, description="Filter by network"),
    profile_name: Optional[str] = Query(None, description="Filter by profile name (case-insensitive)"),
    search: Optional[str] = Query(None, description="Search by destination_name (case-insensitive)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """
    Return all integrations with optional filtering:
    - network
    - profile_name (from linked profile)
    - search (destination_name)
    """
    params: List[Any] = []
    where_clauses: List[str] = []

    if network:
        where_clauses.append("i.network = %s")
        params.append(network)
    if profile_name:
        where_clauses.append("LOWER(p.name) LIKE %s")
        params.append(f"%{profile_name.lower()}%")
    if search:
        where_clauses.append("LOWER(i.destination_name) LIKE %s")
        params.append(f"%{search.lower()}%")

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    offset = (page - 1) * limit

    sql = f"""
        SELECT 
            i.id,
            i.profile_id,
            p.name AS profile_name,
            p.type AS p_type,
            i.destination_name,
            i.ip_address,
            i.port,
            i.auth_token,
            i.network
        FROM syslog_integration i
        JOIN syslog_profiles p ON i.profile_id = p.id
        {where}
        ORDER BY i.destination_name ASC
        LIMIT %s OFFSET %s
    """

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql, tuple(params + [limit, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {"total": len(rows), "page": page, "limit": limit, "items": rows}


# ─────────────────────────────
# Get Single Integration
# ─────────────────────────────
@router.get("/syslog_integrations/single", status_code=status.HTTP_200_OK)
def get_integration(
    integration_id: str = Query(..., description="Integration ID"),
    network: str = Query(..., description="Network for validation"),
):
    """Fetch a single integration record (query-param style)."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT i.*, p.name AS profile_name, p.type AS p_type
            FROM syslog_integration i
            JOIN syslog_profiles p ON i.profile_id = p.id
            WHERE i.id=%s
            """,
            (integration_id,),
        )
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")

    _validate_network_for_profile(row["profile_id"], network)
    return row
