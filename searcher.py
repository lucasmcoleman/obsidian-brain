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

from config import VAULT_PATH, BRAIN_DIR, INDEX_PATH, METADATA_PATH, TOP_K
from embedder import embed_query
from indexer import INDEX_LOCK


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Search the index for notes relevant to the query.
    Returns a list of result dicts with text, note_path, and score (descending),
    one chunk per note. Ranking is FAISS L2 order; the score is a monotonic
    1/(1+distance) transform (not cosine, not comparable across queries).
    Returns [] if the index is absent, unreadable, or inconsistent with its
    metadata (so a crash-window mismatch never returns wrong text — M1/L7/L8).
    """
    # Read the index + metadata together under the lock so we never load a
    # new index against stale metadata (or a half-written file) mid-rebuild.
    with INDEX_LOCK:
        if not os.path.exists(INDEX_PATH) or not os.path.exists(METADATA_PATH):
            return []
        try:
            index = faiss.read_index(INDEX_PATH)
            metadata = json.loads(Path(METADATA_PATH).read_text())
        except (json.JSONDecodeError, OSError, RuntimeError) as e:
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

    query_embedding = embed_query(query)
    query_vec = np.array([query_embedding]).astype("float32")

    # Search FAISS (retrieve extra so dedup-by-note can still fill top_k)
    k = min(top_k * 2, index.ntotal)
    distances, indices = index.search(query_vec, k)

    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx == -1:
            continue
        chunk = chunks[idx]
        # L2 distance to a monotonic similarity score
        score = 1 / (1 + dist)
        results.append({
            "text": chunk["text"],
            "note_path": chunk["note_path"],
            "abs_path": chunk["abs_path"],
            "score": round(score, 4),
        })

    # Results are already in FAISS L2 order; deduplicate to one chunk per note.
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
        lines.append(f"### {i}. {r['note_path']} (score: {r['score']})")
        lines.append(f"```\n{r['text'][:500]}{'...' if len(r['text']) > 500 else ''}\n```")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "project decisions"
    results = search(query)
    print(format_results(results, query))
