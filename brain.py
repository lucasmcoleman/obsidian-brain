"""
Obsidian Brain — main orchestration module.
Handles context retrieval and write-back to the vault.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from config import VAULT_PATH, BRAIN_DIR, ENTITIES_DIR, INDEX_PATH, METADATA_PATH
from indexer import build_index, ensure_dirs
from searcher import search, format_results
from safe_paths import (
    resolve_in_vault,
    PathOutsideVault,
    detect_newline,
    atomic_write_bytes,
)


def _slugify(name: str) -> str:
    """Lowercase, collapse every run of non-alphanumeric characters to a single
    '-', and strip leading/trailing dashes (audit finding L11)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_or_build_index() -> dict:
    """Ensure the index exists and is current."""
    ensure_dirs()
    return build_index()


def query_brain(query: str, top_k: int = 5) -> str:
    """
    Main retrieval entry point. Called by the agent when a user message
    might benefit from vault context.
    Returns a formatted context string. Distinguishes "no index built yet" from
    "index exists but nothing matched" so the agent can self-heal (L19).
    """
    if not os.path.exists(INDEX_PATH) or not os.path.exists(METADATA_PATH):
        return ("No index has been built yet. Run brain_build_index (or the CLI "
                "`indexer.py --force`) before querying the brain.")
    results = search(query, top_k=top_k)
    return format_results(results, query)


def write_entity_note(entity_name: str, initial_content: str = "") -> dict:
    """
    Create an entity note in _brain/entities/. Returns a structured result that
    distinguishes a fresh create from an already-existing note, so a caller's
    supplied content is never silently discarded (audit findings M14/M15).
    Existing entity files are never overwritten — use append_insight to add to one.
    """
    slug = _slugify(entity_name)
    filepath = Path(ENTITIES_DIR) / f"{slug}.md"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if filepath.exists():
        return {
            "status": "exists",
            "slug": slug,
            "path": str(filepath),
            "detail": "Entity already exists; not modified. Use append_insight to add to it.",
        }

    content = f"# {entity_name}\n\n"
    if initial_content:
        content += f"{initial_content}\n\n"
    content += f"> Created by Obsidian Brain on {datetime.now().strftime('%Y-%m-%d')}\n"
    filepath.write_text(content, encoding="utf-8")
    return {"status": "created", "slug": slug, "path": str(filepath)}


def append_insight(note_path: str, insight: str, context: str = "") -> dict:
    """
    Append a timestamped insight section to a note. Returns a structured result
    with an explicit status (audit findings M14/M10). The note_path may be
    absolute or vault-relative; it must resolve inside the vault and be *.md.
    Preserves the note's existing line endings and writes atomically.
    """
    try:
        target = resolve_in_vault(note_path, VAULT_PATH)
    except PathOutsideVault as e:
        return {"status": "error", "detail": f"Refused: {e}"}

    if not target.exists():
        return {"status": "error", "detail": f"Note not found: {target}"}

    raw = target.read_bytes()
    existing = raw.decode("utf-8")
    nl = detect_newline(raw)

    lines = [
        "",
        "",
        f"## Brain Insight — {datetime.now().strftime('%Y-%m-%d')}",
        "",
    ]
    if context:
        lines += [f"**Context:** {context}", ""]
    lines += [insight, "", "> _Recorded by Obsidian Brain_"]
    section = nl.join(lines)

    atomic_write_bytes(target, (existing + section).encode("utf-8"))
    return {"status": "ok", "path": str(target), "detail": f"Appended insight to {target}"}


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
