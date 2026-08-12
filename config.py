"""
Configuration for Obsidian Brain.
Vault path and LM Studio settings.
"""
import os

# Required — no baked-in default: a public checkout must not implicitly point at
# any machine-specific path. Set OBSIDIAN_VAULT_PATH to the vault root.
VAULT_PATH = os.environ.get("OBSIDIAN_VAULT_PATH", "")
if not VAULT_PATH:
    raise SystemExit("OBSIDIAN_VAULT_PATH must be set to the Obsidian vault root directory")
BRAIN_DIR = os.path.join(VAULT_PATH, "_brain")
INDEX_PATH = os.path.join(BRAIN_DIR, "index.faiss")
METADATA_PATH = os.path.join(BRAIN_DIR, "metadata.json")
ENTITIES_DIR = os.path.join(BRAIN_DIR, "entities")

# Env-driven so the deploy compose (which already sets both) actually takes
# effect for the CORE retrieval path too, not just the maintenance subprocesses
# (these used to be hardcoded literals).
LM_BASE_URL = os.environ.get("LM_BASE_URL", "http://localhost:1234/v1")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v2-moe")
CHUNK_SIZE = 500  # tokens per chunk
CHUNK_OVERLAP = 50  # tokens overlap
TOP_K = 5  # number of chunks to retrieve


def _default_prefixes(model: str) -> tuple[str, str]:
    """(doc_prefix, query_prefix) for the given embedding model family.

    Every family wants a DIFFERENT instruction format, and applying the wrong one
    silently degrades retrieval (the M-A lesson):
    - nomic-embed: "search_document: " on passages, "search_query: " on queries.
    - Qwen3-Embedding: documents RAW; the query wrapped in an Instruct/Query
      template (measured on the live endpoint: the template widens the
      relevant-vs-irrelevant cosine gap vs a raw query).
    - unknown models: no prefixes — safer than guessing another family's format.
    """
    m = model.lower()
    if "qwen3-embedding" in m:
        return ("", "Instruct: Given a search query, retrieve relevant passages "
                    "from the user's notes\nQuery: ")
    if "nomic-embed" in m:
        return ("search_document: ", "search_query: ")
    return ("", "")


# Applied to the embedding INPUT only; stored chunk text stays clean. Env override
# always wins — changing either changes the index, so they participate in the
# freshness/rebuild decision (see indexer._index_params).
_DOC_DEFAULT, _QUERY_DEFAULT = _default_prefixes(EMBEDDING_MODEL)
EMBED_DOC_PREFIX = os.environ.get("EMBED_DOC_PREFIX", _DOC_DEFAULT)
EMBED_QUERY_PREFIX = os.environ.get("EMBED_QUERY_PREFIX", _QUERY_DEFAULT)

# Hard per-input character clamp for embedding requests. Some endpoints REJECT
# over-context input with HTTP 400 instead of truncating (measured: LM Studio +
# Qwen3-Embedding-8B at its loaded context), and one rejected batch would abort
# the whole index build. Chunks are far smaller than this; it only catches
# pathological inputs. ~12000 chars ≈ 3000 tokens, under the ~4k loaded context.
EMBED_MAX_INPUT_CHARS = int(os.environ.get("EMBED_MAX_INPUT_CHARS", "12000"))

# Truth-maintenance retrieval weights (design: layered defense, Layer 4). Applied
# as score multipliers at search time so stale/contested/raw notes rank below
# full-weight sources but are NEVER dropped — a stale sole source still beats
# nothing, and the ⚠ annotation (format_results) is what stops an agent citing it.
TRUTH_WEIGHT_SUPERSEDED = float(os.environ.get("TRUTH_WEIGHT_SUPERSEDED", "0.4"))
TRUTH_WEIGHT_CONTESTED = float(os.environ.get("TRUTH_WEIGHT_CONTESTED", "0.7"))
TRUTH_WEIGHT_UNREVIEWED = float(os.environ.get("TRUTH_WEIGHT_UNREVIEWED", "0.6"))

# Search argument hardening + quality knobs .
SEARCH_MAX_TOP_K = int(os.environ.get("SEARCH_MAX_TOP_K", "100"))       # clamp absurd/negative top_k
SEARCH_MAX_QUERY_CHARS = int(os.environ.get("SEARCH_MAX_QUERY_CHARS", "4000"))  # bound query size
SEARCH_MIN_SCORE = float(os.environ.get("SEARCH_MIN_SCORE", "0"))      # drop results below this (0 = off)

# Embedding request hardening . All env-overridable so a
# large vault or a slow endpoint can be tuned without editing code.
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "64"))  # texts per request
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "30"))      # per-request seconds
EMBED_MAX_RETRIES = int(os.environ.get("EMBED_MAX_RETRIES", "3")) # per-batch attempts
