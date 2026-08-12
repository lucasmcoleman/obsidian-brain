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


def test_chunk_text_hard_caps_oversized_sentence():
    # A long run with no .!? is one "sentence"; it must be sub-split so no single
    # chunk blows past the model's context window and gets silently truncated (M-B).
    text = " ".join(f"word{i}" for i in range(4000))
    chunks = indexer.chunk_text(text, max_tokens=500, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert indexer.count_tokens(c["text"]) <= 500
    joined = " ".join(c["text"] for c in chunks)
    for i in (0, 1999, 3999):
        assert f"word{i}" in joined  # no content silently dropped


def test_scan_vault_strips_moc_linker_managed_blocks(brain_paths, make_note):
    # The linker writes '## Related Notes' wikilink blocks into note bodies; those
    # must not be re-embedded, or a note becomes retrievable for topics it merely
    # links to — a self-reinforcing precision drift (M-E).
    body = ("Real content about apples.\n\n"
            "<!-- moc-linker:related:begin (auto-generated; edit outside this block) -->\n"
            "## Related Notes\n- [[Some Other Note]] — a description\n"
            "<!-- moc-linker:related:end -->\n")
    make_note("a.md", body)
    notes = indexer.scan_vault(str(brain_paths["vault"]))
    a = next(n for n in notes if n["path"] == "a.md")
    assert "Real content about apples" in a["content"]
    assert "Some Other Note" not in a["content"]
    assert "moc-linker" not in a["content"]


def test_scan_vault_prepends_note_title(brain_paths, make_note):
    # In Obsidian the filename IS the title and is often the only place a
    # person/project name appears; it must reach the embeddings (M-C).
    make_note("Weekly Sync 1-1 2026-06-30.md", "Discussed the roadmap; the title is the only identifier.")
    notes = indexer.scan_vault(str(brain_paths["vault"]))
    n = next(x for x in notes if x["path"] == "Weekly Sync 1-1 2026-06-30.md")
    assert n["content"].startswith("Weekly Sync 1-1 2026-06-30")
    assert "Discussed the roadmap" in n["content"]


def test_scan_vault_includes_entity_notes(brain_paths, make_note):
    # Entity notes in _brain/entities/ must be indexed so brain_write_entity content
    # is retrievable, not write-only (M-D).
    ent = brain_paths["brain_dir"] / "entities"
    ent.mkdir(parents=True, exist_ok=True)
    (ent / "john-smith.md").write_text("John Smith leads Delta.", encoding="utf-8")
    make_note("regular.md", "a normal note")
    notes = indexer.scan_vault(str(brain_paths["vault"]))
    paths = {n["path"] for n in notes}
    assert "regular.md" in paths
    assert any(p.replace("\\", "/").endswith("entities/john-smith.md") for p in paths)


def test_scan_vault_indexes_nested_brain_dir(brain_paths, make_note):
    # Unlike tasks._iter_md_files (which excludes any path containing a `_brain`
    # component at any depth), indexer._is_excluded_path anchors the `_brain`
    # exclusion at the vault TOP level only — a nested Projects/_brain/x.md is a
    # regular note path as far as the indexer is concerned and IS indexed. This
    # pins that current (drifted) behavior exactly.
    make_note("Projects/_brain/x.md", "Nested brain-dir content, not derivative index data.")
    notes = indexer.scan_vault(str(brain_paths["vault"]))
    paths = {n["path"].replace("\\", "/") for n in notes}
    assert "Projects/_brain/x.md" in paths


def test_scan_vault_skips_trash_dotdirs_and_livesync_logs(brain_paths, make_note):
    # Obsidian's .trash/ (deleted notes), dot-directories (.obsidian/ config), and
    # LiveSync's operational logs (livesync_log_*.md) are NOT knowledge. Indexing
    # them pollutes retrieval; worse, a deleted note under .trash/ has a different
    # path than its live copy, so dedupe-by-note can't collapse them and a stale
    # deleted version can surface as current. (Measured on a real vault: the
    # majority of a live index can be .trash + these logs.)
    make_note("real.md", "Genuine note content about apples.")
    make_note(".trash/deleted note 37afcd4b.md", "A trashed duplicate; must not index.")
    make_note(".obsidian/some-config-note.md", "Obsidian config area, not a note.")
    make_note("livesync_log_2026-06-16.md",
              "vault-1234:6/16/2026->[ModuleObsidianEvents] Loaded")
    notes = indexer.scan_vault(str(brain_paths["vault"]))
    paths = {n["path"].replace("\\", "/") for n in notes}
    assert "real.md" in paths
    assert not any(p.startswith(".trash/") for p in paths), "trash must be skipped"
    assert not any(p.startswith(".obsidian/") for p in paths), "dot-dirs must be skipped"
    assert not any(Path(p).name.startswith("livesync_log_") for p in paths), \
        "livesync logs must be skipped"


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
    # forever with no rebuild path .
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


def test_incremental_build_rebuilds_on_embedding_model_change(brain_paths, make_note, fake_embed, monkeypatch):
    # Swapping to a different (same-dim) embedding model without --force must
    # rebuild, not keep serving the stale-model index .
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    monkeypatch.setattr(indexer, "EMBEDDING_MODEL", "some-other-embedding-model")
    result = indexer.build_index(force=False)
    assert result["status"] == "built"


def test_incremental_build_rebuilds_on_chunk_size_change(brain_paths, make_note, fake_embed, monkeypatch):
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    monkeypatch.setattr(indexer, "CHUNK_SIZE", 123)
    result = indexer.build_index(force=False)
    assert result["status"] == "built"


def test_incremental_build_rebuilds_on_doc_prefix_change(brain_paths, make_note, fake_embed, monkeypatch):
    # Changing the nomic doc prefix changes the embeddings → must reindex (M-A/M-L).
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    monkeypatch.setattr(indexer, "EMBED_DOC_PREFIX", "different_prefix: ")
    result = indexer.build_index(force=False)
    assert result["status"] == "built"


def test_incremental_build_stays_current_when_params_unchanged(brain_paths, make_note, fake_embed):
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    result = indexer.build_index(force=False)
    assert result["status"] == "already_current"  # params match → no needless re-embed


def test_incremental_build_reuses_cached_embeddings(brain_paths, make_note, monkeypatch):
    # A content-hash embedding cache means only new/changed chunks hit the endpoint
    # on a rebuild, instead of re-embedding the whole vault (capability #2).
    counted = {"texts": 0}

    def counting_embed(texts):
        counted["texts"] += len(texts)
        return [[float(len(t))] * 8 for t in texts]

    monkeypatch.setattr(indexer, "embed_texts", counting_embed)
    make_note("a.md", "alpha content here.")
    make_note("b.md", "beta content here.")
    indexer.build_index(force=True)
    first = counted["texts"]
    assert first >= 2  # embedded both notes

    make_note("c.md", "gamma content here.")  # one new note
    counted["texts"] = 0
    indexer.build_index(force=False)          # vault changed → rebuild, but cache hits a & b
    assert counted["texts"] >= 1              # embedded the new note
    assert counted["texts"] < first           # did NOT re-embed the unchanged notes


def test_cleanup_removes_leftover_tmp_files(brain_paths):
    brain_dir = brain_paths["brain_dir"]
    brain_dir.mkdir(parents=True, exist_ok=True)
    leftover = brain_dir / "index.faiss.tmp"
    leftover.write_text("stale", encoding="utf-8")

    indexer.cleanup_tmp_files()

    assert not leftover.exists()
