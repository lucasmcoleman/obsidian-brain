---
name: obsidian-brain
description: "Query the user's Obsidian vault for personal context, record new entities, and append insights to notes. Use naturally — no special commands."
trigger: "when answering questions about people, projects, decisions, or past conversations; or when the user says 'remember that' / 'I decided'"
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository. It is **also loaded as a skill** (see the frontmatter above) — keep the frontmatter
at the top of the file or skill loading breaks.

# Obsidian Brain

A persistent, agent-native knowledge layer over an Obsidian vault. The vault's `.md` files are
the source of truth — there is no separate database. The agent decides when to query; there are
no user-facing commands. Embeddings are generated locally (LM Studio / llama-swap), so there is
zero cloud/API cost or dependency.

There are **two distinct audiences** for this file:
1. **Working on the code** — see "Development" and "Architecture" below.
2. **Consuming the brain as an agent** — see "Using the brain" below.

## Development

There is no linter or build step; development is otherwise run-and-observe. A
`pytest` suite lives under `tests/` (temp vault + deterministic in-process fake
embedder — no network or real vault needed). **Use TDD: write the failing test first.**

```bash
python -m pytest tests/ -q                          # run the test suite

export OBSIDIAN_VAULT_PATH=/path/to/vault           # required by every entry point

python indexer.py --force                          # (re)build the FAISS index from scratch
python searcher.py "what did I decide about X"     # one-off semantic search from the CLI
python brain.py                                    # smoke test: build index + sample query
python consolidate.py --force                      # full rebuild (external cron entry point)

python mcp_server.py                               # MCP server over stdio (local agents)
python mcp_server.py --http                        # MCP server, streamable-HTTP :8000/mcp

# Nightly vault maintenance (need a local OpenAI-compatible CHAT model, not just embeddings):
python moc_linker.py --dry-run                     # preview MOC classification + Related links
python moc_linker.py --apply --tag-notes --related
python ledger_update.py --dry-run                  # preview action-item ledger changes
python ledger_update.py --apply

# Container deployment (see deploy/README.md for the compose file):
cd deploy && docker compose up -d --build
```

Prerequisites: Python 3.11+ and LM Studio reachable at `LM_BASE_URL`
(default `http://localhost:1234/v1`) with the embedding model loaded. Without a running
embedding endpoint, indexing and search fail.

## Architecture

Single-responsibility modules layered under one orchestrator:

```
config.py     paths + LM Studio settings, all env-driven (OBSIDIAN_VAULT_PATH is central)
embedder.py   OpenAI-compatible client → LM Studio /v1/embeddings (singleton client)
indexer.py    scan_vault → chunk_text → embed → FAISS IndexFlatL2 + metadata.json
searcher.py   embed query → FAISS L2 search → score 1/(1+dist) → dedupe by note
brain.py      orchestration: query_brain / write_entity_note / append_insight / consolidate
tasks.py      deterministic checkbox scanner (scan/count/complete) — NOT semantic
mcp_server.py FastMCP server exposing all of the above as tools (stdio or streamable-HTTP)
```

Things that span multiple files and are easy to get wrong:

- **Where index data lives.** The index is written *inside the vault* at `_brain/index.faiss`
  + `_brain/metadata.json` (`config.py`), and these index artifacts under `_brain/` are never
  re-indexed. `scan_vault` and the task scanner otherwise apply divergent `_brain` policies
  (see `safe_paths.is_scannable_md`): entity notes under `_brain/entities/` are indexed for
  semantic search but not task-scanned. `_brain/` is gitignored by Obsidian LiveSync and
  replicated separately from notes.

- **Concurrency model.** `indexer.INDEX_LOCK` (an `RLock`) is shared by `indexer` and
  `searcher` for in-process readers; a cross-process `fcntl` lock (`_build_lock`) serializes
  whole builds so a manual `consolidate.py`/`indexer.py --force` can't interleave its swap with
  the scheduler's. The *heavy* work (scan + embed) runs outside `INDEX_LOCK`; only the brief
  read (search) and the atomic file swap hold it. `build_index` writes `*.tmp` then `os.replace`s,
  cleans up leftover `*.tmp` on the next build, and `search` guards `index.ntotal == len(chunks)`
  (returning `[]` + a "rebuild needed" log on mismatch) so a crash between the two `os.replace`s
  never yields wrong text. Preserve these invariants.

- **Incremental rebuild.** `build_index(force=False)` compares a `vault_signature` (hash of the
  sorted set of relpaths + mtimes) so deletions/renames trigger a rebuild — not just `max(mtime)`.
  `--force` / `force=True` re-embeds all. Embeddings are batched (`EMBED_BATCH_SIZE`) with
  per-batch backoff retry (`EMBED_MAX_RETRIES`) and an explicit `EMBED_TIMEOUT`.

- **Chunking.** `chunk_text` splits on sentence boundaries (`.!?`), not token counts, for
  semantic coherence; tokens are approximated as `words * 1.3`. Frontmatter is stripped in
  `scan_vault` before chunking.

- **Retrieval.** FAISS returns by L2 distance; `search` retrieves `2*top_k`, converts distance
  to a `1/(1+dist)` score, then **dedupes to one chunk per note** before returning `top_k`.
  Semantic search finds *relevant* notes; `tasks.py` is the separate, exhaustive path for
  "every checkbox" questions — don't use semantic search when completeness matters.

- **Two index-refresh paths.** (1) `consolidate.py` as an external cron entry; (2) a daemon
  thread baked into `mcp_server.py` that, *only in HTTP mode*, rebuilds ~30s after boot and then
  nightly at `BRAIN_REFRESH_AT_HOUR`, after which it runs `moc_linker.py` + `ledger_update.py`
  as subprocesses. All toggled by `BRAIN_*` env vars; every step is isolated so one failure
  only logs and never kills the thread.

- **Maintenance scripts are deliberately pure-stdlib + reversible.** `moc_linker.py` and
  `ledger_update.py` use only `urllib`/`json` (no `openai` dep), call a local *chat* model, work
  inside managed `<!-- ... -->` blocks for idempotency, and back up files **outside the vault**
  (so Obsidian/LiveSync never index the backups). `ledger_update.py` imports helpers from
  `moc_linker.py`. Keep edits surgical and reversible.

## MCP server

`mcp_server.py` is the production interface. Tool names/behavior are identical across both
transports; transport is chosen by `--http`/`--stdio` or `MCP_TRANSPORT` (default `stdio`).
HTTP mode runs **stateless** (`stateless_http`, override with `BRAIN_STATELESS_HTTP=0`) to avoid
session-expiry churn, and exposes `GET /health` for container probes. All tools are thin
wrappers over `brain.py` + `tasks.py`.

- **Auth:** set `BRAIN_AUTH_TOKEN` to require `Authorization: Bearer <token>` on every HTTP
  request except `/health` (middleware in `auth.py`, wired via `_build_http_app`). Unset = no
  auth (a startup warning is logged); restrict the port to a trusted network in that case.
- **Write safety:** `brain_append_insight` / `brain_complete_task` confine writes to the vault
  via `safe_paths.resolve_in_vault` (reject `..`/absolute escapes + non-`.md`, follow symlinks),
  preserve line endings, and write atomically. `brain_append_insight` / `brain_write_entity`
  return structured JSON (`status` `ok`/`error`/`created`/`exists`) — branch on it.

| Tool | Purpose |
|------|---------|
| `brain_query(query, top_k=5)` | Semantic retrieval. Synthesize results into your answer; don't paste raw. |
| `brain_tasks(status, query)` | Exhaustive checkbox list (`open`/`done`/`all`), optional substring filter. Use instead of `brain_query` for "what are my open tasks". |
| `brain_complete_task(note_path, match)` | Flip one open `- [ ]` to `- [x] … ✅ <date>` in place; errors if `match` isn't unique. |
| `brain_write_entity(name, initial_content)` | Create `_brain/entities/<slug>.md` (slug = lowercased, spaces/`/` → `-`). |
| `brain_append_insight(note_path, insight, context)` | Append a timestamped `## Brain Insight` block to an existing note. |
| `brain_build_index(force)` | Rebuild when notes changed externally and results look stale. |
| `brain_status()` | Vault path, embedding model, index stats, entity + task counts. |

## Using the brain (as a consuming agent)

Register over stdio in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "obsidian-brain": {
      "command": "python3",
      "args": ["/path/to/obsidian-brain/mcp_server.py"],
      "env": { "OBSIDIAN_VAULT_PATH": "/path/to/vault" }
    }
  }
}
```

For the remote HTTP deployment: `{ "mcpServers": { "obsidian-brain": { "url": "http://<host>:8053/mcp" } } }`.

**Query the vault when:** a question mentions a person/project/client/company; asks what the
user decided/agreed/concluded; references past notes, meetings, or conversations; or the user
says "remember that…" / "I decided…". **Skip it for** general world knowledge or anything
answerable from the current conversation.

**Write conventions:** confirm with the user before writing (`write_entity`, `append_insight`,
`complete_task`) **unless** they explicitly said to remember it / that it's done. Entity names
are slugified; insights are timestamped and appended (never overwrite).

## Tech Stack

- Vault: any Obsidian vault (point `OBSIDIAN_VAULT_PATH` at it); index in `_brain/` syncs separately from notes
- Index: FAISS `IndexFlatL2`, 768-dim vectors
- Embeddings: `text-embedding-nomic-embed-text-v2-moe` via LM Studio (OpenAI-compatible)
- Maintenance chat model: a reasoning model via llama-swap (emits `reasoning_content` + `content`)
- Deps: `faiss-cpu`, `numpy`, `openai`, `requests`, `mcp>=1.26.0`, `uvicorn`
- Deployment: containerized streamable-HTTP, host `8053` → container `8000`; see `deploy/README.md`