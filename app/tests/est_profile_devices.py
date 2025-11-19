# app/tests/test_profile_devices.py

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# -------------------------------------------------------------------
# DB PATCH UTILITY
# -------------------------------------------------------------------
def patch_db(cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__.return_value = fake_db
    ctx.__exit__.return_value = False

    return patch("app.routers.profile_devices.get_db_connection", return_value=ctx)


# -------------------------------------------------------------------
# GET ALL DEVICE IDS
# -------------------------------------------------------------------
def test_get_all_device_ids_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        ("dev1",),
        ("dev2",),
    ]

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_devices?page=1&limit=10"
        )

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["device_ids"] == ["dev1", "dev2"]


def test_get_all_device_ids_invalid_page_limit():
    res = client.get(
        "/user/u1/vdms/v1/docker/DockA/syslog_devices?page=abc&limit=10"
    )
    assert res.status_code == 422


def test_get_all_device_ids_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("db broke")

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_devices?page=1&limit=10"
        )

    assert res.status_code == 400
    assert "DB error" in res.json()["detail"]


# -------------------------------------------------------------------
# GET DEVICES BY PROFILE
# -------------------------------------------------------------------
def test_get_devices_by_profile_success():
    cursor = MagicMock()

    cursor.fetchone.return_value = {"docker_name": "DockA"}
    cursor.fetchall.return_value = [
        {"device_id": "devX"},
        {"device_id": "devY"},
    ]

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_profile_devices/p123"
        )

    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert data["device_ids"] == ["devX", "devY"]


def test_get_devices_by_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_profile_devices/p123"
        )

    assert res.status_code == 404


def test_get_devices_by_profile_docker_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "RealDock"}

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/WrongDock/syslog_profile_devices/p777"
        )

    assert res.status_code == 403


def test_get_devices_by_profile_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("explode!")

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_profile_devices/p123"
        )

    assert res.status_code == 400
    assert "DB error" in res.json()["detail"]


# -------------------------------------------------------------------
# GET DEVICE IDS WITH PROFILE TYPES
# -------------------------------------------------------------------
def test_get_device_idtypes_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"device_id": "dev1", "profile_type": "internal"},
        {"device_id": "dev2", "profile_type": "external"},
    ]

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_device_idtypes?page=1&limit=10"
        )

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["items"][0]["device_id"] == "dev1"


def test_get_device_idtypes_invalid_page_limit():
    res = client.get(
        "/user/u1/vdms/v1/docker/DockA/syslog_device_idtypes?page=x&limit=1"
    )
    assert res.status_code == 422


def test_get_device_idtypes_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("sql fail")

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/DockA/syslog_device_idtypes?page=1&limit=10"
        )

    assert res.status_code == 400
    assert "DB error" in res.json()["detail"]
