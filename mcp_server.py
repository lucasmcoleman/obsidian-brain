#!/usr/bin/env python3
"""
Obsidian Brain MCP Server.

Exposes the brain as MCP tools over either stdio (local agents such as Claude
Code / Copilot Coworker) or streamable-HTTP (containerized / remote agents).
Tool names and behavior are identical across transports.

Usage:
    python mcp_server.py                 # stdio (default)
    python mcp_server.py --http          # streamable-HTTP on 0.0.0.0:8000/mcp
    MCP_TRANSPORT=streamable-http python mcp_server.py

Environment:
    OBSIDIAN_VAULT_PATH   path to the Obsidian vault (default /server/obsidian)
    LM_BASE_URL           LM Studio URL (default http://192.168.0.29:1234/v1)
    EMBEDDING_MODEL       embedding model id
    MCP_TRANSPORT         stdio | streamable-http   (default stdio)
    MCP_HOST              bind host for HTTP         (default 0.0.0.0)
    MCP_PORT              bind port for HTTP         (default 8000)
    MCP_PATH              streamable-HTTP path       (default /mcp)
    BRAIN_AUTH_TOKEN      require Authorization: Bearer <token> on HTTP (unset = off)
    BRAIN_ALLOWED_HOSTS   comma-separated host[:port] allowlist enabling DNS-rebinding
                          protection when binding 0.0.0.0 (unset = protection off)
    BRAIN_ALLOWED_ORIGINS comma-separated Origin allowlist (default: http://<each host>)

Local registration (stdio), e.g. in ~/.claude/settings.json:
    {
      "mcpServers": {
        "obsidian-brain": {
          "command": "python3",
          "args": ["/server/programming/obsidian-brain/mcp_server.py"],
          "env": {"OBSIDIAN_VAULT_PATH": "/server/obsidian"}
        }
      }
    }
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# Make sibling modules importable no matter the working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from brain import (
    query_brain,
    write_entity_note,
    append_insight,
    build_index,
)
from tasks import scan_tasks, complete_task, count_tasks, format_tasks
from config import (
    VAULT_PATH,
    INDEX_PATH,
    METADATA_PATH,
    ENTITIES_DIR,
    EMBEDDING_MODEL,
    LM_BASE_URL,
)

def _transport_security():
    """Explicit DNS-rebinding / Host+Origin allowlist for streamable-HTTP. The MCP
    SDK auto-enables this ONLY when binding a loopback host; this service binds
    0.0.0.0 so a published host port can reach it, which silently disables it — a
    browser the user merely visits could then DNS-rebind to the vault. Opt in by
    setting BRAIN_ALLOWED_HOSTS to the host[:port] value(s) clients send
    (comma-separated), optionally BRAIN_ALLOWED_ORIGINS (audit finding M-N)."""
    hosts = [h.strip() for h in os.environ.get("BRAIN_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not hosts:
        return None
    origins = [o.strip() for o in os.environ.get("BRAIN_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if not origins:
        origins = [f"http://{h}" for h in hosts]
    from mcp.server.transport_security import TransportSecuritySettings
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


mcp = FastMCP(
    "obsidian-brain",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path=os.environ.get("MCP_PATH", "/mcp"),
    transport_security=_transport_security(),
    # Stateless streamable-HTTP: every request is self-contained, so there is no
    # server-side session ID that can expire/recycle out from under a long-lived
    # client. This eliminates the keepalive churn where the client's periodic
    # list_tools hit a stale session and got "Session terminated"/404, forcing a
    # reconnect. All tools here are plain request/response (no server-initiated
    # streaming), so stateless mode is a clean fit. Override with
    # BRAIN_STATELESS_HTTP=0 if a future feature needs sticky sessions.
    stateless_http=os.environ.get("BRAIN_STATELESS_HTTP", "1").strip().lower()
    in ("1", "true", "yes", "on"),
)


@mcp.tool()
def brain_query(query: str, top_k: int = 5) -> str:
    """Query the Obsidian vault for contextually relevant notes.

    Call this before answering questions about people, projects, clients,
    decisions, or past conversations. Pass the user's full natural-language
    message as `query`. `top_k` caps the number of distinct notes returned
    (default 5). Returns a formatted context block of the most relevant note
    excerpts; synthesize them into your answer rather than pasting them raw.
    """
    return query_brain(query, top_k=top_k)


@mcp.tool()
def brain_write_entity(name: str, initial_content: str = "") -> str:
    """Create a new entity note in _brain/entities/ for a person, project, or concept.

    Use when you encounter something new worth tracking persistently. Returns a
    JSON result with a status of "created" or "exists" (an existing entity is
    never overwritten — use brain_append_insight to add to it). Confirm with the
    user before writing unless they explicitly said to remember it.
    """
    return json.dumps(
        write_entity_note(entity_name=name, initial_content=initial_content), indent=2
    )


@mcp.tool()
def brain_append_insight(note_path: str, insight: str, context: str = "") -> str:
    """Append a new insight, decision, or fact to an existing vault note.

    `note_path` may be absolute or relative to the vault (e.g. 'Projects/Delta.md').
    Use after a conversation where you learn something new about a person, project,
    or decision. `context` optionally records what prompted the insight. Returns
    a JSON result with an explicit "ok"/"error" status. Confirm with the user
    before writing unless they explicitly said to remember it.
    """
    return json.dumps(
        append_insight(note_path=note_path, insight=insight, context=context), indent=2
    )


@mcp.tool()
def brain_tasks(status: str = "open", query: str = "") -> str:
    """List checkbox tasks across the whole vault — exhaustive and deterministic.

    Use this (not brain_query) for "what are my open tasks" style questions, where
    you need every task, not just semantically-similar notes. `status` is 'open',
    'done', or 'all'. Optional `query` filters to tasks whose text or note path
    contains that substring (e.g. a project name). Results are grouped by note.
    """
    tasks = scan_tasks(status=status)
    if query:
        q = query.lower()
        tasks = [t for t in tasks if q in t["text"].lower() or q in t["note_path"].lower()]
    return format_tasks(tasks, status)


@mcp.tool()
def brain_complete_task(note_path: str, match: str) -> str:
    """Mark an open task complete in place: flips '- [ ]' to '- [x] … ✅ <date>'.

    `note_path` is absolute or vault-relative. `match` is a substring identifying
    the open task; it must match exactly one open task in that note (otherwise the
    call returns an error/ambiguous result and writes nothing). Confirm with the
    user before completing unless they clearly said the task is done.
    """
    return json.dumps(complete_task(note_path=note_path, match=match), indent=2)


@mcp.tool()
def brain_build_index(force: bool = False) -> str:
    """Rebuild the FAISS index from the current vault state.

    Use if notes were added or changed externally and the index is stale. Set
    `force=True` to rebuild unconditionally. Returns a JSON summary of the build.
    """
    return json.dumps(build_index(force=force), indent=2)


@mcp.tool()
def brain_status() -> str:
    """Report brain state: vault path, embedding model, index stats, entity count."""
    status: dict = {
        "vault_path": VAULT_PATH,
        "embedding_model": EMBEDDING_MODEL,
        "lm_base_url": LM_BASE_URL,
    }

    if os.path.exists(METADATA_PATH):
        try:
            meta = json.loads(Path(METADATA_PATH).read_text())
            status.update({
                "notes_indexed": meta.get("num_notes", "unknown"),
                "chunks": meta.get("num_chunks", "unknown"),
                "embedding_model": meta.get("embedding_model", EMBEDDING_MODEL),
                "index_mtime": meta.get("index_mtime", "unknown"),
            })
        except (json.JSONDecodeError, OSError) as e:
            status["notes_indexed"] = f"metadata unreadable ({e})"
    else:
        status["notes_indexed"] = "no index"

    if os.path.exists(INDEX_PATH):
        import faiss
        status["index_vector_count"] = faiss.read_index(INDEX_PATH).ntotal
    else:
        status["index_vector_count"] = "no index"

    if os.path.isdir(ENTITIES_DIR):
        status["entity_count"] = len(
            [f for f in os.listdir(ENTITIES_DIR) if f.endswith(".md")]
        )
    else:
        status["entity_count"] = 0

    status["tasks"] = count_tasks()

    return json.dumps(status, indent=2)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Lightweight liveness probe for container healthchecks / load balancers."""
    return JSONResponse({"status": "ok", "service": "obsidian-brain"})


def _resolve_transport() -> str:
    if "--http" in sys.argv:
        return "streamable-http"
    if "--stdio" in sys.argv:
        return "stdio"
    return os.environ.get("MCP_TRANSPORT", "stdio")


def _build_http_app():
    """Build the streamable-HTTP Starlette app, installing bearer-token auth when
    BRAIN_AUTH_TOKEN is set. Auth is opt-in so network-isolated deployments keep
    working unchanged; when unset we log a loud warning (audit finding H1)."""
    from auth import BearerAuthMiddleware

    app = mcp.streamable_http_app()
    token = os.environ.get("BRAIN_AUTH_TOKEN", "").strip()
    if token:
        app.add_middleware(BearerAuthMiddleware, token=token, public_paths={"/health"})
        print("[auth] bearer-token auth enabled on HTTP transport", flush=True)
    else:
        print("[auth] WARNING: BRAIN_AUTH_TOKEN unset — HTTP tools are UNAUTHENTICATED; "
              "restrict the port to a trusted network or set a token", flush=True)
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("BRAIN_ALLOWED_HOSTS", "").strip():
        print(f"[security] WARNING: binding {host} without BRAIN_ALLOWED_HOSTS — "
              "DNS-rebinding/Origin protection is DISABLED; set BRAIN_ALLOWED_HOSTS "
              "to the host[:port] clients use, or bind loopback behind a proxy "
              "(audit finding M-N)", flush=True)
    return app


# ── Nightly index refresh (baked into the service) ──────────────────────────
#
# When run as the HTTP service, a daemon thread rebuilds the index nightly so
# new/edited notes become searchable without any external cron. The heavy work
# (scan + embed) happens off the request path; only the brief file swap is
# locked (see indexer.INDEX_LOCK). Controlled via env:
#   BRAIN_REFRESH_ENABLED   (default 1)
#   BRAIN_REFRESH_AT_HOUR   (default 3 — local hour, honors container TZ)
#   BRAIN_REFRESH_ON_START  (default 1 — one refresh shortly after boot)
#   BRAIN_REFRESH_FORCE     (default 0 — full re-embed vs. rebuild-if-changed)

def _truthy(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _parse_hour(raw, default: int = 3) -> int:
    """Parse BRAIN_REFRESH_AT_HOUR into a legal 0-23 hour, falling back with a
    logged warning on a non-numeric or out-of-range value. Without this an input
    like "24"/"3am" passed int() (or crashed it) and then raised inside
    datetime.replace(hour=..) on the first loop iteration, silently killing the
    scheduler thread while /health kept returning 200 (audit finding M-O)."""
    try:
        hour = int(raw)
    except (TypeError, ValueError):
        print(f"[refresh] WARNING: BRAIN_REFRESH_AT_HOUR={raw!r} is not an integer; "
              f"using {default}", flush=True)
        return default
    if not 0 <= hour <= 23:
        print(f"[refresh] WARNING: BRAIN_REFRESH_AT_HOUR={raw!r} is out of range 0-23; "
              f"using {default}", flush=True)
        return default
    return hour


def _seconds_until_hour(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _refresh_once(force: bool) -> None:
    try:
        result = build_index(force=force)
        print(f"[refresh] {datetime.now().isoformat(timespec='seconds')} {result}", flush=True)
    except Exception as e:  # a refresh failure must never kill the thread
        print(f"[refresh] error: {e}", flush=True)


def _run_script(script: str, argv: list, label: str) -> None:
    """Run a bundled maintenance script as a subprocess; never raise."""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), script), *argv],
            capture_output=True, text=True, timeout=3600,
        )
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-4:])
        print(f"[{label}] exit={proc.returncode}\n{tail}", flush=True)
        if proc.returncode != 0 and proc.stderr:
            print(f"[{label}] stderr: {proc.stderr.strip()[-800:]}", flush=True)
    except Exception as e:
        print(f"[{label}] error: {e}", flush=True)


def _post_refresh_tasks() -> None:
    """After the index refresh: re-link MOCs/Related + frontmatter, then update the
    Open Action Items ledger. Each step is env-toggled and isolated so any failure
    just logs and never kills the scheduler thread.

    Endpoints default to LM_BASE_URL so a fresh clone with a single LLM endpoint
    works out of the box; this deployment overrides the chat URL to llama-swap.
    """
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "/vault")
    lm = os.environ.get("LM_BASE_URL", "http://localhost:1234/v1")
    chat_url = os.environ.get("LINKER_CHAT_URL", lm)
    chat_model = os.environ.get("LINKER_CHAT_MODEL", "qwen3.6-35b-a3b-mtp")
    embed_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v2-moe")
    # The ledger's reconcile/extract task is harder than the linker's per-note
    # classification, so it can use a stronger model. Defaults to the linker's if
    # unset (e.g. linker on the NPU 9B, ledger on the GPU 35B which is free overnight).
    ledger_url = os.environ.get("LEDGER_CHAT_URL", chat_url)
    ledger_model = os.environ.get("LEDGER_CHAT_MODEL", chat_model)

    if _truthy("BRAIN_LINKER_ENABLED", "1"):
        _run_script("moc_linker.py", [
            "--apply", "--tag-notes", "--related", "--vault", vault,
            "--endpoint", chat_url, "--model", chat_model,
            "--embed-endpoint", lm, "--embed-model", embed_model,
        ], "linker")
    if _truthy("BRAIN_LEDGER_ENABLED", "1"):
        _run_script("ledger_update.py", [
            "--apply", "--vault", vault, "--endpoint", ledger_url, "--model", ledger_model,
        ], "ledger")


def _refresh_loop(hour: int, force: bool, on_start: bool) -> None:
    if on_start:
        time.sleep(30)  # let the server finish starting first
        _refresh_once(force=False)  # cheap: only rebuilds if the vault changed
        if _truthy("BRAIN_POSTREFRESH_ON_START", "0"):
            _post_refresh_tasks()  # heavy (~minutes); off by default, on for the nightly run
    while True:
        # Guard the whole iteration: no single failure (a bad sleep interval, an
        # unguarded call added later) may kill the daemon thread — there is no
        # watchdog to restart it (audit findings M-O / M-6).
        try:
            time.sleep(_seconds_until_hour(hour))
            _refresh_once(force=force)
            _post_refresh_tasks()
        except Exception as e:
            print(f"[refresh] loop iteration error (continuing): {e}", flush=True)
            time.sleep(60)  # avoid a hot spin if the failure recurs immediately


def _maybe_start_scheduler() -> None:
    if not _truthy("BRAIN_REFRESH_ENABLED", "1"):
        print("[refresh] scheduler disabled", flush=True)
        return
    hour = _parse_hour(os.environ.get("BRAIN_REFRESH_AT_HOUR", "3"))
    force = _truthy("BRAIN_REFRESH_FORCE", "0")
    on_start = _truthy("BRAIN_REFRESH_ON_START", "1")
    threading.Thread(
        target=_refresh_loop, args=(hour, force, on_start),
        daemon=True, name="brain-refresh",
    ).start()
    print(f"[refresh] nightly scheduler started: daily at {hour:02d}:00 local "
          f"(force={force}, on_start={on_start})", flush=True)


if __name__ == "__main__":
    transport = _resolve_transport()
    if transport == "streamable-http":
        _maybe_start_scheduler()
        # Run uvicorn ourselves (mirroring FastMCP.run_streamable_http_async) so we
        # can install the auth middleware on the app before it starts serving.
        import uvicorn

        uvicorn.run(
            _build_http_app(),
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
        )
    else:
        mcp.run(transport=transport)
