# OAuth 2.0 shim for the hosted Claude custom connector

**Status:** approved by user, ready for implementation planning
**Date:** 2026-07-17

## Problem

The brain's HTTP transport is gated by a single static `BRAIN_AUTH_TOKEN` checked via
`auth.BearerAuthMiddleware` (`auth.py`). This works for local stdio registration and for
manual `curl`/UI use, but Claude's hosted "Add Custom Connector" flow (claude.ai / Desktop /
Cowork / mobile — connecting from Anthropic's cloud infrastructure, not the user's device)
only offers two auth options in its dialog:

- **Request headers** (send an arbitrary header like `Authorization: Bearer <token>` on every
  request) — currently in limited beta; not available on this account.
- **OAuth Client ID / Client Secret** — the only option actually present in the dialog.

The brain has no OAuth server today, so neither field currently does anything useful. This
spec adds the minimum OAuth 2.0 surface for Claude's hosted connector to complete an
Authorization Code + PKCE flow against the brain itself, ending with Claude holding exactly
the existing `BRAIN_AUTH_TOKEN` as its bearer credential — so `BearerAuthMiddleware` and every
other route need zero changes.

Per Anthropic's connector docs (`/connectors/building`, fetched 2026-07-17): Claude supports
the 2025-03-26 / 2025-06-18 / 2025-11-25 MCP auth specs, and "Custom credentials for non-DCR
servers" — i.e. if the server's metadata omits a `registration_endpoint`, Claude uses whatever
Client ID/Secret the user manually entered in Advanced Settings instead of attempting Dynamic
Client Registration. This spec relies on that fallback rather than implementing DCR.

## Scope

Single personal user, single static set of OAuth credentials, self-hosted behind SWAG at
`https://brain.example.com`. This is explicitly not a multi-tenant or multi-client OAuth
server — no user accounts, no consent UI beyond a silent redirect, no refresh tokens (the
underlying access token never expires).

## Design

### New module: `oauth.py`

Mirrors `auth.py`'s single-responsibility style: one small module, no new external deps
(stdlib `secrets`, `hashlib`, `time`, `hmac`).

- **In-memory store** for pending authorization codes: `dict[code] -> {client_id,
  redirect_uri, code_challenge, expires_at}`. TTL ~120s. Single-use — popped (not just read)
  on redemption at `/token`, so a code can't be replayed.
- Lost on process restart. Acceptable: this is a one-time interactive grant per connector
  install, not a steady-state request path. If a restart lands mid-flow, Claude's connector
  setup fails once and the user retries "Add" — no different from any transient network blip
  during OAuth setup elsewhere.
- Two entry points, called from `custom_route` handlers in `mcp_server.py` (same pattern as
  the existing `/ui/api/*` handlers delegating to `brain.py`/`tasks.py`):
  - `handle_authorize(request) -> Response`
  - `handle_token(request) -> JSONResponse`

### Endpoints (added to `mcp_server.py` via `@mcp.custom_route`)

**`GET /.well-known/oauth-authorization-server`**
Static JSON (RFC 8414 subset):
```json
{
  "issuer": "https://brain.example.com",
  "authorization_endpoint": "https://brain.example.com/authorize",
  "token_endpoint": "https://brain.example.com/token",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"]
}
```
No `registration_endpoint` key — this is the deliberate signal that triggers Claude's
"Custom credentials for non-DCR servers" fallback to the manually-entered Client ID/Secret.

**`GET /authorize`**
Query params: `response_type=code`, `client_id`, `redirect_uri`, `code_challenge`,
`code_challenge_method=S256`, `state` (opaque, echoed back verbatim).

Validation, in order, each failure returning a 400 **without redirecting** (an unverified
`redirect_uri` must never receive a callback):
1. `response_type == "code"`
2. `client_id` matches `OAUTH_CLIENT_ID` (env) via `hmac.compare_digest`
3. `redirect_uri` exactly equals the fixed constant `https://claude.ai/api/mcp/auth_callback`
4. `code_challenge_method == "S256"` and `code_challenge` present

On success: mint `secrets.token_urlsafe(32)`, store it with the four fields above + expiry,
and issue a `302` to `{redirect_uri}?code={code}&state={state}` — silent, no confirmation
page (approved: the user chose "silent auto-approve" since real access is still gated by
`client_secret` at `/token`, which only the user's own Claude connector config holds).

**`POST /token`**
Body (`application/x-www-form-urlencoded`, standard OAuth): `grant_type=authorization_code`,
`code`, `redirect_uri`, `client_id`, `client_secret` (or HTTP Basic — support both since
Claude's docs don't commit to one), `code_verifier`.

Validation, in order, each failure returning `400` with an OAuth-shaped error body
(`{"error": "invalid_grant"}` etc., not a bare 401 — this is a token endpoint, not the
resource server):
1. `client_id` + `client_secret` match `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` (env),
   `hmac.compare_digest` on both, same constant-time-compare discipline as
   `BearerAuthMiddleware`.
2. `code` exists in the store, is unexpired, and `redirect_uri` matches what was stored.
3. `code_verifier` hashes (SHA-256, base64url, no padding) to the stored `code_challenge`.
4. Pop the code (single-use) only after all checks pass.

On success: `{"access_token": "<BRAIN_AUTH_TOKEN>", "token_type": "Bearer"}` — no
`expires_in`, no `refresh_token`. The token literally *is* `BRAIN_AUTH_TOKEN`; Claude will
send `Authorization: Bearer <BRAIN_AUTH_TOKEN>` on every subsequent `/mcp` request, which
`BearerAuthMiddleware` already accepts unchanged. (Approved trade-off: this means revoking
Claude's connector access requires rotating `BRAIN_AUTH_TOKEN` for every other consumer too —
acceptable for a single-user setup; noted as a known limitation, not a gap to fix now.)

### Wiring into `mcp_server.py` / `auth.py`

- Register the three routes with `@mcp.custom_route(...)`.
- Add all three paths (`/.well-known/oauth-authorization-server`, `/authorize`, `/token`) to
  `BearerAuthMiddleware`'s `public_paths` set at the call site (currently
  `{"/health", "/ui"}`, `mcp_server.py:546`) — these must be reachable *before* Claude holds a
  bearer token. This is safe because:
  - `/authorize` only ever hands back a short-lived, single-use, PKCE-bound code tied to a
    pre-registered `redirect_uri` — useless without also knowing `client_secret`.
  - `/token` is itself gated by `client_secret`, checked constant-time.
- No changes to `BearerAuthMiddleware` itself, to any existing route, or to the tool-gating
  logic in `brain.py`/`tasks.py`.

### Config

Two new env vars, set alongside `BRAIN_AUTH_TOKEN` in the compose block
(`deploy/README.md` service definition):
- `OAUTH_CLIENT_ID` — any stable string (e.g. `obsidian-brain`); not secret.
- `OAUTH_CLIENT_SECRET` — generate with `openssl rand -hex 32`, distinct from
  `BRAIN_AUTH_TOKEN`.

Both values are re-entered into Claude's **Add Custom Connector → Advanced Settings** dialog,
with the connector URL set to `https://brain.example.com/mcp` (unchanged from the plain
bearer-token attempt).

`deploy/README.md` gets a new subsection documenting this setup path (mirroring the existing
"Reverse proxy (SWAG)" section's level of detail) once implemented.

### Testing

New `tests/test_oauth.py`, following the existing fake-app/`TestClient` pattern used in
`tests/test_auth.py` and `tests/test_auth_wiring.py`:

- Full round trip: `GET /authorize` with valid PKCE → 302 with `code` → `POST /token` with
  matching `code_verifier` → 200 with `access_token == BRAIN_AUTH_TOKEN`.
- `/authorize` rejects: wrong `client_id`, wrong `redirect_uri` (must NOT redirect on this
  failure — assert on status code + body, not a Location header), missing/malformed
  `code_challenge`.
- `/token` rejects: wrong `client_secret`, wrong `code_verifier`, expired code, replayed
  (already-consumed) code, mismatched `redirect_uri` between `/authorize` and `/token` calls.
- `mcp_server._build_http_app()` wiring test (parallel to `test_auth_wiring.py`): the three
  new paths are present in `public_paths` whenever `BRAIN_AUTH_TOKEN` (and thus the
  middleware) is installed.

## Out of scope / explicitly not doing

- Dynamic Client Registration (`/register`) — relying on Claude's documented non-DCR
  fallback instead.
- Refresh tokens / token expiry — the underlying credential doesn't expire today either.
- Independent revocation of Claude's access (a wrapped/mapped token) — rejected in favor of
  reusing `BRAIN_AUTH_TOKEN` directly; revisit only if a real need for independent revocation
  shows up.
- A consent/confirmation page at `/authorize` — silent redirect approved instead.
- Any change to the Obsidian plugin, local stdio registration, or `/ui/api/*` — this shim is
  additive and only serves the hosted-connector auth path.
