# app/tests/test_incidents.py

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from fastapi import HTTPException

client = TestClient(app)

# ------------------------------------------------------
# Utility: Patch DB Connection
# ------------------------------------------------------
def patch_db(cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__.return_value = fake_db
    ctx.__exit__.return_value = False

    return patch("app.routers.incidents.get_db_connection", return_value=ctx)


# ------------------------------------------------------
# DEVICE → DOCKER VALIDATION
# ------------------------------------------------------

def test_incidents_device_not_found():
    cursor = MagicMock()

    # device_id not found
    cursor.fetchone.return_value = None

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_incidents/dev1/incidents"
        )

    assert res.status_code == 404
    assert "Device not found" in res.json()["detail"]


def test_incidents_device_docker_mismatch():
    cursor = MagicMock()

    # Found device but wrong docker
    cursor.fetchone.return_value = {"docker_name": "RealDock"}

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/WrongDock/syslog_incidents/dev1/incidents"
        )

    assert res.status_code == 403
    assert "docker_name mismatch" in res.json()["detail"]


def test_incidents_device_db_error():
    cursor = MagicMock()

    # Validation crashes DB
    cursor.fetchone.side_effect = Exception("boom!")

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_incidents/dev1/incidents"
        )

    assert res.status_code == 500
    assert "Error validating docker_name" in res.json()["detail"]


# ------------------------------------------------------
# PAGINATION + FILTER VALIDATION
# ------------------------------------------------------

def test_incidents_invalid_page():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "Dock"}  # validation ok

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_incidents/dev1/incidents?page=abc"
        )

    assert res.status_code == 422
    assert "page and limit must be integers" in res.json()["detail"]


def test_incidents_invalid_priority_filter():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"docker_name": "Dock"}  # validation ok

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_incidents/dev1/incidents?priority_code=xxx"
        )

    assert res.status_code == 422
    assert "must be integers" in res.json()["detail"]


# ------------------------------------------------------
# SUCCESS CASE
# ------------------------------------------------------

def test_incidents_success():
    cursor = MagicMock()

    # 1st call: validate device → docker
    cursor.fetchone.side_effect = [
        {"docker_name": "Dock"},               # validation ok
        {"cnt": 2},                            # COUNT(*)
    ]

    cursor.fetchall.side_effect = [
        [                                       # rows returned
            {
                "id": "i1",
                "device_id": "dev1",
                "profile_id": "p1",
                "priority_code": 3,
                "facility_code": 4,
                "message": "hi",
                "timestamp": "2020-01-01",
            },
            {
                "id": "i2",
                "device_id": "dev1",
                "profile_id": "p1",
                "priority_code": 5,
                "facility_code": 6,
                "message": "hello",
                "timestamp": "2020-01-02",
            },
        ]
    ]

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_incidents/dev1/incidents?page=1&limit=10"
        )

    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["total"] == 2
    assert data["count"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["device_id"] == "dev1"

    # label expansion happens (unknown is fine for test)
    assert "priority_label" in data["items"][0]
    assert "facility_label" in data["items"][0]


# ------------------------------------------------------
# DB ERROR DURING INCIDENT FETCH
# ------------------------------------------------------

def test_incidents_db_failure_on_fetch():
    cursor = MagicMock()

    # Validation OK
    cursor.fetchone.return_value = {"docker_name": "Dock"}

    # Later SQL query fails
    cursor.execute.side_effect = [
        None,  # validation query ok
        Exception("fetch fail")  # fail on COUNT or SELECT
    ]

    with patch_db(cursor):
        res = client.get(
            "/user/u1/vdms/v1/docker/Dock/syslog_incidents/dev1/incidents"
        )

    assert res.status_code == 500
    assert "DB error fetching syslog incidents" in res.json()["detail"]
