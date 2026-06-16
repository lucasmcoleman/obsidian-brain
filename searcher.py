"""
Semantic search over the Obsidian Brain index.
"""
import json
import math
import os
from pathlib import Path
from typing import Optional

import faiss
import numpy as np

from config import VAULT_PATH, BRAIN_DIR, INDEX_PATH, METADATA_PATH, TOP_K
from embedder import embed_query
from indexer import INDEX_LOCK


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b + 1e-8)


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Search the index for notes relevant to the query.
    Returns a list of result dicts with text, note_path, and score.
    """
    # Read the index + metadata together under the lock so we never load a
    # new index against stale metadata (or a half-written file) mid-rebuild.
    with INDEX_LOCK:
        if not os.path.exists(INDEX_PATH) or not os.path.exists(METADATA_PATH):
            return []
        index = faiss.read_index(INDEX_PATH)
        metadata = json.loads(Path(METADATA_PATH).read_text())

    chunks = metadata.get("chunks", [])

    if not chunks:
        return []

    query_embedding = embed_query(query)
    query_vec = np.array([query_embedding]).astype("float32")

    # Search FAISS
    k = min(top_k * 2, index.ntotal)  # retrieve extra, then re-rank
    distances, indices = index.search(query_vec, k)

    # Collect results with re-ranking by cosine similarity
    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx == -1:
            continue
        chunk = chunks[idx]
        # L2 distance to similarity score
        score = 1 / (1 + dist)
        results.append({
            "text": chunk["text"],
            "note_path": chunk["note_path"],
            "abs_path": chunk["abs_path"],
            "score": round(score, 4),
        })

    # Sort by L2 distance (FAISS already does this, but deduplicate by note)
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
