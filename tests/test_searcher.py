"""searcher argument hardening + candidate-pool behaviour (low-9 / low-5)."""
import indexer
import searcher


def test_search_negative_top_k_does_not_crash(brain_paths, make_note, fake_embed):
    make_note("a.md", "alpha content here.")
    make_note("b.md", "beta content here.")
    indexer.build_index(force=True)

    res = searcher.search("alpha", top_k=-5)  # would hit faiss.search(vec, -10) before
    assert isinstance(res, list)
    assert len(res) <= 1  # clamped to a minimum of 1


def test_search_non_int_top_k_falls_back(brain_paths, make_note, fake_embed):
    make_note("a.md", "alpha content here.")
    indexer.build_index(force=True)
    assert isinstance(searcher.search("alpha", top_k="oops"), list)


def test_search_returns_empty_when_embedding_endpoint_down(brain_paths, make_note, fake_embed, monkeypatch):
    # An embedding-endpoint outage must degrade to [] (as SKILL.md documents), not
    # propagate an exception out of search() (audit finding: doc/graceful-path).
    make_note("a.md", "alpha content.")
    indexer.build_index(force=True)

    def boom(_text):
        raise RuntimeError("embedding endpoint down")

    monkeypatch.setattr(searcher, "embed_query", boom)
    assert searcher.search("alpha") == []


def test_search_fills_top_k_across_distinct_notes(brain_paths, make_note, fake_embed):
    for i in range(8):
        make_note(f"n{i}.md", f"note number {i} about the shared topic word.")
    indexer.build_index(force=True)
    res = searcher.search("topic", top_k=5)
    assert len(res) == 5
    assert len({r["note_path"] for r in res}) == 5  # distinct notes


def test_search_caches_index_between_queries(brain_paths, make_note, fake_embed, monkeypatch):
    # Per-query faiss.read_index + full-metadata json.loads grows linearly with the
    # vault; unchanged files must be served from the in-process cache (scalability).
    make_note("a.md", "alpha content here.")
    indexer.build_index(force=True)

    reads = {"n": 0}
    real_read = searcher.faiss.read_index

    def counting_read(path):
        reads["n"] += 1
        return real_read(path)

    monkeypatch.setattr(searcher.faiss, "read_index", counting_read)
    searcher._INDEX_CACHE.clear()

    assert searcher.search("alpha")          # cold: reads from disk
    assert searcher.search("alpha")          # warm: served from cache
    assert reads["n"] == 1

    make_note("b.md", "beta content here.")  # rebuild swaps new files in
    indexer.build_index(force=False)
    res = searcher.search("beta")            # cache must notice and reload
    assert reads["n"] == 2
    assert any(r["note_path"] == "b.md" for r in res)


def test_search_returns_empty_on_dimension_mismatch(brain_paths, make_note, fake_embed, monkeypatch):
    # Query embedded at a different dimension than the stored index (mid-migration
    # to a new embedding model, before the rebuild lands) must degrade to [] with
    # a "rebuild needed" log — not crash with a bare faiss AssertionError (M-3).
    make_note("a.md", "alpha content here.")
    indexer.build_index(force=True)  # fake embedder → 8-dim index

    monkeypatch.setattr(searcher, "embed_query", lambda t: [0.5] * 16)  # wrong dim
    assert searcher.search("alpha") == []


def test_search_results_are_json_serializable(brain_paths, make_note, fake_embed):
    import json
    make_note("a.md", "alpha content here about a topic.")
    indexer.build_index(force=True)
    res = searcher.search("topic", top_k=3)
    assert res and isinstance(res[0]["score"], float)  # native float, not numpy
    json.dumps(res)  # must not raise (JSON consumers like /ui/api/search)
