# app/tests/test_profile_devices.py

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app   # assumes routers are included here

client = TestClient(app)


# ---------------------------------------------------------
# Utility: mock DB connection
# ---------------------------------------------------------
def mock_db(cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__.return_value = fake_db
    ctx.__exit__.return_value = False
    return ctx


# =========================================================
# TEST: get_all_device_ids
# =========================================================

def test_get_all_device_ids_success():
    cursor = MagicMock()
    cursor.fetchall.return_value = [( "dev1", ), ("dev2", )]

    with patch("app.routers.profile_devices.get_db_connection", return_value=mock_db(cursor)):
        resp = client.get(
            "/user/u/vdms/v/docker/dock/syslog_devices?page=1&limit=2"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["device_ids"] == ["dev1", "dev2"]
    assert data["page"] == 1
    assert data["limit"] == 2


def test_get_all_device_ids_invalid_page_limit():
    resp = client.get("/user/u/vdms/v/docker/dock/syslog_devices?page=x&limit=y")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "page and limit must be integers"


def test_get_all_device_ids_page_limit_lt1():
    resp = client.get("/user/u/vdms/v/docker/dock/syslog_devices?page=0&limit=10")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "page and limit must be >= 1"


def test_get_all_device_ids_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("DB failed")

    with patch("app.routers.profile_devices.get_db_connection", return_value=mock_db(cursor)):
        resp = client.get("/user/u/vdms/v/docker/dock/syslog_devices")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "DB error fetching device IDs"


# =========================================================
# TEST: get_devices_by_profile
# =========================================================

def test_get_devices_by_profile_success():
    cursor = MagicMock()

    # First query: validate profile docker_name
    cursor.fetchone.side_effect = [
        {"docker_name": "dock"},  # profile match
    ]

    # Second query: return devices
    cursor.fetchall.return_value = [
        {"device_id": "devA"},
        {"device_id": "devB"},
    ]

    with patch("app.routers.profile_devices.get_db_connection", return_value=mock_db(cursor)):
        resp = client.get("/user/u/vdms/v/docker/dock/syslog_profile_devices/p1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["device_ids"] == ["devA", "devB"]
    assert data["count"] == 2


def test_get_devices_by_profile_not_found():
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # profile missing

    with patch("app.routers.profile_devices.get_db_connection", return_value=mock_db(cursor)):
        resp = client.get("/user/u/vdms/v/docker/dock/syslog_profile_devices/abc")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Profile not found"


def test_get_devices_by_profile_docker_mismatch():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "other_dock"}

    with patch("app.routers.profile_devices.get_db_connection", return_value=mock_db(cursor)):
        resp = client.get("/user/u/vdms/v/docker/mydock/syslog_profile_devices/xyz")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "docker_name mismatch"


def test_get_devices_by_profile_db_error():
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Boom!")

    with patch("app.routers.profile_devices.get_db_connection", return_value=mock_db(cursor)):
        resp = client.get("/user/u/vdms/v/docker/dock/syslog_profile_devices/p1")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "DB error fetching devices"
