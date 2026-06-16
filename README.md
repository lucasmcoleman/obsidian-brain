# Obsidian Brain

A persistent, agent-native knowledge layer that uses your Obsidian vault as the memory substrate. No special commands, no separate database, no cloud dependencies.

Built as a gbrain alternative with two key differences:
- **Vault over database** — reads from `.md` files directly, no data sync required
- **Natural language, not commands** — the agent decides when to query the vault, not the user

## Architecture

```
Obsidian vault (.md files)
        │
        ▼
  indexer.py         ──► LM Studio embeddings (768-dim)
        │                        │
        ▼                        ▼
  FAISS index      ◄──── text-embedding-nomic-embed-text-v2-moe
  (metadata.json)
        │
        ▼
  searcher.py      ←─ query_brain() — agent calls this before answering
        │
        ▼
  brain.py         ←─ write_entity_note() / append_insight() — agent writes back
        │
        ▼
  Vault notes updated (.md files — Obsidian LiveSync keeps them in sync)
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- LM Studio running at `http://192.168.0.29:1234/v1`
- Obsidian vault accessible at your vault path

### 2. Install

```bash
cd /workspace/research
git clone https://github.com/lucasmcoleman/obsidian-brain.git
cd obsidian-brain
pip install -r requirements.txt
```

### 3. Configure

```bash
export OBSIDIAN_VAULT_PATH=/server/obsidian   # your vault path
```

### 4. Build the index

```bash
python indexer.py --force
```

### 5. Search

```bash
python searcher.py "what did I decide about the Delta Heartland project"
```

## Core Functions

### Query (context retrieval)

```python
from brain import query_brain
context = query_brain("SWCA project pipeline decisions", top_k=5)
```

Returns formatted context from semantically relevant notes. The agent calls this automatically before answering questions about people, projects, decisions, or past conversations.

### Write entity

```python
from brain import write_entity_note
path = write_entity_note("Sarah Chen", initial_content="Environmental scientist, SWCA team lead.")
```

Creates `_brain/entities/sarah-chen.md` — a persistent note for a person, project, or concept.

### Append insight

```python
from brain import append_insight
result = append_insight(
    "/server/obsidian/Projects/Delta Heartland.md",
    "Lucas prefers biweekly check-ins over daily standups.",
    context="Discussion about team cadence on 2026-06-15"
)
```

Appends a timestamped insight block to an existing note.

### Rebuild index

```python
from brain import build_index
result = build_index(force=True)
```

Rebuilds the FAISS index. Run this if new notes were added externally and retrieval results seem stale.

## External Agent Access

### MCP Server

For Copilot Coworker, Claude Code, or any MCP-compatible agent:

```json
{
  "mcpServers": {
    "obsidian-brain": {
      "command": "python3",
      "args": ["/path/to/obsidian-brain/mcp_server.py"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/server/obsidian"
      }
    }
  }
}
```

Tools: `brain_query`, `brain_write_entity`, `brain_append_insight`, `brain_build_index`, `brain_status`.

### Claude Code / OpenClaw

Drop `CLAUDE.md` into your Claude Code skills directory. The agent auto-loads it when vault context is relevant and uses the tools naturally without special commands.

## Project Structure

```
obsidian-brain/
  config.py          — vault path, embedding model, chunk settings
  embedder.py        — LM Studio OpenAI-compatible API client
  indexer.py         — vault scanner, sentence chunker, FAISS builder
  searcher.py        — semantic search with cosine re-ranking
  brain.py           — orchestration: retrieval + write-back
  consolidate.py     — standalone nightly consolidation script
  mcp_server.py      — stdio MCP server for external agents
  CLAUDE.md          — Claude Code skill file
  requirements.txt   — faiss-cpu, numpy, openai, requests
  RESUME.md          — project state + resume instructions
```

## Nightly Consolidation

A cron job runs `consolidate.py --force` at 2 AM ET Monday through Friday, rebuilding the index to incorporate new and changed notes.

Cron job ID: `4d0564d5324a`

## Key Design Decisions

- **Sentence-based chunking** — splits at `.!?` boundaries rather than token counts, preserves semantic coherence
- **L2 distance + cosine re-rank** — FAISS returns by L2 distance, then re-ranked by cosine similarity
- **Local embeddings only** — uses `text-embedding-nomic-embed-text-v2-moe` via LM Studio, zero API cost
- **`_brain/` directory is LiveSync-aware** — index files live in the vault's `_brain/` directory so LiveSync can replicate them alongside notes

## Troubleshooting

**No index found:** Run `python indexer.py --force` to build from scratch.

**Embedding errors:** Verify LM Studio is running and `text-embedding-nomic-embed-text-v2-moe` is loaded.

**Empty search results:** Check that `OBSIDIAN_VAULT_PATH` points to the correct vault directory and that `.md` files exist.

**MCP server won't start:** Ensure `OBSIDIAN_VAULT_PATH` is set in the environment before launching.
