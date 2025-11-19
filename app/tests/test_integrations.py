# app/tests/test_integrations.py

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.main import app

client = TestClient(app)


# -------------------------------------------------------------------
# DB PATCH
# -------------------------------------------------------------------
def patch_db(cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__.return_value = fake_db
    ctx.__exit__.return_value = False

    return patch("app.routers.integrations.get_db_connection", return_value=ctx)


# -------------------------------------------------------------------
# validate_docker_name_for_profile PATCH
# -------------------------------------------------------------------
def patch_validate(ok=True):
    """Return a context manager that patches validation to succeed or fail."""
    if ok:
        return patch("app.routers.integrations._validate_docker_name_for_profile", MagicMock())

    def fail(*args, **kwargs):
        raise HTTPException(status_code=403, detail="docker mismatch")

    return patch("app.routers.integrations._validate_docker_name_for_profile", side_effect=fail)


# -------------------------------------------------------------------
# CREATE INTEGRATION
# -------------------------------------------------------------------
def test_create_integration_success():
    cursor = MagicMock()

    with patch_db(cursor), patch_validate(ok=True):
        body = {
            "profile_id": "p1",
            "destination_name": "Dest",
            "ip_address": "1.1.1.1",
            "port": 514,
            "auth_token": "abc",
        }
        res = client.post("/user/u1/vdms/v1/docker/Dock/syslog_integrations/create", json=body)

    assert res.status_code == 201
    assert res.json()["status"] == "created"


def test_create_integration_profile_mismatch():
    cursor = MagicMock()

    with patch_db(cursor), patch_validate(ok=False):
        body = {
            "profile_id": "p1",
            "destination_name": "Dest",
            "ip_address": "1.1.1.1",
            "port": 514,
            "auth_token": "abc",
        }
        res = client.post("/user/u1/vdms/v1/docker/Dock/syslog_integrations/create", json=body)

    assert res.status_code == 403


def test_create_integration_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("db explode")

    with patch_db(cursor), patch_validate(ok=True):
        body = {
            "profile_id": "p1",
            "destination_name": "Dest",
            "ip_address": "1.1.1.1",
            "port": 514,
            "auth_token": "abc",
        }
        res = client.post("/user/u1/vdms/v1/docker/Dock/syslog_integrations/create", json=body)

    assert res.status_code == 400
    assert "db explode" in res.json()["detail"]


# -------------------------------------------------------------------
# UPDATE INTEGRATION
# -------------------------------------------------------------------
def test_update_integration_success():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"profile_id": "old_p"}

    with patch_db(cursor), patch_validate(ok=True):
        body = {
            "profile_id": "new_p",
            "destination_name": "Updated",
            "ip_address": "2.2.2.2",
            "port": 600,
            "auth_token": "xyz",
        }
        res = client.put(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i123/update",
            json=body
        )

    assert res.status_code == 200
    assert res.json()["status"] == "updated"


def test_update_integration_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor):
        body = {
            "profile_id": "p1",
            "destination_name": "X",
            "ip_address": "1.1.1.1",
            "port": 100,
            "auth_token": "abc",
        }
        res = client.put(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i999/update",
            json=body,
        )

    assert res.status_code == 404


def test_update_integration_old_profile_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"profile_id": "old_p"}

    with patch_db(cursor), patch_validate(ok=False):
        body = {
            "profile_id": "new_p",
            "destination_name": "X",
            "ip_address": "1.1.1.1",
            "port": 100,
            "auth_token": "abc",
        }
        res = client.put(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i123/update",
            json=body,
        )

    assert res.status_code == 403


def test_update_integration_new_profile_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"profile_id": "old_p"}

    # simulate first validate OK (old profile), second fails (new profile)
    validate = MagicMock()
    validate.side_effect = [
        None,
        HTTPException(status_code=403, detail="docker mismatch")
    ]

    with patch_db(cursor), patch("app.routers.integrations._validate_docker_name_for_profile", validate):
        body = {
            "profile_id": "new_p",
            "destination_name": "X",
            "ip_address": "1.1.1.1",
            "port": 100,
            "auth_token": "abc",
        }
        res = client.put(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i123/update",
            json=body,
        )

    assert res.status_code == 403


def test_update_integration_db_error():
    cursor = MagicMock()

    # First SELECT should succeed → return existing row
    cursor.fetchone.return_value = {"profile_id": "old_p"}

    # First execute() = SELECT (OK)
    # Second execute() = UPDATE (FAILS)
    cursor.execute.side_effect = [
        None,                      # SELECT ok
        Exception("update boom"),  # UPDATE fails
    ]

    with patch_db(cursor), patch_validate(ok=True):
        body = {
            "profile_id": "new_p",
            "destination_name": "X",
            "ip_address": "1.1.1.1",
            "port": 200,
            "auth_token": "abc",
        }

        res = client.put(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i123/update",
            json=body,
        )

    assert res.status_code == 400
    assert "update boom" in res.json()["detail"]



# -------------------------------------------------------------------
# DELETE MULTIPLE
# -------------------------------------------------------------------
def test_delete_multiple_integrations_success():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"profile_id": "p1"},
        {"profile_id": "p1"},
    ]

    with patch_db(cursor), patch_validate(ok=True):
        res = client.post(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/delete",
            json={"integration_ids": ["id1", "id2"]}
        )

    assert res.status_code == 200
    assert res.json()["deleted"] == ["id1", "id2"]


def test_delete_multiple_integrations_some_missing():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        None,
        {"profile_id": "p2"},
    ]

    with patch_db(cursor), patch_validate(ok=True):
        res = client.post(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/delete",
            json={"integration_ids": ["id1", "id2"]}
        )

    assert res.status_code == 200
    assert res.json()["not_found"] == ["id1"]


def test_delete_multiple_integrations_mismatch():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"profile_id": "p1"},
        {"profile_id": "p2"},
    ]

    validate = MagicMock()
    validate.side_effect = [
        None,
        HTTPException(status_code=403, detail="docker mismatch"),
    ]

    with patch_db(cursor), patch("app.routers.integrations._validate_docker_name_for_profile", validate):
        res = client.post(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/delete",
            json={"integration_ids": ["id1", "id2"]}
        )

    assert res.status_code == 200
    assert res.json()["docker_mismatch"] == ["id2"]


def test_delete_multiple_integrations_invalid_body():
    res = client.post(
        "/user/u1/vdms/v1/docker/Dock/syslog_integrations/delete",
        json={"integration_ids": []}
    )
    assert res.status_code == 422


# -------------------------------------------------------------------
# DELETE ALL INTEGRATIONS
# -------------------------------------------------------------------
def test_delete_all_integrations_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [("p1",), ("p2",)]
    cursor.rowcount = 5

    with patch_db(cursor):
        res = client.delete(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/delete/all"
        )

    assert res.status_code == 200
    assert res.json()["deleted"] == 5


def test_delete_all_integrations_none_found():
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    with patch_db(cursor):
        res = client.delete(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/delete/all"
        )

    assert res.status_code == 200
    assert res.json()["detail"] == "No integrations found for docker_name"


# -------------------------------------------------------------------
# GET ALL
# -------------------------------------------------------------------
def test_get_all_integrations_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"id": "i1", "profile_id": "p1", "profile_name": "Alpha",
         "profile_type": "internal", "destination_name": "Dst1",
         "ip_address": "1.1.1.1", "port": 10, "auth_token": "t",
         "docker_name": "Dock"}
    ]

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations?page=1&limit=10"
        )

    assert res.status_code == 200
    assert res.json()["total"] == 1


def test_get_all_integrations_invalid_page():
    res = client.get(
        "/user/u1/vdms/v1/docker/Dock/syslog_integrations?page=x&limit=10"
    )
    assert res.status_code == 422


# -------------------------------------------------------------------
# GET ONE
# -------------------------------------------------------------------
def test_get_integration_success():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": "i1",
        "profile_id": "p1",
        "profile_name": "Alpha",
        "profile_type": "internal",
        "docker_name": "Dock",
    }

    with patch_db(cursor), patch_validate(ok=True):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i1"
        )

    assert res.status_code == 200
    assert res.json()["id"] == "i1"


def test_get_integration_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/unknown"
        )

    assert res.status_code == 404


def test_get_integration_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": "i1",
        "profile_id": "p1",
    }

    def fail(*args, **kwargs):
        raise HTTPException(status_code=403, detail="mismatch")

    with patch_db(cursor), patch("app.routers.integrations._validate_docker_name_for_profile", fail):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_integrations/i1"
        )

    assert res.status_code == 403
