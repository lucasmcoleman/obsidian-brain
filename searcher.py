"""
Semantic search over the Obsidian Brain index.
"""
import json
import os
import sys
from pathlib import Path
from typing import Optional

import faiss
import numpy as np

from config import (
    VAULT_PATH, BRAIN_DIR, INDEX_PATH, METADATA_PATH, TOP_K, EMBED_QUERY_PREFIX,
    SEARCH_MAX_TOP_K, SEARCH_MAX_QUERY_CHARS, SEARCH_MIN_SCORE,
    TRUTH_WEIGHT_SUPERSEDED, TRUTH_WEIGHT_CONTESTED, TRUTH_WEIGHT_UNREVIEWED,
)


def _truth_multiplier(chunk: dict) -> float:
    """Truth-maintenance Layer 4: down-weight superseded/contested/raw-import
    chunks so full-weight sources rank first — but never drop them (a stale sole
    source still beats nothing; the ⚠ annotation is what warns the agent)."""
    status = chunk.get("review_status", "")
    if status == "superseded" or chunk.get("superseded_by"):
        return TRUTH_WEIGHT_SUPERSEDED
    if status == "contested":
        return TRUTH_WEIGHT_CONTESTED
    if status == "unreviewed" and chunk.get("source_type") in ("transcript", "ocr"):
        return TRUTH_WEIGHT_UNREVIEWED
    return 1.0
from embedder import embed_query
from indexer import INDEX_LOCK

# In-process cache of the loaded FAISS index + parsed metadata, keyed by both
# files' stat signature. Every query previously re-read index.faiss and re-parsed
# the FULL metadata.json (which stores every chunk's text) from disk — a per-query
# cost that grows linearly with the vault and is re-paid on every stateless-HTTP
# request. A rebuild swaps the files via os.replace (new inode/mtime), so the key
# changes and the next query reloads (scalability gap, AUDIT 2026-07-03).
_INDEX_CACHE: dict = {}


def _stat_key(path: str) -> tuple:
    st = os.stat(path)
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Search the index for notes relevant to the query.
    Returns a list of result dicts with text, note_path, and score (descending),
    one chunk per note. Ranking is FAISS L2 order; the score is a monotonic
    1/(1+distance) transform (not cosine, not comparable across queries).
    Returns [] if the index is absent, unreadable, or inconsistent with its
    metadata (so a crash-window mismatch never returns wrong text — M1/L7/L8).
    """
    # Harden the arguments: a non-int / negative / absurd top_k would otherwise
    # flow into faiss.search and raise or over-allocate; an unbounded query wastes
    # the embed call (audit finding low-9).
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = TOP_K
    top_k = max(1, min(top_k, SEARCH_MAX_TOP_K))
    query = (query or "")[:SEARCH_MAX_QUERY_CHARS]

    # Read the index + metadata together under the lock so we never load a
    # new index against stale metadata (or a half-written file) mid-rebuild.
    with INDEX_LOCK:
        if not os.path.exists(INDEX_PATH) or not os.path.exists(METADATA_PATH):
            _INDEX_CACHE.clear()
            return []
        try:
            key = (_stat_key(INDEX_PATH), _stat_key(METADATA_PATH))
            cached = _INDEX_CACHE.get("entry")
            if cached and cached[0] == key:
                index, metadata = cached[1], cached[2]
            else:
                index = faiss.read_index(INDEX_PATH)
                metadata = json.loads(Path(METADATA_PATH).read_text())
                _INDEX_CACHE["entry"] = (key, index, metadata)
        except (json.JSONDecodeError, OSError, RuntimeError) as e:
            _INDEX_CACHE.clear()
            print(f"[searcher] index/metadata unreadable, returning no results: {e}",
                  file=sys.stderr)
            return []

    chunks = metadata.get("chunks", [])

    if not chunks:
        return []

    # Consistency guard: FAISS row i must map to chunks[i]. A crash between the
    # two os.replace calls (or an external/partial write) can leave the index and
    # metadata mismatched; rather than return wrong text or raise IndexError,
    # bail out and signal a rebuild is needed (audit findings M1 / L7).
    if index.ntotal != len(chunks):
        print(f"[searcher] index/metadata out of sync "
              f"(ntotal={index.ntotal}, chunks={len(chunks)}); rebuild needed",
              file=sys.stderr)
        return []

    # Query gets the nomic "search_query: " prefix to match the "search_document: "
    # prefix on indexed passages; both must be applied together (M-A).
    try:
        query_embedding = embed_query(EMBED_QUERY_PREFIX + query)
    except Exception as e:  # embedding endpoint down/misconfigured
        # Degrade to [] (as documented) instead of propagating, but log loudly so
        # an outage is visible in the container logs rather than silent.
        print(f"[searcher] query embedding failed, returning no results: {e}",
              file=sys.stderr)
        return []
    query_vec = np.array([query_embedding]).astype("float32")

    # Dimension guard (July-1 M-3): mid-migration to a new embedding model the
    # query embeds at a different dimensionality than the stored index; faiss
    # would raise a bare AssertionError. Degrade to [] and say why — the next
    # build_index rebuilds automatically via the stored index_params (M-L).
    if query_vec.shape[1] != index.d:
        print(f"[searcher] query dim {query_vec.shape[1]} != index dim {index.d} — "
              f"embedding model changed; rebuild needed (returning no results)",
              file=sys.stderr)
        return []

    # Retrieve a generous candidate pool so dedup-to-one-chunk-per-note can still
    # fill top_k even when several of the nearest chunks belong to one big note
    # (audit finding low-5).
    k = min(max(top_k * 5, 25), index.ntotal)
    distances, indices = index.search(query_vec, k)

    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx == -1:
            continue
        chunk = chunks[idx]
        # L2 distance to a monotonic similarity score. Cast to a native float:
        # FAISS distances are numpy.float32, which is not JSON-serializable and
        # would 500 any JSON consumer (e.g. the /ui/api/search route).
        score = round(float(1 / (1 + dist)) * _truth_multiplier(chunk), 4)
        if SEARCH_MIN_SCORE > 0 and score < SEARCH_MIN_SCORE:
            continue  # relevance floor (opt-in; 0 = keep all) — low-5
        results.append({
            "text": chunk["text"],
            "note_path": chunk["note_path"],
            "abs_path": chunk["abs_path"],
            "score": score,
            "review_status": chunk.get("review_status", ""),
            "superseded_by": chunk.get("superseded_by", ""),
            "source_type": chunk.get("source_type", ""),
        })

    # The truth multiplier can reorder relative to raw FAISS distance, so re-sort
    # by adjusted score (stable: ties keep FAISS order) before deduping.
    results.sort(key=lambda r: r["score"], reverse=True)

    # Deduplicate to one chunk per note.
    seen_notes = set()
    deduped = []
    for r in results:
        if r["note_path"] not in seen_notes:
            seen_notes.add(r["note_path"])
            deduped.append(r)
            if len(deduped) >= top_k:
                break

    return deduped


def format_results(results: list[dict], query: str) -> str:
    """Format search results as a readable context block."""
    if not results:
        return "No relevant notes found."

    lines = [f"## Relevant notes for: {query}\n"]
    for i, r in enumerate(results, 1):
        # Layer 4's load-bearing line: the agent that would compound a stale claim
        # is the one that must SEE the flag — down-weighting alone is not enough.
        flag = ""
        status = r.get("review_status", "")
        if status == "superseded" or r.get("superseded_by"):
            flag = f"  ⚠️ SUPERSEDED → {r.get('superseded_by') or 'a newer note'} (do not cite as current)"
        elif status == "contested":
            flag = "  ⚠️ CONTESTED (conflicting claims exist — check the review queue)"
        elif status == "unreviewed" and r.get("source_type") in ("transcript", "ocr"):
            flag = f"  ⚠️ UNREVIEWED {r['source_type'].upper()} (raw import, may contain errors)"
        lines.append(f"### {i}. {r['note_path']} (score: {r['score']}){flag}")
        lines.append(f"```\n{r['text'][:500]}{'...' if len(r['text']) > 500 else ''}\n```")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "project decisions"
    results = search(query)
    print(format_results(results, query))
