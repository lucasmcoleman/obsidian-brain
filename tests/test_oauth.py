"""Authorization Code + PKCE shim so Claude's hosted connector (OAuth-only dialog)
can authenticate against the existing BRAIN_AUTH_TOKEN bearer gate."""
import base64
import hashlib

from starlette.testclient import TestClient

import mcp_server
from oauth import REDIRECT_URI


def _pkce_pair():
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _client(monkeypatch, token="brain-tok", client_id="cid", client_secret="csecret"):
    monkeypatch.setenv("BRAIN_AUTH_TOKEN", token)
    monkeypatch.setenv("OAUTH_CLIENT_ID", client_id)
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", client_secret)
    return TestClient(mcp_server._build_http_app())


def test_metadata_is_public_and_has_no_registration_endpoint(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_endpoint"].endswith("/authorize")
    assert body["token_endpoint"].endswith("/token")
    assert "registration_endpoint" not in body  # signals the non-DCR fallback


def test_metadata_also_served_at_path_insertion_variant(monkeypatch):
    # RFC 8414 3.1: some clients probe /.well-known/<doc>/<path> for a resource at /mcp.
    c = _client(monkeypatch)
    assert c.get("/.well-known/oauth-authorization-server/mcp").status_code == 200


def test_metadata_trusts_x_forwarded_proto_from_the_reverse_proxy(monkeypatch):
    # SWAG terminates TLS and proxies to the container over plain HTTP, so the
    # request the app sees is scheme=http even though Claude connected over
    # https — the advertised endpoints must reflect the public https URL, not
    # the internal hop, or an external OAuth client may refuse them.
    c = _client(monkeypatch)
    r = c.get("/.well-known/oauth-authorization-server",
              headers={"X-Forwarded-Proto": "https"})
    body = r.json()
    assert body["issuer"].startswith("https://")
    assert body["authorization_endpoint"].startswith("https://")
    assert body["token_endpoint"].startswith("https://")


def test_metadata_falls_back_to_request_scheme_without_a_proxy(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/.well-known/oauth-authorization-server")
    assert r.json()["issuer"].startswith("http://")  # TestClient's own scheme, no proxy in front


def test_authorize_rejects_wrong_client_id(monkeypatch):
    c = _client(monkeypatch)
    _, challenge = _pkce_pair()
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": "wrong", "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
    })
    assert r.status_code == 400


def test_authorize_rejects_wrong_redirect_uri_without_redirecting(monkeypatch):
    c = _client(monkeypatch)
    _, challenge = _pkce_pair()
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": "cid", "redirect_uri": "https://evil.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
    }, follow_redirects=False)
    assert r.status_code == 400  # never redirect to an unrecognized redirect_uri
    assert "location" not in {k.lower() for k in r.headers.keys()}


def test_authorize_rejects_missing_pkce(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": "cid", "redirect_uri": REDIRECT_URI,
    }, follow_redirects=False)
    assert r.status_code == 400


def test_authorize_success_redirects_with_code_and_state(monkeypatch):
    c = _client(monkeypatch)
    _, challenge = _pkce_pair()
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": "cid", "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
    }, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith(REDIRECT_URI)
    assert "state=xyz" in location
    assert "code=" in location


def _get_code(client, challenge, state="s"):
    r = client.get("/authorize", params={
        "response_type": "code", "client_id": "cid", "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    }, follow_redirects=False)
    location = r.headers["location"]
    query = location.split("?", 1)[1]
    params = dict(p.split("=", 1) for p in query.split("&"))
    return params["code"]


def test_token_round_trip_returns_brain_auth_token(monkeypatch):
    c = _client(monkeypatch, token="the-real-token")
    verifier, challenge = _pkce_pair()
    code = _get_code(c, challenge)

    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": "cid", "client_secret": "csecret", "code_verifier": verifier,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "the-real-token"
    assert body["token_type"] == "Bearer"


def test_token_accepts_basic_auth_for_client_credentials(monkeypatch):
    c = _client(monkeypatch, token="the-real-token")
    verifier, challenge = _pkce_pair()
    code = _get_code(c, challenge)

    basic = base64.b64encode(b"cid:csecret").decode("ascii")
    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }, headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 200
    assert r.json()["access_token"] == "the-real-token"


def test_token_rejects_wrong_client_secret(monkeypatch):
    c = _client(monkeypatch)
    verifier, challenge = _pkce_pair()
    code = _get_code(c, challenge)

    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": "cid", "client_secret": "nope", "code_verifier": verifier,
    })
    assert r.status_code == 400


def test_token_rejects_wrong_code_verifier(monkeypatch):
    c = _client(monkeypatch)
    verifier, challenge = _pkce_pair()
    code = _get_code(c, challenge)

    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": "cid", "client_secret": "csecret", "code_verifier": "b" * 64,
    })
    assert r.status_code == 400


def test_token_rejects_replayed_code(monkeypatch):
    c = _client(monkeypatch)
    verifier, challenge = _pkce_pair()
    code = _get_code(c, challenge)
    data = {
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": "cid", "client_secret": "csecret", "code_verifier": verifier,
    }
    assert c.post("/token", data=data).status_code == 200
    assert c.post("/token", data=data).status_code == 400  # single-use


def test_token_rejects_mismatched_redirect_uri(monkeypatch):
    c = _client(monkeypatch)
    verifier, challenge = _pkce_pair()
    code = _get_code(c, challenge)

    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": "https://evil.example/cb",
        "client_id": "cid", "client_secret": "csecret", "code_verifier": verifier,
    })
    assert r.status_code == 400


def test_token_rejects_unknown_code(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": "made-up", "redirect_uri": REDIRECT_URI,
        "client_id": "cid", "client_secret": "csecret", "code_verifier": "x" * 64,
    })
    assert r.status_code == 400


def test_oauth_paths_are_public_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("BRAIN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret")
    from auth import BearerAuthMiddleware
    app = mcp_server._build_http_app()
    mw = next(m for m in app.user_middleware if m.cls is BearerAuthMiddleware)
    public = mw.kwargs["public_paths"]
    assert "/.well-known/oauth-authorization-server" in public
    assert "/.well-known/oauth-authorization-server/mcp" in public
    assert "/authorize" in public
    assert "/token" in public
