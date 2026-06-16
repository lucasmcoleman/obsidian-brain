"""
Obsidian vault indexer.
Scans .md files, chunks content, generates embeddings, and builds a FAISS index.
"""
import json
import os
import re
import math
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from config import VAULT_PATH, BRAIN_DIR, ENTITIES_DIR, INDEX_PATH, METADATA_PATH, CHUNK_SIZE, CHUNK_OVERLAP, EMBEDDING_MODEL
from embedder import embed_texts


def ensure_dirs():
    os.makedirs(BRAIN_DIR, exist_ok=True)
    os.makedirs(ENTITIES_DIR, exist_ok=True)


def count_tokens(text: str) -> int:
    """Rough token count (words * 1.3 is a decent approximation for English)."""
    return int(len(text.split()) * 1.3)


def chunk_text(text: str, max_tokens: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict[str, Any]]:
    """
    Split a note into token-bounded chunks with overlap.
    Returns list of {'text': str, 'start': int, 'end': int}.
    """
    # Split by sentence boundaries for cleaner chunks
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk_words = []
    current_tokens = 0
    start_word = 0

    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if current_tokens + sentence_tokens > max_tokens and current_chunk_words:
            # Save current chunk
            chunk_text_str = " ".join(current_chunk_words)
            chunks.append({
                "text": chunk_text_str,
                "start": start_word,
                "end": start_word + len(" ".join(current_chunk_words).split()),
            })
            # Start next chunk with overlap
            overlap_words = " ".join(current_chunk_words).split()[-int(overlap / 1.3):]
            current_chunk_words = overlap_words + [sentence]
            current_tokens = count_tokens(" ".join(current_chunk_words))
            start_word = len(" ".join(current_chunk_words).split()) - len(overlap_words)
        else:
            current_chunk_words.append(sentence)
            current_tokens += sentence_tokens

    if current_chunk_words:
        chunks.append({
            "text": " ".join(current_chunk_words),
            "start": start_word,
            "end": start_word + len(" ".join(current_chunk_words).split()),
        })

    return chunks


def scan_vault(vault_path: str) -> list[dict[str, Any]]:
    """
    Walk the vault and return all markdown notes with their content.
    Skips files in _brain/ directory.
    """
    notes = []
    vault = Path(vault_path)
    for md_file in vault.rglob("*.md"):
        if "_brain" in md_file.parts:
            continue
        rel_path = md_file.relative_to(vault)
        try:
            content = md_file.read_text(encoding="utf-8")
            # Strip Obsidian metadata frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            notes.append({
                "path": str(rel_path),
                "abs_path": str(md_file),
                "content": content,
                "mtime": os.path.getmtime(md_file),
            })
        except Exception:
            continue
    return notes


def build_index(force: bool = False) -> dict[str, Any]:
    """
    Scan the vault, chunk all notes, generate embeddings, and build the FAISS index.
    Returns a summary dict.
    """
    ensure_dirs()

    if not force and os.path.exists(INDEX_PATH) and os.path.exists(METADATA_PATH):
        existing_meta = json.loads(Path(METADATA_PATH).read_text())
        existing_index_mtime = existing_meta.get("index_mtime", 0)
        # Quick check: rebuild only if vault is newer
        vault_mtimes = []
        for note in scan_vault(VAULT_PATH):
            vault_mtimes.append(note["mtime"])
        if vault_mtimes and max(vault_mtimes) <= existing_index_mtime:
            return {"status": "already_current", "notes": len(existing_meta.get("chunks", [])), "path": INDEX_PATH}

    print("Scanning vault...")
    notes = scan_vault(VAULT_PATH)
    print(f"Found {len(notes)} notes")

    all_chunks = []
    for note in notes:
        chunks = chunk_text(note["content"])
        for chunk in chunks:
            all_chunks.append({
                "text": chunk["text"],
                "note_path": note["path"],
                "abs_path": note["abs_path"],
                "mtime": note["mtime"],
            })

    print(f"Created {len(all_chunks)} chunks")

    if not all_chunks:
        return {"status": "no_content", "notes": 0}

    print("Generating embeddings...")
    texts = [c["text"] for c in all_chunks]
    embeddings = embed_texts(texts)

    # Build FAISS index
    dim = len(embeddings[0])
    index = faiss.IndexFlatL2(dim)
    index.add(np.array(embeddings).astype("float32"))

    # Save index
    faiss.write_index(index, INDEX_PATH)

    # Save metadata
    metadata = {
        "chunks": all_chunks,
        "index_mtime": max(n["mtime"] for n in notes),
        "num_notes": len(notes),
        "num_chunks": len(all_chunks),
        "embedding_model": EMBEDDING_MODEL,
    }
    Path(METADATA_PATH).write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Index saved: {INDEX_PATH}")
    return {"status": "built", "notes": len(notes), "chunks": len(all_chunks), "path": INDEX_PATH}


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = build_index(force=force)
    print(result)
