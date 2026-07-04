# Obsidian Brain — Project Resume

**Last updated:** 2026-06-16 (architecture partially stale — see note)
**Status:** Operational. Index built, nightly refresh active, write-back tested.

> **Stale-architecture note (2026-07-03):** this file predates the containerized
> deployment. Corrections: retrieval is FAISS **L2** order with note-level dedup
> (no cosine re-ranking); the nightly refresh is a **daemon thread baked into the
> HTTP MCP server** (`BRAIN_REFRESH_AT_HOUR`), **not** an external host cron; and
> the container runs **streamable-HTTP**, not stdio. See `README.md` /
> `deploy/README.md` for the current design.

## What This Is

A "gbrain alternative" — a persistent knowledge layer that:
- Reads from an Obsidian vault of markdown files (no database)
- Uses natural language — no special commands needed
- Agent decides autonomously when vault context is needed
- Writes back insights/decisions to the vault automatically
- Indexes via FAISS + local LM Studio embeddings (zero cost)

## Architecture

```
/server/programming/obsidian-brain/
  config.py          — vault path, embedding model, chunk settings
  embedder.py        — LM Studio OpenAI-compatible API client
  indexer.py         — vault scanner, chunker, FAISS index builder
  searcher.py        — semantic search (FAISS L2 order, note-level dedup)
  brain.py           — orchestration: retrieval + write-back to vault
  consolidate.py     — standalone nightly consolidation script
  mcp_server.py      — stdio MCP server for external agents (Copilot Coworker, etc.)
  CLAUDE.md          — Claude Code / OpenClaw skill file
  obsidian-brain.md  — Hermes skill documentation
  requirements.txt   — faiss-cpu, numpy, openai, requests
  RESUME.md          — this file

Skill installed at: ~/.hermes/skills/obsidian-brain/SKILL.md
```

## Current State

- **Vault:** `/server/obsidian` (OBSIDIAN_VAULT_PATH env var set)
- **Index:** `/server/obsidian/_brain/index.faiss` (1.4MB)
- **Metadata:** `/server/obsidian/_brain/metadata.json` (1.4MB)
- **Entities:** `/server/obsidian/_brain/entities/` (empty, created on demand)
- **Stats:** 26 notes indexed, 462 chunks, 768-dim embeddings
- **Cron:** `4d0564d5324a` — nightly consolidation at 2 AM ET (Mon-Fri)

## How It Works

1. **indexer.py** scans the vault for .md files, chunks them (~500 token sentences), generates embeddings via LM Studio, stores in FAISS
2. **searcher.py** takes a query, embeds it, searches FAISS, returns top-k deduplicated results
3. **brain.py** wraps retrieval + entity creation + insight appending
4. **Skill** tells the agent when to invoke (person/project/decision questions) and when not to (general knowledge)

## Key Config

- Vault: `/server/obsidian` (via OBSIDIAN_VAULT_PATH)
- Embedding model: `text-embedding-nomic-embed-text-v2-moe` (via LM Studio)
- Chunk size: 500 tokens with 50 token overlap
- TOP_K: 5 results returned

## Prerequisites (verify in new sandbox)

1. `claude` CLI available at `/usr/local/bin/claude` (v2.1.178)
2. Python packages: `pip install faiss-cpu numpy openai requests`
3. LM Studio running at `http://192.168.0.29:1234/v1`
4. Obsidian vault volume mounted at `/server/obsidian`
5. `OBSIDIAN_VAULT_PATH=/server/obsidian` set in environment

## Commands

```bash
# Rebuild index
cd /server/programming/obsidian-brain && OBSIDIAN_VAULT_PATH=/server/obsidian python indexer.py --force

# Search
cd /server/programming/obsidian-brain && OBSIDIAN_VAULT_PATH=/server/obsidian python searcher.py "your query"

# Full consolidation (rebuild + entity enrichment)
cd /server/programming/obsidian-brain && OBSIDIAN_VAULT_PATH=/server/obsidian python consolidate.py --force
```

## Resume Steps (new sandbox)

1. Verify deps: `python -c "import faiss, numpy; from openai import OpenAI; print('OK')"`
2. Verify LM Studio: `curl -s http://192.168.0.29:1234/v1/models`
3. Verify vault: `ls /server/obsidian`
4. Build index: `cd /server/programming/obsidian-brain && OBSIDIAN_VAULT_PATH=/server/obsidian python indexer.py --force`
5. Test: `python searcher.py "project decisions"`

## Bugs Fixed During Setup

- Missing `EMBEDDING_MODEL` import in `indexer.py` (line 15) — patched

## Next Steps / TODO

- [ ] Add entity enrichment logic (auto-update entity pages with new facts from conversations)
- [ ] Consider adding a pre-retrieval intent classifier to reduce false-positive vault queries
- [ ] Monitor cron job delivery for first few runs
- [ ] Fine-tune chunk size / TOP_K as vault grows
