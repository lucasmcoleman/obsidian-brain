#!/usr/bin/env python3
"""
Obsidian Brain MCP Server.

Exposes brain functions as stdio MCP tools for external agent integration.
Compatible with Claude Code MCP, Copilot Coworker, and any MCP client.

Usage:
    python mcp_server.py

Environment:
    OBSIDIAN_VAULT_PATH   — path to Obsidian vault (required)
    OBSIDIAN_BRAIN_DIR    — path to _brain dir (default: {vault}/_brain)
    LM_BASE_URL           — LM Studio URL (default: http://192.168.0.29:1234/v1)

Install / Register:
    Add to your MCP config (~/.claude/settings.json or Claude Code MCP config):

    {
      "mcpServers": {
        "obsidian-brain": {
          "command": "python3",
          "args": ["/workspace/research/obsidian-brain/mcp_server.py"],
          "env": {
            "OBSIDIAN_VAULT_PATH": "/server/obsidian"
          }
        }
      }
    }
"""
import json
import os
import sys
from pathlib import Path

# Add project dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server

from brain import (
    query_brain,
    write_entity_note,
    append_insight,
    build_index,
    consolidate,
    get_or_build_index,
)

# ── Config ────────────────────────────────────────────────────────────────────────

VAULT_PATH = os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    "/server/obsidian"
)
LM_BASE_URL = os.environ.get(
    "LM_BASE_URL",
    "http://192.168.0.29:1234/v1"
)
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    "text-embedding-nomic-embed-text-v2-moe"
)

# ── Server Setup ────────────────────────────────────────────────────────────────

server = Server("obsidian-brain")


# ── Tools ────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    """Expose all brain functions as MCP tools."""
    return [
        Tool(
            name="brain_query",
            description=(
                "Query the Obsidian vault for contextually relevant notes. "
                "Call this before answering questions about people, projects, "
                "decisions, or past conversations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query string.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max number of results to return (default: 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="brain_write_entity",
            description=(
                "Create a new entity note in _brain/entities/ for a person, "
                "project, or concept. Use this when you encounter something new "
                "worth tracking persistently."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Entity name (e.g. 'Sarah Chen' or 'Delta Heartland Project').",
                    },
                    "initial_content": {
                        "type": "string",
                        "description": "Initial notes about the entity.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="brain_append_insight",
            description=(
                "Append a new insight, decision, or fact to an existing vault note. "
                "Use this after a conversation where you learn something new about "
                "a person, project, or decision."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the note, or relative to the vault. "
                            "Example: 'Projects/Delta Heartland.md' or "
                            "'/server/obsidian/Projects/Delta Heartland.md'"
                        ),
                    },
                    "insight": {
                        "type": "string",
                        "description": "The new fact, decision, or conclusion to record.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context about what prompted this insight.",
                    },
                },
                "required": ["note_path", "insight"],
            },
        ),
        Tool(
            name="brain_build_index",
            description=(
                "Rebuild the FAISS index from the current vault state. "
                "Use this if new notes have been added externally and the index is stale."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Force full rebuild even if index appears current (default: False).",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="brain_status",
            description=(
                "Get the current state of the brain: index stats, vault path, "
                "last index mtime, and entity count."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict,
) -> list[TextContent]:
    """Handle tool calls."""
    vault = VAULT_PATH

    if name == "brain_query":
        query = arguments["query"]
        top_k = arguments.get("top_k", 5)
        context = query_brain(query, top_k=top_k)
        return [TextContent(type="text", text=context)]

    elif name == "brain_write_entity":
        path = write_entity_note(
            entity_name=arguments["name"],
            initial_content=arguments.get("initial_content", ""),
        )
        return [TextContent(
            type="text",
            text=f"Entity note created: {path}",
        )]

    elif name == "brain_append_insight":
        result = append_insight(
            note_path=arguments["note_path"],
            insight=arguments["insight"],
            context=arguments.get("context", ""),
        )
        return [TextContent(type="text", text=result)]

    elif name == "brain_build_index":
        force = arguments.get("force", False)
        result = build_index(force=force)
        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2),
        )]

    elif name == "brain_status":
        from config import BRAIN_DIR, INDEX_PATH, METADATA_PATH, EMBEDDING_MODEL
        import json as _json

        status = {
            "vault_path": vault,
            "embedding_model": EMBEDDING_MODEL,
            "lm_base_url": LM_BASE_URL,
        }

        if os.path.exists(METADATA_PATH):
            meta = _json.loads(Path(METADATA_PATH).read_text())
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
            idx = faiss.read_index(INDEX_PATH)
            status["index_vector_count"] = idx.ntotal
        else:
            status["index_vector_count"] = "no index"

        entity_dir = os.path.join(vault, "_brain", "entities")
        if os.path.exists(entity_dir):
            status["entity_count"] = len([
                f for f in os.listdir(entity_dir)
                if f.endswith(".md")
            ])
        else:
            status["entity_count"] = 0

        return [TextContent(
            type="text",
            text=json.dumps(status, indent=2),
        )]

    else:
        return [TextContent(
            type="text",
            text=f"Unknown tool: {name}",
        )]


# ── Entry Point ──────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
