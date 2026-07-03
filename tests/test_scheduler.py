"""Nightly scheduler hour parsing (audit finding M-O / M-6).

An out-of-range or non-numeric BRAIN_REFRESH_AT_HOUR used to pass int() (or crash
it), then blow up inside datetime.replace(hour=..) on the first loop iteration,
silently killing the daemon thread while /health stayed green. Parsing must clamp
to a valid hour with a logged fallback instead.
"""
import mcp_server


def test_parse_hour_accepts_valid_range():
    assert mcp_server._parse_hour("0") == 0
    assert mcp_server._parse_hour("5") == 5
    assert mcp_server._parse_hour("23") == 23


def test_parse_hour_out_of_range_falls_back_to_default():
    assert mcp_server._parse_hour("24") == 3
    assert mcp_server._parse_hour("99") == 3
    assert mcp_server._parse_hour("-1") == 3


def test_parse_hour_non_numeric_falls_back_to_default():
    assert mcp_server._parse_hour("3am") == 3
    assert mcp_server._parse_hour("") == 3
    assert mcp_server._parse_hour(None) == 3


def test_parse_hour_fallback_hour_is_itself_usable():
    # Whatever _parse_hour returns must be a legal argument to _seconds_until_hour
    # (i.e. never re-raise the ValueError the fix exists to prevent).
    hour = mcp_server._parse_hour("24")
    assert mcp_server._seconds_until_hour(hour) > 0
