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
      - "8053:8000"
    volumes:
      - /server/obsidian:/vault
    environment:
      - OBSIDIAN_VAULT_PATH=/vault
      - LM_BASE_URL=http://192.168.0.29:1234/v1
      - EMBEDDING_MODEL=text-embedding-nomic-embed-text-v2-moe
      - PYTHONUNBUFFERED=1
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

## Transports

`mcp_server.py` supports both:

- **stdio** (default) — local agents (Claude Code / Copilot). See `CLAUDE.md`.
- **streamable-HTTP** — set `MCP_TRANSPORT=streamable-http` (the container default).

## Register with an MCP client

```jsonc
// Remote (streamable-HTTP)
{ "mcpServers": { "obsidian-brain": { "url": "http://<host>:8053/mcp" } } }
```
