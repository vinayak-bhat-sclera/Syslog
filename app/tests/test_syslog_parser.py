import pytest
from app.services.syslog_parser import extract_pri_from_message, parse_syslog_line


# =====================================================================
# Tests for extract_pri_from_message
# =====================================================================

def test_extract_pri_valid_basic():
    assert extract_pri_from_message("<34>hello") == (4, 2)  # 34 = 4*8 + 2


def test_extract_pri_valid_at_end():
    assert extract_pri_from_message("msg <16>") == (2, 0)


def test_extract_pri_valid_not_at_start():
    assert extract_pri_from_message("noise <40> something") == (5, 0)


def test_extract_pri_invalid_no_brackets():
    assert extract_pri_from_message("no pri here") is None


def test_extract_pri_invalid_non_numeric():
    assert extract_pri_from_message("<abc>") is None


def test_extract_pri_invalid_empty_brackets():
    assert extract_pri_from_message("<>") is None


def test_extract_pri_invalid_incomplete_brackets():
    assert extract_pri_from_message("<12") is None
    assert extract_pri_from_message("12>") is None


#def test_extract_pri_exception_handling():
    # Force an exception inside int() conversion
    #assert extract_pri_from_message("<999999999999999999999999999999>") is None


# =====================================================================
# Tests for parse_syslog_line
# =====================================================================

def test_parse_syslog_line_with_valid_pri():
    data = "<34>System rebooted"
    res = parse_syslog_line(data, "host1")

    assert res["host"] == "host1"
    assert res["facility_code"] == 4
    assert res["priority_code"] == 2
    assert res["message"] == "System rebooted"
    assert res["raw"] == data


def test_parse_syslog_line_valid_pri_not_at_start():
    data = "noise <40> something"
    res = parse_syslog_line(data, "h")

    assert res["facility_code"] == 5
    assert res["priority_code"] == 0
    assert res["message"] == "something"


def test_parse_syslog_line_pri_present_but_message_has_no_text():
    data = "<10>"
    res = parse_syslog_line(data, "host")

    assert res["facility_code"] == 1
    assert res["priority_code"] == 2
    assert res["message"] == ""


def test_parse_syslog_line_invalid_pri_fallback():
    data = "no pri here"
    res = parse_syslog_line(data, "device")

    # Fallback branch: facility=1, priority=6
    assert res["facility_code"] == 1
    assert res["priority_code"] == 6
    assert res["message"] == data.strip()


def test_parse_syslog_line_invalid_brackets():
    data = "<> message"
    res = parse_syslog_line(data, "dev")

    assert res["facility_code"] == 1
    assert res["priority_code"] == 6
    assert res["message"] == data.strip()


def test_parse_syslog_line_pri_parsing_error_keeps_raw_message():
    data = "<abc> bad"
    res = parse_syslog_line(data, "dev")

    assert res["facility_code"] == 1
    assert res["priority_code"] == 6
    assert res["message"] == data.strip()


def test_parse_syslog_line_incomplete_pri():
    data = "<12 something"
    res = parse_syslog_line(data, "host")

    assert res["facility_code"] == 1
    assert res["priority_code"] == 6
    assert res["message"] == data.strip()


def test_parse_syslog_line_whitespace_trim():
    data = "    <8>   Hi     "
    res = parse_syslog_line(data, "x")

    assert res["facility_code"] == 1
    assert res["priority_code"] == 0
    assert res["message"] == "Hi"


def test_parse_syslog_line_only_whitespace():
    data = "     "
    res = parse_syslog_line(data, "h")

    assert res["facility_code"] == 1
    assert res["priority_code"] == 6
    assert res["message"] == ""


def test_parse_syslog_line_unicode_message():
    data = "<5>🔥 Test ✔"
    res = parse_syslog_line(data, "host")

    assert res["facility_code"] == 0
    assert res["priority_code"] == 5
    assert res["message"] == "🔥 Test ✔"
