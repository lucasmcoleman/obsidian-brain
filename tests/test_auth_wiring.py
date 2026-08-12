"""The HTTP app builder installs auth only when BRAIN_AUTH_TOKEN is set."""
import mcp_server
from auth import BearerAuthMiddleware


def _middleware_classes(app):
    return [m.cls for m in app.user_middleware]


def test_builder_adds_auth_when_token_set(monkeypatch):
    monkeypatch.setenv("BRAIN_AUTH_TOKEN", "tok")
    app = mcp_server._build_http_app()
    assert BearerAuthMiddleware in _middleware_classes(app)


def test_builder_skips_auth_when_no_token(monkeypatch):
    monkeypatch.delenv("BRAIN_AUTH_TOKEN", raising=False)
    app = mcp_server._build_http_app()
    assert BearerAuthMiddleware not in _middleware_classes(app)
