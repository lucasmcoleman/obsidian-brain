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


def test_post_refresh_runs_truth_before_linker_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_run_script", lambda script, argv, label: calls.append(label))
    monkeypatch.setenv("BRAIN_TRUTH_ENABLED", "1")
    monkeypatch.setenv("BRAIN_LINKER_ENABLED", "1")
    monkeypatch.setenv("BRAIN_LEDGER_ENABLED", "1")
    mcp_server._post_refresh_tasks()
    # truth runs FIRST so the linker's writes don't churn the mtimes its recency
    # window depends on; ledger last.
    assert calls == ["truth", "linker", "ledger"]


def test_post_refresh_skips_truth_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_run_script", lambda script, argv, label: calls.append(label))
    monkeypatch.delenv("BRAIN_TRUTH_ENABLED", raising=False)
    monkeypatch.setenv("BRAIN_LINKER_ENABLED", "1")
    monkeypatch.setenv("BRAIN_LEDGER_ENABLED", "1")
    mcp_server._post_refresh_tasks()
    assert "truth" not in calls  # observe-only rollout: off until explicitly enabled


def test_parse_hour_fallback_hour_is_itself_usable():
    # Whatever _parse_hour returns must be a legal argument to _seconds_until_hour
    # (i.e. never re-raise the ValueError the fix exists to prevent).
    hour = mcp_server._parse_hour("24")
    assert mcp_server._seconds_until_hour(hour) > 0
