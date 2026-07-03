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

# Embedding request hardening (audit findings M4/M5/M6). All env-overridable so a
# large vault or a slow endpoint can be tuned without editing code.
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "64"))  # texts per request
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "30"))      # per-request seconds
EMBED_MAX_RETRIES = int(os.environ.get("EMBED_MAX_RETRIES", "3")) # per-batch attempts
