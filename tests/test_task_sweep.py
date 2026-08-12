"""Vault-wide nightly task-completion sweep (task_sweep.py).

The sweep proposes completions for ANY open checkbox in the vault (the ledger is
excluded — ledger_update owns it) and only applies ones whose evidence survives
ledger_update's deterministic evidence gate. Bias is conservative: a missed
completion stays visibly open; a false completion silently hides undone work.
"""
import re

import pytest

import task_sweep
import moc_linker as ml
from conftest import write_note


def _fake_model(monkeypatch, completed):
    """Stub the chat endpoint to return a canned completions object."""
    def fake_post(url, payload, timeout):
        import json
        return {"choices": [{"message": {"content": json.dumps({"completed": completed})}}]}
    monkeypatch.setattr(ml, "_post_json", fake_post)


def _run(vault, monkeypatch, apply=False, **kw):
    argv = ["task_sweep.py", "--vault", str(vault),
            "--apply" if apply else "--dry-run"]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    monkeypatch.setattr("sys.argv", argv)
    return task_sweep.main()


def test_candidates_exclude_ledger(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    write_note(vault, "open-action-items-ledger.md", "- [ ] ledger item\n")
    write_note(vault, "Daily Notes/2026-07-01.md", "- [ ] daily item\n")
    cands = task_sweep.collect_candidates(vault)
    texts = [c["text"] for c in cands]
    assert "daily item" in texts
    assert "ledger item" not in texts
    assert [c["n"] for c in cands] == list(range(1, len(cands) + 1))


def test_batches_split_evenly():
    items = list(range(120))
    got = list(task_sweep.batches(items, 50))
    assert [len(b) for b in got] == [50, 50, 20]
    assert [x for b in got for x in b] == items


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    orig = "# Log\n\n- [ ] send the cohort welcome email\n"
    write_note(vault, "Daily Notes/2026-07-01.md", orig)
    write_note(vault, "Daily Notes/2026-07-05.md",
               "I sent the cohort welcome email this morning.\n")
    _fake_model(monkeypatch, [
        {"n": 1, "evidence": "sent the cohort welcome email this morning"}])
    assert _run(vault, monkeypatch, apply=False) == 0
    assert (vault / "Daily Notes/2026-07-01.md").read_text(encoding="utf-8") == orig


def test_apply_flips_evidenced_task_and_backs_up(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("MOC_BACKUP_DIR", str(tmp_path / "backups"))
    write_note(vault, "Daily Notes/2026-07-01.md",
               "- [ ] send the cohort welcome email\n- [ ] book the venue\n")
    write_note(vault, "Daily Notes/2026-07-05.md",
               "I sent the cohort welcome email this morning.\n")
    _fake_model(monkeypatch, [
        {"n": 1, "evidence": "sent the cohort welcome email this morning"}])
    assert _run(vault, monkeypatch, apply=True) == 0
    text = (vault / "Daily Notes/2026-07-01.md").read_text(encoding="utf-8")
    assert re.search(r"- \[x\] send the cohort welcome email ✅ \d{4}-\d{2}-\d{2}", text)
    assert "- [ ] book the venue" in text  # untouched sibling
    baks = list((tmp_path / "backups" / "sweep").glob("*.bak.md"))
    assert len(baks) == 1  # pre-edit copy, outside the vault


def test_hallucinated_evidence_is_rejected(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    orig = "- [ ] send the cohort welcome email\n"
    write_note(vault, "Daily Notes/2026-07-01.md", orig)
    write_note(vault, "Daily Notes/2026-07-05.md", "Talked about unrelated things.\n")
    _fake_model(monkeypatch, [
        {"n": 1, "evidence": "finished the cohort welcome email yesterday"}])
    assert _run(vault, monkeypatch, apply=True) == 0
    assert (vault / "Daily Notes/2026-07-01.md").read_text(encoding="utf-8") == orig


def test_cap_limits_completions_per_run(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("MOC_BACKUP_DIR", str(tmp_path / "backups"))
    write_note(vault, "Daily Notes/2026-07-01.md",
               "- [ ] send the cohort welcome email\n- [ ] draft the vendor contract\n")
    write_note(vault, "Daily Notes/2026-07-05.md",
               "I sent the cohort welcome email this morning. "
               "Also finished — the vendor contract draft is done and shared.\n")
    _fake_model(monkeypatch, [
        {"n": 1, "evidence": "sent the cohort welcome email this morning"},
        {"n": 2, "evidence": "the vendor contract draft is done and shared"}])
    assert _run(vault, monkeypatch, apply=True, max_completions=1) == 0
    text = (vault / "Daily Notes/2026-07-01.md").read_text(encoding="utf-8")
    assert text.count("- [x]") == 1  # only one applied this run
    assert text.count("- [ ]") == 1  # the other stays open for the next run


def test_ambiguous_duplicate_is_skipped_without_write(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("MOC_BACKUP_DIR", str(tmp_path / "backups"))
    orig = "- [ ] call the Wilson team\n- [ ] call the Wilson team\n"
    write_note(vault, "Daily Notes/2026-07-01.md", orig)
    write_note(vault, "Daily Notes/2026-07-05.md",
               "I did call the Wilson team today about the invoice.\n")
    _fake_model(monkeypatch, [
        {"n": 1, "evidence": "did call the Wilson team today"}])
    assert _run(vault, monkeypatch, apply=True) == 0  # skip is not a crash
    # complete_task's unique-match rule refuses: identical duplicates stay open.
    assert (vault / "Daily Notes/2026-07-01.md").read_text(encoding="utf-8") == orig
