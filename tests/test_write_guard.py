"""The write tools must refuse paths outside the vault (H2 / M9)."""
import brain
import tasks


def test_append_insight_refuses_path_outside_vault(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "VAULT_PATH", str(vault))
    outside = tmp_path / "outside.md"
    outside.write_text("original", encoding="utf-8")

    result = brain.append_insight(str(outside), "secret insight")

    assert outside.read_text(encoding="utf-8") == "original"  # untouched
    assert result["status"] == "error"


def test_append_insight_writes_inside_vault(vault, monkeypatch):
    monkeypatch.setattr(brain, "VAULT_PATH", str(vault))
    note = vault / "Note.md"
    note.write_text("body", encoding="utf-8")

    brain.append_insight("Note.md", "an insight", context="ctx")

    assert "an insight" in note.read_text(encoding="utf-8")


def test_complete_task_refuses_path_outside_vault(vault, tmp_path):
    outside = tmp_path / "out.md"
    outside.write_text("- [ ] do thing\n", encoding="utf-8")

    res = tasks.complete_task(str(outside), "do thing", vault_path=str(vault))

    assert res["status"] == "error"
    assert outside.read_text(encoding="utf-8") == "- [ ] do thing\n"  # untouched


def test_complete_task_inside_vault_still_works(vault):
    note = vault / "T.md"
    note.write_text("- [ ] finish report\n", encoding="utf-8")

    res = tasks.complete_task("T.md", "finish report", vault_path=str(vault))

    assert res["status"] == "completed"
    assert "[x]" in note.read_text(encoding="utf-8")
