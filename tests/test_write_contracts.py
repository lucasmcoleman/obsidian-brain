"""Write-path contracts & resilience: structured returns, CRLF-preserving atomic
writes, created-vs-exists, robust slug, no-index signal (M10/M14/M15/L11/L19)."""
import brain
import tasks


# ── M14: structured returns for write tools ────────────────────────────────────
def test_append_insight_returns_status_dict_on_success(vault, monkeypatch):
    monkeypatch.setattr(brain, "VAULT_PATH", str(vault))
    (vault / "n.md").write_text("body\n", encoding="utf-8")

    res = brain.append_insight("n.md", "an insight")

    assert isinstance(res, dict)
    assert res["status"] == "ok"


def test_append_insight_returns_error_status_when_missing(vault, monkeypatch):
    monkeypatch.setattr(brain, "VAULT_PATH", str(vault))
    res = brain.append_insight("nope.md", "x")
    assert res["status"] == "error"


# ── M10: CRLF preservation + atomic write ──────────────────────────────────────
def test_append_insight_preserves_crlf(vault, monkeypatch):
    monkeypatch.setattr(brain, "VAULT_PATH", str(vault))
    p = vault / "crlf.md"
    p.write_bytes(b"line one\r\nline two\r\n")

    brain.append_insight("crlf.md", "fresh insight")

    raw = p.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")  # no bare LF introduced


def test_complete_task_preserves_crlf(vault):
    p = vault / "t.md"
    p.write_bytes(b"- [ ] finish report\r\nother line\r\n")

    res = tasks.complete_task("t.md", "finish report", vault_path=str(vault))

    assert res["status"] == "completed"
    raw = p.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")  # CRLF not flattened to LF


def test_complete_task_leaves_no_tmp_file(vault):
    p = vault / "t.md"
    p.write_text("- [ ] do it\n", encoding="utf-8")
    tasks.complete_task("t.md", "do it", vault_path=str(vault))
    assert [f.name for f in vault.iterdir()] == ["t.md"]  # no leftover .tmp


# ── M15 + L11: entity created-vs-exists + robust slug ──────────────────────────
def test_write_entity_signals_created_then_exists(vault, monkeypatch):
    monkeypatch.setattr(brain, "ENTITIES_DIR", str(vault / "entities"))
    first = brain.write_entity_note("Sarah Chen", "lead")
    second = brain.write_entity_note("Sarah Chen", "new info")

    assert first["status"] == "created"
    assert second["status"] == "exists"  # not silently success-shaped


def test_write_entity_slug_collapses_punctuation(vault, monkeypatch):
    monkeypatch.setattr(brain, "ENTITIES_DIR", str(vault / "entities"))
    res = brain.write_entity_note("AI / ML !!", "x")
    assert res["status"] == "created"
    assert res["slug"] == "ai-ml"  # collapsed runs, no leading/trailing dash


# ── L19: distinguish "no index" from "no matches" ──────────────────────────────
def test_query_brain_signals_when_no_index(brain_paths, monkeypatch):
    import brain as brain_mod
    monkeypatch.setattr(brain_mod, "INDEX_PATH", brain_paths["index_path"], raising=False)
    monkeypatch.setattr(brain_mod, "METADATA_PATH", brain_paths["meta_path"], raising=False)
    out = brain_mod.query_brain("anything")
    assert "build" in out.lower() or "no index" in out.lower()
