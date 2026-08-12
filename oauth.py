"""Minimal OAuth 2.0 Authorization Code + PKCE shim.

Claude's hosted "Add Custom Connector" dialog only accepts an OAuth Client ID/
Secret (raw bearer-header auth is a separate, not-yet-available beta), so this
gives the connector an OAuth surface to complete against. /token hands back the
existing BRAIN_AUTH_TOKEN itself as the access_token, so auth.BearerAuthMiddleware
needs zero changes — the shim is pure front door, not a parallel auth system.
See docs/maestro/specs/2026-07-17-oauth-shim-design.md for the full design.

Single personal user, single static client_id/client_secret: no DCR, no refresh
tokens (the underlying token doesn't expire), no consent UI (real access is
still gated by client_secret at /token, which only the configured connector
holds — /authorize can safely auto-approve).
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

from starlette.responses import JSONResponse, RedirectResponse

# All of Claude's hosted surfaces (claude.ai, Desktop, Cowork, mobile) authenticate
# from Anthropic's cloud infrastructure through this one callback.
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
CODE_TTL_SECONDS = 120

# code -> {client_id, redirect_uri, code_challenge, expires_at}. In-memory and
# single-use by design: a lost-on-restart code just means a one-time interactive
# connector setup is retried, never a steady-state request path.
_codes = {}


def _eq(a: str, b: str) -> bool:
    """Constant-time compare, tolerant of missing/empty input (never raises)."""
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def _pkce_matches(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256((code_verifier or "").encode("utf-8")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return _eq(computed, code_challenge)


def authorization_server_metadata(issuer: str) -> dict:
    """RFC 8414 subset. No registration_endpoint: this is the deliberate signal
    that makes Claude fall back to the manually-configured client_id/secret
    instead of attempting Dynamic Client Registration."""
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    }


async def handle_authorize(request):
    expected_client_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
    if not expected_client_id:
        return JSONResponse(
            {"error": "server_error", "error_description": "OAuth not configured"},
            status_code=500)

    params = request.query_params
    if params.get("response_type") != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if not _eq(params.get("client_id", ""), expected_client_id):
        return JSONResponse({"error": "unauthorized_client"}, status_code=400)
    # Never redirect on a redirect_uri we didn't issue — an open redirect would
    # let an attacker-controlled client_id (were one ever accepted) or a typo'd
    # config exfiltrate a code to an arbitrary URI.
    redirect_uri = params.get("redirect_uri", "")
    if redirect_uri != REDIRECT_URI:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "redirect_uri not recognized"},
            status_code=400)
    code_challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not code_challenge:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "PKCE S256 code_challenge required"},
            status_code=400)

    code = secrets.token_urlsafe(32)
    _codes[code] = {
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "expires_at": time.time() + CODE_TTL_SECONDS,
    }
    location = f"{redirect_uri}?code={code}"
    state = params.get("state")
    if state:
        location += f"&state={state}"
    return RedirectResponse(location, status_code=302)


def _client_credentials_from_basic(request) -> tuple[str, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return "", ""
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:
        return "", ""
    client_id, _, client_secret = decoded.partition(":")
    return client_id, client_secret


async def handle_token(request):
    expected_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
    expected_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
    access_token = os.environ.get("BRAIN_AUTH_TOKEN", "").strip()
    if not expected_id or not expected_secret or not access_token:
        return JSONResponse(
            {"error": "server_error", "error_description": "OAuth not configured"},
            status_code=500)

    form = await request.form()
    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    if not client_id and not client_secret:
        client_id, client_secret = _client_credentials_from_basic(request)

    if not (_eq(client_id, expected_id) and _eq(client_secret, expected_secret)):
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if form.get("grant_type") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    code = form.get("code", "")
    entry = _codes.get(code)
    if not entry or entry["expires_at"] < time.time():
        _codes.pop(code, None)
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if form.get("redirect_uri", "") != entry["redirect_uri"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _pkce_matches(form.get("code_verifier", ""), entry["code_challenge"]):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    del _codes[code]  # single-use: consumed only once every check has passed
    return JSONResponse({"access_token": access_token, "token_type": "Bearer"})
