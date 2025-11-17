import asyncio
import pytest
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_udp_server_high_load(udp_server):
    """
    Flood the UDP server with ~1000 messages rapidly.
    Goal: ensure no crashes, pipeline processes all messages.
    """

    with patch("app.services.udp_server.parse_syslog_line") as mock_parse, \
         patch("app.services.udp_server.get_device_id", new=AsyncMock()) as mock_get_dev, \
         patch("app.services.udp_server.get_profiles_for_device") as mock_profiles:

        # Every message parses successfully
        mock_parse.return_value = {
            "message": "load test",
            "priority_code": 5,
            "facility_code": 4,
        }

        # Device always resolves
        mock_get_dev.return_value = "dev-stress"

        # No profiles = fast exit for each
        mock_profiles.return_value = []

        # UDP client
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", udp_server)
        )

        # Flood 1000 messages ASAP
        for i in range(1000):
            msg = f"<45>MSG {i}".encode()
            transport.sendto(msg)

        await asyncio.sleep(0.5)

        # ensure server processed all calls
        assert mock_parse.call_count == 1000
        assert mock_get_dev.await_count == 1000
        assert mock_profiles.call_count == 1000
