"""Bearer-token auth middleware for the streamable-HTTP transport (H1)."""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from auth import BearerAuthMiddleware


def _app(token):
    async def health(_req):
        return JSONResponse({"status": "ok"})

    async def mcp(_req):
        return JSONResponse({"tool": "ran"})

    app = Starlette(routes=[Route("/health", health), Route("/mcp", mcp, methods=["POST"])])
    app.add_middleware(BearerAuthMiddleware, token=token, public_paths={"/health"})
    return TestClient(app)


def test_public_health_path_needs_no_token():
    assert _app("s3cr3t").get("/health").status_code == 200


def test_protected_path_rejects_missing_token():
    assert _app("s3cr3t").post("/mcp").status_code == 401


def test_protected_path_rejects_wrong_token():
    c = _app("s3cr3t")
    r = c.post("/mcp", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_protected_path_allows_correct_token():
    c = _app("s3cr3t")
    r = c.post("/mcp", headers={"Authorization": "Bearer s3cr3t"})
    assert r.status_code == 200
    assert r.json() == {"tool": "ran"}


def test_non_ascii_authorization_header_is_401_not_500():
    # A raw header with a non-latin-1/high byte decodes (latin-1) to a non-ASCII
    # str; hmac.compare_digest on a non-ASCII str raises TypeError. Drive dispatch
    # directly (httpx would reject the header client-side) and require a clean 401,
    # not a raised TypeError / 500 (audit finding low-8).
    import asyncio
    from starlette.requests import Request

    async def _call_next(_req):
        return JSONResponse({"tool": "ran"})

    mw = BearerAuthMiddleware(app=None, token="s3cr3t", public_paths={"/health"})
    scope = {
        "type": "http", "method": "POST", "path": "/mcp",
        "headers": [(b"authorization", b"Bearer \xff\xfe")],
    }
    resp = asyncio.run(mw.dispatch(Request(scope), _call_next))
    assert resp.status_code == 401
