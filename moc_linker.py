#!/usr/bin/env python3
"""
MOC Linker — classify every vault note into the appropriate Map-of-Content (MOC)
and link them, using a LOCAL OpenAI-compatible model (llama-swap or LM Studio).

What it does
------------
1. Scans the Obsidian vault for `.md` notes (skipping the brain index, trash,
   the MOC files themselves, and the noisy livesync logs).
2. Asks a local model to classify each note into one of the existing MOCs
   (discovered from the `MOCs/` folder, excluding the top-level "Home MOC")
   and to produce a short one-line description.
3. Writes each MOC file as a curated index of `- [[note]] — description` links,
   inside a managed block so re-runs are idempotent and any hand-written content
   outside the block is preserved.
4. Optionally writes an upward `moc:` link into each note's frontmatter
   (--tag-notes, off by default).

Every MOC file is backed up before being written.

Local model
-----------
Defaults to llama-swap at http://localhost:4004/v1 (no API key needed). The
target models here are reasoning models: they emit chain-of-thought into a
separate `reasoning_content` field and the final answer into `content`, so we
give generous max_tokens and parse `content`.

Usage
-----
    # See the plan without touching anything:
    python moc_linker.py --dry-run

    # Apply: (re)write the MOC index files:
    python moc_linker.py --apply

    # Also stamp each note's frontmatter with its MOC:
    python moc_linker.py --apply --tag-notes

    # Point at LM Studio instead of llama-swap:
    python moc_linker.py --apply \
        --endpoint http://192.168.0.29:1234/v1 --model gpt-oss-20b
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    """POST JSON and return parsed JSON, using only the stdlib."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ----------------------------------------------------------------------------
# Defaults (override via CLI flags)
# ----------------------------------------------------------------------------
DEFAULT_VAULT = "/server/obsidian"
DEFAULT_ENDPOINT = "http://localhost:4004/v1"      # llama-swap (classification chat model)
DEFAULT_MODEL = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"  # fast A3B MoE on llama-swap
# Embedding endpoint for semantic cross-linking (LM Studio):
DEFAULT_EMBED_ENDPOINT = "http://192.168.0.29:1234/v1"
DEFAULT_EMBED_MODEL = "text-embedding-nomic-embed-text-v2-moe"
EMBED_INPUT_CHARS = 1600   # embedding model context is ~512 tokens
DEFAULT_TOP_RELATED = 5
MOC_SUBDIR = "MOCs"
# MOCs that act as meta-indexes, not classification targets:
META_MOCS = {"Home MOC"}
# Files/dirs never classified:
SKIP_DIR_PARTS = {"_brain", ".trash", ".obsidian", MOC_SUBDIR}
SKIP_NAME_PATTERNS = [re.compile(r"^livesync_log_.*\.md$", re.I)]

MANAGED_BEGIN = "<!-- moc-linker:begin (auto-generated; edit outside this block) -->"
MANAGED_END = "<!-- moc-linker:end -->"
RELATED_BEGIN = "<!-- moc-linker:related:begin (auto-generated; edit outside this block) -->"
RELATED_END = "<!-- moc-linker:related:end -->"

BODY_CHARS_FOR_PROMPT = 1800  # how much note body to send the model


# ----------------------------------------------------------------------------
# Vault scanning
# ----------------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Frontmatter parsed loosely (no yaml dep)."""
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_fm, body = parts[1], parts[2].lstrip("\n")
            for line in raw_fm.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
    return fm, body


def should_skip(md: Path, vault: Path) -> bool:
    if any(part in SKIP_DIR_PARTS for part in md.relative_to(vault).parts):
        return True
    return any(p.match(md.name) for p in SKIP_NAME_PATTERNS)


def scan_notes(vault: Path, include_dailies: bool) -> list[dict[str, Any]]:
    notes = []
    for md in sorted(vault.rglob("*.md")):
        if should_skip(md, vault):
            continue
        rel = md.relative_to(vault)
        if not include_dailies and rel.parts and rel.parts[0] == "Daily Notes":
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  ! skip {rel}: {e}", file=sys.stderr)
            continue
        fm, body = split_frontmatter(text)
        notes.append({
            "rel": str(rel),
            "abs": md,
            "title": md.stem,
            "fm": fm,
            "body": body,
        })
    return notes


def discover_mocs(vault: Path) -> list[str]:
    """MOC names (without .md) from MOCs/, excluding meta indexes."""
    moc_dir = vault / MOC_SUBDIR
    if not moc_dir.is_dir():
        return []
    names = [p.stem for p in sorted(moc_dir.glob("*.md"))]
    return [n for n in names if n not in META_MOCS]


# ----------------------------------------------------------------------------
# Local model client
# ----------------------------------------------------------------------------
def _iter_json_objects(text: str):
    """Yield every balanced top-level {...} object in text, tracking JSON string
    state so braces INSIDE quoted values (even unbalanced ones) don't throw off
    the depth count. The old non-nested-brace regex could not match any object
    containing a brace at all — a Templater brace pair, a dict/LaTeX snippet, or a
    stray closing brace in a quoted value silently dropped the note to Unsorted
    forever (audit finding M-I)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            yield obj
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)


def extract_json(content: str) -> Optional[dict]:
    """Pull the answer JSON object out of a model response. Prefers the LAST
    balanced object carrying a "moc" key (reasoning models often echo the template
    before the real answer)."""
    if not content:
        return None
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    # Strip any stray <think> blocks that leaked into content.
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    objects = list(_iter_json_objects(content))
    if not objects:
        return None
    for obj in reversed(objects):
        if obj.get("moc"):
            return obj
    return objects[-1]


def classify_note(
    note: dict, mocs: list[str], endpoint: str, model: str,
    timeout: int, retries: int,
) -> dict:
    """Return {'moc': <name or 'Unsorted'>, 'desc': str}."""
    fm = note["fm"]
    hints = []
    for k in ("type", "topics", "tags"):
        if fm.get(k):
            hints.append(f"{k}: {fm[k]}")
    hint_str = ("\nFrontmatter hints: " + "; ".join(hints)) if hints else ""
    body = note["body"][:BODY_CHARS_FOR_PROMPT]
    choices = mocs + ["Unsorted"]

    system = (
        "You are a precise note classifier for an Obsidian knowledge vault. "
        "Choose the single best Map-of-Content (MOC) for the note. "
        "Respond with ONLY a compact JSON object, no prose."
    )
    user = (
        f"Available MOCs: {choices}\n"
        f'Pick exactly one. Use "Unsorted" only if it clearly fits none.\n\n'
        f"Note title: {note['title']}{hint_str}\n"
        f"Note body (truncated):\n{body}\n\n"
        f'Return JSON: {{"moc": "<one of {choices}>", '
        f'"desc": "<= 12 word summary of the note"}}'
        f" /no_think"  # suppress chain-of-thought: reliable + fast direct JSON
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 1200,  # headroom for reasoning models
    }

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = _post_json(f"{endpoint}/chat/completions", payload, timeout)
            msg = resp["choices"][0]["message"]
            content = msg.get("content", "") or ""
            parsed = extract_json(content)
            if not parsed:
                # Reasoning models sometimes emit the JSON only inside their
                # chain-of-thought; fall back to the tail of reasoning_content.
                parsed = extract_json(msg.get("reasoning_content", "") or "")
            if parsed and parsed.get("moc"):
                moc = parsed["moc"].strip()
                if moc not in choices:
                    # Snap to closest known MOC name, else Unsorted.
                    moc = next((c for c in choices if c.lower() == moc.lower()), "Unsorted")
                return {"moc": moc, "desc": (parsed.get("desc") or "").strip()}
            last_err = f"unparseable content: {content[:120]!r}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.5 * attempt)
    print(f"  ! classify failed for {note['rel']}: {last_err}", file=sys.stderr)
    return {"moc": "Unsorted", "desc": ""}


# ----------------------------------------------------------------------------
# Writing MOC files
# ----------------------------------------------------------------------------
# The managed blocks carry a minute-resolution "*Updated <ts> by moc_linker*"
# stamp that changes every run. Comparing note content with the stamp lines
# stripped lets an idempotent night detect "nothing but the timestamp would
# change" and skip the write, so unchanged notes don't have their mtime bumped
# every night — which otherwise defeats the ledger's recency filter and forces a
# full whole-vault re-embed (audit finding H-B).
_STAMP_RE = re.compile(r"^\*Updated .* by moc_linker.*\*\s*$", re.M)


def _without_stamps(text: str) -> str:
    return _STAMP_RE.sub("", text)


def render_managed_block(entries: list[dict]) -> str:
    lines = [MANAGED_BEGIN, f"*Updated {datetime.now():%Y-%m-%d %H:%M} by moc_linker.*", ""]
    for e in sorted(entries, key=lambda x: x["title"].lower()):
        link = f"[[{e['title']}]]"
        lines.append(f"- {link}" + (f" — {e['desc']}" if e["desc"] else ""))
    lines.append("")
    lines.append(MANAGED_END)
    return "\n".join(lines)


def upsert_managed_block(existing: str, block: str, title: str) -> str:
    """Replace the managed block in `existing`, or append it under a heading."""
    pattern = re.compile(
        re.escape(MANAGED_BEGIN) + r".*?" + re.escape(MANAGED_END), flags=re.S
    )
    if len(pattern.findall(existing)) > 1:
        # More than one managed pair (a LiveSync conflict merge, or a note that
        # quotes the markers) — replacing all would destroy the extra content.
        # Refuse and leave it for a human (audit finding low-7).
        print("[moc_linker] multiple managed blocks found; skipping to avoid data loss",
              file=sys.stderr)
        return existing
    if pattern.search(existing):
        # `block` carries model-generated desc/titles; pass it as a function
        # replacement so backslashes / \1 group-refs are inserted verbatim
        # rather than interpreted as a re.sub template (audit finding H-A). count=1
        # so only the single managed pair is touched.
        return pattern.sub(lambda _m: block, existing, count=1).rstrip() + "\n"
    header = existing.strip()
    if not header:
        header = f"# {title}"
    return f"{header}\n\n{block}\n"


def backup_root(vault: Path) -> Path:
    """Backups live OUTSIDE the vault so Obsidian never indexes them (they were
    cluttering graph view). Override with MOC_BACKUP_DIR (e.g. a mounted host path
    in the container, so backups are durable); else a sibling of the vault."""
    env = os.environ.get("MOC_BACKUP_DIR")
    if env:
        return Path(env)
    return vault.parent / f".{vault.name}-moc-backups"


def backup_file(path: Path, backup_dir: Path) -> Optional[Path]:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{path.stem}.{stamp}.bak.md"
    dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def write_mocs(vault: Path, by_moc: dict[str, list[dict]], apply: bool,
               all_mocs: Optional[list[str]] = None) -> None:
    moc_dir = vault / MOC_SUBDIR
    backup_dir = backup_root(vault) / "mocs"
    # Regenerate EVERY known MOC each run (empty block for ones that got zero notes
    # this run), so a note reclassified elsewhere is removed from its former MOC
    # instead of being left dangling in two MOCs (audit finding M-J). Otherwise
    # write_mocs only rewrites MOCs that received >=1 note this run.
    targets = dict(by_moc)
    for m in (all_mocs or []):
        targets.setdefault(m, [])
    for moc_name, entries in sorted(targets.items()):
        if moc_name == "Unsorted":
            continue  # surfaced in the report, not written to a MOC
        path = moc_dir / f"{moc_name}.md"
        block = render_managed_block(entries)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        new_content = upsert_managed_block(existing, block, moc_name)
        print(f"\n=== {moc_name}.md ({len(entries)} notes) ===")
        if apply:
            if _without_stamps(new_content) == _without_stamps(existing):
                print(f"  unchanged, skipped {path.relative_to(vault)}")  # H-B: no mtime churn
                continue
            b = backup_file(path, backup_dir)
            if b:
                print(f"  backed up -> {b}")
            path.write_text(new_content, encoding="utf-8")
            print(f"  wrote {path.relative_to(vault)}")
        else:
            preview = "\n".join(new_content.splitlines()[:12])
            print(preview + ("\n  ..." if len(new_content.splitlines()) > 12 else ""))


_FM_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)


def _looks_like_frontmatter(head: str) -> bool:
    """True only if the block between leading '---' fences is really YAML
    frontmatter (its first non-empty line is a key), not body content sitting
    between two '---' thematic breaks (audit finding M-H)."""
    for line in head.splitlines():
        if not line.strip():
            continue
        return bool(re.match(r"^[ \t]*[A-Za-z0-9_.\-]+[ \t]*:", line))
    return False


def _set_frontmatter_moc(text: str, link: str) -> str:
    """Set `moc: <link>` in a note's frontmatter without corrupting it:

    - Only treats a leading '---' block as frontmatter when it really is one, so a
      note that merely OPENS with a '---' divider doesn't get its body hoisted in.
    - Replaces an existing `moc:` key AND any indented/list continuation lines
      under it, so a block/list-form `moc:` value isn't left as an orphaned,
      invalid-YAML list item beneath the new scalar (audit finding M-H).
    """
    m = _FM_RE.match(text)
    if not m or not _looks_like_frontmatter(m.group(1)):
        return f"---\nmoc: {link}\n---\n\n{text}"
    head, rest = m.group(1), text[m.end():]
    out, dropping = [], False
    for line in head.splitlines():
        if re.match(r"^moc[ \t]*:", line):
            dropping = True  # drop the moc key and its block/list continuation
            continue
        if dropping:
            if re.match(r"^([ \t]+\S|[ \t]*-[ \t])", line):
                continue
            dropping = False
        out.append(line)
    out.append(f"moc: {link}")
    return "---\n" + "\n".join(out) + "\n---\n" + rest


def tag_notes(vault: Path, results: list[dict], apply: bool) -> None:
    """Optionally add `moc: "[[<MOC>]]"` to each note's frontmatter."""
    backup_dir = backup_root(vault) / "notes"
    for r in results:
        if r["moc"] == "Unsorted":
            continue
        path: Path = r["abs"]
        text = path.read_text(encoding="utf-8")
        link = f'"[[{r["moc"]}]]"'
        new_text = _set_frontmatter_moc(text, link)
        if apply:
            if new_text == text:
                continue  # moc: already correct; skip to avoid mtime churn (H-B)
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{path.stem}.bak.md").write_text(text, encoding="utf-8")
            path.write_text(new_text, encoding="utf-8")
    if apply:
        print(f"\nTagged {sum(1 for r in results if r['moc'] != 'Unsorted')} notes with moc: frontmatter.")


# ----------------------------------------------------------------------------
# Semantic cross-linking ("## Related Notes")
# ----------------------------------------------------------------------------
def embed_text(text: str, endpoint: str, model: str, timeout: int, retries: int) -> Optional[list[float]]:
    """Return an embedding vector for `text`, or None on failure."""
    payload = {"model": model, "input": text[:EMBED_INPUT_CHARS]}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = _post_json(f"{endpoint}/embeddings", payload, timeout)
            return resp["data"][0]["embedding"]
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0 * attempt)
    print(f"  ! embed failed: {last_err}", file=sys.stderr)
    return None


def _normalize(v: list[float]) -> list[float]:
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v] if norm else v


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def render_related_block(neighbors: list[dict]) -> str:
    lines = [RELATED_BEGIN, "## Related Notes",
             f"*Updated {datetime.now():%Y-%m-%d %H:%M} by moc_linker (semantic).*", ""]
    for n in neighbors:
        lines.append(f"- [[{n['title']}]]" + (f" — {n['desc']}" if n.get("desc") else ""))
    lines.append("")
    lines.append(RELATED_END)
    return "\n".join(lines)


def upsert_related_block(text: str, block: str) -> str:
    """Replace the managed Related block, or append it at the end of the note."""
    pattern = re.compile(re.escape(RELATED_BEGIN) + r".*?" + re.escape(RELATED_END), flags=re.S)
    if len(pattern.findall(text)) > 1:
        # Multiple related pairs (LiveSync conflict merge or a note quoting the
        # markers): refuse rather than replace-all and destroy content (low-7).
        print("[moc_linker] multiple related blocks found; skipping to avoid data loss",
              file=sys.stderr)
        return text
    if pattern.search(text):
        # Function replacement: model-generated block inserted verbatim, never
        # interpreted as a re.sub replacement template (audit finding H-A). count=1
        # touches only the single managed pair.
        return pattern.sub(lambda _m: block, text, count=1).rstrip() + "\n"
    return text.rstrip() + "\n\n" + block + "\n"


def cross_link(notes: list[dict], endpoint: str, model: str, top_k: int,
               timeout: int, retries: int, apply: bool,
               desc_by_title: Optional[dict] = None) -> None:
    """Embed every note, then write a top-K semantic '## Related Notes' section into each."""
    desc_by_title = desc_by_title or {}
    print(f"\n=== Semantic cross-linking via {model} @ {endpoint} ===")
    vecs: list[Optional[list[float]]] = []
    for i, note in enumerate(notes, 1):
        v = embed_text(f"{note['title']}\n\n{note['body']}", endpoint, model, timeout, retries)
        vecs.append(_normalize(v) if v else None)
        print(f"  embed [{i:>3}/{len(notes)}] {note['rel']}{'' if v else '  (FAILED)'}")

    written = 0
    for i, note in enumerate(notes):
        if vecs[i] is None:
            continue
        sims = []
        for j, other in enumerate(notes):
            if j == i or vecs[j] is None:
                continue
            sims.append((_dot(vecs[i], vecs[j]), other["title"]))
        sims.sort(reverse=True)
        neighbors = [{"title": t, "desc": desc_by_title.get(t, "")} for _, t in sims[:top_k]]
        if not neighbors:
            continue
        block = render_related_block(neighbors)
        path: Path = note["abs"]
        original = path.read_text(encoding="utf-8")
        new_text = upsert_related_block(original, block)
        if apply:
            if _without_stamps(new_text) == _without_stamps(original):
                continue  # same neighbors; only the stamp differs — skip (H-B)
            # backups live OUTSIDE the vault so Obsidian never indexes them
            bdir = _related_backup_dir(note)
            bdir.mkdir(parents=True, exist_ok=True)
            (bdir / f"{path.stem}.bak.md").write_text(original, encoding="utf-8")
            path.write_text(new_text, encoding="utf-8")
            written += 1
        else:
            top = ", ".join(n["title"] for n in neighbors)
            print(f"  [{i+1:>3}] {note['title']}  ->  {top}")
    if apply:
        print(f"Wrote '## Related Notes' into {written} notes.")


def _related_backup_dir(note: dict) -> Path:
    """related/ under the external backup root (outside the vault)."""
    rel_parts = note["rel"].split("/")
    vault_root = note["abs"]
    for _ in rel_parts:
        vault_root = vault_root.parent
    return backup_root(vault_root) / "related"


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="OpenAI-compatible base URL")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--exclude-dailies", action="store_true", help="skip the Daily Notes/ folder (included by default)")
    ap.add_argument("--tag-notes", action="store_true", help="also write moc: into each note's frontmatter")
    ap.add_argument("--related", action="store_true",
                    help="write a semantic '## Related Notes' section (top-K) into each note")
    ap.add_argument("--embed-endpoint", default=DEFAULT_EMBED_ENDPOINT, help="embeddings base URL (LM Studio)")
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--top-related", type=int, default=DEFAULT_TOP_RELATED)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: show plan, write nothing")
    mode.add_argument("--apply", action="store_true", help="write files")
    args = ap.parse_args()

    apply = args.apply  # default (no flag) == dry-run
    vault = Path(args.vault)
    if not vault.is_dir():
        print(f"Vault not found: {vault}", file=sys.stderr)
        return 2

    notes = scan_notes(vault, include_dailies=not args.exclude_dailies)
    # Classification runs unless ONLY --related was requested (it's the slow LLM pass).
    do_classify = (not args.related) or args.tag_notes
    print(f"Vault: {vault}")
    print(f"Notes: {len(notes)}")
    print(f"Passes: {'classify ' if do_classify else ''}{'tag-notes ' if args.tag_notes else ''}{'related' if args.related else ''}".strip())
    print(f"Mode: {'APPLY (writing files)' if apply else 'DRY-RUN (no writes)'}\n")

    results: list[dict] = []
    by_moc: dict[str, list[dict]] = {}
    if do_classify:
        mocs = discover_mocs(vault)
        if not mocs:
            print(f"No target MOCs found in {vault / MOC_SUBDIR} (excluding {META_MOCS}).", file=sys.stderr)
            return 2
        print(f"Target MOCs: {mocs}  |  classifier: {args.model} @ {args.endpoint}")
        for i, note in enumerate(notes, 1):
            res = classify_note(note, mocs, args.endpoint, args.model, args.timeout, args.retries)
            entry = {"title": note["title"], "rel": note["rel"], "abs": note["abs"],
                     "moc": res["moc"], "desc": res["desc"]}
            results.append(entry)
            by_moc.setdefault(res["moc"], []).append(entry)
            print(f"[{i:>3}/{len(notes)}] {res['moc']:<14} {note['rel']}")

        # Retry transient flakes: the local model occasionally returns an empty
        # response, dropping a note to "Unsorted". A fresh attempt almost always
        # classifies it, so retry each Unsorted note once before writing.
        for entry in list(by_moc.get("Unsorted", [])):
            note = next(n for n in notes if n["rel"] == entry["rel"])
            res = classify_note(note, mocs, args.endpoint, args.model, args.timeout, args.retries)
            if res["moc"] != "Unsorted":
                by_moc["Unsorted"].remove(entry)
                entry["moc"], entry["desc"] = res["moc"], res["desc"]
                by_moc.setdefault(res["moc"], []).append(entry)
                print(f"  retry: {entry['rel']} -> {res['moc']}")
        if not by_moc.get("Unsorted"):
            by_moc.pop("Unsorted", None)

        write_mocs(vault, by_moc, apply, all_mocs=mocs)
        if args.tag_notes:
            tag_notes(vault, results, apply)

    if args.related:
        desc_by_title = {e["title"]: e["desc"] for e in results}
        cross_link(notes, args.embed_endpoint, args.embed_model, args.top_related,
                   args.timeout, args.retries, apply, desc_by_title)

    # Summary
    if do_classify:
        print("\n========== SUMMARY ==========")
        for moc_name in sorted(by_moc):
            print(f"  {moc_name:<14} {len(by_moc[moc_name])}")
        unsorted = by_moc.get("Unsorted", [])
        if unsorted:
            print("\nUnsorted (review manually):")
            for e in unsorted:
                print(f"  - {e['rel']}")
    if not apply:
        print("\nDry-run only. Re-run with --apply to write files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
