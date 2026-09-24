"""Workflow invariants: source-grounded review and durable accepted state."""
import json
from datetime import date

import pytest

from workspace import Workspace, WorkspaceError


@pytest.fixture
def work(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Riverside.md").write_text(
        "---\ntype: project-note\ndate: 2026-06-10\n---\n# Riverside\n\n"
        "- [ ] Send report\n- [x] File permit ✅ 2026-06-11\n", encoding="utf-8")
    return Workspace(vault)


def test_intake_preserves_business_date_and_requires_review(work):
    work.sync()
    pending = work.records(review="pending")
    task = next(r for r in pending if r["title"] == "Send report")
    assert task["kind"] == "commitment"
    assert task["evidence"][0]["event_date"] == "2026-06-10"
    assert task["owner"] is None and task["due"] is None
    assert not work.attention(today=date(2026, 9, 10))["items"]
    assert next(r for r in pending if r["title"] == "File permit")["status"] == "done"


def test_accepted_state_survives_sync_database_loss_and_mtime_changes(work):
    work.sync()
    item = next(r for r in work.records() if r["title"] == "Send report")
    accepted = work.change(item["id"], "accept", item["version"],
                           {"owner": "Jordan", "due": "2026-09-11"})
    assert accepted["review"] == "accepted"
    work.sync()
    work.db_path.unlink()
    restored = Workspace(work.vault)
    restored.sync()
    record = restored.record(item["id"])
    assert record["owner"] == "Jordan" and record["due"] == "2026-09-11"
    assert record["review"] == "accepted"
    assert len([r for r in restored.records() if r["title"] == "Send report"]) == 1
    assert restored.attention(today=date(2026, 9, 10))["items"]


def test_completion_and_acknowledgement_are_different(work):
    work.sync()
    r = next(r for r in work.records() if r["title"] == "Send report")
    r = work.change(r["id"], "accept", r["version"], {"due": "2026-09-11"})
    r = work.change(r["id"], "acknowledge", r["version"])
    assert r["status"] == "open"
    assert not work.attention(today=date(2026, 9, 10))["items"]
    r = work.change(r["id"], "resolve", r["version"], {"note": "Confirmed delivered"})
    assert r["status"] == "done"
    work.sync()
    assert work.record(r["id"])["status"] == "done"
    assert "[ ] Send report" in (work.vault / "Riverside.md").read_text()


def test_conflicting_review_does_not_overwrite(work):
    work.sync()
    r = next(r for r in work.records() if r["title"] == "Send report")
    work.change(r["id"], "accept", r["version"], {"owner": "Jordan"})
    with pytest.raises(WorkspaceError) as err:
        work.change(r["id"], "accept", r["version"], {"owner": "Casey"})
    assert err.value.status == 409
    assert work.record(r["id"])["owner"] == "Jordan"


def test_source_changes_require_review_and_do_not_reopen_done_records(work):
    work.sync()
    r = next(r for r in work.records() if r["title"] == "File permit")
    r = work.change(r["id"], "accept", r["version"])
    path = work.vault / "Riverside.md"
    path.write_text(path.read_text().replace("[x] File permit ✅ 2026-06-11", "[ ] File permit"))
    work.sync()
    assert work.record(r["id"])["status"] == "done"
    assert len([x for x in work.records() if x["title"] == "File permit"]) == 1
    assert work.record(r["id"])["source_changed"]


def test_long_note_task_and_duplicate_across_projects(work):
    (work.vault / "Lakeside.md").write_text("# Lakeside\n" + "Long meeting. " * 2500 + "\n- [ ] Send report\n")
    work.sync()
    assert len([r for r in work.records() if r["title"] == "Send report"]) == 2


def test_deleted_source_and_renamed_source_have_honest_evidence(work):
    work.sync()
    r = next(r for r in work.records() if r["title"] == "Send report")
    (work.vault / "Riverside.md").rename(work.vault / "Renamed.md")
    work.sync()
    evidence = work.source_for_record(r["id"])
    assert evidence["note_path"] == "Renamed.md"
    (work.vault / "Renamed.md").unlink()
    work.sync()
    evidence = work.source_for_record(r["id"])
    assert evidence["availability"] == "missing"
    assert evidence["evidence"]["quote"]


def test_lexical_search_works_without_models_and_keeps_exact_identifiers(work):
    (work.vault / "Reference.md").write_text("# Method\nProject RFQ-2611 includes wetland mitigation.\n")
    work.sync()
    result = work.search("RFQ-2611")
    assert result["results"][0]["note_path"] == "Reference.md"
    assert work.source("Reference.md")["content"].startswith("# Method")


def test_read_containment_and_source_policy(work, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("classified")
    (work.vault / "alias.md").symlink_to(secret)
    (work.vault / ".obsidian").mkdir()
    (work.vault / ".obsidian" / "private.md").write_text("private")
    work.sync()
    assert not work.search("classified")["results"]
    for path in ("../secret.md", "alias.md", ".obsidian/private.md"):
        with pytest.raises(WorkspaceError):
            work.source(path)


def test_model_proposals_need_exact_source_evidence(work):
    work.sync()
    source = work.source("Riverside.md")
    with pytest.raises(WorkspaceError):
        work.propose("Riverside.md", source["revision"],
                     [{"kind": "decision", "title": "Invented", "quote": "Never said"}])
    result = work.propose("Riverside.md", source["revision"],
                          [{"kind": "decision", "title": "Review permit filing", "quote": "File permit"}])
    assert result[0]["review"] == "pending"


def test_derived_records_are_not_reimported_as_sources(work):
    work.sync()
    count = len(work.records())
    work.sync()
    assert len(work.records()) == count
    assert all(not r["note_path"].startswith("Brain Workspace/") for r in work.sources())


def test_identical_notes_do_not_swap_identity(work):
    original = (work.vault / "Riverside.md").read_text()
    (work.vault / "Copy.md").write_text(original)
    work.sync()
    before = {r["id"]: r["evidence"][0]["note_path"] for r in work.records()}
    work.sync()
    assert before == {r["id"]: r["evidence"][0]["note_path"] for r in work.records()}


def test_removed_task_and_changed_project_evidence_are_visible(work):
    work.sync()
    task = next(r for r in work.records() if r["title"] == "Send report")
    project = next(r for r in work.records() if r["kind"] == "project")
    task = work.change(task["id"], "accept", task["version"])
    (work.vault / "Riverside.md").write_text("---\ntype: project-note\n---\n# Riverside\nNew status.\n")
    work.sync()
    assert work.record(task["id"])["source_changed"]
    assert work.record(project["id"])["source_changed"]
    assert work.record(task["id"])["status"] == "open"


def test_thematic_break_is_not_frontmatter_and_generated_blocks_are_masked(work):
    (work.vault / "Breaks.md").write_text("---\n- [ ] Real action\n---\n<!-- moc-linker:begin -->\n- [ ] Generated\n<!-- moc-linker:end -->\n")
    work.sync()
    titles = {r["title"] for r in work.records()}
    assert "Real action" in titles and "Generated" not in titles
