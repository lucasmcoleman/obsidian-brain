---
name: obsidian-brain-usage
description: "Obsidian Brain — a persistent knowledge layer over Lucas's Obsidian vault. Invoke automatically (no special commands) whenever vault context could improve an answer, or when something worth remembering emerges. Backed by an MCP server exposing semantic search + write-back. (Consuming-agent skill; the CLAUDE.md dev skill is named `obsidian-brain` — distinct names avoid a manifest collision.)"
platforms: [linux, macos, windows]
---

# Obsidian Brain

A gbrain-style persistent memory backed by Lucas's Obsidian vault. Use it naturally —
there is no command to type. When a question could benefit from personal context, or
when you learn something worth keeping, reach for the brain's MCP tools.

## How it's exposed

The brain runs as an MCP server (`obsidian-brain`) and is reached through MCP tools —
the same tools whether you're a local agent (stdio) or a remote one (streamable-HTTP
container at `:8053/mcp`). The vault lives at `/server/obsidian`; all brain-generated
notes live under `_brain/` (entities in `_brain/entities/`).

## When to invoke

Call `brain_query` automatically when:

- The question mentions a person, project, client, or company
- The question asks what Lucas decided, agreed to, or concluded
- The question references something Lucas has been working on or discussed
- The question asks about past notes, meetings, or conversations
- Lucas says "remember that…" or "I decided…"

Do NOT call it for general world knowledge, math, or coding questions unrelated to
Lucas's personal context, or things clearly answerable from the current conversation.

## Tools

- **`brain_query(query, top_k=5)`** — semantic search over the vault. Pass the user's
  full message. Returns ranked note excerpts. Synthesize them into your answer; don't
  paste raw.
- **`brain_write_entity(name, initial_content="")`** — create an entity note for a new
  person/project/concept in `_brain/entities/`.
- **`brain_append_insight(note_path, insight, context="")`** — append a fact/decision to
  an existing note (`note_path` absolute or vault-relative, e.g. `Projects/Delta.md`).
- **`brain_tasks(status="open", query="")`** — exhaustive, deterministic list of every
  checkbox task across the vault (`open` / `done` / `all`), grouped by note. Use this —
  not `brain_query` — for "what's open / what are my tasks" questions. `query` filters by
  substring (e.g. a project name).
- **`brain_complete_task(note_path, match)`** — mark an open task done in place
  (`- [ ]` → `- [x] … ✅ <date>`). `match` must identify exactly one open task. Ask
  first unless Lucas clearly said it's done.
- **`brain_build_index(force=False)`** — rebuild the FAISS index after bulk note changes.
- **`brain_status()`** — index stats, vault path, embedding model, entity + task counts.

## Tasks

Tasks are Obsidian checkboxes (`- [ ]` open, `- [x]` done) scattered across notes.
`brain_query` (semantic) finds *relevant* notes; `brain_tasks` finds *every* task
precisely. When Lucas asks what's on his plate, call `brain_tasks("open")`. When he says
a task is finished, call `brain_complete_task` (confirm the exact task first if ambiguous).

## Integrating context

1. Read the excerpts `brain_query` returns.
2. Synthesize the relevant facts into your answer (reference the note path when helpful,
   e.g. "Based on your notes in Project X…").
3. Don't dump the raw block.

## Writing back

After a substantive conversation, proactively offer to record:

- **Decisions** → `brain_append_insight` on the relevant project/personal note
- **New people/projects** → `brain_write_entity`
- **Preferences / commitments** → `brain_append_insight` on the relevant note

**Ask before writing** unless Lucas explicitly says "remember this." Prefer appending to
an existing note over creating new files.

## Pitfalls

- Embeddings come from LM Studio (`http://192.168.0.29:1234/v1`). If it's down, retrieval
  fails gracefully (empty results) — say so rather than guessing.
- Don't call `brain_query` on every message. Use judgment; when unsure, ask first.
- The index is rebuilt nightly by a consolidation job — no manual action needed normally.
