"""
Structured task layer for Obsidian Brain.

Deterministically scans the vault for Obsidian-style markdown checkboxes and lets
the agent (1) list open/done tasks exhaustively across all notes and (2) mark a
task complete in place. This complements the semantic search in searcher.py:
semantic recall finds *relevant* notes; this finds *every* checkbox precisely.
"""
import re
from datetime import date
from pathlib import Path
from typing import Optional

from config import VAULT_PATH
from safe_paths import (
    resolve_in_vault,
    PathOutsideVault,
    detect_newline,
    atomic_write_bytes,
)

# A markdown task line: optional indent, bullet (- * +), [ ]/[x]/[X], then text.
TASK_RE = re.compile(
    r"^(?P<indent>\s*)(?P<bullet>[-*+])\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*\S)\s*$"
)


def _iter_md_files(vault: Path):
    for md in vault.rglob("*.md"):
        if "_brain" in md.parts:  # skip brain-generated notes
            continue
        yield md


def scan_tasks(status: str = "open", vault_path: Optional[str] = None) -> list[dict]:
    """Scan the vault for checkbox tasks.

    status: "open" (unchecked), "done" (checked), or "all".
    Returns a list of {note_path, abs_path, line, text, status}, in vault order.
    """
    vault = Path(vault_path or VAULT_PATH)
    want = status.lower()
    results: list[dict] = []
    for md in _iter_md_files(vault):
        try:
            lines = md.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        rel = str(md.relative_to(vault))
        for i, line in enumerate(lines, 1):
            m = TASK_RE.match(line)
            if not m:
                continue
            st = "done" if m.group("mark") in ("x", "X") else "open"
            if want != "all" and st != want:
                continue
            results.append({
                "note_path": rel,
                "abs_path": str(md),
                "line": i,
                "text": m.group("text").strip(),
                "status": st,
            })
    return results


def count_tasks(vault_path: Optional[str] = None) -> dict:
    """Return {open, done, total} counts across the vault."""
    all_tasks = scan_tasks("all", vault_path)
    open_n = sum(1 for t in all_tasks if t["status"] == "open")
    return {"open": open_n, "done": len(all_tasks) - open_n, "total": len(all_tasks)}


def complete_task(
    note_path: str,
    match: str,
    vault_path: Optional[str] = None,
    completion_date: Optional[str] = None,
) -> dict:
    """Flip an open checkbox to done, in place.

    note_path: absolute or vault-relative path to the note.
    match: case-insensitive substring identifying the open task line. Must match
           exactly one OPEN task in the note; otherwise nothing is written and an
           error/ambiguous result is returned.
    Appends ' ✅ YYYY-MM-DD' (Obsidian Tasks completion format) if not present.
    """
    vault = Path(vault_path or VAULT_PATH)
    try:
        p = resolve_in_vault(note_path, str(vault))
    except PathOutsideVault as e:
        return {"status": "error", "error": str(e)}
    if not p.exists():
        return {"status": "error", "error": f"Note not found: {p}"}

    needle = match.strip().lower()
    raw = p.read_bytes()
    nl = detect_newline(raw)
    # Split on the file's own line ending only (not splitlines(), which would
    # later force every line ending to LF on rewrite — audit finding M10).
    lines = raw.decode("utf-8").split(nl)
    hits = [
        (i, m)
        for i, line in enumerate(lines)
        if (m := TASK_RE.match(line))
        and m.group("mark") == " "
        and needle in m.group("text").strip().lower()
    ]

    if not hits:
        return {"status": "error", "error": f"No open task matching {match!r} in {p}"}
    if len(hits) > 1:
        return {
            "status": "ambiguous",
            "error": f"{len(hits)} open tasks match {match!r}; be more specific.",
            "matches": [lines[i].strip() for i, _ in hits],
        }

    i, m = hits[0]
    cdate = completion_date or date.today().isoformat()
    new_line = re.sub(r"\[ \]", "[x]", lines[i], count=1)
    if "✅" not in new_line:
        new_line = new_line.rstrip() + f" ✅ {cdate}"
    lines[i] = new_line
    # Rejoin with the original newline (round-trips exactly) and write atomically.
    atomic_write_bytes(p, nl.join(lines).encode("utf-8"))

    rel = str(p.relative_to(vault)) if str(p).startswith(str(vault)) else str(p)
    return {"status": "completed", "note_path": rel, "line": i + 1,
            "text": m.group("text").strip(), "completion_date": cdate}


def format_tasks(tasks: list[dict], status: str) -> str:
    """Render tasks grouped by note for readable agent consumption."""
    if not tasks:
        return f"No {status} tasks found."
    by_note: dict[str, list[dict]] = {}
    for t in tasks:
        by_note.setdefault(t["note_path"], []).append(t)
    out = [f"## {status.capitalize()} tasks ({len(tasks)})\n"]
    for note in sorted(by_note):
        out.append(f"### {note}")
        for t in by_note[note]:
            box = "x" if t["status"] == "done" else " "
            out.append(f"- [{box}] {t['text']}  _(L{t['line']})_")
        out.append("")
    return "\n".join(out)
