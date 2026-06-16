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

Local registration (stdio), e.g. in ~/.claude/settings.json:
    {
      "mcpServers": {
        "obsidian-brain": {
          "command": "python3",
          "args": ["/workspace/research/obsidian-brain/mcp_server.py"],
          "env": {"OBSIDIAN_VAULT_PATH": "/server/obsidian"}
        }
      }
    }
"""
import json
import os
import sys
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

mcp = FastMCP(
    "obsidian-brain",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path=os.environ.get("MCP_PATH", "/mcp"),
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

    Use when you encounter something new worth tracking persistently. Returns the
    path to the created (or already-existing) note. Confirm with the user before
    writing unless they explicitly said to remember it.
    """
    return write_entity_note(entity_name=name, initial_content=initial_content)


@mcp.tool()
def brain_append_insight(note_path: str, insight: str, context: str = "") -> str:
    """Append a new insight, decision, or fact to an existing vault note.

    `note_path` may be absolute or relative to the vault (e.g. 'Projects/Delta.md').
    Use after a conversation where you learn something new about a person, project,
    or decision. `context` optionally records what prompted the insight. Confirm
    with the user before writing unless they explicitly said to remember it.
    """
    return append_insight(note_path=note_path, insight=insight, context=context)


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
        meta = json.loads(Path(METADATA_PATH).read_text())
        status.update({
            "notes_indexed": meta.get("num_notes", "unknown"),
            "chunks": meta.get("num_chunks", "unknown"),
            "embedding_model": meta.get("embedding_model", EMBEDDING_MODEL),
            "index_mtime": meta.get("index_mtime", "unknown"),
        })
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


if __name__ == "__main__":
    mcp.run(transport=_resolve_transport())
