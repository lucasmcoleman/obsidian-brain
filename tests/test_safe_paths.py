"""Tests for the vault-containment path guard (audit findings H2 / M9)."""
import pytest

from safe_paths import resolve_in_vault, PathOutsideVault


def test_relative_md_path_resolves_inside_vault(vault):
    (vault / "Projects").mkdir()
    (vault / "Projects" / "Delta.md").write_text("x", encoding="utf-8")
    resolved = resolve_in_vault("Projects/Delta.md", str(vault))
    assert resolved == (vault / "Projects" / "Delta.md").resolve()


def test_absolute_path_inside_vault_is_allowed(vault):
    note = vault / "Note.md"
    note.write_text("x", encoding="utf-8")
    resolved = resolve_in_vault(str(note), str(vault))
    assert resolved == note.resolve()


def test_absolute_path_outside_vault_is_rejected(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(PathOutsideVault):
        resolve_in_vault(str(outside), str(vault))


def test_dotdot_escape_is_rejected(vault):
    with pytest.raises(PathOutsideVault):
        resolve_in_vault("../../etc/passwd.md", str(vault))


def test_non_markdown_extension_is_rejected(vault):
    (vault / "config.yaml").write_text("x", encoding="utf-8")
    with pytest.raises(PathOutsideVault):
        resolve_in_vault("config.yaml", str(vault))


def test_internal_dotdot_that_stays_inside_is_allowed(vault):
    (vault / "a").mkdir()
    (vault / "b.md").write_text("x", encoding="utf-8")
    resolved = resolve_in_vault("a/../b.md", str(vault))
    assert resolved == (vault / "b.md").resolve()


def test_symlink_escaping_vault_is_rejected(vault, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("top secret", encoding="utf-8")
    link = vault / "link.md"
    link.symlink_to(secret)
    with pytest.raises(PathOutsideVault):
        resolve_in_vault("link.md", str(vault))
