"""
Embedding generation via LM Studio OpenAI-compatible API.
"""
import os
from openai import OpenAI
from config import LM_BASE_URL, EMBEDDING_MODEL

_client = None

def get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=LM_BASE_URL,
            api_key="not-required",  # LM Studio doesn't need auth
        )
    return _client

def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of text chunks.
    Returns a list of embedding vectors.
    """
    client = get_client()
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]

def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]
