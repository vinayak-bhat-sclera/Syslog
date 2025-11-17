# app/tests/test_profiles.py

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app   # must import the app that includes this router


client = TestClient(app)


# ------------------------------------------------------
# Utility: Patch DB connection
# ------------------------------------------------------
def patch_db(cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__.return_value = fake_db
    ctx.__exit__.return_value = False

    return patch("app.routers.profiles.get_db_connection", return_value=ctx)


# ------------------------------------------------------
# CREATE PROFILE
# ------------------------------------------------------
def test_create_profile_success():
    cursor = MagicMock()

    # simulate successful INSERT
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []

    body = {
        "name": "TestProfile",
        "type": "internal",
        "priorities": [1, 2],
        "facilities": [3, 4],
        "keywords": ["Error", "Warn"],
        "device_ids": ["devA", "devB"],
    }

    with patch_db(cursor), \
         patch("asyncio.create_task", MagicMock()):

        res = client.post(
            "/user/u1/vdms/v1/docker/DockX/syslog_profiles/create",
            json=body,
        )

    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "created"
    assert "id" in data


def test_create_profile_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("DB failed")

    body = {"name": "Err", "type": "internal"}

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.post(
            "/user/u1/vdms/v1/docker/DockY/syslog_profiles/create",
            json=body,
        )

    assert res.status_code == 400
    assert "DB failed" in res.json()["detail"]


# ------------------------------------------------------
# UPDATE PROFILE
# ------------------------------------------------------
def test_update_profile_success():
    cursor = MagicMock()

    cursor.fetchone.side_effect = [
        {"id": "p1", "name": "Old", "type": "internal",
         "docker_name": "DockZ", "priorities": None,
         "facilities": None, "keywords": None},
    ]

    body = {"name": "UpdatedProfile"}

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.put(
            "/user/u1/vdms/v1/docker/DockZ/syslog_profiles/p1/update",
            json=body,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "updated"
    assert data["id"] == "p1"


def test_update_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.put(
            "/user/u1/vdms/v1/docker/DockZ/syslog_profiles/p404/update",
            json={"name": "X"},
        )

    assert res.status_code == 404


def test_update_profile_docker_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "RealDock", "type": "internal"}

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.put(
            "/user/u1/vdms/v1/docker/WrongDock/syslog_profiles/p1/update",
            json={"name": "X"},
        )

    assert res.status_code == 403


# ------------------------------------------------------
# DELETE PROFILE
# ------------------------------------------------------
def test_delete_profile_success():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "Dock1"}

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.delete(
            "/user/u1/vdms/v1/docker/Dock1/syslog_profiles/p1/delete"
        )

    assert res.status_code == 200
    assert res.json()["status"] == "deleted"


def test_delete_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.delete(
            "/user/u1/vdms/v1/docker/DockX/syslog_profiles/p404/delete"
        )

    assert res.status_code == 404


def test_delete_profile_docker_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "RealDock"}

    with patch_db(cursor), patch("asyncio.create_task", MagicMock()):
        res = client.delete(
            "/user/u1/vdms/v1/docker/OtherDock/syslog_profiles/p1/delete"
        )

    assert res.status_code == 403


# ------------------------------------------------------
# GET ALL PROFILES
# ------------------------------------------------------
def test_get_all_profiles_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"id": "p1", "name": "Alpha", "profile_type": "internal",
         "docker_name": "DockA", "priorities": None,
         "facilities": None, "keywords": None}
    ]

    with patch_db(cursor):
        res = client.get("/user/u1/vdms/v1/docker/DockA/syslog_profiles?page=1&limit=10")

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "p1"


def test_get_all_profiles_invalid_page():
    res = client.get("/user/u1/vdms/v1/docker/DockA/syslog_profiles?page=abc&limit=10")
    assert res.status_code == 422


# ------------------------------------------------------
# GET ONE PROFILE
# ------------------------------------------------------
def test_get_profile_success():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": "p1",
        "name": "MyProfile",
        "profile_type": "internal",
        "docker_name": "DockQ",
        "priorities": None,
        "facilities": None,
        "keywords": None,
    }

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockQ/syslog_profiles/p1"
        )

    assert res.status_code == 200
    assert res.json()["id"] == "p1"


def test_get_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockQ/syslog_profiles/p404"
        )

    assert res.status_code == 404


def test_get_profile_docker_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": "p1",
        "name": "X",
        "profile_type": "internal",
        "docker_name": "RealDock",
        "priorities": None,
        "facilities": None,
        "keywords": None,
    }

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/WrongDock/syslog_profiles/p1"
        )

    assert res.status_code == 403
