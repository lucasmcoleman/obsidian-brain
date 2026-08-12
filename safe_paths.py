"""Vault-containment guard for agent-supplied note paths.

The MCP write tools (``brain_append_insight``, ``brain_complete_task``) accept a
caller-supplied ``note_path``. Without containment, an absolute or ``../`` path
lets a caller read/append/modify files anywhere the process can reach — a real
exposure once the server is network-facing . This module
resolves a requested path against the vault root and rejects anything that
escapes it (including via symlinks) or that is not a markdown file.
"""
import os
import tempfile
from pathlib import Path


class PathOutsideVault(ValueError):
    """Raised when a requested note path resolves outside the vault or is not *.md."""


def is_scannable_md(rel_path: Path, *, include_entities: bool, brain_top_level_only: bool) -> bool:
    """True for a vault-relative markdown path that is real knowledge and should be
    scanned by an indexer/task-scanner walk — i.e. NOT one of:
      - _brain/ (derivative index data), EXCEPT _brain/entities/ (curated notes
        from brain_write_entity, which must stay retrievable).
        when ``include_entities`` is True.
      - Any dot-directory component: Obsidian's .trash/ (deleted notes), .obsidian/
        (config), .git/, etc. A deleted note under .trash/ is especially harmful —
        its path differs from the live copy, so dedupe-by-note can't collapse the
        two and a stale/deleted version can surface as current.
      - LiveSync's operational debug logs (livesync_log_*.md), pure noise.

    ``brain_top_level_only`` controls how the ``_brain`` exclusion is anchored:
      - True: only a path whose FIRST component is ``_brain`` is excluded, so a
        nested ``Projects/_brain/x.md`` is treated as a normal note.
      - False: a path is excluded if ANY component is ``_brain``, at any depth.

    Two callers, two historically-drifted policies, reproduced exactly:
      - indexer.py's scan_vault : ``include_entities=True,
        brain_top_level_only=True``.
      - tasks.py's task scanner: ``include_entities=False,
        brain_top_level_only=False`` (no entities carve-out; matches ``_brain`` at
        any depth).

    Measurements on real vaults showed the majority of a live index could be
    .trash + livesync log noise.
    """
    parts = rel_path.parts
    if brain_top_level_only:
        is_brain = bool(parts) and parts[0] == "_brain"
    else:
        is_brain = "_brain" in parts
    if is_brain:
        is_entities = include_entities and len(parts) >= 2 and parts[0] == "_brain" and parts[1] == "entities"
        if not is_entities:
            return False
    # Any dot-directory in the path (exclude the filename itself, parts[:-1]).
    if any(p.startswith(".") for p in parts[:-1]):
        return False
    if rel_path.name.startswith("livesync_log_"):
        return False
    return True


def detect_newline(raw: bytes) -> str:
    """Return the dominant line ending of a file's raw bytes ('\\r\\n' or '\\n').
    Used so edits don't silently flatten a CRLF note to LF ."""
    return "\r\n" if b"\r\n" in raw else "\n"


def atomic_write_bytes(path, data: bytes) -> None:
    """Write bytes via a sibling temp file + os.replace, so a crash mid-write can
    never truncate or corrupt the original note ."""
    path = str(path)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def resolve_in_vault(note_path: str, vault_path: str) -> Path:
    """Resolve ``note_path`` (absolute or vault-relative) to a real path that is
    guaranteed to live inside ``vault_path`` and end in ``.md``.

    Symlinks are followed (``resolve()``) before the containment check, so a link
    inside the vault that points outside is rejected. Raises ``PathOutsideVault``
    on any violation; returns the resolved ``Path`` on success.
    """
    vault_root = Path(vault_path).resolve()

    candidate = Path(note_path)
    if not candidate.is_absolute():
        candidate = vault_root / candidate
    resolved = candidate.resolve()

    if resolved.suffix.lower() != ".md":
        raise PathOutsideVault(f"Not a markdown (.md) file: {note_path!r}")

    # Containment: resolved must be the vault root or a descendant of it.
    if resolved != vault_root and vault_root not in resolved.parents:
        raise PathOutsideVault(
            f"Path {note_path!r} resolves outside the vault ({vault_root})"
        )

    return resolved
