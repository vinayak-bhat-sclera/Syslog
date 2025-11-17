import pytest
from unittest.mock import patch, MagicMock
from app.services.forwarding import forward_to_integration


# =====================================================================================
# Helper: Build parsed syslog dict mock
# =====================================================================================

def make_parsed(fac=1, pri=2, host="1.2.3.4", msg="test msg"):
    return {
        "facility_code": fac,
        "priority_code": pri,
        "host": host,
        "message": msg,
    }


# =====================================================================================
# TEST: Basic successful forwarding
# =====================================================================================

def test_forward_to_integration_success(monkeypatch):
    fake_socket = MagicMock()
    fake_sock_inst = MagicMock()
    fake_socket.return_value.__enter__.return_value = fake_sock_inst

    # Patch socket.socket
    with patch("app.services.forwarding.socket.socket", fake_socket):

        integration = {"ip_address": "10.10.10.10", "port": 5514}
        parsed = make_parsed(fac=4, pri=3, host="myhost", msg="Hello world")

        forward_to_integration(integration, parsed)

        # Validate socket was created
        fake_socket.assert_called_once()

        # Validate sendto called once
        assert fake_sock_inst.sendto.called

        sent_data, sent_addr = fake_sock_inst.sendto.call_args[0]

        # Validate the target address
        assert sent_addr == ("10.10.10.10", 5514)

        # Validate RFC3164 PRI calculation: PRI = facility*8 + priority
        assert sent_data.startswith(b"<35>")  # 4*8+3 = 35

        # Validate message contains host and actual text
        assert b"myhost" in sent_data
        assert b"Hello world" in sent_data


# =====================================================================================
# TEST: Default port when missing (514)
# =====================================================================================

def test_forward_default_port(monkeypatch):
    fake_socket = MagicMock()
    fake_sock_inst = MagicMock()
    fake_socket.return_value.__enter__.return_value = fake_sock_inst

    with patch("app.services.forwarding.socket.socket", fake_socket):

        integration = {"ip_address": "192.168.1.5"}  # no port
        parsed = make_parsed()

        forward_to_integration(integration, parsed)

        _, sent_addr = fake_sock_inst.sendto.call_args[0]
        assert sent_addr == ("192.168.1.5", 514)


# =====================================================================================
# TEST: Missing host or message gracefully handled
# =====================================================================================

@pytest.mark.parametrize("parsed", [
    {"facility_code": 1, "priority_code": 2},  # no host or message
    {"facility_code": 1, "priority_code": 2, "host": None, "message": None},
])
def test_forward_missing_fields(monkeypatch, parsed):
    fake_socket = MagicMock()
    fake_sock_inst = MagicMock()
    fake_socket.return_value.__enter__.return_value = fake_sock_inst

    with patch("app.services.forwarding.socket.socket", fake_socket):

        integration = {"ip_address": "8.8.8.8", "port": 6000}
        forward_to_integration(integration, parsed)

        sent_data, _ = fake_sock_inst.sendto.call_args[0]

        # defaults: host = "-", message = ""
        host = parsed.get("host")
        message = parsed.get("message")

        if host is None and "host" not in parsed:
        # fallback to "-"
            assert b"-" in sent_data
        elif host is None:
            # explicit None → appears as b"None"
            assert b"None" in sent_data
    # message:
        if message is None and "message" not in parsed:
            assert sent_data.endswith(b"- ") or sent_data.endswith(b"-")
        elif message is None:
            assert b"None" in sent_data


# =====================================================================================
# TEST: Exception handling path
# =====================================================================================

def test_forward_exception_logged(monkeypatch, caplog):
    caplog.set_level("ERROR")

    # Raise exception inside socket to simulate failure
    with patch("app.services.forwarding.socket.socket", side_effect=Exception("socket boom")):
        integration = {"ip_address": "1.1.1.1", "port": 5000}
        parsed = make_parsed()

        forward_to_integration(integration, parsed)

    # Verify error logged
    assert "Failed forwarding" in caplog.text

