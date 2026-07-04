# Deploying obsidian-brain as a remote MCP service

The brain runs as a containerized **streamable-HTTP MCP server** so any remote agent
(or the local Hermes agent) can reach it. The image is self-contained (brain modules +
`mcp_server.py` baked in); the vault is bind-mounted at runtime.

## Where it runs

- **Compose:** `/server/docker/compose/mcp/docker-compose.yml` (service `obsidian-brain-mcp`)
- **Image build context:** this directory (`/server/programming/obsidian-brain`)
- **Host port:** `8053` → container `8000`
- **Endpoint:** `http://<host>:8053/mcp` (MCP), `http://<host>:8053/health` (liveness)
- **Network:** `backend` (external) — same as `obsidian-mcp`; reaches LM Studio on the LAN

## Service block

The block below is appended under `services:` in the mcp compose file. Kept here so the
deployment is reproducible from the repo (the compose file itself lives outside the repo).

```yaml
  obsidian-brain-mcp:
    build:
      context: /server/programming/obsidian-brain
    container_name: obsidian-brain-mcp
    restart: unless-stopped
    ports:
      # Publish to LOOPBACK ONLY — the tools read AND write the vault, so never
      # expose this on the LAN. Reach it remotely via SSH port-forward or a
      # reverse proxy (SWAG/Authelia). (audit findings H-1 / M-N)
      - "127.0.0.1:8053:8000"
    volumes:
      - /server/obsidian:/vault
      - /server/.obsidian-moc-backups:/backups   # durable pre-edit backups (outside the vault)
    environment:
      - OBSIDIAN_VAULT_PATH=/vault
      - LM_BASE_URL=http://192.168.0.29:1234/v1
      - EMBEDDING_MODEL=text-embedding-nomic-embed-text-v2-moe
      # Require a bearer token on every HTTP request except /health (audit H-1).
      # Generate once: `openssl rand -hex 32`. Without it the tools are open.
      - BRAIN_AUTH_TOKEN=CHANGE_ME_openssl_rand_hex_32
      # Enable DNS-rebinding/Origin protection despite the 0.0.0.0 container bind
      # (the SDK only auto-enables it for a loopback bind) — audit M-N.
      - BRAIN_ALLOWED_HOSTS=localhost:8053,127.0.0.1:8053
      - PYTHONUNBUFFERED=1
      - TZ=America/New_York
      - BRAIN_REFRESH_AT_HOUR=3
      # Nightly vault maintenance after the index refresh (see section below).
      - BRAIN_LINKER_ENABLED=1
      - BRAIN_LEDGER_ENABLED=1
      - LINKER_CHAT_URL=http://192.168.0.29:4004/v1
      - LINKER_CHAT_MODEL=qwen3.5:9b                          # NPU (FastFlowLM), GPU-free
      - LEDGER_CHAT_MODEL=unsloth/Qwen3.6-35B-A3B-MTP-GGUF    # stronger; ledger only
      - MOC_BACKUP_DIR=/backups
    networks:
      backend:
        aliases:
          - obsidian-brain-mcp
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
```

## Operate

```bash
cd /server/docker/compose/mcp
docker compose up -d --build obsidian-brain-mcp   # build + (re)start
docker compose logs -f obsidian-brain-mcp         # tail
curl -fsS http://localhost:8053/health            # liveness
```

After editing brain code, `up -d --build` rebuilds only the cheap COPY layer.

## Nightly index refresh (baked in)

The HTTP service starts a daemon thread that rebuilds the index automatically — no
host cron. It rebuilds once ~30s after boot (so a redeploy is immediately current)
and then **daily at the configured hour**. Only notes that changed trigger a re-embed;
the brief index file-swap is locked + atomic, so live queries never see a partial index.

Env (set in the compose service):

| Var | Default | Meaning |
|-----|---------|---------|
| `TZ` | `America/New_York` | local time for the schedule |
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
single-endpoint clone works; this deployment points the chat model at llama-swap.

| Var | Default | Meaning |
|-----|---------|---------|
| `BRAIN_LINKER_ENABLED` | `1` | run the MOC/Related linker after refresh |
| `BRAIN_LEDGER_ENABLED` | `1` | run the action-items ledger update after refresh |
| `BRAIN_POSTREFRESH_ON_START` | `0` | also run these on boot (heavy; nightly always runs them) |
| `LINKER_CHAT_URL` | `LM_BASE_URL` | chat endpoint for the linker (and ledger, unless overridden) |
| `LINKER_CHAT_MODEL` | `qwen3.6-35b-a3b-mtp` | linker classification model (NPU `qwen3.5:9b` here) |
| `LEDGER_CHAT_URL` | `LINKER_CHAT_URL` | chat endpoint for the ledger update |
| `LEDGER_CHAT_MODEL` | `LINKER_CHAT_MODEL` | ledger model — harder task, use a stronger model (35B here) |

Embeddings reuse `LM_BASE_URL` + `EMBEDDING_MODEL`. Verify:
`docker logs obsidian-brain-mcp | grep -E 'linker|ledger'`.

## Security

The tools read **and write** the vault, so treat the endpoint as privileged:

- **Bind loopback + token.** Publish `127.0.0.1:8053:8000` and set `BRAIN_AUTH_TOKEN`
  (above). With no token the startup log prints `[auth] WARNING … UNAUTHENTICATED`.
- **DNS-rebinding.** The MCP SDK only auto-enables Host/Origin validation for a
  loopback *container* bind; this container binds `0.0.0.0`, so set
  `BRAIN_ALLOWED_HOSTS` to the host[:port] clients send. Without it the startup log
  prints a `[security] WARNING … protection is DISABLED`.
- **Remote access** goes through SSH port-forward or a reverse proxy (SWAG/Authelia)
  that terminates TLS and can inject the bearer header — never a raw LAN publish.

| Var | Default | Meaning |
|-----|---------|---------|
| `BRAIN_AUTH_TOKEN` | *(unset = open)* | require `Authorization: Bearer <token>` (except `/health`) |
| `BRAIN_ALLOWED_HOSTS` | *(unset = off)* | comma-separated host[:port] allowlist; enables DNS-rebinding protection |
| `BRAIN_ALLOWED_ORIGINS` | `http://<each host>` | comma-separated browser `Origin` allowlist |

## Reverse proxy (SWAG)

The live deployment fronts the brain with SWAG at **`https://brain.lucascoleman.me`**
so the web UI and MCP endpoint are reachable from any device without an SSH tunnel.
SWAG reaches the container over the `backend` docker network (`obsidian-brain-mcp:8000`),
so this works regardless of the loopback host-port bind. The proxy conf lives at
[`deploy/swag/brain.subdomain.conf`](swag/brain.subdomain.conf) (mirror of the file
in the SWAG volume at `/config/nginx/proxy-confs/`, kept here so the setup is
reproducible from the repo).

- **Auth model: bearer token, uniform.** The brain's `BRAIN_AUTH_TOKEN` gates every
  route except `GET /health` and the `GET /ui` HTML shell — for the browser UI, the
  Obsidian plugin, and MCP agents alike. This is why the conf does **not** enable
  Authelia: an interactive login would break the plugin/agents (they present a
  bearer, not a session cookie). To add an Authelia login in front of the *browser
  shell only*, uncomment the `authelia-*` includes in `location /` (leave `/ui/api`,
  `/mcp`, `/refresh` bearer-only).
- **Allowlist.** `brain.lucascoleman.me` is added to `BRAIN_ALLOWED_HOSTS` and
  `https://brain.lucascoleman.me` to `BRAIN_ALLOWED_ORIGINS` (above) so the proxied
  Host/Origin pass DNS-rebinding validation.
- **Install:** `docker cp deploy/swag/brain.subdomain.conf swag:/config/nginx/proxy-confs/`
  then `docker exec swag nginx -s reload`. Requires the `brain.*` name to resolve
  (covered by the existing `*.lucascoleman.me` wildcard DNS + cert).

Accessing the UI: open `https://brain.lucascoleman.me/ui`, click **Token**, paste
the value of `BRAIN_AUTH_TOKEN` (kept in the browser's `sessionStorage`). Point the
Obsidian plugin's base URL at the same host with the same token.

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
