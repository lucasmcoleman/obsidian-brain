"""
Obsidian Brain — main orchestration module.
Handles context retrieval and write-back to the vault.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from config import VAULT_PATH, BRAIN_DIR, ENTITIES_DIR
from indexer import build_index, ensure_dirs
from searcher import search, format_results


def get_or_build_index() -> dict:
    """Ensure the index exists and is current."""
    ensure_dirs()
    return build_index()


def query_brain(query: str, top_k: int = 5) -> str:
    """
    Main retrieval entry point. Called by the agent when a user message
    might benefit from vault context.
    Returns a formatted context string.
    """
    results = search(query, top_k=top_k)
    return format_results(results, query)


def write_entity_note(entity_name: str, initial_content: str = "") -> str:
    """
    Create or update an entity note in _brain/entities/.
    Called when the agent learns about a new person, project, or concept.
    """
    ensure_dirs()
    slug = entity_name.lower().replace(" ", "-").replace("/", "-")
    filepath = Path(ENTITIES_DIR) / f"{slug}.md"
    if filepath.exists():
        return str(filepath)

    content = f"# {entity_name}\n\n"
    if initial_content:
        content += f"{initial_content}\n\n"
    content += f"> Created by Obsidian Brain on {datetime.now().strftime('%Y-%m-%d')}\n"
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)


def append_insight(note_path: str, insight: str, context: str = "") -> str:
    """
    Append an insight to a note. Used after conversations to record
    new facts, decisions, or conclusions.
    The note_path can be absolute or relative to the vault.
    """
    if not os.path.isabs(note_path):
        note_path = os.path.join(VAULT_PATH, note_path)

    if not os.path.exists(note_path):
        return f"Note not found: {note_path}"

    existing = Path(note_path).read_text(encoding="utf-8")

    section = f"\n\n## Brain Insight — {datetime.now().strftime('%Y-%m-%d')}\n\n"
    if context:
        section += f"**Context:** {context}\n\n"
    section += f"{insight}\n"
    section += f"\n> _Recorded by Obsidian Brain_"

    updated = existing + section
    Path(note_path).write_text(updated, encoding="utf-8")
    return f"Appended insight to {note_path}"


def consolidate() -> dict:
    """
    Nightly 'dream cycle': rebuild the index to incorporate new notes,
    and optionally enrich entity pages (placeholder for future logic).
    """
    result = build_index(force=True)
    return result


if __name__ == "__main__":
    # Quick smoke test
    build_index()
    results = search("project management decisions")
    print(format_results(results, "project management"))
