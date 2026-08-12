"""nomic-embed-v2 task-instruction prefixes .

The model card requires `search_document: ` before every indexed passage and
`search_query: ` before every query; omitting them discards the retrieval-tuned
query/document asymmetry the model was trained on. These tests pin that the
prefixes are applied on both the index and the query side, while the *stored*
chunk text stays clean (prefix only decorates the embedding input).
"""
import config
import indexer
import searcher


def test_documents_embedded_with_doc_prefix(brain_paths, make_note, monkeypatch):
    captured = {}

    def capturing_embed(texts):
        captured["texts"] = list(texts)
        return [[0.0] * 8 for _ in texts]

    monkeypatch.setattr(indexer, "embed_texts", capturing_embed)
    make_note("a.md", "Alpha content about apples. Beta gamma delta.")

    indexer.build_index(force=True)

    assert captured["texts"], "expected at least one chunk to be embedded"
    assert all(t.startswith(config.EMBED_DOC_PREFIX) for t in captured["texts"])


def test_stored_chunk_text_is_not_prefixed(brain_paths, make_note, fake_embed):
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    import json
    from pathlib import Path
    meta = json.loads(Path(brain_paths["meta_path"]).read_text())
    assert meta["chunks"]
    for c in meta["chunks"]:
        assert not c["text"].startswith(config.EMBED_DOC_PREFIX)


def test_query_embedded_with_query_prefix(brain_paths, make_note, fake_embed, monkeypatch):
    make_note("a.md", "Alpha content about apples.")
    indexer.build_index(force=True)

    captured = {}

    def capturing_query(text):
        captured["q"] = text
        return [0.0] * 8

    monkeypatch.setattr(searcher, "embed_query", capturing_query)
    searcher.search("what is alpha")

    assert captured["q"].startswith(config.EMBED_QUERY_PREFIX)
