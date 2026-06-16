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
