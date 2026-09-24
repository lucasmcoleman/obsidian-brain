from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

import pytest
import brain
import indexer
import searcher
import tasks


def test_parallel_insights_all_survive(vault, monkeypatch):
    monkeypatch.setattr(brain, "VAULT_PATH", str(vault))
    note = vault / "Note.md"
    note.write_text("# Note\n")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: brain.append_insight("Note.md", f"unique insight {i}."), range(20)))
    assert all(r["status"] == "ok" for r in results)
    text = note.read_text()
    assert all(f"unique insight {i}." in text for i in range(20))


def test_force_reembeds_and_empty_build_clears_deleted_evidence(brain_paths, make_note, monkeypatch):
    calls = []
    monkeypatch.setattr(indexer, "embed_texts", lambda texts: calls.extend(texts) or [[1., 0.] for _ in texts])
    monkeypatch.setattr(searcher, "embed_query", lambda _: [1., 0.])
    p = make_note("a.md", "alpha")
    indexer.build_index(force=True)
    calls.clear()
    indexer.build_index(force=True)
    assert calls
    p.unlink()
    indexer.build_index(force=True)
    assert searcher.search("alpha") == []


def test_interrupted_publish_keeps_a_complete_generation(brain_paths, make_note, fake_embed, monkeypatch):
    p = make_note("a.md", "original alpha evidence")
    indexer.build_index(force=True)
    original = searcher.search("alpha")[0]["text"]
    p.write_text("replacement beta evidence")
    replace = indexer.os.replace
    def fail_metadata(src, dest):
        if str(dest) == indexer.METADATA_PATH:
            raise OSError("interrupted publication")
        return replace(src, dest)
    monkeypatch.setattr(indexer.os, "replace", fail_metadata)
    with pytest.raises(OSError):
        indexer.build_index(force=True)
    assert searcher.search("alpha")[0]["text"] == original


def test_production_query_reports_outage(brain_paths, make_note, fake_embed, monkeypatch):
    make_note("a.md", "alpha")
    indexer.build_index(force=True)
    monkeypatch.setattr(brain, "INDEX_PATH", brain_paths["index_path"])
    monkeypatch.setattr(brain, "METADATA_PATH", brain_paths["meta_path"])
    monkeypatch.setattr(searcher, "embed_query", lambda _: (_ for _ in ()).throw(RuntimeError("down")))
    assert "unavailable" in brain.query_brain("alpha").lower()
    assert "No relevant notes" not in brain.query_brain("alpha")


def test_read_scans_reject_external_symlinks(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("- [ ] confidential\n")
    (vault / "link.md").symlink_to(outside)
    assert indexer.scan_vault(str(vault)) == []
    assert tasks.scan_tasks(vault_path=str(vault)) == []


def test_generated_workspace_records_are_not_reindexed(vault):
    p = vault / "Brain Workspace" / "Records" / "one.md"
    p.parent.mkdir(parents=True)
    p.write_text("# Generated duplicate evidence\n- [ ] duplicate\n")
    assert indexer.scan_vault(str(vault)) == []
    assert tasks.scan_tasks(vault_path=str(vault)) == []
