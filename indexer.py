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


def _emb_cache_path() -> str:
    return os.path.join(BRAIN_DIR, "embcache.npz")


def _emb_key(text: str) -> str:
    """Cache key for a chunk's embedding. Includes the model and doc prefix, so a
    model/prefix change naturally invalidates every entry (the whole index rebuilds
    on such a change anyway — M-L)."""
    h = hashlib.sha256()
    h.update(EMBEDDING_MODEL.encode("utf-8"))
    h.update(b"\0")
    h.update(EMBED_DOC_PREFIX.encode("utf-8"))
    h.update(b"\0")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _load_emb_cache() -> dict:
    """Load the content-hash → vector cache (best-effort; a missing/corrupt cache
    just means a full re-embed)."""
    path = _emb_cache_path()
    if not os.path.exists(path):
        return {}
    try:
        data = np.load(path, allow_pickle=False)
        keys, vectors = data["keys"], data["vectors"]
        return {str(k): vectors[i] for i, k in enumerate(keys)}
    except Exception as e:  # noqa: BLE001 — any read failure → rebuild from scratch
        print(f"[indexer] embedding cache unreadable ({e}); re-embedding all", file=sys.stderr)
        return {}


def _save_emb_cache(cache: dict) -> None:
    if not cache:
        return
    path = _emb_cache_path()
    # np.savez appends '.npz' unless the name already ends in it, so the tmp name
    # must end in '.npz' or the os.replace source won't exist.
    tmp = path + ".building.npz"
    try:
        keys = np.array(list(cache.keys()))
        vectors = np.array(list(cache.values()), dtype="float32")
        np.savez(tmp, keys=keys, vectors=vectors)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[indexer] could not write embedding cache: {e}", file=sys.stderr)


def _embed_chunks_cached(chunk_texts: list[str]) -> list:
    """Return an embedding per chunk, embedding ONLY texts not already in the cache
    (keyed by content hash), so a rebuild after a small edit re-embeds just the
    changed chunks instead of the whole vault (capability #2). The cache is pruned
    to the current chunk set each build so it can't grow unbounded."""
    cache = _load_emb_cache()
    keys = [_emb_key(t) for t in chunk_texts]
    missing_idx = [i for i, k in enumerate(keys) if k not in cache]
    if missing_idx:
        fresh = embed_texts([EMBED_DOC_PREFIX + chunk_texts[i] for i in missing_idx])
        for i, vec in zip(missing_idx, fresh):
            cache[keys[i]] = np.array(vec, dtype="float32")
    embeddings = [cache[k] for k in keys]
    _save_emb_cache({k: cache[k] for k in keys})  # prune to current keys
    return embeddings


def _fsync_path(path: str) -> None:
    """Best-effort fsync of a file or directory, so an os.replace'd index/metadata
    actually reaches disk before we trust it — a power loss between write and
    flush could otherwise leave a torn file on this power-cycled host (low-1)."""
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


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


def _index_params() -> dict[str, Any]:
    """The index-defining parameters that, if changed, invalidate an existing
    index even when the vault files are untouched. Folded into the freshness
    decision so a same-dimension model swap, a CHUNK_SIZE/OVERLAP change, or a
    change to the nomic embedding prefix triggers a rebuild instead of silently
    serving a stale-model / stale-chunking index (audit finding M-L)."""
    return {
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "doc_prefix": EMBED_DOC_PREFIX,
    }


def _index_is_consistent(existing_meta: dict[str, Any]) -> bool:
    """True only if index.faiss is loadable AND its row count matches the metadata
    chunk count. A signature match alone does not prove the on-disk index is usable
    — a corrupt/truncated index.faiss (bad LiveSync replication of _brain/, a torn
    swap) with intact metadata would otherwise be trusted forever, silently serving
    zero results with no rebuild path (audit finding M-K)."""
    try:
        idx = faiss.read_index(INDEX_PATH)
    except (RuntimeError, OSError) as e:
        print(f"[indexer] existing index unreadable ({e}); forcing rebuild", file=sys.stderr)
        return False
    if idx.ntotal != len(existing_meta.get("chunks", [])):
        print(f"[indexer] existing index/metadata out of sync "
              f"(ntotal={idx.ntotal}, chunks={len(existing_meta.get('chunks', []))}); "
              f"forcing rebuild", file=sys.stderr)
        return False
    return True


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
    # Hard-split any single "sentence" that already exceeds the budget (a table,
    # code block, link/bullet list, or OCR run with no .!? terminator) so no chunk
    # can blow past the model's context window and get silently truncated (M-B).
    # Split target is max_tokens-overlap so the overlap prepend can't push a chunk
    # back over max_tokens.
    max_words = max(1, int(max(max_tokens - overlap, max_tokens // 2) / 1.3))
    bounded = []
    for s in sentences:
        if count_tokens(s) <= max_tokens:
            bounded.append(s)
            continue
        words = s.split()
        for i in range(0, len(words), max_words):
            bounded.append(" ".join(words[i:i + max_words]))
    sentences = bounded
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


# The linker writes managed <!-- moc-linker:...:begin -->…<!-- ...:end --> blocks
# (MOC links + "## Related Notes") into note bodies; strip them before embedding so
# generated link boilerplate doesn't re-enter the index and make a note retrievable
# for topics it merely links to (audit finding M-E). `:end` (not bare `end`) anchors
# to the real close marker so stray "…end -->" text in prose can't truncate it.
_MANAGED_BLOCK_RE = re.compile(r"<!--\s*moc-linker:.*?:end\s*-->", re.S)


def scan_vault(vault_path: str) -> list[dict[str, Any]]:
    """
    Walk the vault and return all markdown notes with their content.
    Skips files in _brain/ directory.
    """
    notes = []
    vault = Path(vault_path)
    for md_file in vault.rglob("*.md"):
        rel_path = md_file.relative_to(vault)
        # Skip everything under _brain/ EXCEPT _brain/entities/, so the derivative
        # index files stay out but curated entity notes (brain_write_entity) are
        # indexed and retrievable rather than write-only (audit finding M-D).
        if rel_path.parts and rel_path.parts[0] == "_brain" and not (
                len(rel_path.parts) >= 2 and rel_path.parts[1] == "entities"):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            # Strip Obsidian metadata frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            # Strip the linker's managed blocks so generated link boilerplate is
            # never re-embedded (M-E).
            content = _MANAGED_BLOCK_RE.sub("", content).strip()
            # Prepend the note title (filename stem) so it participates in the
            # embeddings — in Obsidian the filename is the title and is often the
            # only place a person/project name appears (M-C).
            content = f"{rel_path.stem}\n\n{content}".strip()
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
            params_match = existing_meta.get("index_params") == _index_params()
            if (existing_sig is not None and existing_sig == current_sig
                    and params_match and _index_is_consistent(existing_meta)):
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
    # Embed via the content-hash cache so only new/changed chunks hit the endpoint;
    # the EMBED_DOC_PREFIX (nomic task instruction) is applied to the embed INPUT
    # only, so the stored chunk text stays clean for retrieval (M-A / capability #2).
    embeddings = _embed_chunks_cached([c["text"] for c in all_chunks])

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
        # Index-defining params; compared on the incremental path so a model/chunk/
        # prefix change forces a rebuild even with an unchanged vault (M-L).
        "index_params": _index_params(),
    }

    # Hold a cross-process lock around the swap so a manual build can't interleave
    # its file swap with the scheduler's (M2); the in-process INDEX_LOCK still
    # guards same-process readers. Writes go to *.tmp then os.replace so a
    # concurrent search sees either the complete old or complete new index.
    with _build_lock(), INDEX_LOCK:
        # Clear leftover *.tmp from a crashed prior build INSIDE the lock, so a
        # concurrent build can't delete this build's in-flight tmp mid-swap (low-2).
        cleanup_tmp_files()
        tmp_index = INDEX_PATH + ".tmp"
        tmp_meta = METADATA_PATH + ".tmp"
        faiss.write_index(index, tmp_index)
        Path(tmp_meta).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        # fsync the tmp files before the replaces, and the directory after, so a
        # crash can't leave a torn index/metadata pair on disk (low-1).
        _fsync_path(tmp_index)
        _fsync_path(tmp_meta)
        os.replace(tmp_index, INDEX_PATH)
        os.replace(tmp_meta, METADATA_PATH)
        _fsync_path(BRAIN_DIR)

    print(f"Index saved: {INDEX_PATH}")
    return {"status": "built", "notes": len(notes), "chunks": len(all_chunks), "path": INDEX_PATH}


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = build_index(force=force)
    print(result)
