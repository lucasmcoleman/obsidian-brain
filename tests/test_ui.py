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


def test_ui_search_empty_query_returns_empty(monkeypatch):
    c = _client(monkeypatch, token="tok")
    r = c.get("/ui/api/search?q=", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json() == {"results": []}
