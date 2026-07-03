"""Index durability: delete-aware rebuild, consistency guard, corrupt-metadata
resilience, dead-code removal (M1/M2/M7/L1/L2/L7/L8/L12)."""
import json
from pathlib import Path

import pytest

import indexer
import searcher


def test_chunk_text_has_no_dead_start_end_fields():
    chunks = indexer.chunk_text("One sentence. Two sentence. Three.")
    assert chunks
    for c in chunks:
        assert "text" in c
        assert "start" not in c and "end" not in c  # L2: dead offsets removed


def test_cosine_sim_dead_code_removed():
    assert not hasattr(searcher, "cosine_sim")  # L1


def test_rebuild_after_note_deletion_drops_stale_chunks(brain_paths, make_note, fake_embed):
    make_note("a.md", "Alpha content about apples.")
    make_note("b.md", "Beta content about bananas.")
    indexer.build_index(force=True)

    # Delete one note, then do a NON-force rebuild — must notice the set changed.
    (brain_paths["vault"] / "b.md").unlink()
    result = indexer.build_index(force=False)

    assert result["status"] == "built"  # not "already_current" (M7)
    meta = json.loads(Path(brain_paths["meta_path"]).read_text())
    paths = {c["note_path"] for c in meta["chunks"]}
    assert paths == {"a.md"}


def test_search_returns_empty_on_count_mismatch(brain_paths, make_note, fake_embed):
    # Long multi-sentence note → several chunks, so dropping one still leaves a
    # non-empty-but-mismatched metadata (the real M1/L7 crash window).
    big = ". ".join(f"sentence number {i} has several words in it" for i in range(300)) + "."
    make_note("a.md", big)
    indexer.build_index(force=True)

    meta = json.loads(Path(brain_paths["meta_path"]).read_text())
    assert len(meta["chunks"]) >= 2  # guard the test's own premise
    meta["chunks"] = meta["chunks"][:-1]  # drop one → ntotal != len(chunks), still non-empty
    Path(brain_paths["meta_path"]).write_text(json.dumps(meta), encoding="utf-8")

    # Must not raise IndexError or return mismatched text — guard returns [].
    assert searcher.search("sentence") == []  # M1/L7


def test_search_survives_corrupt_metadata_json(brain_paths, make_note, fake_embed):
    make_note("a.md", "Alpha content.")
    indexer.build_index(force=True)
    Path(brain_paths["meta_path"]).write_text("{ this is not json", encoding="utf-8")

    assert searcher.search("alpha") == []  # L8: no JSONDecodeError escapes


def test_build_survives_corrupt_metadata_in_freshness_check(brain_paths, make_note, fake_embed):
    make_note("a.md", "Alpha content.")
    indexer.build_index(force=True)
    Path(brain_paths["meta_path"]).write_text("garbage", encoding="utf-8")

    # Non-force build must treat corrupt metadata as "needs rebuild", not crash (L8).
    result = indexer.build_index(force=False)
    assert result["status"] == "built"


def test_scan_vault_skips_unreadable_note_without_crashing(brain_paths, make_note, capsys):
    make_note("good.md", "readable content")
    bad = make_note("bad.md", "x")
    # Make it undecodable as utf-8.
    bad.write_bytes(b"\xff\xfe\x00bad bytes")

    notes = indexer.scan_vault(str(brain_paths["vault"]))
    paths = {n["path"] for n in notes}
    assert "good.md" in paths  # L12: survivors still indexed


def test_incremental_build_rebuilds_when_index_file_is_corrupt(brain_paths, make_note, fake_embed):
    # A corrupt index.faiss with intact metadata + unchanged vault must NOT be
    # trusted as already_current — otherwise every query silently returns nothing
    # forever with no rebuild path (audit finding M-K).
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    Path(brain_paths["index_path"]).write_bytes(b"not a real faiss index")
    result = indexer.build_index(force=False)

    assert result["status"] == "built"  # not "already_current"
    import faiss
    idx = faiss.read_index(brain_paths["index_path"])
    meta = json.loads(Path(brain_paths["meta_path"]).read_text())
    assert idx.ntotal == len(meta["chunks"])  # rebuilt to a consistent state


def test_incremental_build_rebuilds_on_index_metadata_count_mismatch(brain_paths, make_note, fake_embed):
    # index.faiss and metadata disagree on row count (a torn swap / bad LiveSync
    # replication of _brain/), vault unchanged → must rebuild, not report current.
    big = ". ".join(f"sentence number {i} has several words in it" for i in range(300)) + "."
    make_note("a.md", big)
    indexer.build_index(force=True)

    meta = json.loads(Path(brain_paths["meta_path"]).read_text())
    assert len(meta["chunks"]) >= 2
    meta["chunks"] = meta["chunks"][:-1]  # drop a chunk; vault_signature still matches
    Path(brain_paths["meta_path"]).write_text(json.dumps(meta), encoding="utf-8")

    result = indexer.build_index(force=False)
    assert result["status"] == "built"


def test_cleanup_removes_leftover_tmp_files(brain_paths):
    brain_dir = brain_paths["brain_dir"]
    brain_dir.mkdir(parents=True, exist_ok=True)
    leftover = brain_dir / "index.faiss.tmp"
    leftover.write_text("stale", encoding="utf-8")

    indexer.cleanup_tmp_files()

    assert not leftover.exists()
