"""MCP tool-layer behavior: resilience and contracts (L8, plus task-5 contracts)."""
import json
from pathlib import Path

import pytest

import mcp_server


@pytest.fixture
def mcp_paths(brain_paths, monkeypatch):
    """brain_paths repoints indexer/searcher; also repoint the names mcp_server
    imported so brain_status reads the temp vault's index files."""
    monkeypatch.setattr(mcp_server, "VAULT_PATH", str(brain_paths["vault"]), raising=False)
    monkeypatch.setattr(mcp_server, "INDEX_PATH", brain_paths["index_path"], raising=False)
    monkeypatch.setattr(mcp_server, "METADATA_PATH", brain_paths["meta_path"], raising=False)
    monkeypatch.setattr(mcp_server, "ENTITIES_DIR",
                        str(brain_paths["brain_dir"] / "entities"), raising=False)
    return brain_paths


def test_brain_status_survives_corrupt_metadata(mcp_paths, make_note, fake_embed):
    import indexer
    make_note("a.md", "Alpha content here.")
    indexer.build_index(force=True)
    Path(mcp_paths["meta_path"]).write_text("{ not json", encoding="utf-8")

    out = mcp_server.brain_status()  # must not raise (L8)
    status = json.loads(out)
    assert "vault_path" in status
