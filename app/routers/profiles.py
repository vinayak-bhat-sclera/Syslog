# app/routers/profiles.py
import json
import uuid
import logging
import asyncio
from typing import Dict, Optional, Any, List
import httpx
from app.config import settings

from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    Query,
    status,
    Body,
)

from app.db import get_db_connection
from app.models import profiles as profile_models
from app.models.profile_devices import ProfileDeviceDeleteRequest

from app.services.device_cache import (
    clear_cache,
    clear_profile_devices,
    notify_profile_change,
)

from pydantic import BaseModel


class ProfileDeleteRequest(BaseModel):
    profile_ids: List[str]


from fastapi import Query, Body

class DeviceDeleteBody(BaseModel):
    device_ids: Optional[List[str]] = []


router = APIRouter()
logger = logging.getLogger("app.routers.profiles")


# ─────────────────────────────
# Helper
# ─────────────────────────────
def _validate_docker_name(docker_name: Optional[str]):
    if not docker_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="docker_name path param is required",
        )


# ─────────────────────────────
# CREATE PROFILE  (UPDATED)
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/create",
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    search_key: Optional[str] = Query(None),
    is_select_all: bool = Query(False),
    p: profile_models.ProfileIn = Body(...),
):

    _validate_docker_name(docker_name)
    pid = str(uuid.uuid4())

    device_ids_input = p.device_ids or []
    search_key_clean = (search_key or "").strip()
    final_device_ids = set()

    # --------------------------------------------------------------------
    # STRICT VALIDATION: NO duplicate profile name in same docker
    # --------------------------------------------------------------------
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)
            cursor.execute(
                """
                    SELECT id FROM syslog_profiles
                    WHERE name = %s AND docker_name = %s
                """,
                (p.name.strip(), docker_name),
            )
            dup = cursor.fetchone()
            cursor.fetchall()
            cursor.close()

            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=f"Profile name '{p.name}' already exists in docker '{docker_name}'",
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB validation failed: {e}")

    # --------------------------------------------------------------------
    # Handle metadata for type
    # --------------------------------------------------------------------
    if p.type == "external":
        prio_json = fac_json = kws_json = None
    else:
        prio_json = json.dumps(p.priorities) if p.priorities else None
        fac_json = json.dumps(p.facilities) if p.facilities else None
        kws_json = json.dumps(p.keywords) if p.keywords else None

    spring_url = (
        f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"
        f"/docker/{docker_name}/getalldeviceids"
    )

    # --------------------------------------------------------------------
    # CASE 1
    # --------------------------------------------------------------------
    if device_ids_input and not is_select_all:
        case = "device_ids_only"
        final_device_ids.update(device_ids_input)

    # --------------------------------------------------------------------
    # CASE 2
    # --------------------------------------------------------------------
    elif device_ids_input and is_select_all and search_key_clean != "":
        case = "device_ids_plus_springboot"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                spring_url,
                params={"search_key": search_key_clean, "is_select_all": True},
            )
        data = resp.json()
        final_device_ids.update(data)
        final_device_ids.update(device_ids_input)

    # --------------------------------------------------------------------
    # CASE 3
    # --------------------------------------------------------------------
    elif is_select_all and search_key_clean != "":
        case = "springboot_only"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                spring_url,
                params={"search_key": search_key_clean, "is_select_all": True},
            )
        data = resp.json()
        final_device_ids.update(data)

    # --------------------------------------------------------------------
    # CASE 4
    # --------------------------------------------------------------------
    elif is_select_all and search_key_clean == "":
        case = "select_all_no_search"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                spring_url,
                params={"is_select_all": True},
            )
        data = resp.json()
        final_device_ids.update(data)

    # --------------------------------------------------------------------
    # CASE 5
    # --------------------------------------------------------------------
    else:
        case = "none"

    # convert to list
    final_ids_list = sorted(final_device_ids)

    # --------------------------------------------------------------------
    # NEW FILTERING LOGIC – Prevent reuse of same device under same profile type
    # --------------------------------------------------------------------
    existing_map = {}  # device_id → type already assigned

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT pd.device_id, sp.type
                FROM syslog_profile_devices pd
                JOIN syslog_profiles sp ON sp.id = pd.profile_id
                """
            )
            rows = cursor.fetchall() or []
            cursor.close()

            for r in rows:
                d = r["device_id"]
                t = r["type"]
                # device may appear twice → keep ALL types assigned
                existing_map.setdefault(d, set()).add(t)

    except Exception:
        raise HTTPException(500, "Failed loading existing profile mappings")

    # filter out devices violating rules
    filtered_ids = []
    for dev_id in final_ids_list:
        previous_types = existing_map.get(dev_id, set())

        # if same type already exists → skip
        if p.type in previous_types:
            continue

        filtered_ids.append(dev_id)

    final_ids_list = filtered_ids

    # --------------------------------------------------------------------
    # INSERT INTO DB
    # --------------------------------------------------------------------
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True)

            cursor.execute(
                """
                INSERT INTO syslog_profiles 
                (id, name, `type`, docker_name, priorities, facilities, keywords)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (pid, p.name, p.type, docker_name, prio_json, fac_json, kws_json),
            )

            for dev_id in final_ids_list:
                rid = str(uuid.uuid4())
                cursor.execute(
                    """
                        INSERT IGNORE INTO syslog_profile_devices 
                        (id, profile_id, device_id)
                        VALUES (%s,%s,%s)
                    """,
                    (rid, pid, dev_id),
                )

            cnx.commit()
            cursor.close()

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"create_profile failed: {e}")

    #  Refresh cache for this new profile
    asyncio.create_task(notify_profile_change(pid))

    return {
        "status": "created",
        "id": pid,
        "case": case,
        "final_count": len(final_ids_list),
        "final_device_ids": final_ids_list,
    }


# ─────────────────────────────
# UPDATE PROFILE  (NO DEVICE IDs)
# ─────────────────────────────
@router.put(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/update",
    response_model=Dict[str, str],
)
async def update_profile(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(..., description="Docker name"),
    profile_id: str = Path(...),
    p: profile_models.ProfileUpdate = Body(None),
):
    """
    Update profile fields: name, priorities, facilities, keywords.
     device_ids not accepted here.
    """
    _validate_docker_name(docker_name)
    update_all = (docker_name.lower().strip() == "all")

    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED

            # ------------------------------
            # Fetch existing profile
            # ------------------------------
            cursor.execute("SELECT * FROM syslog_profiles WHERE id=%s", (profile_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Profile not found")

            # ------------------------------
            # Enforce docker match
            # ------------------------------
            if not update_all and existing["docker_name"] != docker_name:
                raise HTTPException(status_code=403, detail="docker_name mismatch")

            # ------------------------------
            # Validate new name NOT duplicate
            # ------------------------------
            if p.name and p.name.strip():
                new_name = p.name.strip()

                cursor.execute(
                    """
                    SELECT id FROM syslog_profiles 
                    WHERE name=%s AND docker_name=%s AND id!=%s
                    """,
                    (new_name, existing["docker_name"], profile_id),
                )
                dup = cursor.fetchone()
                if dup:
                    raise HTTPException(
                        status_code=409,
                        detail="A profile with this name already exists in this docker",
                    )
            else:
                new_name = existing["name"]

            preserved_type = existing["type"]

            # type-based rules
            if preserved_type == "external":
                prio_json = fac_json = kws_json = None
            else:
                prio_json = (
                    json.dumps(p.priorities)
                    if p.priorities is not None
                    else existing["priorities"]
                )
                fac_json = (
                    json.dumps(p.facilities)
                    if p.facilities is not None
                    else existing["facilities"]
                )
                kws_json = (
                    json.dumps(p.keywords)
                    if p.keywords is not None
                    else existing["keywords"]
                )

            # ------------------------------
            # Perform UPDATE
            # ------------------------------
            cursor.execute(
                """
                UPDATE syslog_profiles
                SET name=%s, priorities=%s, facilities=%s, keywords=%s
                WHERE id=%s
                """,
                (new_name, prio_json, fac_json, kws_json, profile_id),
            )

            cnx.commit()
            cursor.close()

        asyncio.create_task(notify_profile_change(profile_id))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_profile failed")
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "updated",
        "id": profile_id,
        "mode": "all-dockers" if update_all else "single-docker",
    }


# ─────────────────────────────
# DELETE MULTIPLE / SINGLE PROFILES
# ─────────────────────────────
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/delete",
    status_code=200,
)
async def delete_multiple_profiles(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),

    # REQUIRED QUERY PARAMS
    search_key: str = Query("", description="Search profiles by name (prefix match)"),
    is_select_all: bool = Query(False),
    profile_type: str = Query("", description="Filter by internal|external"),

    # BODY: only profile_ids list
    body: dict = Body(default={}),
):
    """
    Enhanced deletion logic for profiles.

    SUPPORTED CASES:
    1. profile_ids[] ONLY → delete exactly those profiles.
    2. is_select_all=true → delete ALL profiles in docker_name (or all if docker='all').
    3. search_key + is_select_all=true → delete profiles whose name starts with search_key.
    4. profile_type + is_select_all=true → delete profiles filtered by type.
    5. profile_type + search_key + is_select_all=true → delete profiles matching both filters.
    """

    docker_clean = docker_name.lower().strip()
    docker_all = (docker_clean == "all")

    profile_ids = body.get("profile_ids", [])
    search_key = search_key.strip()
    profile_type = profile_type.strip().lower()

    deleted = []
    skipped = []

    # Allowed types
    valid_types = ("internal", "external")
    if profile_type and profile_type not in valid_types:
        raise HTTPException(status_code=422, detail="profile_type must be 'internal' or 'external'")

    # -------------------------------------------------------------------
    # CASE 1 — profile_ids[] provided & is_select_all = False
    # -------------------------------------------------------------------
    if profile_ids and not is_select_all:
        try:
            with get_db_connection() as cnx:
                cursor = cnx.cursor(buffered=True, dictionary=True)

                for pid in profile_ids:
                    cursor.execute(
                        "SELECT docker_name FROM syslog_profiles WHERE id=%s",
                        (pid,),
                    )
                    row = cursor.fetchone()

                    if not row:
                        skipped.append(pid)
                        continue

                    # docker validation (unless docker="all")
                    if not docker_all and row["docker_name"] != docker_name:
                        skipped.append(pid)
                        continue

                    # DELETE LINKED ENTRIES
                    cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (pid,))
                    cursor.execute("DELETE FROM syslog_integration WHERE profile_id=%s", (pid,))
                    cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (pid,))
                    deleted.append(pid)

                cnx.commit()
                cursor.close()

            #  Refresh cache for all deleted profiles
            for pid in deleted:
                asyncio.create_task(notify_profile_change(pid))

            return {
                "status": "success",
                "deleted_ids": deleted,
                "skipped_ids": skipped,
                "mode": "all-dockers" if docker_all else "single-docker",
            }

        except Exception as e:
            logger.exception("Bulk delete profiles failed")
            raise HTTPException(status_code=500, detail=str(e))

    # -------------------------------------------------------------------
    # CASE 2 — All OTHER scenarios require is_select_all = True
    # -------------------------------------------------------------------
    if not is_select_all:
        raise HTTPException(
            status_code=422,
            detail="Either provide profile_ids[] or set is_select_all=true",
        )

    # -------------------------------------------------------------------
    # BUILD FILTERS for search_key + profile_type
    # -------------------------------------------------------------------
    where_clauses = []
    params = []

    # docker filter unless docker=='all'
    if not docker_all:
        where_clauses.append("docker_name = %s")
        params.append(docker_name)

    # filter by type
    if profile_type:
        where_clauses.append("type = %s")
        params.append(profile_type)

    # prefix search
    if search_key:
        where_clauses.append("name LIKE %s")
        params.append(search_key + "%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # -------------------------------------------------------------------
    # FETCH profiles that match filters
    # -------------------------------------------------------------------
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)

        cursor.execute(
            f"SELECT id FROM syslog_profiles {where_sql}",
            tuple(params),
        )
        rows = cursor.fetchall()

        if not rows:
            cursor.close()
            return {
                "status": "success",
                "deleted_ids": [],
                "skipped_ids": [],
                "message": "No profiles matched filters",
            }

        ids_to_delete = [r["id"] for r in rows]

        # DELETE ALL MATCHED PROFILES
        for pid in ids_to_delete:
            cursor.execute("DELETE FROM syslog_profile_devices WHERE profile_id=%s", (pid,))
            cursor.execute("DELETE FROM syslog_integration WHERE profile_id=%s", (pid,))
            cursor.execute("DELETE FROM syslog_profiles WHERE id=%s", (pid,))
            deleted.append(pid)

        cnx.commit()
        cursor.close()

    #  Refresh cache for all deleted profiles (matched via filters)
    for pid in deleted:
        asyncio.create_task(notify_profile_change(pid))

    return {
        "status": "success",
        "deleted_ids": deleted,
        "skipped_ids": [],
        "mode": "all-dockers" if docker_all else "single-docker",
    }


# ─────────────────────────────
# GET ALL PROFILES  (with device_count)
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles",
    status_code=status.HTTP_200_OK,
)
def get_all_profiles(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_type: Optional[str] = Query(None, description="Single or comma-separated values"),
    search_key: Optional[str] = Query(None),
    page_no: Any = Query(1),
    page_size: Any = Query(50),
):
    """
    List profiles + device_count.
    - docker_name = exact match
    - docker_name = 'all' → return all dockers
    - profile_type supports multi-select
    - search by profile name
    """

    # Validate docker if not "all"
    if docker_name.lower() != "all":
        _validate_docker_name(docker_name)

    # Pagination checks
    try:
        page_no = int(page_no)
        page_size = int(page_size)
    except:
        raise HTTPException(status_code=422, detail="page and limit must be integers")

    if page_no < 1 or page_size < 1:
        raise HTTPException(status_code=422, detail="page and limit must be >= 1")

    offset = (page_no - 1) * page_size

    # WHERE clauses
    where_clauses = []
    params: List[Any] = []

    # 1. docker filter
    if docker_name.lower() != "all":
        where_clauses.append("p.docker_name = %s")
        params.append(docker_name)

    # 2. profile_type multi-select
    if profile_type:
        type_list = [t.strip().lower() for t in profile_type.split(",") if t.strip()]
        type_filters = " OR ".join(["LOWER(p.type) = %s" for _ in type_list])
        where_clauses.append(f"({type_filters})")
        params.extend(type_list)

    # 3. name search
    if search_key:
        where_clauses.append("LOWER(p.name) LIKE %s")
        params.append(f"%{search_key.lower().strip()}%")

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    # FINAL QUERY — WITH DEVICE COUNT
    sql = f"""
        SELECT 
            p.id,
            p.name,
            p.type AS profile_type,
            p.docker_name,
            p.priorities,
            p.facilities,
            p.keywords,
            COUNT(pd.device_id) AS device_count
        FROM syslog_profiles p
        LEFT JOIN syslog_profile_devices pd 
            ON pd.profile_id = p.id
        {where_sql}
        GROUP BY 
            p.id, p.name, p.type, p.docker_name, 
            p.priorities, p.facilities, p.keywords
        ORDER BY p.name ASC
        LIMIT %s OFFSET %s
    """

    with get_db_connection() as cnx:
        cursor = cnx.cursor(dictionary=True)
        cursor.execute(sql, tuple(params + [page_size, offset]))
        rows = cursor.fetchall() or []
        cursor.close()

    return {
        "total": len(rows),
        "page_no": page_no,
        "page_size": page_size,
        "items": rows,
    }


# ─────────────────────────────
# GET SINGLE PROFILE
# ─────────────────────────────
@router.get(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}",
    status_code=status.HTTP_200_OK,
)
async def get_profile_device_details(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_id: str = Path(...),
    search_key: str = Query(None),
    page_no: int = Query(1),
    page_size: int = Query(100),
):
    _validate_docker_name(docker_name)

    # --------------------------------------------------------------
    # 1. Fetch PROFILE & DEVICE IDs   (buffered=True FIX APPLIED)
    # --------------------------------------------------------------
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIX

        cursor.execute(
            "SELECT id, docker_name FROM syslog_profiles WHERE id=%s",
            (profile_id,),
        )
        profile_row = cursor.fetchone()
        if not profile_row:
            cursor.close()
            raise HTTPException(status_code=404, detail="Profile not found")

        cursor.execute(
            "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
            (profile_id,),
        )
        device_rows = cursor.fetchall()
        cursor.close()

    asset_count = len(device_rows)
    device_ids = [d["device_id"] for d in device_rows]

    # --------------------------------------------------------------
    # 2. Docker validation (skip if ALL)
    # --------------------------------------------------------------
    if docker_name.lower().strip() != "all":
        if profile_row["docker_name"] != docker_name:
            raise HTTPException(status_code=403, detail="docker_name mismatch")

    # --------------------------------------------------------------
    # 3. Prepare default response
    # --------------------------------------------------------------
    device_details_response = {
        "total": asset_count,   # total devices assigned to profile
        "page_no": page_no,
        "page_size": page_size,
        "items": [],
        "asset_count": asset_count,
    }

    # --------------------------------------------------------------
    # 4. If no devices assigned → return empty
    # --------------------------------------------------------------
    if not device_ids:
        return device_details_response

    # If frontend sends "", we send "" to SpringBoot exactly.
    search_param = search_key

    # --------------------------------------------------------------
    # 5. Call SpringBoot for device details
    # --------------------------------------------------------------
    try:
        spring_url = (
            f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"
            f"/docker/{docker_name}/getdevicedetailsbyids"
        )

        async with httpx.AsyncClient(timeout=10) as client:
            spring_resp = await client.post(
                spring_url,
                json=device_ids,
                params={
                    "page_no": page_no,
                    "page_size": page_size,
                    "search_key": search_param,
                },
            )

        data = spring_resp.json()

        # ----------------------------------------------------------
        # NEW FORMAT (LIST): SpringBoot already paginated → DO NOT re-paginate
        # ----------------------------------------------------------
        if isinstance(data, list):
            device_details_response.update({
                "items": data,
                "total": asset_count,   # keep original total
            })

        # ----------------------------------------------------------
        # OLD FORMAT: backend sends dict with items + total
        # ----------------------------------------------------------
        elif isinstance(data, dict) and "items" in data:
            device_details_response = data
            device_details_response["asset_count"] = asset_count

        else:
            device_details_response["items"] = []

    except Exception as e:
        logger.error("SpringBoot call failed: %s", e)
        return device_details_response

    return device_details_response


#===================================================
# DELETE DEVICE FOR A PROFILE  new added endpoint
# ==================================================
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/devices/delete",
    status_code=200,
)
async def delete_profile_devices(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_id: str = Path(...),

    # Query params (NOT inside body)
    is_select_all: bool = Query(False, description="If true, delete all or filtered"),
    search_key: Optional[str] = Query(None, description="Filter keyword for SpringBoot filtering"),

    # Body expecting: { "device_ids": [...] }
    body: DeviceDeleteBody = Body(default=DeviceDeleteBody()),
):
    """
    Delete devices from a profile.
    """

    device_ids_input = [d.strip() for d in (body.device_ids or []) if d and d.strip()]

    # -------------------------------------------
    # Validate profile exists + get docker_name
    # -------------------------------------------
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED
        cursor.execute(
            "SELECT docker_name FROM syslog_profiles WHERE id=%s",
            (profile_id,),
        )
        row = cursor.fetchone()
        cursor.close()

    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile_docker = row["docker_name"]

    if docker_name.lower().strip() != "all":
        if profile_docker != docker_name:
            raise HTTPException(status_code=403, detail="docker_name mismatch")

    # -------------------------------------------
    # Fetch current devices in profile
    # -------------------------------------------
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED
        cursor.execute(
            "SELECT device_id FROM syslog_profile_devices WHERE profile_id=%s",
            (profile_id,),
        )
        rows = cursor.fetchall()
        cursor.close()

    current_devices = {r["device_id"] for r in rows}

    case = None
    deleted = set()
    skipped = set()

    # ---------------- CASES --------------------
    if device_ids_input:
        case = "device_ids_only"
        requested = set(device_ids_input)
        deleted = current_devices & requested
        skipped = requested - current_devices

    elif is_select_all and not search_key:
        case = "select_all"
        deleted = current_devices

    elif is_select_all and search_key:
        case = "search_and_select_all"

        spring_url = (
            f"http://{settings.SPRINGBOOT_HOST}:{settings.SPRINGBOOT_PORT}"
            f"/docker/{profile_docker}/getalldeviceids"
        )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    spring_url,
                    params={"search_key": search_key, "is_select_all": True},
                )
            spring_ids = set(resp.json())

        except Exception as e:
            return {
                "status": "springboot_error",
                "details": str(e),
            }

        deleted = current_devices & spring_ids
        skipped = spring_ids - current_devices

    else:
        case = "none"
        deleted = set()
        skipped = set()

    # -------------------------------------------
    # Apply deletions
    # -------------------------------------------
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor(buffered=True)  # ★ FIXED

            for dev in deleted:
                cursor.execute(
                    """
                    DELETE FROM syslog_profile_devices
                    WHERE profile_id=%s AND device_id=%s
                    """,
                    (profile_id, dev),
                )

            cnx.commit()
            cursor.close()

    except Exception as e:
        logger.exception("delete_profile_devices failed")
        raise HTTPException(status_code=500, detail=str(e))

    # 🔁 Refresh cache after device deletions
    asyncio.create_task(notify_profile_change(profile_id))

    return {
        "status": "completed",
        "case": case,
        "deleted_count": len(deleted),
        "deleted_ids": sorted(deleted),
        "skipped_count": len(skipped),
        "skipped_ids": sorted(skipped),
        "profile_id": profile_id,
    }


#===================================================
# ADD DEVICE FOR A PROFILE    new added endpoint
# ==================================================
@router.post(
    "/user/{username}/vdms/{vdmsid}/docker/{docker_name}/syslog_profiles/{profile_id}/devices/add",
    status_code=200,
)
async def add_devices_to_profile(
    username: str = Path(...),
    vdmsid: str = Path(...),
    docker_name: str = Path(...),
    profile_id: str = Path(...),
    body: Dict[str, List[str]] = Body(..., description="Body with device_ids list"),
):
    """
    Add device_ids to a profile.
    - If docker_name == 'all', docker validation is ignored.
    - Otherwise ensure profile belongs to that docker.
    """

    # Extract device_ids
    device_ids = body.get("device_ids")
    if not device_ids or not isinstance(device_ids, list):
        raise HTTPException(status_code=422, detail="device_ids must be a non-empty list")

    # 1 Validate profile_id exists & belongs to docker_name (unless docker='all')
    with get_db_connection() as cnx:
        cursor = cnx.cursor(buffered=True, dictionary=True)  # ★ FIXED

        cursor.execute(
            "SELECT id, docker_name FROM syslog_profiles WHERE id=%s",
            (profile_id,)
        )
        profile = cursor.fetchone()

        if not profile:
            cursor.close()
            raise HTTPException(status_code=404, detail="Profile not found")

        # Validate docker_name except when docker_name=='all'
        if docker_name.lower().strip() != "all":
            if profile["docker_name"] != docker_name:
                cursor.close()
                raise HTTPException(
                    status_code=403,
                    detail="docker_name mismatch with profile"
                )

        # 2 Add device_ids (INSERT IGNORE avoids duplicates)
        added = []
        skipped = []

        for dev_id in device_ids:
            try:
                rid = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT IGNORE INTO syslog_profile_devices (id, profile_id, device_id)
                    VALUES (%s, %s, %s)
                    """,
                    (rid, profile_id, dev_id.strip())
                )

                # INSERT IGNORE → rowcount = 1 if inserted, 0 if duplicate
                if cursor.rowcount > 0:
                    added.append(dev_id)
                else:
                    skipped.append(dev_id)

            except Exception:
                skipped.append(dev_id)

        cnx.commit()
        cursor.close()

    #  Refresh cache after adding devices
    asyncio.create_task(notify_profile_change(profile_id))

    # 3 Return result summary
    return {
        "status": "success",
        "profile_id": profile_id,
        "docker_name_validated": (docker_name.lower().strip() != "all"),
        "added": added,
        "skipped_already_existing": skipped,
        "total_added": len(added),
        "total_skipped": len(skipped),
    }
