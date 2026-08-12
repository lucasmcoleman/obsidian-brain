"""brain.write_entity_note slug handling  and consolidate wiring."""
import brain


def _entities(tmp_path, monkeypatch):
    ent = tmp_path / "entities"
    ent.mkdir()
    monkeypatch.setattr(brain, "ENTITIES_DIR", str(ent))
    return ent


def test_same_entity_name_is_exists(tmp_path, monkeypatch):
    _entities(tmp_path, monkeypatch)
    assert brain.write_entity_note("John Smith", "x")["status"] == "created"
    assert brain.write_entity_note("John Smith", "y")["status"] == "exists"


def test_distinct_names_colliding_on_slug_do_not_lose_content(tmp_path, monkeypatch):
    ent = _entities(tmp_path, monkeypatch)
    r1 = brain.write_entity_note("John Smith - client", "first entity content")
    r2 = brain.write_entity_note("John Smith, client", "second, different entity")
    assert r1["status"] == "created"
    assert r2["status"] == "created"          # not silently 'exists'
    assert r1["slug"] != r2["slug"]           # distinct files
    bodies = [p.read_text(encoding="utf-8") for p in ent.glob("*.md")]
    assert any("first entity content" in b for b in bodies)
    assert any("second, different entity" in b for b in bodies)


def test_non_ascii_name_gets_a_nonempty_slug(tmp_path, monkeypatch):
    _entities(tmp_path, monkeypatch)
    r = brain.write_entity_note("日本語プロジェクト", "content")
    assert r["status"] == "created"
    assert r["slug"]                          # never an empty '.md' dotfile
    assert r["slug"] != ""


def test_consolidate_threads_force_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(brain, "build_index", lambda force=False: calls.setdefault("force", force) or {"status": "built"})
    brain.consolidate(force=False)
    assert calls["force"] is False            # low-3: no longer hardcoded True
