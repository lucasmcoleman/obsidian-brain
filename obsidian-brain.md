---
name: obsidian-brain
description: "Obsidian Brain — persistent knowledge layer that reads from and writes to your Obsidian vault. No special commands required. The agent invokes this skill automatically when vault context could improve the response."
platforms: [linux, macos, windows]
---

# Obsidian Brain

Use this skill whenever vault context could help you answer Lucas's question or when you learn something new that belongs in his personal knowledge base. No special commands needed — just use this skill naturally.

## Vault Path

`/server/docker/docker-data/volumes/obsidian/vault`

Set `OBSIDIAN_VAULT_PATH` in the environment before running scripts.

## Brain Subdirectory

All brain-generated notes live in `_brain/` under the vault root. Entity notes go in `_brain/entities/`.

## When to Invoke

Invoke this skill automatically when:

- The question mentions a person, project, client, or company
- The question asks what Lucas decided, agreed to, or concluded
- The question references something Lucas has been working on or discussed
- The question asks about past notes, meetings, or conversations
- Lucas says "remember that..." or "I decided..."
- After a conversation where significant new information emerged about people, projects, or decisions

You do NOT need to invoke this skill for:
- General world knowledge (geography, history, definitions)
- Math, coding, or technical questions unrelated to Lucas's personal context
- Questions clearly answerable from the current conversation alone

## Tools

### 1. Query the Brain (Context Retrieval)

Before answering a contextual question, call the retrieval function:

```python
from brain import query_brain
context = query_brain(query, top_k=5)
```

Use the user's full message as the query string. Then incorporate the returned context into your answer naturally — do not just dump it. Synthesize the relevant facts into your response.

### 2. Record a New Entity

When you learn about a new person, project, or concept that should be tracked:

```python
from brain import write_entity_note
path = write_entity_note("Entity Name", initial_content="What you know about them.")
```

### 3. Append an Insight

When you learn a new fact, decision, or conclusion after a conversation:

```python
from brain import append_insight
result = append_insight(note_path, insight_text, context="What prompted this insight")
```

Use the absolute vault path or relative path like `Projects/Deltainitiative.md`.

### 4. Rebuild the Index

If the vault has changed significantly (new notes added outside this session):

```python
from brain import build_index
result = build_index(force=True)
```

Run this before long research sessions or after bulk note changes.

## How to Integrate Context

When you retrieve results from `query_brain`, do not simply paste the raw context. Instead:

1. Read the retrieved excerpts
2. Synthesize the relevant facts into your answer
3. Reference the note path if it helps ("Based on your notes in Project X...")

## Writing Insights

After any substantive conversation, proactively offer to record:
- **Decisions made** → append to the relevant project/personal note
- **New people encountered** → create an entity note in `_brain/entities/`
- **Preferences or commitments** → append to the relevant context note

Ask first before writing unless Lucas explicitly says "remember this."

## Nightly Consolidation

A cron job runs the `consolidate()` function nightly to rebuild the index. No manual action needed.

## Running the Indexer

To build or rebuild the index:

```bash
cd /server/programming/obsidian-brain && python indexer.py --force
```

This generates embeddings via LM Studio at `http://192.168.0.29:1234/v1` using the `text-embedding-nomic-embed-text-v2-moe` model.

## Module Structure

```
/server/programming/obsidian-brain/
  config.py      — paths and settings
  embedder.py    — LM Studio API client
  indexer.py     — vault scanner + FAISS index builder
  searcher.py    — semantic search over index
  brain.py       — orchestration + write-back
  requirements.txt
```

## Pitfalls

- The vault path must be accessible. If you get file-not-found errors, check that the volume mount is active.
- Embeddings are generated locally via LM Studio. If the server is down, retrieval will fail gracefully (returns empty).
- Do not invoke this skill for every message. Use judgment. When in doubt, ask first.
- When appending insights, use the existing note path — do not create new files unless explicitly asked.
