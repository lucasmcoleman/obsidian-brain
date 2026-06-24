"""Shared pytest fixtures for the obsidian-brain test suite.

A session-wide temp vault is set via OBSIDIAN_VAULT_PATH *before* any app module
is imported, so importing config.py (and everything that derives paths from it)
never touches the real /server/obsidian vault. Per-test isolation is achieved by
pointing the relevant module-level path constants at a fresh tmp dir.
"""
import os
import sys
import tempfile

# Must run before the first `import config` anywhere in the test process.
os.environ.setdefault("OBSIDIAN_VAULT_PATH", tempfile.mkdtemp(prefix="brain-test-vault-"))

# Make the project modules importable regardless of pytest's rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def vault(tmp_path):
    """A fresh empty vault directory for one test."""
    v = tmp_path / "vault"
    v.mkdir()
    return v


def write_note(vault_dir, rel_path, text):
    """Helper: create a note at vault_dir/rel_path with the given text."""
    p = vault_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def make_note(vault):
    def _make(rel_path, text):
        return write_note(vault, rel_path, text)
    return _make


def _deterministic_vector(text, dim=8):
    """A stable unit-ish vector derived from the text, so identical text always
    embeds identically and tests can reason about ordering."""
    import hashlib
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return [((h[i % len(h)] / 255.0) - 0.5) for i in range(dim)]


@pytest.fixture
def brain_paths(vault, monkeypatch):
    """Repoint every module-level index path constant at a fresh temp vault, so
    build/search tests never touch the real vault and are isolated per-test."""
    import config
    import indexer
    import searcher

    brain_dir = vault / "_brain"
    entities_dir = brain_dir / "entities"
    index_path = str(brain_dir / "index.faiss")
    meta_path = str(brain_dir / "metadata.json")

    for mod in (config, indexer):
        monkeypatch.setattr(mod, "VAULT_PATH", str(vault), raising=False)
        monkeypatch.setattr(mod, "BRAIN_DIR", str(brain_dir), raising=False)
        monkeypatch.setattr(mod, "ENTITIES_DIR", str(entities_dir), raising=False)
        monkeypatch.setattr(mod, "INDEX_PATH", index_path, raising=False)
        monkeypatch.setattr(mod, "METADATA_PATH", meta_path, raising=False)
    for mod in (searcher,):
        monkeypatch.setattr(mod, "INDEX_PATH", index_path, raising=False)
        monkeypatch.setattr(mod, "METADATA_PATH", meta_path, raising=False)

    return {
        "vault": vault,
        "brain_dir": brain_dir,
        "index_path": index_path,
        "meta_path": meta_path,
    }


@pytest.fixture
def fake_embed(monkeypatch):
    """Patch the embedding entry points to deterministic local vectors (no network).

    Patches both indexer.embed_texts and searcher.embed_query (the names actually
    bound in those modules), plus embedder.* for direct callers.
    """
    import embedder

    def embed_texts(texts):
        return [_deterministic_vector(t) for t in texts]

    def embed_query(text):
        return _deterministic_vector(text)

    monkeypatch.setattr(embedder, "embed_texts", embed_texts, raising=True)
    monkeypatch.setattr(embedder, "embed_query", embed_query, raising=True)
    # Rebind the names imported into the consumer modules, if already imported.
    import indexer
    import searcher
    monkeypatch.setattr(indexer, "embed_texts", embed_texts, raising=True)
    monkeypatch.setattr(searcher, "embed_query", embed_query, raising=True)
    return _deterministic_vector
