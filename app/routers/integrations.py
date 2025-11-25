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
        cursor = cnx.cursor(buffered=True)  # ★ FIXED
        cursor.execute(
            "SELECT docker_name FROM syslog_profiles WHERE id=%s",
            (profile_id,),
        )
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

    # Validate the profile belongs to this docker_name
    _validate_docker_name_for_profile(i.profile_id, docker_name)

    # -------------------------------------------------------
    # NEW VALIDATION: destination_name must be unique per docker
    # -------------------------------------------------------
    # with get_db_connection() as cnx:
    #     cursor = cnx.cursor(buffered=True, dictionary=True)
    #     cursor.execute(
    #         """
    #         SELECT id 
    #         FROM syslog_integration
    #         WHERE LOWER(destination_name) = LOWER(%s)
    #           AND docker_name = %s
    #         """,
    #         (i.destination_name.strip(), docker_name),
    #     )
    #     existing = cursor.fetchone()
    #     cursor.close()

    # if existing:
    #     raise HTTPException(
    #         status_code=409,
    #         detail=f"destination_name '{i.destination_name}' already exists"
    #     )

    # -------------------------------------------------------
    # STRICT ENFORCEMENT (existing logic)
    # Check that same profile_id is not used under another docker
    # -------------------------------------------------------
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True)
        cursor.execute(
            """
            SELECT docker_name 
            FROM syslog_integration 
            WHERE profile_id = %s
            """,
            (i.profile_id,),
        )
        rows = cursor.fetchall()
        cursor.close()

    for (existing_docker,) in rows:
        if existing_docker != docker_name:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Profile ID '{i.profile_id}' is already used under docker '{existing_docker}'. "
                    f"Cannot reuse it under '{docker_name}'."
                ),
            )

    # -------------------------------------------------------
    # CREATE INTEGRATION
    # -------------------------------------------------------
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
    """
    Update integration.

    Behavior:
    - docker_name == "all" → update regardless of profile/docker matching
    - otherwise            → validate old + new profile_id belong to this docker_name
    """

    update_all = (docker_name.lower().strip() == "all")

    # Fetch existing integration → get old profile_id
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED
        cursor.execute(
            "SELECT profile_id FROM syslog_integration WHERE id=%s",
            (integration_id,),
        )
        existing = cursor.fetchone()
        cursor.close()

    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")

    old_profile_id = existing["profile_id"]

    # Validate ONLY if docker_name != "all"
    if not update_all:
        _validate_docker_name_for_profile(old_profile_id, docker_name)
        _validate_docker_name_for_profile(i.profile_id, docker_name)

    # Perform update
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True)  # ★ FIXED

            cursor.execute(
                """
                UPDATE syslog_integration
                SET destination_name=%s,
                    ip_address=%s,
                    port=%s,
                    auth_token=%s,
                    profile_id=%s
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

    return {
        "status": "updated",
        "id": integration_id,
        "mode": "all-dockers" if update_all else "single-docker",
    }


# # ─────────────────────────────
# # Delete Integration
# # ─────────────────────────────
# @router.delete(
#     "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/{integration_id}/delete",
#     status_code=status.HTTP_200_OK,
# )
# def delete_integration(
#     username: str = Path(...),
#     vdmsid: str = Path(...),
#     docker_name: str = Path(...),
#     integration_id: str = Path(...),
# ):

#     with get_db_connection() as cnx:
#         cursor = cnx.cursor(dictionary=True)
#         cursor.execute("SELECT profile_id FROM syslog_integration WHERE id=%s", (integration_id,))
#         row = cursor.fetchone()
#         cursor.close()

#     if not row:
#         raise HTTPException(status_code=404, detail="Integration not found")

#     _validate_docker_name_for_profile(row["profile_id"], docker_name)

#     try:
#         with get_db_connection() as cnx:
#             cursor = cnx.cursor()
#             cursor.execute("DELETE FROM syslog_integration WHERE id=%s", (integration_id,))
#             cnx.commit()
#             cursor.close()

#     except Exception as e:
#         logger.exception("delete_integration failed")
#         raise HTTPException(status_code=400, detail=str(e))

#     return {"status": "deleted", "id": integration_id}


# ─────────────────────────────
# DELETE MULTIPLE INTEGRATIONS (POST)
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/delete",
    status_code=status.HTTP_200_OK,
)
def delete_multiple_integrations(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),

    # NEW QUERY PARAMS
    search_key: Optional[str] = Query(None, description="Prefix match on destination_name"),
    profile_name: Optional[str] = Query(None, description="Filter by profile name"),
    is_select_all: bool = Query(False, description="Enable bulk delete mode"),

    # BODY → integration_ids
    body: Dict[str, Any] = Body(..., description="integration_ids list"),
):
    integration_ids = body.get("integration_ids", [])
    docker_clean = docker_name.lower().strip()
    docker_all = (docker_clean == "all")

    deleted = []
    not_found = []
    docker_mismatch = []

    # -------------------------------------------------------------
    # CASE A — Delete exactly integration_ids[]
    # -------------------------------------------------------------
    if integration_ids and not is_select_all:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)
            for iid in integration_ids:

                cursor.execute(
                    """
                    SELECT i.id, i.profile_id, p.docker_name
                    FROM syslog_integration i
                    JOIN syslog_profiles p ON p.id = i.profile_id
                    WHERE i.id=%s
                    """,
                    (iid,),
                )
                row = cursor.fetchone()

                if not row:
                    not_found.append(iid)
                    continue

                # docker validation unless docker="all"
                if not docker_all and row["docker_name"] != docker_name:
                    docker_mismatch.append(iid)
                    continue

                cursor.execute("DELETE FROM syslog_integration WHERE id=%s", (iid,))
                deleted.append(iid)

            cnx.commit()
            cursor.close()

        return {
            "status": "completed",
            "mode": "all-dockers" if docker_all else "single-docker",
            "deleted": deleted,
            "not_found": not_found,
            "docker_mismatch": docker_mismatch,
        }

    # -------------------------------------------------------------
    # ANY OTHER CASE needs is_select_all=true
    # -------------------------------------------------------------
    if not is_select_all:
        raise HTTPException(
            status_code=422,
            detail="Either provide integration_ids[] or set is_select_all=true",
        )

    # -------------------------------------------------------------
    # BUILD FILTERS for search_key + profile_name
    # -------------------------------------------------------------
    filters = []
    params = []

    # docker filter unless docker_name="all"
    if not docker_all:
        filters.append("p.docker_name = %s")
        params.append(docker_name)

    # prefix search on destination_name
    if search_key:
        filters.append("i.destination_name LIKE %s")
        params.append(search_key + "%")

    # profile_name search
    if profile_name:
        filters.append("p.name LIKE %s")
        params.append(profile_name + "%")

    where_sql = ""
    if filters:
        where_sql = "WHERE " + " AND ".join(filters)

    # -------------------------------------------------------------
    # FETCH MATCHING INTEGRATIONS
    # -------------------------------------------------------------
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)

        cursor.execute(
            f"""
            SELECT i.id
            FROM syslog_integration i
            JOIN syslog_profiles p ON p.id = i.profile_id
            {where_sql}
            """,
            tuple(params),
        )
        rows = cursor.fetchall() or []

        ids_to_delete = [r["id"] for r in rows]

        if not ids_to_delete:
            cursor.close()
            return {
                "status": "completed",
                "deleted": [],
                "not_found": [],
                "docker_mismatch": [],
                "message": "No integrations matched filters",
            }

        # ---------------------------------------------------------
        # DELETE MATCHED INTEGRATIONS
        # ---------------------------------------------------------
        for iid in ids_to_delete:
            cursor.execute("DELETE FROM syslog_integration WHERE id=%s", (iid,))
            deleted.append(iid)

        cnx.commit()
        cursor.close()

    return {
        "status": "completed",
        "mode": "all-dockers" if docker_all else "single-docker",
        "deleted": deleted,
        "filtered_by": {
            "search_key": search_key,
            "profile_name": profile_name,
        },
    }


# ─────────────────────────────
# DELETE ALL INTEGRATIONS FOR DOCKER
# ─────────────────────────────
# @router.delete(
#     "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/delete/all",
#     status_code=status.HTTP_200_OK,
# )
# def delete_all_integrations(
#     username: str = Path(...),
#     vdmsid: str = Path(...),
#     docker_name: str = Path(...)
# ):
#     """
#     Delete ALL integrations.

#     Modes:
#     docker_name == "all"  → Delete ALL integrations globally
#     Specific docker_name   → Delete integrations only for profiles under that docker
#     """

#     with get_db_connection() as cnx:
#         cursor = cnx.cursor()

#         # ─────────────────────────────
#         # MODE 1: DELETE EVERYTHING
#         # ─────────────────────────────
#         if docker_name.lower().strip() == "all":
#             cursor.execute("DELETE FROM syslog_integration")
#             deleted_count = cursor.rowcount
#             cnx.commit()
#             cursor.close()

#             return {
#                 "status": "completed",
#                 "deleted": deleted_count,
#                 "docker_name": "all"
#             }

#         # ─────────────────────────────
#         # MODE 2: DELETE for specific docker_name
#         # ─────────────────────────────
#         cursor.execute(
#             "SELECT id FROM syslog_profiles WHERE docker_name=%s",
#             (docker_name,)
#         )
#         profiles = [r[0] for r in cursor.fetchall() or []]

#         if not profiles:
#             cursor.close()
#             return {
#                 "status": "completed",
#                 "deleted": 0,
#                 "detail": f"No integrations found for docker_name '{docker_name}'"
#             }

#         placeholder = ",".join(["%s"] * len(profiles))

#         cursor.execute(
#             f"""
#             DELETE FROM syslog_integration 
#             WHERE profile_id IN ({placeholder})
#             """,
#             profiles
#         )

#         deleted_count = cursor.rowcount
#         cnx.commit()
#         cursor.close()

#     return {
#         "status": "completed",
#         "deleted": deleted_count,
#         "docker_name": docker_name
#     }


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
    profile_name: Optional[str] = Query(None, description="Single or comma-separated values"),
    search_key: Optional[str] = Query(None),
    page_no: Any = Query(1),
    page_size: Any = Query(50),
):
    """Get integrations with multi-select profile_name, search, and docker_name='all' support."""

    # Convert to int
    try:
        page_no = int(page_no)
        page_size = int(page_size)
    except Exception:
        raise HTTPException(status_code=422, detail="page_no and page_size must be integers")

    if page_no < 1 or page_size < 1:
        raise HTTPException(status_code=422, detail="page_no & page_size must be >= 1")

    where_clauses = []
    params: List[Any] = []

    # ----------------------------------
    # 1️⃣ Docker Name Filter
    # ----------------------------------
    if docker_name.lower() != "all":
        where_clauses.append("i.docker_name = %s")
        params.append(docker_name)

    # ----------------------------------
    # 2️⃣ Profile Name Multi-Select Filter
    # ----------------------------------
    if profile_name:
        names = [x.strip().lower() for x in profile_name.split(",") if x.strip()]

        profile_filters = " OR ".join(["LOWER(p.name) LIKE %s" for _ in names])
        where_clauses.append(f"({profile_filters})")

        params.extend([f"%{n}%" for n in names])

    # ----------------------------------
    # 3️⃣ Search Filter
    # ----------------------------------
    if search_key:
        where_clauses.append("LOWER(i.destination_name) LIKE %s")
        params.append(f"%{search_key.lower()}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    offset = (page_no - 1) * page_size

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
        JOIN syslog_profiles p 
            ON i.profile_id = p.id
        {where_sql}
        ORDER BY i.destination_name ASC
        LIMIT %s OFFSET %s
    """

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql, tuple(params + [page_size, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {
        "total": len(rows),
        "page": page_no,
        "limit": page_size,
        "items": rows,
    }

# ─────────────────────────────
# Get Single Integration
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_integrations/{integration_id}",
    status_code=status.HTTP_200_OK,
)
def get_integration(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    integration_id: str = Path(...),
):

    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED
        cursor.execute(
            """
            SELECT i.*, p.name AS profile_name, p.type AS profile_type, p.docker_name AS profile_docker
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

    # ─────────────────────────────────────────────
    # NEW: allow docker_name = "all"
    # ─────────────────────────────────────────────
    if docker_name.lower().strip() != "all":
        if row["profile_docker"] != docker_name:
            raise HTTPException(status_code=403, detail="docker_name mismatch")

    return row
