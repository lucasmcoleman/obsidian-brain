"""Characterization tests for the four "POST to chat/completions, parse
content-or-reasoning_content, retry-with-backoff, log-and-return-sentinel" loops
in moc_linker.classify_note, ledger_update.ask_model,
truth_maintenance.judge_contradiction, and task_sweep.ask_model.

These pin behavior BEFORE a planned consolidation into a single shared helper, so
the divergences between the four sites (parser, validity predicate, backoff
multiplier, unparseable-record verbosity, fail-line text, and sentinel) survive the
refactor unchanged. See moc_linker.call_chat_json once it exists.

All four sites POST via _post_json — moc_linker.classify_note references it as a
bare module-global name, and the other three modules `import moc_linker as ml` and
call `ml._post_json` — so patching moc_linker._post_json covers every site, before
AND after the refactor (the new helper still lives in moc_linker and still calls
_post_json). All four modules `import time` and call `time.sleep(...)`, so patching
the shared `time` module's `sleep` attribute covers every site too.
"""
import time

import moc_linker
import ledger_update
import truth_maintenance
import task_sweep


def _stub_post(monkeypatch, responses):
    """Patch moc_linker._post_json to return responses[i] on the i-th call (or
    raise it, when the item is an exception instance). Records every URL and
    payload posted, in call order."""
    calls = {"urls": [], "payloads": []}
    it = iter(responses)

    def fake(url, payload, timeout):
        calls["urls"].append(url)
        calls["payloads"].append(payload)
        item = next(it)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(moc_linker, "_post_json", fake)
    return calls


def _stub_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _resp(content, reasoning_content=""):
    return {"choices": [{"message": {"content": content, "reasoning_content": reasoning_content}}]}


NOTE = {"rel": "n.md", "title": "Note Title", "fm": {}, "body": "some body text"}


# ─────────────────────────────── moc_linker.classify_note ──────────────────────
def test_classify_note_success_from_content(monkeypatch):
    _stub_post(monkeypatch, [_resp('{"moc": "Work MOC", "desc": "d"}')])
    result = moc_linker.classify_note(NOTE, ["Work MOC"], "http://x/v1", "m", 5, retries=1)
    assert result == {"moc": "Work MOC", "desc": "d"}


def test_classify_note_falls_back_to_reasoning_content(monkeypatch):
    _stub_post(monkeypatch, [_resp("not json at all",
                                    '{"moc": "Work MOC", "desc": "from reasoning"}')])
    result = moc_linker.classify_note(NOTE, ["Work MOC"], "http://x/v1", "m", 5, retries=1)
    assert result == {"moc": "Work MOC", "desc": "from reasoning"}


def test_classify_note_retries_then_succeeds(monkeypatch):
    calls = _stub_post(monkeypatch, [
        RuntimeError("boom"),
        _resp('{"moc": "Work MOC", "desc": "d"}'),
    ])
    sleeps = _stub_sleep(monkeypatch)
    result = moc_linker.classify_note(NOTE, ["Work MOC"], "http://x/v1", "m", 5, retries=3)
    assert result == {"moc": "Work MOC", "desc": "d"}
    assert sleeps == [1.5]  # classify_note's multiplier; one failed attempt, success doesn't sleep
    assert calls["urls"] == ["http://x/v1/chat/completions"] * 2


def test_classify_note_exhaustion_returns_sentinel_and_logs(monkeypatch, capsys):
    _stub_post(monkeypatch, [_resp("junk one"), _resp("junk two")])
    sleeps = _stub_sleep(monkeypatch)
    result = moc_linker.classify_note(NOTE, ["Work MOC"], "http://x/v1", "m", 5, retries=2)
    assert result == {"moc": "Unsorted", "desc": ""}
    err = capsys.readouterr().err
    assert "  ! classify failed for n.md:" in err
    assert "unparseable content:" in err and "junk two" in err
    assert sleeps == [1.5, 3.0]  # sleeps after every attempt, including the last


def test_classify_note_parses_fenced_json_reply(monkeypatch):
    # extract_json's fence-stripping — a naive consolidation onto find_json_object
    # would drop this (find_json_object has no fence-stripping step).
    content = '```json\n{"moc": "Work MOC", "desc": "fenced"}\n```'
    _stub_post(monkeypatch, [_resp(content)])
    result = moc_linker.classify_note(NOTE, ["Work MOC"], "http://x/v1", "m", 5, retries=1)
    assert result == {"moc": "Work MOC", "desc": "fenced"}


# ─────────────────────────────── ledger_update.ask_model ───────────────────────
def test_ledger_ask_model_success_from_content(monkeypatch):
    _stub_post(monkeypatch, [_resp('{"completed": [{"n": 1, "evidence": "e"}], "new_items": []}')])
    result = ledger_update.ask_model([], [], "ctx", "http://x/v1", "m", 5, retries=1)
    assert result == {"completed": [{"n": 1, "evidence": "e"}], "new_items": []}


def test_ledger_ask_model_falls_back_to_reasoning_content(monkeypatch):
    _stub_post(monkeypatch, [_resp("nope", '{"new_items": [{"text": "y"}]}')])
    result = ledger_update.ask_model([], [], "ctx", "http://x/v1", "m", 5, retries=1)
    assert result == {"completed": [], "new_items": [{"text": "y"}]}  # setdefault fills "completed"


def test_ledger_ask_model_retries_then_succeeds(monkeypatch):
    calls = _stub_post(monkeypatch, [
        RuntimeError("boom"),
        _resp('{"completed": [], "new_items": []}'),
    ])
    sleeps = _stub_sleep(monkeypatch)
    result = ledger_update.ask_model([], [], "ctx", "http://x/v1", "m", 5, retries=3)
    assert result == {"completed": [], "new_items": []}
    assert sleeps == [1.5]
    assert calls["urls"] == ["http://x/v1/chat/completions"] * 2


def test_ledger_ask_model_exhaustion_returns_sentinel_and_logs(monkeypatch, capsys):
    _stub_post(monkeypatch, [_resp("junk"), _resp("junk")])
    sleeps = _stub_sleep(monkeypatch)
    result = ledger_update.ask_model([], [], "ctx", "http://x/v1", "m", 5, retries=2)
    assert result == {"completed": [], "new_items": []}
    err = capsys.readouterr().err
    assert "  ! model call failed: unparseable" in err
    assert sleeps == [1.5, 3.0]


# ─────────────────────────────── truth_maintenance.judge_contradiction ─────────
def test_judge_contradiction_success_from_content(monkeypatch):
    _stub_post(monkeypatch, [_resp(
        '{"verdict": "contradicts", "subject": "s", "evidence_a": "a", '
        '"evidence_b": "b", "confidence": 0.9}')])
    result = truth_maintenance.judge_contradiction(
        "claim a", "a.md", "claim b", "b.md", "http://x/v1", "m", 5, retries=1)
    assert result == {"verdict": "contradicts", "subject": "s", "evidence_a": "a",
                      "evidence_b": "b", "confidence": 0.9}


def test_judge_contradiction_falls_back_to_reasoning_content(monkeypatch):
    _stub_post(monkeypatch, [_resp("nope", '{"verdict": "neutral"}')])
    result = truth_maintenance.judge_contradiction(
        "claim a", "a.md", "claim b", "b.md", "http://x/v1", "m", 5, retries=1)
    assert result == {"verdict": "neutral"}


def test_judge_contradiction_retries_then_succeeds(monkeypatch):
    calls = _stub_post(monkeypatch, [
        RuntimeError("boom"),
        _resp('{"verdict": "neutral"}'),
    ])
    sleeps = _stub_sleep(monkeypatch)
    result = truth_maintenance.judge_contradiction(
        "claim a", "a.md", "claim b", "b.md", "http://x/v1", "m", 5, retries=3)
    assert result == {"verdict": "neutral"}
    # 1.0 multiplier — NOT 1.5 — this endpoint 503s while llama-swap loads a model.
    assert sleeps == [1.0]
    assert calls["urls"] == ["http://x/v1/chat/completions"] * 2


def test_judge_contradiction_exhaustion_returns_none_and_logs(monkeypatch, capsys):
    _stub_post(monkeypatch, [_resp("junk"), _resp("junk")])
    sleeps = _stub_sleep(monkeypatch)
    result = truth_maintenance.judge_contradiction(
        "claim a", "a.md", "claim b", "b.md", "http://x/v1", "m", 5, retries=2)
    assert result is None
    err = capsys.readouterr().err
    assert "  ! judge call failed: unparseable" in err
    assert sleeps == [1.0, 2.0]


# ─────────────────────────────── task_sweep.ask_model ──────────────────────────
_BATCH = [{"n": 1, "text": "some task", "note_path": "n.md"}]


def test_task_sweep_ask_model_success_from_content(monkeypatch):
    _stub_post(monkeypatch, [_resp('{"completed": [{"n": 1, "evidence": "e"}]}')])
    result = task_sweep.ask_model(_BATCH, "ctx", "http://x/v1", "m", 5, retries=1)
    assert result == [{"n": 1, "evidence": "e"}]


def test_task_sweep_ask_model_falls_back_to_reasoning_content(monkeypatch):
    _stub_post(monkeypatch, [_resp("nope", '{"completed": [{"n": 2, "evidence": "e2"}]}')])
    result = task_sweep.ask_model(_BATCH, "ctx", "http://x/v1", "m", 5, retries=1)
    assert result == [{"n": 2, "evidence": "e2"}]


def test_task_sweep_ask_model_retries_then_succeeds(monkeypatch):
    calls = _stub_post(monkeypatch, [
        RuntimeError("boom"),
        _resp('{"completed": []}'),
    ])
    sleeps = _stub_sleep(monkeypatch)
    result = task_sweep.ask_model(_BATCH, "ctx", "http://x/v1", "m", 5, retries=3)
    assert result == []
    assert sleeps == [1.5]
    assert calls["urls"] == ["http://x/v1/chat/completions"] * 2


def test_task_sweep_ask_model_exhaustion_returns_sentinel_and_logs(monkeypatch, capsys):
    _stub_post(monkeypatch, [_resp("junk"), _resp("junk")])
    sleeps = _stub_sleep(monkeypatch)
    result = task_sweep.ask_model(_BATCH, "ctx", "http://x/v1", "m", 5, retries=2)
    assert result == []
    err = capsys.readouterr().err
    assert "  ! model call failed: unparseable" in err
    assert sleeps == [1.5, 3.0]


def test_task_sweep_ask_model_payload_disables_thinking(monkeypatch):
    calls = _stub_post(monkeypatch, [_resp('{"completed": []}')])
    task_sweep.ask_model(_BATCH, "ctx", "http://x/v1", "m", 5, retries=1)
    assert calls["payloads"][0]["chat_template_kwargs"] == {"enable_thinking": False}
