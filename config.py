"""
Configuration for Obsidian Brain.
Vault path and LM Studio settings.
"""
import os

VAULT_PATH = os.environ.get("OBSIDIAN_VAULT_PATH", "/server/obsidian")
BRAIN_DIR = os.path.join(VAULT_PATH, "_brain")
INDEX_PATH = os.path.join(BRAIN_DIR, "index.faiss")
METADATA_PATH = os.path.join(BRAIN_DIR, "metadata.json")
ENTITIES_DIR = os.path.join(BRAIN_DIR, "entities")

LM_BASE_URL = "http://192.168.0.29:1234/v1"
EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v2-moe"
CHUNK_SIZE = 500  # tokens per chunk
CHUNK_OVERLAP = 50  # tokens overlap
TOP_K = 5  # number of chunks to retrieve

# nomic-embed-v2 is trained with task-instruction prefixes: indexed passages get
# "search_document: " and queries get "search_query: ". Omitting them discards the
# query/document asymmetry the model was tuned for (audit finding M-A). Applied to
# the embedding INPUT only; stored chunk text stays clean. Env-overridable (set to
# "" for a model that does not use prefixes) — changing them changes the index, so
# they participate in the freshness/rebuild decision (see indexer._index_params).
EMBED_DOC_PREFIX = os.environ.get("EMBED_DOC_PREFIX", "search_document: ")
EMBED_QUERY_PREFIX = os.environ.get("EMBED_QUERY_PREFIX", "search_query: ")

# Truth-maintenance retrieval weights (design: layered defense, Layer 4). Applied
# as score multipliers at search time so stale/contested/raw notes rank below
# full-weight sources but are NEVER dropped — a stale sole source still beats
# nothing, and the ⚠ annotation (format_results) is what stops an agent citing it.
TRUTH_WEIGHT_SUPERSEDED = float(os.environ.get("TRUTH_WEIGHT_SUPERSEDED", "0.4"))
TRUTH_WEIGHT_CONTESTED = float(os.environ.get("TRUTH_WEIGHT_CONTESTED", "0.7"))
TRUTH_WEIGHT_UNREVIEWED = float(os.environ.get("TRUTH_WEIGHT_UNREVIEWED", "0.6"))

# Search argument hardening + quality knobs (audit findings low-9 / low-5).
SEARCH_MAX_TOP_K = int(os.environ.get("SEARCH_MAX_TOP_K", "100"))       # clamp absurd/negative top_k
SEARCH_MAX_QUERY_CHARS = int(os.environ.get("SEARCH_MAX_QUERY_CHARS", "4000"))  # bound query size
SEARCH_MIN_SCORE = float(os.environ.get("SEARCH_MIN_SCORE", "0"))      # drop results below this (0 = off)

# Embedding request hardening (audit findings M4/M5/M6). All env-overridable so a
# large vault or a slow endpoint can be tuned without editing code.
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "64"))  # texts per request
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "30"))      # per-request seconds
EMBED_MAX_RETRIES = int(os.environ.get("EMBED_MAX_RETRIES", "3")) # per-batch attempts
