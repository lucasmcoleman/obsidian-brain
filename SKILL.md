---
name: obsidian-brain-usage
description: "Use the Obsidian Brain MCP server for the user's projects, clients, people, commitments, decisions and source notes. Provides accepted attention records, evidence review, exact/semantic lookup and authorized write-back. Unrelated general knowledge does not need this skill."
---

# Obsidian Brain

A gbrain-style persistent memory backed by the user's Obsidian vault. Use it naturally —
there is no command to type. When a question could benefit from personal context, or
when you learn something worth keeping, reach for the brain's MCP tools.

## How it's exposed

The brain runs as an MCP server (`obsidian-brain`) and is reached through MCP tools —
the same tools whether you're a local agent (stdio) or a remote one (streamable-HTTP
container, e.g. at `<host>:8053/mcp`). The vault lives wherever `OBSIDIAN_VAULT_PATH`
points (often mounted as `/vault` in containers). Durable director records and
history live under `Brain Workspace/`; older entity notes live in `_brain/entities/`.

## Daily attention and review

For "what needs my attention?", start with `brain_attention`. It returns accepted
commitments and decisions with reasons and coverage. An empty attention list with
pending or stale sources is not evidence that all work is clear.

- `brain_records(kind, review)` and `brain_brief(record_id)` show accepted and
  pending project/client/person context. `brain_source` opens supporting evidence.
- `brain_workspace_sync` reads eligible sources into pending observations without
  changing source notes. Historical checkbox dates are not automatically current.
- `brain_lookup(query)` finds exact project numbers and terms without embeddings.
- `brain_extract_meeting(note_path)` starts a background extraction job;
  `brain_extraction_status(job_id)` reports progress/failure. Results stay pending.
- `brain_review(record_id, action, version, fields)` applies authorized changes.
  Accept only what the user confirms. Keep unknown owners/dates null. Acknowledge
  and defer affect reminders; resolve records a confirmed outcome. Changed evidence
  can be accepted with `accept_changes`, or the accepted position explicitly
  retained with `keep_current`. Stale versions require a reload.
- `brain_record_create(kind, title, fields)` records user-confirmed information.

Preserve source quotations, original event dates, review state and uncertainty.
Do not silently merge similarly worded work from different projects or turn a
model's interpretation into accepted facts. See
[the workspace guide](docs/director-workspace.md) for storage and API details.

## When to invoke

Call `brain_query` automatically when:

- The question mentions a person, project, client, or company
- The question asks what the user decided, agreed to, or concluded
- The question references something the user has been working on or discussed
- The question asks about past notes, meetings, or conversations
- The user says "remember that…" or "I decided…"

Do NOT call it for general world knowledge, math, or coding questions unrelated to the
user's personal context, or things clearly answerable from the current conversation.

## Tools

- **`brain_query(query, top_k=5)`** — semantic search over the vault. Pass the user's
  full message. Returns ranked note excerpts. Synthesize them into your answer; don't
  paste raw.
- **`brain_write_entity(name, initial_content="")`** — create an entity note for a new
  person/project/concept in `_brain/entities/`.
- **`brain_append_insight(note_path, insight, context="")`** — append a fact/decision to
  an existing note (`note_path` vault-relative, e.g. `Projects/Delta.md`).
- **`brain_tasks(status="open", query="")`** — exhaustive, deterministic list of every
  checkbox task across the vault (`open` / `done` / `all`), grouped by note. Use this —
  not `brain_query` — for "what's open / what are my tasks" questions. `query` filters by
  substring (e.g. a project name).
- **`brain_complete_task(note_path, match)`** — mark an open task done in place
  (`- [ ]` → `- [x] … ✅ <date>`). `match` must identify exactly one open task. Ask
  first unless the user clearly said it's done.
- **`brain_build_index(force=False)`** — rebuild the FAISS index after bulk note changes.
- **`brain_status()`** — index stats, vault path, embedding model, entity + task counts.

## Tasks

Tasks are Obsidian checkboxes (`- [ ]` open, `- [x]` done) scattered across notes.
`brain_query` (semantic) finds *relevant* notes; `brain_tasks` finds *every* task
precisely. For the current accepted position use `brain_attention`; use
`brain_tasks("open")` when the user wants original source checkboxes. When they
say a task is finished, call `brain_complete_task` (confirm the exact task first if
ambiguous).

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

**Ask before writing** unless the user explicitly says "remember this." Prefer appending to
an existing note over creating new files.

## Pitfalls

- Embeddings use the configured OpenAI-compatible endpoint (`LM_BASE_URL`). If
  unavailable, production retrieval reports an error; do not describe it as zero
  matches. Exact lookup, intake and record review do not require embeddings.
- Don't call `brain_query` on every message. Use judgment; when unsure, ask first.
- The index is rebuilt nightly by a consolidation job — no manual action needed normally.
