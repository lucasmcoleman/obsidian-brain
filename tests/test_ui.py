"""Human-facing web UI routes on the streamable-HTTP app (web-UI feature)."""
from starlette.testclient import TestClient

import mcp_server


def _client(monkeypatch, token=None):
    if token:
        monkeypatch.setenv("BRAIN_AUTH_TOKEN", token)
    else:
        monkeypatch.delenv("BRAIN_AUTH_TOKEN", raising=False)
    return TestClient(mcp_server._build_http_app())


def test_ui_page_is_public_and_html(monkeypatch):
    r = _client(monkeypatch, token="tok").get("/ui")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Obsidian Brain" in r.text
    assert "brain_token" in r.text  # the client-side token flow is present


def test_ui_api_requires_token_when_set(monkeypatch):
    c = _client(monkeypatch, token="tok")
    assert c.get("/ui/api/status").status_code == 401
    assert c.get("/ui/api/status", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_ui_insight_refuses_without_configured_token(monkeypatch):
    # With no BRAIN_AUTH_TOKEN the middleware is a no-op; the write route must
    # refuse rather than expose an anonymous write path.
    r = _client(monkeypatch).post("/ui/api/insight", json={"note_path": "x.md", "insight": "y"})
    assert r.status_code == 503


def test_ui_page_has_insight_form_hooks(monkeypatch):
    # The append-insight affordance lives on the search-result cards: the page
    # must ship JS that posts to /ui/api/insight (this pins the endpoint to a
    # UI caller, so it can't go orphan again).
    r = _client(monkeypatch, token="tok").get("/ui")
    assert "/ui/api/insight" in r.text
    assert "＋ Insight" in r.text  # the per-result toggle button label


def test_ui_insight_appends_to_note(monkeypatch, tmp_path):
    # Characterization of the (previously untested) happy path: the endpoint
    # appends a timestamped Brain Insight block to an existing vault note.
    import brain as brain_mod
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(brain_mod, "VAULT_PATH", str(vault))

    c = _client(monkeypatch, token="tok")
    r = c.post("/ui/api/insight",
               json={"note_path": "note.md", "insight": "the key decision",
                     "context": "from the standup"},
               headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    text = (vault / "note.md").read_text(encoding="utf-8")
    assert "## Brain Insight" in text
    assert "the key decision" in text
    assert "from the standup" in text


def test_ui_search_empty_query_returns_empty(monkeypatch):
    c = _client(monkeypatch, token="tok")
    r = c.get("/ui/api/search?q=", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json() == {"results": []}


def test_ui_complete_refuses_without_configured_token(monkeypatch):
    # Same rule as /ui/api/insight: no BRAIN_AUTH_TOKEN → the middleware is a
    # no-op, so the write route must refuse rather than allow anonymous writes.
    r = _client(monkeypatch).post(
        "/ui/api/complete", json={"note_path": "x.md", "match": "y"})
    assert r.status_code == 503


def test_ui_insight_rejects_bad_body(monkeypatch):
    c = _client(monkeypatch, token="tok")
    r = c.post("/ui/api/insight", content=b"not json",
               headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
    assert r.status_code == 400


def test_ui_complete_rejects_bad_or_missing_body(monkeypatch):
    c = _client(monkeypatch, token="tok")
    h = {"Authorization": "Bearer tok"}
    r = c.post("/ui/api/complete", content=b"not json",
               headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 400
    r = c.post("/ui/api/complete", json={"note_path": "x.md"}, headers=h)
    assert r.status_code == 400  # match is required
    r = c.post("/ui/api/complete", json={"match": "y"}, headers=h)
    assert r.status_code == 400  # note_path is required


def test_ui_complete_flips_task_in_place(monkeypatch, tmp_path):
    import tasks as tasks_mod
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "todo.md").write_text(
        "# Todo\n\n- [ ] buy milk\n- [ ] walk dog\n", encoding="utf-8")
    monkeypatch.setattr(tasks_mod, "VAULT_PATH", str(vault))

    c = _client(monkeypatch, token="tok")
    h = {"Authorization": "Bearer tok"}
    r = c.post("/ui/api/complete",
               json={"note_path": "todo.md", "match": "buy milk"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    text = (vault / "todo.md").read_text(encoding="utf-8")
    assert "- [x] buy milk ✅" in text
    assert "- [ ] walk dog" in text  # only the matched task was touched


def test_ui_complete_surfaces_ambiguous_without_writing(monkeypatch, tmp_path):
    import tasks as tasks_mod
    vault = tmp_path / "vault"
    vault.mkdir()
    orig = "- [ ] send report\n- [ ] send report to Jon\n"
    (vault / "dup.md").write_text(orig, encoding="utf-8")
    monkeypatch.setattr(tasks_mod, "VAULT_PATH", str(vault))

    c = _client(monkeypatch, token="tok")
    r = c.post("/ui/api/complete",
               json={"note_path": "dup.md", "match": "send report"},
               headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json()["status"] == "ambiguous"
    assert (vault / "dup.md").read_text(encoding="utf-8") == orig  # untouched


def test_refresh_requires_token_and_runs_build(monkeypatch):
    c = _client(monkeypatch, token="tok")
    assert c.post("/refresh").status_code == 401  # gated like every non-/health route
    monkeypatch.setattr(mcp_server, "build_index", lambda force=False: {"status": "already_current"})
    r = c.post("/refresh", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json()["status"] == "already_current"
