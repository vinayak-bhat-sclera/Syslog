import asyncio
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.udp_server import start_udp_server


@pytest.fixture
def mock_settings(monkeypatch):
    class MockSettings:
        SYSLOG_PORT = 5514

    monkeypatch.setattr("app.services.udp_server.settings", MockSettings())
    return MockSettings()


@pytest.fixture
async def udp_server(mock_settings):
    """
    Starts the UDP server on a test port, yields, then shuts it down.
    """
    server_task = asyncio.create_task(start_udp_server())
    await asyncio.sleep(0.1)  # allow server to bind

    yield mock_settings.SYSLOG_PORT

    # shut server down
    server_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await server_task


def make_mock_cursor(fetch_result=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_result
    return cursor


# ---------------------------------------------------------------------------
# MAIN SUCCESS TEST – FULL PIPELINE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_udp_server_full_internal_pipeline(udp_server):
    """
    Valid PRI, valid device, internal profile, DB store occurs.
    """
    # --- mocks ---
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev, \
         patch("app.services.udp_server.get_profiles_for_device") as mock_profiles, \
         patch("app.services.udp_server.get_db_connection") as mock_db, \
         patch("app.services.udp_server.find_matching_keyword") as mock_kw:

        mock_parse.return_value = {
            "priority_code": 4,
            "facility_code": 2,
            "message": "Test msg",
        }

        mock_get_dev.return_value = "device-123"

        mock_profiles.return_value = [
            {
                "id": "prof1",
                "type": "internal",
                "priorities": json.dumps([4]),
                "facilities": json.dumps([2]),
                "keywords": json.dumps([]),
            }
        ]

        # DB mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value.__enter__.return_value = mock_conn

        mock_kw.return_value = (None, 0.0)

        # --- send test datagram ---
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )
        transport.sendto(b"<34>Test msg")
        await asyncio.sleep(0.2)

        # Verify DB insert executed
        assert mock_cursor.execute.called
        assert mock_db.return_value.__enter__.return_value.commit.called


# ---------------------------------------------------------------------------
# EXTERNAL FORWARDING FLOW
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_udp_server_external_profile_forward(udp_server):
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev, \
         patch("app.services.udp_server.get_profiles_for_device") as mock_profiles, \
         patch("app.services.udp_server.get_integrations_for_profile") as mock_ints, \
         patch("app.services.udp_server.forward_to_integration") as mock_forward:

        mock_parse.return_value = {
            "priority_code": 4,
            "facility_code": 2,
            "message": "Test msg external",
        }

        mock_get_dev.return_value = "device-123"

        mock_profiles.return_value = [
            {
                "id": "prof-ext",
                "type": "external",
                "priorities": json.dumps([4]),
                "facilities": json.dumps([2]),
                "keywords": json.dumps([]),
            }
        ]

        mock_ints.return_value = [
            {"id": "int1", "type": "http", "endpoint": "http://x"}
        ]

        # Send message
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )
        transport.sendto(b"<34>Test msg external")
        await asyncio.sleep(0.2)

        mock_forward.assert_called_once()


# ---------------------------------------------------------------------------
# DEVICE NOT FOUND
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_udp_server_no_device_id(udp_server):
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev:

        mock_parse.return_value = {"message": "hi", "priority_code": 1, "facility_code": 2}
        mock_get_dev.return_value = None

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )
        transport.sendto(b"<5>hello")
        await asyncio.sleep(0.1)

        # Should exit early and no crash
        assert mock_get_dev.called


# ---------------------------------------------------------------------------
# NO PROFILES ATTACHED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_udp_server_no_profiles(udp_server):
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev, \
         patch("app.services.udp_server.get_profiles_for_device") as mock_profiles:

        mock_parse.return_value = {"message": "x", "priority_code": 1, "facility_code": 2}
        mock_get_dev.return_value = "dev1"
        mock_profiles.return_value = []

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )
        transport.sendto(b"<5>something")
        await asyncio.sleep(0.1)

        mock_profiles.assert_called_once()


# ---------------------------------------------------------------------------
# KEYWORD MATCH FAILURE PATH
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_udp_server_keyword_match_failure(udp_server):
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev, \
         patch("app.services.udp_server.get_profiles_for_device") as mock_profiles, \
         patch("app.services.udp_server.find_matching_keyword") as mock_kw:

        mock_parse.return_value = {"message": "abc test", "priority_code": 2, "facility_code": 1}
        mock_get_dev.return_value = "dev1"

        mock_profiles.return_value = [
            {
                "id": "prof1",
                "type": "internal",
                "priorities": json.dumps([2]),
                "facilities": json.dumps([1]),
                "keywords": json.dumps(["xyz"]),  # does NOT match message
            }
        ]

        mock_kw.side_effect = Exception("keyword error")

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )
        transport.sendto(b"<17>abc test")
        await asyncio.sleep(0.2)

        mock_kw.assert_called_once()


# ---------------------------------------------------------------------------
# INVALID PARSE RETURNS EARLY
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_udp_server_parse_returns_none(udp_server):
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse:

        mock_parse.return_value = None

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )
        transport.sendto(b"bad message")
        await asyncio.sleep(0.1)

        assert mock_parse.called

