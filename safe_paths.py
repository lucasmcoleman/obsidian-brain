"""Vault-containment guard for agent-supplied note paths.

The MCP write tools (``brain_append_insight``, ``brain_complete_task``) accept a
caller-supplied ``note_path``. Without containment, an absolute or ``../`` path
lets a caller read/append/modify files anywhere the process can reach — a real
exposure once the server is network-facing (audit findings H2 / M9). This module
resolves a requested path against the vault root and rejects anything that
escapes it (including via symlinks) or that is not a markdown file.
"""
import os
import tempfile
from pathlib import Path


class PathOutsideVault(ValueError):
    """Raised when a requested note path resolves outside the vault or is not *.md."""


def detect_newline(raw: bytes) -> str:
    """Return the dominant line ending of a file's raw bytes ('\\r\\n' or '\\n').
    Used so edits don't silently flatten a CRLF note to LF (audit finding M10)."""
    return "\r\n" if b"\r\n" in raw else "\n"


def atomic_write_bytes(path, data: bytes) -> None:
    """Write bytes via a sibling temp file + os.replace, so a crash mid-write can
    never truncate or corrupt the original note (audit finding M10)."""
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
