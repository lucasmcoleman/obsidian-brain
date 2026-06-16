---
name: obsidian-brain
description: "Query Lucas's Obsidian vault for personal context, record new entities, and append insights to notes. Use naturally — no special commands."
trigger: "when answering questions about people, projects, decisions, or past conversations; or when Lucas says 'remember that' / 'I decided'"
---

# Obsidian Brain

Use this skill to access Lucas Coleman's personal knowledge base — an indexed Obsidian vault of markdown notes.

## Vault Access

The vault is available via an MCP server. Add to your Claude Code MCP config (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "obsidian-brain": {
      "command": "python3",
      "args": ["/server/programming/obsidian-brain/mcp_server.py"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/server/obsidian"
      }
    }
  }
}
```

Or run directly:
```bash
OBSIDIAN_VAULT_PATH=/server/obsidian python3 /server/programming/obsidian-brain/mcp_server.py
```

For remote access (other machines), serve via SSH port forward:
```bash
ssh user@server -L 8765:localhost:8765  # then connect to localhost:8765
```

## When to Use

**Invoke this skill when:**
- A question mentions a person, project, client, or company
- A question asks what Lucas decided, agreed to, or concluded
- A question references something Lucas has been working on or discussed
- A question asks about past notes, meetings, or conversations
- Lucas says "remember that..." or "I decided..."
- You learn something new that should be recorded for future reference

**Skip this skill when:**
- General world knowledge (geography, definitions, math)
- Questions clearly answerable from the current conversation alone
- Coding or technical questions unrelated to Lucas's personal context

## MCP Tools Available

### `brain_query` — Retrieve relevant context
```json
{"query": "what did Lucas decide about the Delta Heartland project", "top_k": 5}
```
Returns formatted context from relevant vault notes. Incorporate naturally into your answer — do not paste raw context verbatim.

### `brain_write_entity` — Create an entity note
```json
{"name": "Sarah Chen", "initial_content": "Environmental scientist, SWCA team lead on Delta Heartland project."}
```
Creates `_brain/entities/sarah-chen.md`. Use for new people, projects, or concepts worth tracking.

### `brain_append_insight` — Record a new fact
```json
{"note_path": "/server/obsidian/Projects/Delta Heartland.md", "insight": "Lucas prefers biweekly check-ins over daily standups.", "context": "Discussion about team cadence on 2026-06-15"}
```
Appends to an existing note's "Brain Insight" section.

### `brain_build_index` — Rebuild the search index
```json
{"force": true}
```
Use if new notes were added externally and retrieval results seem stale.

### `brain_status` — Check index health
Returns vault path, embedding model, notes indexed, chunks, and entity count.

## Important Conventions

- Entity names are slugified: "Sarah Chen" → `sarah-chen.md`
- Insights are appended with a timestamp and optional context field
- The `_brain/` directory is gitignored by Obsidian LiveSync — index files sync separately from notes
- When in doubt, ask before writing — unless Lucas explicitly says "remember this"

## Tech Stack

- Vault: `/server/obsidian` (Obsidian LiveSync)
- Index: FAISS (768-dim vectors via `text-embedding-nomic-embed-text-v2-moe` on LM Studio)
- Local model: LM Studio at `http://192.168.0.29:1234/v1`
- Nightly rebuild: 2 AM ET Mon-Fri via `consolidate.py`
