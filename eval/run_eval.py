#!/usr/bin/env python3
"""
Retrieval eval harness: run every golden query through searcher.search and score
recall@k + MRR against the expected note. Converts "this model/chunking/prefix
feels better" into numbers on YOUR vault.

The embedding model/prefixes come from the environment (EMBEDDING_MODEL, and
optionally EMBED_DOC_PREFIX/EMBED_QUERY_PREFIX) — config.py resolves them at
import. With --brain-dir the index is built in an ISOLATED directory (the vault
is only read), so A/B runs never touch the production _brain/ index.

A/B example (two isolated indexes over the same vault):
    export OBSIDIAN_VAULT_PATH=/path/to/vault
    EMBEDDING_MODEL=text-embedding-nomic-embed-text-v2-moe \
        python eval/run_eval.py --rebuild --brain-dir /tmp/eval-nomic --label nomic-v2
    EMBEDDING_MODEL=text-embedding-qwen3-embedding-8b \
        python eval/run_eval.py --rebuild --brain-dir /tmp/eval-qwen8b --label qwen3-8b
    python eval/run_eval.py --compare   # print history side by side
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HISTORY = Path(__file__).parent / "results" / "history.jsonl"


def _point_at(brain_dir: str) -> None:
    """Repoint the index paths at an isolated directory (mirrors the test
    fixtures' approach) so eval builds never touch the production index."""
    import config, indexer, searcher
    bd = Path(brain_dir)
    bd.mkdir(parents=True, exist_ok=True)
    index_path = str(bd / "index.faiss")
    meta_path = str(bd / "metadata.json")
    for mod in (config, indexer):
        mod.BRAIN_DIR = str(bd)
        mod.INDEX_PATH = index_path
        mod.METADATA_PATH = meta_path
    searcher.INDEX_PATH = index_path
    searcher.METADATA_PATH = meta_path
    searcher._INDEX_CACHE.clear()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", default=str(Path(__file__).parent / "golden.json"))
    ap.add_argument("--k", type=int, default=5, help="retrieve top-k (scores recall@1/3/k)")
    ap.add_argument("--rebuild", action="store_true", help="(re)build the index first")
    ap.add_argument("--brain-dir", default="", help="isolated index dir (default: production _brain)")
    ap.add_argument("--label", default="", help="tag for this run in the history (e.g. model name)")
    ap.add_argument("--json", dest="json_out", default="", help="also write full results to this path")
    ap.add_argument("--compare", action="store_true", help="print the run history and exit")
    args = ap.parse_args()

    if args.compare:
        if not HISTORY.exists():
            print("No history yet.")
            return 0
        rows = [json.loads(l) for l in HISTORY.read_text().splitlines() if l.strip()]
        print(f"{'when':<17} {'label':<22} {'model':<38} {'n':>3} "
              f"{'r@1':>6} {'r@3':>6} {'r@5':>6} {'mrr':>6}")
        for r in rows:
            print(f"{r['when']:<17} {r['label']:<22} {r['model']:<38} {r['n']:>3} "
                  f"{r['recall@1']:>6.3f} {r['recall@3']:>6.3f} {r['recall@k']:>6.3f} {r['mrr']:>6.3f}")
        return 0

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    if not golden:
        print("Golden set is empty — run eval/generate_golden.py first.", file=sys.stderr)
        return 2

    if args.brain_dir:
        _point_at(args.brain_dir)
    import config
    from indexer import build_index
    from searcher import search

    model = os.environ.get("EMBEDDING_MODEL", config.EMBEDDING_MODEL)
    print(f"model={config.EMBEDDING_MODEL}  doc_prefix={config.EMBED_DOC_PREFIX!r}  "
          f"query_prefix={config.EMBED_QUERY_PREFIX[:40]!r}...")
    if args.rebuild:
        print("building index (isolated)" if args.brain_dir else "building index (PRODUCTION)")
        r = build_index(force=False)
        print(f"  -> {r.get('status')} ({r.get('chunks', '?')} chunks)")

    hits1 = hits3 = hitsk = 0
    rr_total = 0.0
    details = []
    for g in golden:
        results = search(g["query"], top_k=args.k)
        paths = [r["note_path"] for r in results]
        try:
            rank = paths.index(g["expected"]) + 1
        except ValueError:
            rank = 0
        hits1 += rank == 1
        hits3 += 1 <= rank <= 3
        hitsk += rank >= 1
        rr_total += (1.0 / rank) if rank else 0.0
        mark = f"rank {rank}" if rank else "MISS"
        details.append({"query": g["query"], "expected": g["expected"], "rank": rank,
                        "top": paths[:3]})
        print(f"  [{mark:>6}] {g['query'][:70]}")
        if not rank:
            print(f"           expected {g['expected']} | got {paths[:3]}")

    n = len(golden)
    summary = {
        "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "label": args.label or "(unlabeled)",
        "model": config.EMBEDDING_MODEL,
        "n": n,
        "recall@1": round(hits1 / n, 4),
        "recall@3": round(hits3 / n, 4),
        "recall@k": round(hitsk / n, 4),
        "k": args.k,
        "mrr": round(rr_total / n, 4),
    }
    print(f"\nn={n}  recall@1={summary['recall@1']:.3f}  recall@3={summary['recall@3']:.3f}  "
          f"recall@{args.k}={summary['recall@k']:.3f}  MRR={summary['mrr']:.3f}")

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"summary": summary, "details": details}, indent=2), encoding="utf-8")
    print(f"appended -> {HISTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
