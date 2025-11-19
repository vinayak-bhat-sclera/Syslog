# app/tests/test_profiles.py

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def patch_db(cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__.return_value = fake_db
    ctx.__exit__.return_value = False

    return patch("app.routers.profiles.get_db_connection", return_value=ctx)


# -----------------------------
# CREATE PROFILE
# -----------------------------
def test_create_profile_success():
    cursor = MagicMock()
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

    with (
        patch_db(cursor),
        patch("app.routers.profiles.clear_cache", MagicMock()),
        patch("app.routers.profiles.notify_profile_change", MagicMock()),
    ):
        res = client.post(
            "/user/u1/vdms/v1/docker/DockX/syslog_profiles/create",
            json=body,
        )

    assert res.status_code == 201
    assert res.json()["status"] == "created"


def test_create_profile_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("DB failed")

    with (
        patch_db(cursor),
        patch("app.routers.profiles.clear_cache", MagicMock()),
        patch("app.routers.profiles.notify_profile_change", MagicMock()),
    ):
        res = client.post(
            "/user/u1/vdms/v1/docker/DockY/syslog_profiles/create",
            json={"name": "Err", "type": "internal"},
        )

    assert res.status_code == 400


# -----------------------------
# UPDATE PROFILE
# -----------------------------
def test_update_profile_success():
    cursor = MagicMock()

    cursor.fetchone.side_effect = [
        {
            "id": "p1",
            "name": "Old",
            "type": "internal",
            "docker_name": "DockZ",
            "priorities": None,
            "facilities": None,
            "keywords": None
        }
    ]

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", AsyncMock()),
    ):
        res = client.put(
            "/user/u1/vdms/v1/docker/DockZ/syslog_profiles/p1/update",
            json={"name": "UpdatedProfile"},
        )

    assert res.status_code == 200
    assert res.json()["id"] == "p1"



def test_update_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", MagicMock()),
    ):
        res = client.put(
            "/user/u1/vdms/v1/docker/DockZ/syslog_profiles/p404/update",
            json={"name": "X"},
        )

    assert res.status_code == 404


def test_update_profile_docker_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "RealDock", "type": "internal"}

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", MagicMock()),
    ):
        res = client.put(
            "/user/u1/vdms/v1/docker/WrongDock/syslog_profiles/p1/update",
            json={"name": "X"},
        )

    assert res.status_code == 403


# ------------------------------------------------------
# BULK DELETE PROFILES (POST)
# ------------------------------------------------------
def test_bulk_delete_profiles_success():
    cursor = MagicMock()

    # First fetch → profile exists + docker matches
    cursor.fetchone.side_effect = [
        {"docker_name": "Dock1"},  # profile A
        {"docker_name": "Dock1"},  # profile B
    ]

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", AsyncMock()),
    ):
        res = client.post(
            "/user/u1/vdms/v1/docker/Dock1/syslog_profiles/delete",
            json={"profile_ids": ["p1", "p2"]},
        )

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert set(data["deleted_ids"]) == {"p1", "p2"}
    assert data["skipped_ids"] == []


def test_bulk_delete_profiles_some_missing():
    cursor = MagicMock()

    # p1 exists, p2 does not exist
    cursor.fetchone.side_effect = [
        {"docker_name": "Dock1"},
        None,  # missing profile p2
    ]

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", AsyncMock()),
    ):
        res = client.post(
            "/user/u1/vdms/v1/docker/Dock1/syslog_profiles/delete",
            json={"profile_ids": ["p1", "p2"]},
        )

    assert res.status_code == 200
    data = res.json()

    assert data["deleted_ids"] == ["p1"]
    assert data["skipped_ids"] == ["p2"]


def test_bulk_delete_profiles_docker_mismatch():
    cursor = MagicMock()

    # profile found but wrong docker_name
    cursor.fetchone.side_effect = [
        {"docker_name": "OtherDock"},  # mismatch
    ]

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", AsyncMock()),
    ):
        res = client.post(
            "/user/u1/vdms/v1/docker/DockX/syslog_profiles/delete",
            json={"profile_ids": ["pX"]},
        )

    assert res.status_code == 200
    data = res.json()

    assert data["deleted_ids"] == []
    assert data["skipped_ids"] == ["pX"]


def test_bulk_delete_profiles_empty_list():
    res = client.post(
        "/user/u1/vdms/v1/docker/DockX/syslog_profiles/delete",
        json={"profile_ids": []},
    )
    assert res.status_code == 422
    assert "profile_ids" in res.json()["detail"]


# ------------------------------------------------------
# DELETE ALL PROFILES FOR A DOCKER (DELETE)
# ------------------------------------------------------
def test_delete_all_profiles_success():
    cursor = MagicMock()

    cursor.fetchall.return_value = [
        {"id": "p1"},
        {"id": "p2"},
    ]  # profiles under DockX

    with (
        patch_db(cursor),
        patch("app.routers.profiles.notify_profile_change", AsyncMock()),
    ):
        res = client.delete(
            "/user/u1/vdms/v1/docker/DockX/syslog_profiles/delete/all"
        )

    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "deleted_all"
    assert set(data["deleted_ids"]) == {"p1", "p2"}
    assert data["count"] == 2


def test_delete_all_profiles_db_error():
    cursor = MagicMock()
    cursor.fetchall.side_effect = Exception("DB exploded")

    with patch_db(cursor):
        res = client.delete(
            "/user/u1/vdms/v1/docker/DockX/syslog_profiles/delete/all"
        )

    assert res.status_code == 400
    assert "DB exploded" in res.json()["detail"]

    



# -----------------------------
# GET ALL
# -----------------------------
def test_get_all_profiles_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "id": "p1",
            "name": "Alpha",
            "profile_type": "internal",
            "docker_name": "DockA",
            "priorities": None,
            "facilities": None,
            "keywords": None,
        }
    ]

    with patch_db(cursor):
        res = client.get("/user/u1/vdms/v1/docker/DockA/syslog_profiles?page=1&limit=10")

    assert res.status_code == 200


def test_get_all_profiles_invalid_page():
    res = client.get("/user/u1/vdms/v1/docker/DockA/syslog_profiles?page=abc&limit=10")
    assert res.status_code == 422


# -----------------------------
# GET ONE
# -----------------------------
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
        res = client.get("/user/u1/vdms/v1/docker/DockQ/syslog_profiles/p1")

    assert res.status_code == 200


def test_get_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor):
        res = client.get("/user/u1/vdms/v1/docker/DockQ/syslog_profiles/p404")

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
        res = client.get("/user/u1/vdms/v1/docker/WrongDock/syslog_profiles/p1")

    assert res.status_code == 403
