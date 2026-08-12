# Deploying obsidian-brain as a remote MCP service

The brain runs as a containerized **streamable-HTTP MCP server** so any remote agent
can reach it. The image is self-contained (brain modules + `mcp_server.py` baked in);
the vault is bind-mounted at runtime.

## Where it runs

- **Compose:** `docker-compose.yml` in this directory (single-service example), service `obsidian-brain-mcp`
- **Image build context:** the repo root (`..`)
- **Host port:** `8053` → container `8000`
- **Endpoints:** `http://<host>:8053/mcp` (MCP), `http://<host>:8053/health` (liveness)
- **Network:** a plain local bridge by default; if LM Studio runs in Docker, attach
  this service to that network and point `LM_BASE_URL` at the container name.

## Quick start

```bash
export BRAIN_AUTH_TOKEN=$(openssl rand -hex 32)     # gate every route
cd deploy
docker compose up -d --build obsidian-brain-mcp     # build + (re)start
docker compose logs -f obsidian-brain-mcp           # tail
curl -fsS http://localhost:8053/health              # liveness
```

Edit `docker-compose.yml` first: set the vault + backups volume paths
(`/path/to/vault`, `/path/to/backups`), the embeddings endpoint, and optionally the
`TZ` / `BRAIN_REFRESH_AT_HOUR` schedule. After editing brain code, `up -d --build`
rebuilds only the cheap COPY layer.

## Nightly index refresh (baked in)

The HTTP service starts a daemon thread that rebuilds the index automatically — no
host cron. It rebuilds once ~30s after boot (so a redeploy is immediately current)
and then **daily at the configured hour**. Only notes that changed trigger a re-embed;
the brief index file-swap is locked + atomic, so live queries never see a partial index.

Env (set in the compose service):

| Var | Default | Meaning |
|-----|---------|---------|
| `TZ` | unset | local time for the schedule |
| `BRAIN_REFRESH_AT_HOUR` | `3` | daily rebuild hour (local) |
| `BRAIN_REFRESH_ENABLED` | `1` | set `0` to disable the scheduler |
| `BRAIN_REFRESH_ON_START` | `1` | one refresh shortly after boot |
| `BRAIN_REFRESH_FORCE` | `0` | `1` = full re-embed nightly vs. rebuild-only-if-changed |

Verify: `docker logs obsidian-brain-mcp | grep refresh`.

## Nightly vault maintenance (baked in)

After each nightly index refresh, the same daemon thread runs two bundled scripts
against the vault (also baked into the image, so a clone gets them):

1. **`moc_linker.py`** — classifies every note into the right MOC, writes `moc:`
   frontmatter, and regenerates each note's semantic `## Related Notes` section
   (top-5 by embedding similarity). Idempotent; backups are written **outside the
   vault** so Obsidian never indexes them.
2. **`ledger_update.py`** — updates `open-action-items-ledger.md`: checks off open
   items with clear completion evidence in recently-edited notes (surgical line
   edits only) and appends newly-surfaced action items under a managed
   `<!-- ledger-auto -->` block. Backs up the ledger outside the vault first.

These need an OpenAI-compatible **chat** model (classification + ledger reasoning)
and an **embeddings** model (related links). Endpoints default to `LM_BASE_URL` so a
single-endpoint clone works; a llama-swap deployment points the chat model at its
own URL (e.g. `http://localhost:4004/v1`) and usually overrides the model names —
the code defaults assume llama-swap reasoning models that emit `reasoning_content`
plus `content`.

| Var | Default | Meaning |
|-----|---------|---------|
| `BRAIN_LINKER_ENABLED` | `1` | run the MOC/Related linker after refresh |
| `BRAIN_LEDGER_ENABLED` | `1` | run the action-items ledger update after refresh |
| `BRAIN_POSTREFRESH_ON_START` | `0` | also run these on boot (heavy; nightly always runs them) |
| `LINKER_CHAT_URL` | `LM_BASE_URL` | chat endpoint for the linker (and ledger, unless overridden) |
| `LINKER_CHAT_MODEL` | `qwen3.6-35b-a3b-mtp` | linker classification model |
| `LEDGER_CHAT_URL` | `LINKER_CHAT_URL` | chat endpoint for the ledger update |
| `LEDGER_CHAT_MODEL` | `LINKER_CHAT_MODEL` | ledger model — harder task, use a stronger model |

Embeddings reuse `LM_BASE_URL` + `EMBEDDING_MODEL`. Verify:
`docker logs obsidian-brain-mcp | grep -E 'linker|ledger'`.

## Security

The tools read **and write** the vault, so treat the endpoint as privileged:

- **Bind loopback + token.** Publish `127.0.0.1:8053:8000` and set `BRAIN_AUTH_TOKEN`
  (the example compose demands it). With no token the startup log prints
  `[auth] WARNING … UNAUTHENTICATED`.
- **DNS-rebinding.** The MCP SDK only auto-enables Host/Origin validation for a
  loopback *container* bind; this container binds `0.0.0.0`, so set
  `BRAIN_ALLOWED_HOSTS` to the host[:port] clients send. Without it the startup log
  prints a `[security] WARNING … protection is DISABLED`.
- **Remote access** goes through SSH port-forward or a reverse proxy (SWAG/Authelia)
  that terminates TLS — never a raw LAN publish.

| Var | Default | Meaning |
|-----|---------|---------|
| `BRAIN_AUTH_TOKEN` | *(unset = open)* | require `Authorization: Bearer <token>` (except `/health`) |
| `BRAIN_ALLOWED_HOSTS` | *(unset = off)* | comma-separated host[:port] allowlist; enables DNS-rebinding protection |
| `BRAIN_ALLOWED_ORIGINS` | `http://<each host>` | comma-separated browser `Origin` allowlist |

## Reverse proxy (SWAG)

To make the web UI and MCP endpoint reachable from any device without an SSH tunnel,
front the service with SWAG (or any TLS-terminating reverse proxy). SWAG reaches the
container by name over the docker network, so this works regardless of the loopback
host-port bind. A ready-made conf is at
[`deploy/swag/brain.subdomain.conf`](swag/brain.subdomain.conf) (uses the `brain.*`
wildcard subdomain; change `server_name` to match your domain).

- **Auth model: bearer token, uniform.** The brain's `BRAIN_AUTH_TOKEN` gates every
  route except `GET /health` and the `GET /ui` HTML shell — for the browser UI, the
  Obsidian plugin, and MCP agents alike. This is why the conf does **not** enable
  Authelia: an interactive login would break the plugin/agents (they present a
  bearer, not a session cookie). To add an Authelia login in front of the *browser
  shell only*, uncomment the `authelia-*` includes in `location /` (leave `/ui/api`,
  `/mcp`, `/refresh` bearer-only).
- **Allowlist.** Add your public host to `BRAIN_ALLOWED_HOSTS` and your public origin
  to `BRAIN_ALLOWED_ORIGINS` so proxied Host/Origin pass DNS-rebinding validation.
- **Install:** `docker cp deploy/swag/brain.subdomain.conf swag:/config/nginx/proxy-confs/`
  then `docker exec swag nginx -s reload`. Requires the `brain.*` name to resolve.

Accessing the UI: open `https://brain.example.com/ui`, click **Token**, paste the
value of `BRAIN_AUTH_TOKEN` (kept in the browser's `sessionStorage`). Point the
Obsidian plugin's base URL at the same host with the same token.

## Hosted Claude custom connector (OAuth shim)

Claude's "Add Custom Connector" dialog (claude.ai / Desktop / Cowork / mobile —
connecting from Anthropic's cloud infrastructure, not this host) only accepts an
OAuth Client ID/Secret; raw bearer-header auth is a separate, not-yet-generally-
available beta. `oauth.py` adds a minimal Authorization Code + PKCE shim so the
connector can complete OAuth against this server and end up holding exactly
`BRAIN_AUTH_TOKEN` as its bearer credential — `auth.py`'s `BearerAuthMiddleware`
needs no changes. Full design: `docs/maestro/specs/2026-07-17-oauth-shim-design.md`.

Config: `OAUTH_CLIENT_ID` (any stable string) and `OAUTH_CLIENT_SECRET`
(`openssl rand -hex 32`, distinct from `BRAIN_AUTH_TOKEN`), set alongside
`BRAIN_AUTH_TOKEN` in the env the compose service reads.

Setup: **Settings → Connectors → Add custom connector** → URL
`https://brain.example.com/mcp` → Advanced Settings → enter the same
`OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` → Add. Claude discovers
`/.well-known/oauth-authorization-server`, redirects through `/authorize`
(silent — real access is still gated by the client secret at `/token`, so no
consent screen is needed), and exchanges the code at `/token` for
`BRAIN_AUTH_TOKEN`.

Known residual risk (flagged at spec review, not yet observed in practice):
Claude's actual discovery request sequence for hosted connectors hasn't been
directly observed against a stock server. If the initial "Add" fails, check
`docker logs obsidian-brain-mcp` for the first request path hit — if Claude
expects RFC 9728 Protected Resource Metadata + a `WWW-Authenticate` challenge on
`/mcp` rather than fetching `/.well-known/oauth-authorization-server` directly,
that's the next thing to add.

## Transports

`mcp_server.py` supports both:

- **stdio** (default) — local agents (Claude Code / Copilot). See `CLAUDE.md`.
- **streamable-HTTP** — set `MCP_TRANSPORT=streamable-http` (the container default).

## Register with an MCP client

```jsonc
// Remote (streamable-HTTP). With BRAIN_AUTH_TOKEN set, pass it as a bearer header;
// reach the loopback-bound port via an SSH forward or reverse proxy, not the LAN.
{ "mcpServers": { "obsidian-brain": {
    "url": "http://localhost:8053/mcp",
    "headers": { "Authorization": "Bearer <token>" }
} } }
```