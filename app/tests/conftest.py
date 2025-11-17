import asyncio
import pytest
from app.services.udp_server import start_udp_server
import app.services.udp_server as udp_module


@pytest.fixture
def mock_settings(monkeypatch):
    class MockSettings:
        SYSLOG_PORT = 5514

    monkeypatch.setattr(udp_module, "settings", MockSettings())
    return MockSettings()


@pytest.fixture
async def udp_server(mock_settings):
    server_task = asyncio.create_task(start_udp_server())
    await asyncio.sleep(0.1)

    yield mock_settings.SYSLOG_PORT

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass
