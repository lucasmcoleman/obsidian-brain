"""
Obsidian vault indexer.
Scans .md files, chunks content, generates embeddings, and builds a FAISS index.
"""
import hashlib
import json
import os
import re
import sys
import math
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from config import VAULT_PATH, BRAIN_DIR, ENTITIES_DIR, INDEX_PATH, METADATA_PATH, CHUNK_SIZE, CHUNK_OVERLAP, EMBEDDING_MODEL, EMBED_DOC_PREFIX
from embedder import embed_texts

# Serializes the brief index file read (searcher) and write (build_index) within
# this process, so a concurrent search never sees a half-written or mismatched
# index. The heavy work (scan + embed) runs OUTSIDE the lock.
INDEX_LOCK = threading.RLock()


def ensure_dirs():
    os.makedirs(BRAIN_DIR, exist_ok=True)
    os.makedirs(ENTITIES_DIR, exist_ok=True)


def cleanup_tmp_files() -> None:
    """Remove leftover *.tmp files from a crashed/interrupted build so they can
    never be mistaken for a real index (audit finding M1)."""
    for path in (INDEX_PATH + ".tmp", METADATA_PATH + ".tmp"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[indexer] could not remove {path}: {e}", file=sys.stderr)


@contextmanager
def _build_lock():
    """Cross-process advisory lock around a full build, so a manual
    `consolidate.py`/`indexer.py --force` run cannot interleave its file swap with
    the container's scheduler build and corrupt the index (audit finding M2).

    Best-effort: if fcntl is unavailable (non-POSIX) or the lock dir can't be
    created, the build proceeds unlocked rather than failing outright.
    """
    os.makedirs(BRAIN_DIR, exist_ok=True)
    lock_path = os.path.join(BRAIN_DIR, ".build.lock")
    try:
        import fcntl
    except ImportError:
        yield
        return
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _vault_signature(notes: list[dict[str, Any]]) -> str:
    """A fingerprint of the indexed file SET (relative path + mtime), so the
    incremental freshness check detects deletions and renames — not just the max
    mtime, which can never decrease when a note is removed (audit finding M7)."""
    h = hashlib.sha256()
    for n in sorted(notes, key=lambda x: x["path"]):
        h.update(n["path"].encode("utf-8"))
        h.update(b"\0")
        h.update(repr(n["mtime"]).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def count_tokens(text: str) -> int:
    """Rough token count (words * 1.3 is a decent approximation for English)."""
    return int(len(text.split()) * 1.3)


def chunk_text(text: str, max_tokens: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict[str, Any]]:
    """
    Split a note into token-bounded chunks with overlap.
    Returns a list of {'text': str}. (Earlier versions also returned start/end
    word offsets, but they were computed incorrectly and never read; removed to
    avoid a latent landmine — audit finding L2.)
    """
    # Split by sentence boundaries for cleaner chunks
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk_words = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if current_tokens + sentence_tokens > max_tokens and current_chunk_words:
            # Save current chunk
            chunks.append({"text": " ".join(current_chunk_words)})
            # Start next chunk with overlap
            overlap_words = " ".join(current_chunk_words).split()[-int(overlap / 1.3):]
            current_chunk_words = overlap_words + [sentence]
            current_tokens = count_tokens(" ".join(current_chunk_words))
        else:
            current_chunk_words.append(sentence)
            current_tokens += sentence_tokens

    if current_chunk_words:
        chunks.append({"text": " ".join(current_chunk_words)})

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
        except (OSError, UnicodeDecodeError) as e:
            # Don't silently drop notes — a consistently-failing note should be
            # visible, not invisibly missing from the index (audit finding L12).
            print(f"[indexer] skipping unreadable note {rel_path}: {e}", file=sys.stderr)
            continue
    return notes


def build_index(force: bool = False) -> dict[str, Any]:
    """
    Scan the vault, chunk all notes, generate embeddings, and build the FAISS index.
    Returns a summary dict.
    """
    ensure_dirs()
    cleanup_tmp_files()  # clear any leftover *.tmp from a crashed prior build (M1)

    notes = scan_vault(VAULT_PATH)

    if not force and os.path.exists(INDEX_PATH) and os.path.exists(METADATA_PATH):
        try:
            existing_meta = json.loads(Path(METADATA_PATH).read_text())
        except (json.JSONDecodeError, OSError):
            existing_meta = None  # corrupt/unreadable → treat as needs-rebuild (L8)
        if existing_meta is not None:
            existing_sig = existing_meta.get("vault_signature")
            current_sig = _vault_signature(notes)
            # Rebuild if the file SET or any mtime changed (catches deletions and
            # renames, which a max-mtime check alone misses — audit finding M7).
            if existing_sig is not None and existing_sig == current_sig:
                return {
                    "status": "already_current",
                    "notes": existing_meta.get("num_notes", len(notes)),
                    "chunks": len(existing_meta.get("chunks", [])),
                    "path": INDEX_PATH,
                }

    print("Scanning vault...")
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
    # Prefix the embedding INPUT only (nomic task instruction); the stored chunk
    # text stays clean so retrieval returns the original passage (M-A).
    texts = [EMBED_DOC_PREFIX + c["text"] for c in all_chunks]
    embeddings = embed_texts(texts)

    # Build FAISS index
    dim = len(embeddings[0])
    index = faiss.IndexFlatL2(dim)
    index.add(np.array(embeddings).astype("float32"))

    metadata = {
        "chunks": all_chunks,
        "index_mtime": max(n["mtime"] for n in notes),
        "vault_signature": _vault_signature(notes),
        "num_notes": len(notes),
        "num_chunks": len(all_chunks),
        "embedding_model": EMBEDDING_MODEL,
    }

    # Hold a cross-process lock around the swap so a manual build can't interleave
    # its file swap with the scheduler's (M2); the in-process INDEX_LOCK still
    # guards same-process readers. Writes go to *.tmp then os.replace so a
    # concurrent search sees either the complete old or complete new index.
    with _build_lock(), INDEX_LOCK:
        tmp_index = INDEX_PATH + ".tmp"
        tmp_meta = METADATA_PATH + ".tmp"
        faiss.write_index(index, tmp_index)
        Path(tmp_meta).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(tmp_index, INDEX_PATH)
        os.replace(tmp_meta, METADATA_PATH)

    print(f"Index saved: {INDEX_PATH}")
    return {"status": "built", "notes": len(notes), "chunks": len(all_chunks), "path": INDEX_PATH}


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = build_index(force=force)
    print(result)
