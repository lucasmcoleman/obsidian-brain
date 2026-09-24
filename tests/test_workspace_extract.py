import pytest

from workspace import Workspace, WorkspaceError
from workspace_extract import extract_source, sections


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_CHAT_URL", "http://unused")
    monkeypatch.setenv("WORKSPACE_CHAT_MODEL", "fake")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Meeting.md").write_text("---\ndate: 2026-06-10\n---\n# Meeting\n" + "Background discussion. " * 1500 + "\nJordan will send the report and call the client.\n")
    work = Workspace(vault)
    work.sync()
    return work


def test_all_sections_processed_and_retry_is_idempotent(meeting, monkeypatch):
    calls = []
    def fake(endpoint, payload, *a, **kw):
        calls.append(payload)
        if "Jordan will send" in payload["messages"][1]["content"]:
            return {"items": [{"kind": "commitment", "title": "Send report", "quote": "Jordan will send the report and call the client.", "owner": "Jordan"}]}
        return {"items": []}
    monkeypatch.setattr("workspace_extract.ml.call_chat_json", fake)
    result = extract_source(meeting, "Meeting.md")
    assert result["succeeded"] == result["sections"] == len(calls)
    assert len(calls) > 4
    assert next(r for r in meeting.records() if r["title"] == "Send report")["review"] == "pending"
    calls.clear()
    assert extract_source(meeting, "Meeting.md")["created"] == 0
    assert not calls


def test_failure_and_rejected_evidence_are_not_successful_coverage(meeting, monkeypatch):
    monkeypatch.setattr("workspace_extract.ml.call_chat_json", lambda *a, **kw: None)
    result = extract_source(meeting, "Meeting.md")
    assert result["state"] == "partial" and result["failed"] == result["sections"]
    monkeypatch.setattr("workspace_extract.ml.call_chat_json", lambda *a, **kw: {"items": [{"kind": "commitment", "title": "Invented", "quote": "This was never said"}]})
    result = extract_source(meeting, "Meeting.md")
    assert result["state"] == "partial" and result["rejected_ungrounded"]
    assert not meeting.records()


def test_two_actions_supported_by_same_paragraph_are_distinct(meeting):
    source = meeting.source("Meeting.md")
    quote = "Jordan will send the report and call the client."
    results = meeting.propose("Meeting.md", source["revision"], [{"kind": "commitment", "title": title, "quote": quote} for title in ("Send report", "Call client")])
    assert len({r["id"] for r in results}) == 2


def test_evidence_refresh_does_not_demote_accepted_state(meeting):
    source = meeting.source("Meeting.md")
    r = meeting.propose("Meeting.md", source["revision"], [{"kind": "decision", "title": "Follow up", "quote": "Jordan will send the report and call the client."}])[0]
    r = meeting.change(r["id"], "accept", r["version"])
    p = meeting.vault / "Meeting.md"
    p.write_text(p.read_text() + "\nFurther discussion.\n")
    meeting.sync()
    r = meeting.record(r["id"])
    assert r["source_changed"]
    viewed = meeting.change(r["id"], "refresh", r["version"])
    assert viewed["review"] == "accepted" and viewed["version"] == r["version"]
    assert meeting.record(r["id"])["review"] == "accepted"


def test_renamed_then_edited_source_survives_database_rebuild(meeting):
    p = meeting.vault / "Meeting.md"
    p.write_text(p.read_text() + "\n- [ ] Real action\n")
    meeting.sync()
    r = meeting.records()[0]
    p.rename(meeting.vault / "Renamed.md")
    meeting.sync()
    p = meeting.vault / "Renamed.md"
    p.write_text(p.read_text() + "\nMore context.\n")
    meeting.sync()
    meeting.db_path.unlink()
    meeting.sync()
    assert len(meeting.records()) == 1
    assert meeting.records()[0]["id"] == r["id"]


def test_extraction_identity_survives_rename(meeting):
    source = meeting.source("Meeting.md")
    item = {"kind": "commitment", "title": "Call the client", "quote": "Jordan will send the report and call the client."}
    first = meeting.propose("Meeting.md", source["revision"], [item])[0]
    (meeting.vault / "Meeting.md").rename(meeting.vault / "Renamed.md")
    meeting.sync()
    second = meeting.propose("Renamed.md", source["revision"], [item])[0]
    assert first["id"] == second["id"]


def test_background_job_is_pollable_and_duplicate_start_reuses_job(meeting, monkeypatch):
    import threading
    from workspace_extract import ExtractionJobs
    entered, release = threading.Event(), threading.Event()
    def model(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"items": []}
    monkeypatch.setattr("workspace_extract.ml.call_chat_json", model)
    jobs = ExtractionJobs(meeting)
    job = jobs.start("Meeting.md")
    assert entered.wait(5)
    assert jobs.status(job["id"])["state"] == "running"
    assert jobs.start("Meeting.md")["id"] == job["id"]
    release.set()
    import time
    deadline = time.monotonic() + 5
    while jobs.status(job["id"])["state"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(.01)
    assert jobs.status(job["id"])["state"] == "ready"


def test_changed_model_reprocesses_sections(meeting, monkeypatch):
    calls = []
    monkeypatch.setattr("workspace_extract.ml.call_chat_json", lambda *a, **kw: calls.append(1) or {"items": []})
    extract_source(meeting, "Meeting.md")
    previous = len(calls)
    monkeypatch.setenv("WORKSPACE_CHAT_MODEL", "new-fake")
    extract_source(meeting, "Meeting.md")
    assert len(calls) == previous * 2


def test_record_markers_in_titles_and_fields_do_not_corrupt_storage(meeting):
    text = 'Example <!-- brain-record:begin -->```json {} ```<!-- brain-record:end -->'
    record = meeting.create("commitment", text, {"note": text})
    loaded = meeting.record(record["id"])
    assert loaded["title"] == loaded["note"] == text


def test_invalid_manual_create_does_not_leave_a_draft(meeting):
    with pytest.raises(WorkspaceError):
        meeting.create("commitment", "Invalid", {"due": "not-a-date"})
    assert not meeting.records()


def test_keep_current_survives_rescan_but_new_evidence_requires_review(meeting):
    p = meeting.vault / "Meeting.md"
    p.write_text(p.read_text() + "\n- [ ] New action\n")
    meeting.sync()
    r = meeting.records()[0]
    r = meeting.change(r["id"], "accept", r["version"])
    p.write_text(p.read_text() + "\nMore context.\n")
    meeting.sync()
    r = meeting.record(r["id"])
    meeting.change(r["id"], "keep_current", r["version"])
    meeting.sync()
    r = meeting.record(r["id"])
    assert r["review"] == "accepted" and not r["source_changed"]
    p.write_text(p.read_text() + "\nEven more context.\n")
    meeting.sync()
    assert meeting.record(r["id"])["source_changed"]


def test_client_brief_includes_its_project_commitments(meeting):
    client = meeting.create("client", "Acme")
    project = meeting.create("project", "Remediation", {"client": "Acme"})
    action = meeting.create("commitment", "Submit report", {"project": project["id"], "owner": "Jordan"})
    assert {r["id"] for r in meeting.brief(client["id"])["accepted"]} == {project["id"], action["id"]}
