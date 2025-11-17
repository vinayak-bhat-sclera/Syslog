import pytest
import asyncio
import time
from unittest.mock import patch, MagicMock, AsyncMock

import app.services.device_cache as dc


# ------------------------------------------------------------
# AUTO FIXTURE: reset globals for isolation
# ------------------------------------------------------------
@pytest.fixture(autouse=True)
async def reset_cache():
    dc._DEVICE_CACHE.clear()
    dc._PROFILE_DEVICE_MAP.clear()
    dc._CACHE_LOCK = asyncio.Lock()
    yield


# ------------------------------------------------------------
# UTILITY: aiohttp-style async context manager
# ------------------------------------------------------------
class FakeAiohttpResponse:
    def __init__(self, status, json_data=None, text_data=""):
        self.status = status
        self._json = json_data
        self._text = text_data

    async def json(self):
        return self._json

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeAiohttpSession:
    def __init__(self, get_resp=None, post_resp=None):
        self._get_resp = get_resp
        self._post_resp = post_resp

    # MUST return async context manager immediately (not a coroutine)
    def get(self, *args, **kwargs):
        return self._get_resp

    def post(self, *args, **kwargs):
        return self._post_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


# =====================================================================
# SIMPLE CACHE TESTS
# =====================================================================

@pytest.mark.asyncio
async def test_set_and_get_device_cache_hit():
    await dc.set_device_cache("1.1.1.1", "dev1", ttl=5)
    assert await dc.get_device_id("1.1.1.1") == "dev1"


@pytest.mark.asyncio
async def test_get_device_cache_expired():
    await dc.set_device_cache("2.2.2.2", "devX", ttl=-1)

    # avoid calling springboot
    with patch("app.services.device_cache._fetch_from_springboot", AsyncMock(return_value=None)):
        assert await dc.get_device_id("2.2.2.2") is None
        assert "2.2.2.2" not in dc._DEVICE_CACHE


@pytest.mark.asyncio
async def test_get_device_id_fetch_success():
    with patch("app.services.device_cache._fetch_from_springboot", AsyncMock(return_value="NEW")):
        assert await dc.get_device_id("3.3.3.3") == "NEW"
        assert dc._DEVICE_CACHE["3.3.3.3"][0] == "NEW"


# =====================================================================
# SPRINGBOOT LOOKUPS
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_from_springboot_success():
    fake_resp = FakeAiohttpResponse(200, json_data={"device_id": "dev-spring"})
    fake_session = FakeAiohttpSession(get_resp=fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        result = await dc._fetch_from_springboot("10.10.10.10")
        assert result == "dev-spring"


@pytest.mark.asyncio
async def test_fetch_from_springboot_failure():
    fake_resp = FakeAiohttpResponse(404, text_data="not found")
    fake_session = FakeAiohttpSession(get_resp=fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        assert await dc._fetch_from_springboot("11.11.11.11") is None


# =====================================================================
# SPRINGBOOT MAPPING FETCH
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_mappings_from_springboot():
    fake_resp = FakeAiohttpResponse(
        200,
        json_data=[
            {"id": "dev1", "ip": "1.1.1.1"},
            {"device_id": "dev2", "ip_address": "2.2.2.2"},
        ],
    )
    fake_session = FakeAiohttpSession(post_resp=fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        result = await dc._fetch_mappings_from_springboot_for_device_ids(["dev1", "dev2"])

    assert result == {"1.1.1.1": "dev1", "2.2.2.2": "dev2"}


# =====================================================================
# DB PATCH (correct path)
# =====================================================================

def patch_db(fake_cursor):
    fake_db = MagicMock()
    fake_db.cursor.return_value = fake_cursor

    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = fake_db
    mock_ctx.__exit__.return_value = False

    # CORRECT patch target (matches: from app.db import get_db_connection)
    return patch("app.services.device_cache.get_db_connection", return_value=mock_ctx)



# =====================================================================
# PROFILE CACHE OPS
# =====================================================================

@pytest.mark.asyncio
async def test_clear_profile_devices_db():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"device_id": "devX"}]

    with patch_db(cursor):
        await dc.set_device_cache("9.9.9.9", "devX")
        await dc.clear_profile_devices("p1")

    assert "9.9.9.9" not in dc._DEVICE_CACHE
    assert dc._PROFILE_DEVICE_MAP["p1"] == {"devX"}


@pytest.mark.asyncio
async def test_refresh_cache_for_profile():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [{"device_id": "devA"}, {"device_id": "devB"}]
    ]
    cursor.fetchone.return_value = {"docker_name": "DockName"}

    with patch_db(cursor), \
         patch("app.services.device_cache._fetch_mappings_from_springboot_for_device_ids",
               AsyncMock(return_value={"5.5.5.5": "devA"})):

        await dc.refresh_cache_for_profile("pid1")

    assert dc._DEVICE_CACHE["5.5.5.5"][0] == "devA"


@pytest.mark.asyncio
async def test_refresh_all_profiles_cache():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [{"profile_id": "p1", "device_id": "devA"}],
        [{"id": "p1", "docker_name": "Dock1"}],
    ]

    with patch_db(cursor), \
         patch("app.services.device_cache._fetch_mappings_from_springboot_for_device_ids",
               AsyncMock(return_value={"7.7.7.7": "devA"})):

        await dc.refresh_all_profiles_cache()

    assert dc._DEVICE_CACHE["7.7.7.7"][0] == "devA"


# =====================================================================
# notify_profile_change
# =====================================================================

@pytest.mark.asyncio
async def test_notify_profile_change():
    dc._PROFILE_DEVICE_MAP["p"] = {"oldDev"}

    cursor = MagicMock()
    cursor.fetchall.return_value = [{"device_id": "newDev"}]

    with patch_db(cursor), \
         patch("app.services.device_cache._evict_by_device_ids", AsyncMock()) as e1, \
         patch("app.services.device_cache.clear_profile_devices", AsyncMock()) as e2, \
         patch("app.services.device_cache.refresh_cache_for_profile", AsyncMock()) as e3:

        await dc.notify_profile_change("p")

    e1.assert_called()
    e2.assert_called()
    e3.assert_called()


# =====================================================================
# SNAPSHOTS
# =====================================================================

def test_get_cache_snapshot_sync():
    dc._DEVICE_CACHE["a"] = ("devX", int(time.time()) + 100)
    snap = dc.get_cache_snapshot()
    assert snap == {"a": "devX"}


@pytest.mark.asyncio
async def test_get_cache_snapshot_async():
    await dc.set_device_cache("b", "devY")
    snap = await dc.get_cache_snapshot_async()
    assert snap == {"b": "devY"}


# =====================================================================
# BACKGROUND TASK
# =====================================================================

@pytest.mark.asyncio
async def test_start_stop_background_tasks():
    loop = asyncio.get_running_loop()

    task = dc.start_background_tasks(loop)

    await asyncio.sleep(0)  # let loop start

    dc.stop_background_tasks(task)

    # additional yield to propagate cancel
    await asyncio.sleep(0.05)

    assert task.cancelled()
