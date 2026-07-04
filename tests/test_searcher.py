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


def test_search_results_are_json_serializable(brain_paths, make_note, fake_embed):
    import json
    make_note("a.md", "alpha content here about a topic.")
    indexer.build_index(force=True)
    res = searcher.search("topic", top_k=3)
    assert res and isinstance(res[0]["score"], float)  # native float, not numpy
    json.dumps(res)  # must not raise (JSON consumers like /ui/api/search)
