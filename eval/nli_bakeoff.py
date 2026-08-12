#!/usr/bin/env python3
"""
Truth/NLI model bake-off: score candidate chat models on the contradiction-
detection task the nightly truth_maintenance pass depends on.

For this task PRECISION matters most: a false positive floods the human review
queue and trains the owner to ignore it (the "boy who cried wolf" failure the
design is biased against). Recall is secondary — a missed contradiction is just
today's status quo. So candidates are ranked by precision, then recall.

Reuses truth_maintenance.judge_contradiction verbatim, so the bake-off measures
exactly what production would do. The labeled set is synthetic (no vault data) —
edit/extend it; the numbers are only as good as the examples.

Usage:
    python eval/nli_bakeoff.py                 # all default candidates
    python eval/nli_bakeoff.py --only gpt-oss-120b qwen3.6-27b
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from truth_maintenance import judge_contradiction

LS = "http://localhost:4004/v1"     # llama-swap
LM = "http://localhost:1234/v1"     # LM Studio

# (label, endpoint, model-id-on-that-endpoint)
# Runtime notes (single GPU, memory-bound):
# - Run ONE model at a time (concurrent loads thrash swap).
# - gpt-oss-120b IS fast when it runs, but the RUNTIME matters — on LM Studio,
#   the ROCm runtime HANGS this 120B MoE (prompt-processing stuck at ~0% GPU);
#   the VULKAN runtime runs it well (~17 tok/s warm, ~6.7 s/NLI-case) but scored
#   precision 0.83 / recall 1.00 — one false positive on the plan->completion
#   trap ("planning to migrate" vs "migration complete", conf 0.95). So it's
#   fast and correct on real contradictions but trips the exact trap the design
#   guards against.
# - qwen3.6-27b wins: precision 1.00 / recall 1.00, no FP. Precision is what
#   matters for this task, so 27b stays the truth/ledger model; gpt-oss can only tie
#   at best and here it loses a precision point. Kept below for reproducibility.
CANDIDATES = [
    ("qwen3.6-27b",     LS, "unsloth/Qwen3.6-27B-MTP-GGUF"),      # dense 27B — NLI winner + ledger/truth
    ("qwen3.6-35b-a3b", LS, "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"),  # prior default (3B active)
    ("step-3.7-flash",  LS, "unsloth/Step-3.7-Flash-GGUF"),       # flash — DNF (no usable JSON)
]
# Reachable via --only. gpt-oss-120b needs the Vulkan runtime selected first (see notes).
_TOO_BIG = [
    ("gpt-oss-120b",    LM, "openai/gpt-oss-120b"),               # Vulkan: 0.83 prec (1 FP); ROCm: hangs
    ("minimax-m2.7",    LS, "mradermacher/m51Lab-MiniMax-M2.7-REAP-139B-A10B-i1-GGUF"),  # 139B
]

# Positive class = "contradicts". Includes the classic false-positive traps the
# design must resist: plan→completion, actual-vs-target, restatement, off-topic.
CASES = [
    # --- real contradictions (same subject, incompatible at the same time) ---
    ("The Delta project deadline is June 30.",
     "In today's sync we moved the Delta deadline to July 15.", True),
    ("We decided to build the new API in Python.",
     "The team agreed to rewrite the new API in Go instead.", True),
    ("The board meeting is scheduled for Tuesday.",
     "The board meeting has been moved to Thursday.", True),
    ("Pat will lead the product rollout.",
     "Jordan is now leading the product rollout instead of Pat.", True),
    ("The hardware budget was approved at $50k.",
     "The hardware budget was cut to $30k.", True),
    # --- NOT contradictions (must NOT be flagged) ---
    ("The Delta project deadline is July 15.",
     "The Q3 marketing plan is due August 1.", False),          # different subjects
    ("We're planning to migrate the database to Postgres.",
     "The Postgres migration is now complete.", False),          # plan -> completion
    ("Revenue last quarter was $2M.",
     "Our revenue target for next year is $3M.", False),         # actual vs target
    ("The launch deadline is June 30.",
     "The June 30 launch deadline is confirmed.", False),        # restatement
    ("Alice is on the frontend team.",
     "Bob is on the backend team.", False),                      # unrelated facts
]


def score(label, endpoint, model, timeout, retries):
    tp = fp = fn = tn = 0
    errors = 0
    rows = []
    for a, b, is_contra in CASES:
        t0 = time.time()
        v = judge_contradiction(a, "noteA", b, "noteB", endpoint, model, timeout, retries)
        dt = time.time() - t0
        if v is None:
            errors += 1
            pred = None
        else:
            pred = v.get("verdict") == "contradicts"
        if pred is None:
            mark = "ERR "
        elif pred and is_contra:
            tp += 1; mark = "TP  "
        elif pred and not is_contra:
            fp += 1; mark = "FP !"
        elif not pred and is_contra:
            fn += 1; mark = "FN !"
        else:
            tn += 1; mark = "TN  "
        rows.append((mark, dt, a[:32], b[:32], (v or {}).get("confidence", "")))
    n = len(CASES)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    acc = (tp + tn) / n
    avg_dt = sum(r[1] for r in rows) / n
    return {"label": label, "model": model, "precision": prec, "recall": rec,
            "accuracy": acc, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "errors": errors, "avg_s": avg_dt, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--only", nargs="*", default=None, help="subset of candidate labels")
    ap.add_argument("--verbose", action="store_true", help="print per-case results")
    args = ap.parse_args()

    # Default run uses only the fitting CANDIDATES; the oversized ones in _TOO_BIG
    # are reachable ONLY via explicit --only (so you free memory first).
    pool = CANDIDATES + (_TOO_BIG if args.only else [])
    cands = [c for c in pool if not args.only or c[0] in args.only]
    results = []
    for label, endpoint, model in cands:
        print(f"\n### {label}  ({model} @ {endpoint})", flush=True)
        r = score(label, endpoint, model, args.timeout, args.retries)
        results.append(r)
        print(f"  precision={r['precision']:.2f} recall={r['recall']:.2f} "
              f"acc={r['accuracy']:.2f}  TP={r['tp']} FP={r['fp']} FN={r['fn']} "
              f"TN={r['tn']} err={r['errors']}  avg={r['avg_s']:.1f}s/case", flush=True)
        if args.verbose:
            for mark, dt, a, b, conf in r["rows"]:
                print(f"    {mark} {dt:5.1f}s conf={conf!s:>4}  {a!r} vs {b!r}")

    print("\n================ RANKING (precision desc, then recall) ================")
    results.sort(key=lambda r: (r["precision"], r["recall"]), reverse=True)
    print(f"{'model':<18} {'prec':>5} {'rec':>5} {'acc':>5} {'FP':>3} {'FN':>3} {'err':>4} {'s/case':>7}")
    for r in results:
        print(f"{r['label']:<18} {r['precision']:>5.2f} {r['recall']:>5.2f} "
              f"{r['accuracy']:>5.2f} {r['fp']:>3} {r['fn']:>3} {r['errors']:>4} {r['avg_s']:>6.1f}s")
    best = results[0]
    print(f"\nRecommended TRUTH_CHAT_MODEL: {best['label']} "
          f"(precision {best['precision']:.2f}, recall {best['recall']:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
