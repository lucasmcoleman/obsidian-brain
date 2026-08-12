"""Layer 4 of truth maintenance: provenance-aware retrieval.

Frontmatter truth-status (review_status / superseded_by / source_type) must be
projected into chunk metadata at index time, down-weight results at search time
(never drop), and be VISIBLE to the consuming agent in format_results — the
annotation is the load-bearing part, because the agent that compounds an error is
the one that must see the flag.
"""
import indexer
import searcher
from conftest import write_note


def _flat_embed(monkeypatch):
    """All texts embed to the same vector, so raw distance ties and the truth
    multiplier alone decides ranking."""
    same = lambda texts: [[0.5] * 8 for _ in texts]
    monkeypatch.setattr(indexer, "embed_texts", same)
    monkeypatch.setattr(searcher, "embed_query", lambda t: [0.5] * 8)


def test_index_projects_truth_frontmatter_into_chunks(brain_paths, make_note, fake_embed):
    import json
    from pathlib import Path
    make_note("stale.md",
              "---\nreview_status: superseded\nsuperseded_by: \"[[fresh]]\"\nsource_type: transcript\n---\nOld claim.")
    make_note("plain.md", "No frontmatter note.")
    indexer.build_index(force=True)

    meta = json.loads(Path(brain_paths["meta_path"]).read_text())
    by_note = {c["note_path"]: c for c in meta["chunks"]}
    assert by_note["stale.md"]["review_status"] == "superseded"
    assert by_note["stale.md"]["superseded_by"] == "[[fresh]]"
    assert by_note["stale.md"]["source_type"] == "transcript"
    assert by_note["plain.md"].get("review_status", "") == ""


def test_search_downweights_superseded_but_never_drops(brain_paths, make_note, monkeypatch):
    _flat_embed(monkeypatch)
    make_note("stale.md", "---\nreview_status: superseded\n---\nDelta deadline is June 30.")
    make_note("fresh.md", "Delta deadline is July 15.")
    indexer.build_index(force=True)

    res = searcher.search("delta deadline", top_k=5)
    assert {r["note_path"] for r in res} == {"stale.md", "fresh.md"}  # never dropped
    assert res[0]["note_path"] == "fresh.md"       # full-weight note ranks first
    assert res[1]["review_status"] == "superseded"
    assert res[1]["score"] < res[0]["score"]


def test_search_downweights_unreviewed_transcript(brain_paths, make_note, monkeypatch):
    _flat_embed(monkeypatch)
    make_note("raw.md", "---\nreview_status: unreviewed\nsource_type: transcript\n---\nsame words here.")
    make_note("ok.md", "same words here.")
    indexer.build_index(force=True)

    res = searcher.search("same words", top_k=5)
    assert res[0]["note_path"] == "ok.md"
    assert res[1]["note_path"] == "raw.md"


def test_format_results_annotates_truth_status(brain_paths, make_note, monkeypatch):
    _flat_embed(monkeypatch)
    make_note("stale.md", "---\nreview_status: superseded\nsuperseded_by: \"[[fresh]]\"\n---\nOld claim.")
    make_note("raw.md", "---\nreview_status: unreviewed\nsource_type: ocr\n---\nRaw OCR text.")
    indexer.build_index(force=True)

    out = searcher.format_results(searcher.search("claim", top_k=5), "claim")
    assert "SUPERSEDED" in out and "[[fresh]]" in out
    assert "UNREVIEWED" in out
