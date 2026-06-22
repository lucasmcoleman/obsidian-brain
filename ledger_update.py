#!/usr/bin/env python3
"""
Nightly updater for the Open Action Items Master Ledger.

Conservative + reversible by design:
- Backs up the ledger OUTSIDE the vault before writing (Obsidian never sees it).
- COMPLETIONS: only flips existing "- [ ]" lines to "- [x] ... ✅ <date>" in place,
  and only when a local LLM finds clear completion evidence in recently-edited
  notes. It never rewrites or deletes the hand-curated body.
- NEW ITEMS: appends newly-surfaced action items under a managed
  <!-- ledger-auto --> block at the end (before the Related section), grouped by
  the date detected. Deduped against everything already in the ledger.

Runs the local chat model via llama-swap (same as moc_linker).

Usage:
    python ledger_update.py --dry-run     # show proposed changes, write nothing
    python ledger_update.py --apply       # apply completions + append new items
    python ledger_update.py --apply --recent-days 2
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import moc_linker as ml  # reuse _post_json, extract_json, split_frontmatter, backup_root

LEDGER_REL = "open-action-items-ledger.md"
AUTO_BEGIN = "<!-- ledger-auto:begin (auto-detected items; review and fold into the lists above) -->"
AUTO_END = "<!-- ledger-auto:end -->"
# Cap how much note text we feed the model so we stay within context.
MAX_NOTE_CHARS = 1200
MAX_TOTAL_CHARS = 16000

OPEN_RE = re.compile(r"^(\s*[-*+]\s+)\[ \](\s+.*\S)\s*$")


def find_json_object(text: str):
    """Extract the first balanced {...} object (nesting-aware). Returns dict or None.

    ml.extract_json only matches non-nested braces (fine for flat classifier output);
    the ledger response nests objects inside arrays, so we brace-count instead.
    """
    import json
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def split_managed(text: str) -> tuple[str, str]:
    """Return (body_without_related, related_block_or_empty)."""
    m = re.search(re.escape(ml.RELATED_BEGIN) + r".*?" + re.escape(ml.RELATED_END), text, re.S)
    if m:
        return text[:m.start()].rstrip() + "\n", text[m.start():]
    return text, ""


def strip_auto_block(text: str) -> str:
    return re.sub(re.escape(AUTO_BEGIN) + r".*?" + re.escape(AUTO_END), "", text, flags=re.S).rstrip() + "\n"


def list_open_items(text: str) -> list[tuple[int, str]]:
    """Return [(line_index, item_text)] for every open '- [ ]' line."""
    items = []
    for i, line in enumerate(text.splitlines()):
        m = OPEN_RE.match(line)
        if m:
            items.append((i, m.group(2).strip()))
    return items


def gather_recent_notes(vault: Path, recent_days: int, now: datetime) -> list[dict]:
    cutoff = (now - timedelta(days=recent_days)).timestamp()
    notes = []
    for n in ml.scan_notes(vault, include_dailies=True):
        if n["rel"] == LEDGER_REL:
            continue
        try:
            mtime = n["abs"].stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            notes.append({"rel": n["rel"], "body": n["body"][:MAX_NOTE_CHARS], "mtime": mtime})
    notes.sort(key=lambda x: x["mtime"], reverse=True)
    return notes


def build_context(notes: list[dict]) -> str:
    out, total = [], 0
    for n in notes:
        chunk = f"### NOTE: {n['rel']}\n{n['body']}\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            break
        out.append(chunk)
        total += len(chunk)
    return "\n".join(out)


def ask_model(open_items: list[tuple[int, str]], context: str, endpoint: str, model: str,
              timeout: int, retries: int) -> dict:
    numbered = "\n".join(f"{k}. {txt}" for k, (_, txt) in enumerate(open_items, 1))
    system = (
        "You maintain a personal action-item ledger. You are given the current OPEN "
        "items and the text of recently-edited notes. Be conservative and precise. "
        "Respond with ONLY a compact JSON object."
    )
    user = (
        f"OPEN ITEMS (numbered):\n{numbered}\n\n"
        f"RECENT NOTES:\n{context}\n\n"
        "Return JSON with two keys:\n"
        '  "completed": array of {"n": <open-item number>, "evidence": "<short quote/why>"} '
        "— ONLY for items the notes clearly show are DONE. Omit if unsure.\n"
        '  "new_items": array of {"text": "<concise action assigned to the note-taker>", '
        '"category": "<short group>", "source": "<note filename>"} '
        "— action items in the notes that are NOT already an open item above. "
        "Do not duplicate existing items. Omit anything already listed.\n"
        "If none, use empty arrays. /no_think"
    )
    payload = {"model": model, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0, "max_tokens": 3000}
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = ml._post_json(f"{endpoint}/chat/completions", payload, timeout)
            msg = resp["choices"][0]["message"]
            parsed = find_json_object(msg.get("content", "") or "") or \
                find_json_object(msg.get("reasoning_content", "") or "")
            if isinstance(parsed, dict) and ("completed" in parsed or "new_items" in parsed):
                parsed.setdefault("completed", [])
                parsed.setdefault("new_items", [])
                return parsed
            last = "unparseable"
        except Exception as e:
            last = str(e)
        time.sleep(1.5 * attempt)
    print(f"  ! model call failed: {last}", file=sys.stderr)
    return {"completed": [], "new_items": []}


def render_auto_block(existing_block: str, new_items: list[dict], today: str) -> str:
    """Append today's new items to the managed auto block, preserving prior dates."""
    prior = ""
    m = re.search(re.escape(AUTO_BEGIN) + r"(.*?)" + re.escape(AUTO_END), existing_block, re.S)
    if m:
        prior = m.group(1)
        # drop the header lines; keep prior dated subsections
        prior = re.sub(r"^## Auto-detected items.*?\n", "", prior, flags=re.S | re.M)
        prior = re.sub(r"^\*Maintained nightly.*?\*\n", "", prior, flags=re.M).strip("\n")
    lines = [AUTO_BEGIN, "## Auto-detected items",
             "*Maintained nightly by ledger_update; review and fold into the lists above.*", ""]
    if prior:
        lines.append(prior)
        lines.append("")
    if new_items:
        lines.append(f"### Added {today}")
        for it in new_items:
            src = it.get("source", "")
            cat = it.get("category", "")
            tag = "  ".join(x for x in [f"_{cat}_" if cat else "", f"(source: {src})" if src else ""] if x)
            lines.append(f"- [ ] {it['text'].strip()}" + (f"  {tag}" if tag else ""))
        lines.append("")
    lines.append(AUTO_END)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", default=ml.DEFAULT_VAULT)
    ap.add_argument("--endpoint", default=ml.DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=ml.DEFAULT_MODEL)
    ap.add_argument("--recent-days", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--retries", type=int, default=3)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply = args.apply

    vault = Path(args.vault)
    ledger = vault / LEDGER_REL
    if not ledger.exists():
        print(f"Ledger not found: {ledger}", file=sys.stderr)
        return 2
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    full = ledger.read_text(encoding="utf-8")
    body, related = split_managed(full)
    body_no_auto = strip_auto_block(body)
    existing_auto = body[len(body_no_auto):] if AUTO_BEGIN in body else ""
    open_items = list_open_items(body_no_auto)
    recent = gather_recent_notes(vault, args.recent_days, now)
    print(f"Ledger: {ledger}")
    print(f"Open items: {len(open_items)} | recent notes (<= {args.recent_days}d): {len(recent)}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}\n")
    if not recent:
        print("No recently-edited notes; nothing to do.")
        return 0

    result = ask_model(open_items, build_context(recent), args.endpoint, args.model,
                       args.timeout, args.retries)

    # --- Completions: surgical line edits on the body ---
    lines = body_no_auto.splitlines()
    completed = []
    for c in result.get("completed", []):
        try:
            n = int(c.get("n"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(open_items):
            idx, txt = open_items[n - 1]
            if OPEN_RE.match(lines[idx]):
                lines[idx] = re.sub(r"\[ \]", "[x]", lines[idx], count=1).rstrip() + f" ✅ {today}"
                completed.append((txt, c.get("evidence", "")))
    body_done = "\n".join(lines).rstrip() + "\n"

    # --- New items: dedupe against existing ledger text, append to managed block ---
    existing_blob = body.lower()
    new_items = []
    for it in result.get("new_items", []):
        t = (it.get("text") or "").strip()
        if not t:
            continue
        key = re.sub(r"\W+", " ", t.lower()).strip()
        if key and key[:40] in existing_blob:
            continue
        new_items.append(it)

    # --- Report ---
    print(f"Completions detected: {len(completed)}")
    for txt, ev in completed:
        print(f"  ✅ {txt[:80]}\n      ↳ {ev[:100]}")
    print(f"\nNew items detected: {len(new_items)}")
    for it in new_items:
        print(f"  + [{it.get('category','?')}] {it['text'][:90]}  (src: {it.get('source','?')})")

    if not completed and not new_items:
        print("\nNothing to change.")
        return 0

    auto_block = render_auto_block(existing_auto, new_items, today)
    new_body = body_done.rstrip() + "\n\n" + auto_block + "\n"
    new_full = new_body + ("\n" + related if related else "")

    if apply:
        bdir = ml.backup_root(vault) / "ledger"
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / f"{ledger.stem}.{now:%Y%m%d-%H%M%S}.bak.md").write_text(full, encoding="utf-8")
        ledger.write_text(new_full, encoding="utf-8")
        print(f"\nApplied. Backup -> {bdir}")
    else:
        print("\nDry-run only. Re-run with --apply to update the ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
