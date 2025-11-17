import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# @pytest.mark.asyncio
async def test_udp_server_corrupted_packets(udp_server):
    """
    Send various corrupted messages.
    Ensure server never crashes and parse_syslog_line is still called.
    """

    with patch("app.services.udp_server.parse_syslog_line") as mock_parse:
        # parse_syslog_line handles corrupted packets however it wants
        mock_parse.side_effect = [
            None,        # not parseable
            {},          # empty dict
            {"bad": 1},  # malformed but non-crashing
        ]

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )

        corrupted = [
            b"",                    # empty
            b"\x00\x01\x02garbage", # binary junk
            b"<not-a-pri>",         # syntactically wrong
        ]

        for packet in corrupted:
            transport.sendto(packet)

        await asyncio.sleep(0.3)

        assert mock_parse.call_count == 3
@pytest.mark.asyncio
async def test_udp_server_corrupted_packets(udp_server):
    with patch("app.services.udp_server.parse_syslog_line") as mock_parse:

        mock_parse.side_effect = [
            None,
            {},
            {"bad": 1},
        ]

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )

        corrupted = [
            b"",                    # may not be delivered on Windows
            b"\x00\x01\x02garbage",
            b"<not-a-pri>",
        ]

        for packet in corrupted:
            transport.sendto(packet)

        await asyncio.sleep(0.3)

        # Windows does NOT guarantee empty datagrams are delivered.
        assert mock_parse.call_count in (2, 3)


@pytest.mark.asyncio
async def test_udp_server_slow_db(udp_server):
    """
    Simulate slow DB inserts to ensure server does not hang or crash.
    """

    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev, \
         patch("app.services.udp_server.get_profiles_for_device") as mock_profiles, \
         patch("app.services.udp_server.get_db_connection") as mock_db:

        mock_parse.return_value = {
            "priority_code": 3,
            "facility_code": 4,
            "message": "SLOW DB TEST",
        }

        mock_get_dev.return_value = "device-xyz"

        mock_profiles.return_value = [
            {
                "id": "profile-slow-db",
                "type": "internal",
                "priorities": None,
                "facilities": None,
                "keywords": None,
            }
        ]

        # Mock DB latency
        async def slow_commit():
            await asyncio.sleep(0.2)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value.__enter__.return_value = mock_conn
        mock_conn.commit.side_effect = slow_commit

        # Send test packet
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )

        transport.sendto(b"<28>SLOW DB")

        await asyncio.sleep(0.5)

        assert mock_parse.called
        assert mock_get_dev.await_count == 1
        assert mock_cursor.execute.called
        assert mock_conn.commit.called
