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
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from brain import (
    query_brain,
    write_entity_note,
    append_insight,
    build_index,
)
from searcher import search as _search_notes
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


def _collect_status() -> dict:
    """Assemble the brain's state dict. Shared by the brain_status tool and the web
    UI's /ui/api/status route."""
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
        # Guard the read: a corrupt/truncated index must not crash the one surface
        # meant to diagnose exactly that (July-1 finding M-1).
        try:
            status["index_vector_count"] = faiss.read_index(INDEX_PATH).ntotal
        except (RuntimeError, OSError) as e:
            status["index_vector_count"] = f"index unreadable ({e})"
    else:
        status["index_vector_count"] = "no index"

    if os.path.isdir(ENTITIES_DIR):
        status["entity_count"] = len(
            [f for f in os.listdir(ENTITIES_DIR) if f.endswith(".md")]
        )
    else:
        status["entity_count"] = 0

    status["tasks"] = count_tasks()

    return status


@mcp.tool()
def brain_status() -> str:
    """Report brain state: vault path, embedding model, index stats, entity count."""
    return json.dumps(_collect_status(), indent=2)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Lightweight liveness probe for container healthchecks / load balancers."""
    return JSONResponse({"status": "ok", "service": "obsidian-brain"})


# Self-contained page: inline CSS/JS, no external assets (matches the image ethos).
# Vault text is rendered via textContent only, so an injected note body can't run
# script and steal the sessionStorage token.
_UI_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Obsidian Brain</title>
<style>
  :root{--bg:#fbfbfa;--fg:#1c1b19;--muted:#6b6a66;--card:#fff;--line:#e6e4df;--accent:#6a5acd;--accent-fg:#fff}
  @media(prefers-color-scheme:dark){:root{--bg:#1a1a19;--fg:#e9e7e2;--muted:#9a988f;--card:#242422;--line:#34332f;--accent:#8f82e0;--accent-fg:#15140f}}
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
  header{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap;padding:1rem 1.25rem;border-bottom:1px solid var(--line)}
  h1{font-size:1.05rem;margin:0;font-weight:650;letter-spacing:.01em}
  .pill{font-size:.72rem;padding:.2rem .55rem;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
  .pill.ok{color:#12805c;border-color:#12805c55}.pill.bad{color:#c0392b;border-color:#c0392b55}
  .spacer{flex:1}
  main{max-width:820px;margin:0 auto;padding:1.25rem}
  nav{display:flex;gap:.25rem;margin-bottom:1rem}
  nav button{background:none;border:none;color:var(--muted);padding:.5rem .8rem;border-radius:8px;cursor:pointer;font:inherit}
  nav button.active{background:var(--card);color:var(--fg);box-shadow:0 0 0 1px var(--line)}
  input,button.go{font:inherit}
  .row{display:flex;gap:.5rem;flex-wrap:wrap}
  input[type=text],input[type=password],select{flex:1;min-width:8rem;padding:.55rem .7rem;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--fg)}
  button.go{padding:.55rem 1rem;border:none;border-radius:9px;background:var(--accent);color:var(--accent-fg);cursor:pointer;font-weight:600}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.85rem 1rem;margin:.6rem 0}
  .card .path{font-weight:600;font-size:.9rem;word-break:break-word}
  .card .score{float:right;font-size:.72rem;color:var(--muted)}
  .card .snip{color:var(--muted);font-size:.88rem;margin-top:.35rem;white-space:pre-wrap}
  .note-group{margin:.75rem 0}.note-group h3{font-size:.85rem;margin:.2rem 0;color:var(--muted);font-weight:600}
  .task{padding:.15rem 0}.task .box{color:var(--accent);margin-right:.4rem}
  .muted{color:var(--muted)}.hidden{display:none}
  dl{display:grid;grid-template-columns:auto 1fr;gap:.3rem .9rem;margin:0}
  dt{color:var(--muted)}dd{margin:0;word-break:break-word}
  .err{color:#c0392b;font-size:.85rem}
</style></head>
<body>
<header>
  <h1>🧠 Obsidian Brain</h1>
  <span id="conn" class="pill">…</span>
  <div class="spacer"></div>
  <button class="go" id="tokbtn" style="background:none;color:var(--muted);border:1px solid var(--line);font-weight:500">Token</button>
</header>
<main>
  <nav>
    <button data-tab="search" class="active">Search</button>
    <button data-tab="tasks">Tasks</button>
    <button data-tab="status">Status</button>
  </nav>

  <section id="tab-search">
    <div class="row">
      <input type="text" id="q" placeholder="Ask the vault… (people, projects, decisions)" autofocus>
      <button class="go" id="searchgo">Search</button>
    </div>
    <div id="results"></div>
  </section>

  <section id="tab-tasks" class="hidden">
    <div class="row">
      <select id="tstatus"><option value="open">Open</option><option value="done">Done</option><option value="all">All</option></select>
      <input type="text" id="tq" placeholder="filter by text or note…">
      <button class="go" id="tasksgo">List</button>
    </div>
    <div id="taskcounts" class="muted" style="margin:.5rem 0"></div>
    <div id="tasks"></div>
  </section>

  <section id="tab-status" class="hidden">
    <div class="card"><dl id="status"></dl></div>
  </section>
</main>

<script>
const $=s=>document.querySelector(s), el=(t,c)=>{const e=document.createElement(t);if(c)e.className=c;return e};
const TK='brain_token';
function token(){return sessionStorage.getItem(TK)||''}
function setToken(){const t=prompt('Bearer token (leave blank if the server has no BRAIN_AUTH_TOKEN):',token());if(t!==null)sessionStorage.setItem(TK,t.trim());}
async function api(path){
  const h={};const t=token();if(t)h['Authorization']='Bearer '+t;
  const r=await fetch(path,{headers:h});
  if(r.status===401){setToken();throw new Error('unauthorized — token required');}
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
function conn(ok,txt){const c=$('#conn');c.className='pill '+(ok?'ok':'bad');c.textContent=txt;}

async function doSearch(){
  const q=$('#q').value.trim();const box=$('#results');box.textContent='';
  if(!q)return;
  box.innerHTML='<p class="muted">searching…</p>';
  try{
    const d=await api('/ui/api/search?q='+encodeURIComponent(q)+'&k=8');box.textContent='';
    if(!d.results.length){box.innerHTML='<p class="muted">No relevant notes found.</p>';return;}
    for(const r of d.results){
      const c=el('div','card');
      const s=el('span','score');s.textContent='score '+r.score;
      const p=el('div','path');p.appendChild(s);
      const a=el('a');a.textContent=r.note_path;a.href='obsidian://open?path='+encodeURIComponent(r.abs_path||r.note_path);a.style.color='inherit';
      p.appendChild(a);
      const sn=el('div','snip');sn.textContent=(r.text||'').slice(0,500);
      c.appendChild(p);c.appendChild(sn);box.appendChild(c);
    }
  }catch(e){box.innerHTML='';const p=el('p','err');p.textContent=e.message;box.appendChild(p);}
}
async function doTasks(){
  const box=$('#tasks');box.innerHTML='<p class="muted">loading…</p>';
  try{
    const st=$('#tstatus').value,q=$('#tq').value.trim();
    const d=await api('/ui/api/tasks?status='+st+'&q='+encodeURIComponent(q));
    $('#taskcounts').textContent=`open ${d.counts.open} · done ${d.counts.done} · total ${d.counts.total}`;
    box.textContent='';
    const byNote={};for(const t of d.tasks)(byNote[t.note_path]=byNote[t.note_path]||[]).push(t);
    const keys=Object.keys(byNote).sort();
    if(!keys.length){box.innerHTML='<p class="muted">No matching tasks.</p>';return;}
    for(const n of keys){
      const g=el('div','note-group');const h=el('h3');h.textContent=n;g.appendChild(h);
      for(const t of byNote[n]){const d2=el('div','task');const b=el('span','box');b.textContent=t.status==='done'?'☑':'☐';const s=el('span');s.textContent=t.text;d2.appendChild(b);d2.appendChild(s);g.appendChild(d2);}
      box.appendChild(g);
    }
  }catch(e){box.innerHTML='';const p=el('p','err');p.textContent=e.message;box.appendChild(p);}
}
async function loadStatus(){
  try{
    const d=await api('/ui/api/status');const dl=$('#status');dl.textContent='';
    const order=['vault_path','embedding_model','notes_indexed','chunks','index_vector_count','entity_count','index_mtime','lm_base_url'];
    for(const k of order){if(d[k]===undefined)continue;const dt=el('dt');dt.textContent=k.replace(/_/g,' ');const dd=el('dd');dd.textContent=typeof d[k]==='object'?JSON.stringify(d[k]):String(d[k]);dl.appendChild(dt);dl.appendChild(dd);}
    if(d.tasks){const dt=el('dt');dt.textContent='tasks';const dd=el('dd');dd.textContent=`open ${d.tasks.open} · done ${d.tasks.done}`;dl.appendChild(dt);dl.appendChild(dd);}
    conn(true,'connected');
  }catch(e){conn(false,'error');}
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  for(const t of ['search','tasks','status'])$('#tab-'+t).classList.toggle('hidden',t!==b.dataset.tab);
  if(b.dataset.tab==='tasks')doTasks();if(b.dataset.tab==='status')loadStatus();
});
$('#searchgo').onclick=doSearch;$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch()});
$('#tasksgo').onclick=doTasks;$('#tq').addEventListener('keydown',e=>{if(e.key==='Enter')doTasks()});
$('#tokbtn').onclick=setToken;
// Health check (no auth) then a status probe.
fetch('/health').then(r=>conn(r.ok,r.ok?'online':'offline')).catch(()=>conn(false,'offline'));
loadStatus();
</script>
</body></html>"""


# ── Human-facing web UI (served on the same Starlette app) ───────────────────
# GET /ui is a self-contained page (added to the auth public_paths; it contains
# ZERO vault data). Every data call is a same-origin fetch to /ui/api/* carrying
# the bearer token from sessionStorage, so it passes the SAME auth gate as /mcp —
# no new auth code, and no unauthenticated write path. Blocking work (embed HTTP
# call, FAISS read, file walks) runs in a threadpool so a request never stalls the
# uvicorn event loop (and thus /mcp).

@mcp.custom_route("/ui", methods=["GET"])
async def ui_page(_request: Request) -> HTMLResponse:
    return HTMLResponse(_UI_HTML)


@mcp.custom_route("/ui/api/search", methods=["GET"])
async def ui_search(request: Request) -> JSONResponse:
    q = (request.query_params.get("q") or "").strip()
    if not q:
        return JSONResponse({"results": []})
    try:
        k = int(request.query_params.get("k", "5"))
    except (TypeError, ValueError):
        k = 5
    results = await run_in_threadpool(_search_notes, q, k)
    return JSONResponse({"results": results})


@mcp.custom_route("/ui/api/tasks", methods=["GET"])
async def ui_tasks(request: Request) -> JSONResponse:
    status = (request.query_params.get("status") or "open").strip()
    q = (request.query_params.get("q") or "").strip().lower()
    tasks = await run_in_threadpool(scan_tasks, status)
    if q:
        tasks = [t for t in tasks if q in t["text"].lower() or q in t["note_path"].lower()]
    counts = await run_in_threadpool(count_tasks)
    return JSONResponse({"tasks": tasks, "counts": counts})


@mcp.custom_route("/ui/api/status", methods=["GET"])
async def ui_status(_request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(_collect_status))


@mcp.custom_route("/ui/api/insight", methods=["POST"])
async def ui_insight(request: Request) -> JSONResponse:
    # Writes require auth to be configured: with no token the middleware is a no-op,
    # so refuse rather than expose an anonymous write path (the lesson of H-1).
    if not os.environ.get("BRAIN_AUTH_TOKEN", "").strip():
        return JSONResponse(
            {"status": "error", "detail": "writes disabled: BRAIN_AUTH_TOKEN is not set"},
            status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "invalid JSON"}, status_code=400)
    note_path = (body.get("note_path") or "").strip()
    insight = (body.get("insight") or "").strip()
    context = (body.get("context") or "").strip()
    if not note_path or not insight:
        return JSONResponse(
            {"status": "error", "detail": "note_path and insight are required"},
            status_code=400)
    result = await run_in_threadpool(append_insight, note_path, insight, context)
    return JSONResponse(result)


@mcp.custom_route("/refresh", methods=["POST"])
async def http_refresh(_request: Request) -> JSONResponse:
    """Trigger an incremental index rebuild (roadmap #2). Gated by the same bearer
    auth as every non-/health route. Cheap now that embeddings are content-cached:
    a vault writer can hit this after a write instead of waiting for the nightly
    refresh. Reuses build_index's lock + atomic swap, off the event loop."""
    result = await run_in_threadpool(build_index, False)
    return JSONResponse(result)


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
        # /ui is the HTML shell only (no vault data); /ui/api/* stays gated.
        app.add_middleware(BearerAuthMiddleware, token=token, public_paths={"/health", "/ui"})
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

    # Truth maintenance (contradiction triage) runs FIRST — before the linker's
    # note rewrites — so its recency window sees genuine human edits. Off by
    # default: run `truth_maintenance.py --dry-run` by hand to gauge precision,
    # then set BRAIN_TRUTH_ENABLED=1 once trusted (observe-only rollout).
    if _truthy("BRAIN_TRUTH_ENABLED", "0"):
        truth_url = os.environ.get("TRUTH_CHAT_URL", ledger_url)
        truth_model = os.environ.get("TRUTH_CHAT_MODEL", ledger_model)
        _run_script("truth_maintenance.py", [
            "--apply", "--backfill", "--vault", vault,
            "--endpoint", truth_url, "--model", truth_model,
            "--embed-endpoint", lm, "--embed-model", embed_model,
        ], "truth")
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
