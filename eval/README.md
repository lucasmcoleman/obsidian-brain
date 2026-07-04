# Retrieval eval harness

Converts "this embedding model / chunk size / prefix *feels* better" into
recall@k + MRR numbers on **your actual vault**. Use it before committing to any
retrieval-affecting change (embedding model swap, chunking, prefixes, hybrid
search, reranker).

## Files

- `generate_golden.py` — samples real notes and asks the local chat model to
  produce a natural "months later" search query per note. Writes candidate pairs
  to `golden.json` for **human review** (delete/edit bad ones — the numbers are
  only as good as the golden set). `golden.json` is gitignored: it contains
  snippets of personal note content.
- `run_eval.py` — runs every golden query through `searcher.search`, scores
  recall@1/3/k + MRR against the expected note, prints per-query ranks, and
  appends a summary row to `results/history.jsonl` (also gitignored).

## Quick start

```bash
export OBSIDIAN_VAULT_PATH=/server/obsidian

# 1. Generate candidates (then EDIT golden.json — review is not optional)
python eval/generate_golden.py --n 30 \
    --endpoint http://192.168.0.29:4004/v1 --model unsloth/Qwen3.6-35B-A3B-MTP-GGUF

# 2. Score the current production index
python eval/run_eval.py --label baseline

# 3. A/B two embedding models in ISOLATED index dirs (vault is only read;
#    the production _brain/ index is never touched)
EMBEDDING_MODEL=text-embedding-nomic-embed-text-v2-moe \
    python eval/run_eval.py --rebuild --brain-dir /tmp/eval-nomic --label nomic-v2
EMBEDDING_MODEL=text-embedding-qwen3-embedding-8b \
    python eval/run_eval.py --rebuild --brain-dir /tmp/eval-qwen8b --label qwen3-8b

# 4. Compare all runs
python eval/run_eval.py --compare
```

The embedding model + instruction prefixes come from the environment
(`config.py` resolves per-model defaults at import), so each A/B run is just an
env var. `--rebuild --brain-dir` builds a scratch index; expect a full re-embed
per model (the content-hash cache keys include the model).

## Interpreting

- **recall@k** — fraction of queries whose expected note appears in the top k.
  The headline number for "will the agent see the right note".
- **MRR** — mean reciprocal rank; rewards putting the right note *first*.
- Small golden sets (20–40) are noisy: treat differences under ~5 points as a
  tie, and grow the set over time (add real queries that failed you in practice —
  those are worth more than generated ones).
