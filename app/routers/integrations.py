# app/routers/integrations.py
import uuid
import logging
from typing import Dict, Optional, Any, List
from fastapi import APIRouter, HTTPException, Path, Query, Body, status

from app.db import get_db_connection
from app.models import integrations as integ_models

router = APIRouter()
logger = logging.getLogger("app.routers.integrations")


# ─────────────────────────────
# Helpers
# ─────────────────────────────
def _validate_docker_name_for_profile(profile_id: str, docker_name: str):
    """Ensure the given profile_id belongs to the specified docker_name."""
    with get_db_connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute("SELECT docker_name FROM syslog_profiles WHERE id=%s", (profile_id,))
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found for provided profile_id")
    if row[0] != docker_name:
        raise HTTPException(status_code=403, detail="docker_name mismatch for provided profile_id")


# ─────────────────────────────
# Create Integration
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/create",
    status_code=status.HTTP_201_CREATED,
)
def create_integration(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(..., description="Docker name stored in DB"),
    i: integ_models.IntegrationIn = Body(...),
) -> Dict[str, str]:

    _validate_docker_name_for_profile(i.profile_id, docker_name)
    iid = str(uuid.uuid4())

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()

            cursor.execute(
                """
                INSERT INTO syslog_integration 
                (id, profile_id, destination_name, ip_address, port, auth_token, docker_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    iid,
                    i.profile_id,
                    i.destination_name,
                    i.ip_address,
                    i.port,
                    i.auth_token,
                    docker_name,
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
@router.put(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/{integration_id}/update",
    status_code=status.HTTP_200_OK,
)
def update_integration(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    integration_id: str = Path(...),
    i: integ_models.IntegrationIn = Body(...),
):
    """Update an existing integration. docker_name must match profile's stored docker_name."""

    # Step 1: Fetch profile_id from integration
    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
        existing = cursor.fetchone()
        cursor.close()

    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Step 2: Ensure docker_name matches the linked profile's docker_name
    _validate_docker_name_for_profile(existing["profile_id"], docker_name)

    # Step 3: Perform the update
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
@router.delete(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/{integration_id}/delete",
    status_code=status.HTTP_200_OK,
)
def delete_integration(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    integration_id: str = Path(...),
):
    """Delete integration. docker_name must match."""

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")

    _validate_docker_name_for_profile(row["profile_id"], docker_name)

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
# Get All Integrations
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations",
    status_code=status.HTTP_200_OK,
)
def get_all_integrations(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_name: Optional[str] = Query(None, description="Filter by profile name (case-insensitive)"),
    search: Optional[str] = Query(None, description="Search by destination_name (case-insensitive)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    params: List[Any] = []
    where_clauses: List[str] = ["i.docker_name = %s"]
    params.append(docker_name)

    if profile_name:
        where_clauses.append("LOWER(p.name) LIKE %s")
        params.append(f"%{profile_name.lower()}%")

    if search:
        where_clauses.append("LOWER(i.destination_name) LIKE %s")
        params.append(f"%{search.lower()}%")

    where = "WHERE " + " AND ".join(where_clauses)
    offset = (page - 1) * limit

    sql = f"""
        SELECT 
            i.id,
            i.profile_id,
            p.name AS profile_name,
            p.type AS profile_type,
            i.destination_name,
            i.ip_address,
            i.port,
            i.auth_token,
            i.docker_name
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
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/{integration_id}/get",
    status_code=status.HTTP_200_OK,
)
def get_integration(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    integration_id: str = Path(...),
):
    """Fetch a single integration record."""

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT i.*, p.name AS profile_name, p.type AS profile_type
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

    _validate_docker_name_for_profile(row["profile_id"], docker_name)

    return row
