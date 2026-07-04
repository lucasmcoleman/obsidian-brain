"""Per-model embedding prefix defaults (embedding-swap).

Each embedding family wants a DIFFERENT instruction format — nomic uses
search_document:/search_query: prefixes on both sides; Qwen3-Embedding wants the
query wrapped in an Instruct/Query template and documents raw. Applying the wrong
format silently degrades retrieval (the M-A lesson), so the defaults must follow
the configured model, with env overrides always winning.
"""
import config


def test_nomic_defaults():
    doc, query = config._default_prefixes("text-embedding-nomic-embed-text-v2-moe")
    assert doc == "search_document: "
    assert query == "search_query: "


def test_qwen3_embedding_defaults():
    doc, query = config._default_prefixes("text-embedding-qwen3-embedding-8b")
    assert doc == ""  # documents are embedded raw
    assert query.startswith("Instruct:")
    assert query.rstrip().endswith("Query:")


def test_unknown_model_gets_no_prefixes():
    doc, query = config._default_prefixes("some-future-embedder")
    assert doc == "" and query == ""


def test_embed_input_clamp_is_positive():
    assert config.EMBED_MAX_INPUT_CHARS > 1000
