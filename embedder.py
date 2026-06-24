"""
Embedding generation via LM Studio OpenAI-compatible API.

Requests are batched (so a large vault never sends one oversized request), each
batch is retried with bounded backoff, and the client carries an explicit timeout
so an unresponsive endpoint fails fast instead of hanging on the SDK's 600s
default (audit findings M4/M5/M6).
"""
import time

from openai import OpenAI
from config import (
    LM_BASE_URL,
    EMBEDDING_MODEL,
    EMBED_BATCH_SIZE,
    EMBED_TIMEOUT,
    EMBED_MAX_RETRIES,
)

_client = None


def get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=LM_BASE_URL,
            api_key="not-required",  # LM Studio doesn't need auth
            timeout=EMBED_TIMEOUT,
            # We do our own batch-level retry with backoff; keep the SDK's own
            # retry minimal so failures surface to our loop promptly.
            max_retries=0,
        )
    return _client


def _embed_one_batch(batch: list[str], max_retries: int) -> list[list[float]]:
    """Embed a single batch with bounded exponential backoff. Re-raises the last
    exception if every attempt fails."""
    client = get_client()
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
            return [item.embedding for item in response.data]
        except Exception as e:  # noqa: BLE001 — endpoint errors are heterogeneous
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(min(2 ** attempt, 8))  # 1s, 2s, 4s, capped at 8s
    raise last_exc


def embed_texts(
    texts: list[str],
    batch_size: int = None,
    max_retries: int = None,
) -> list[list[float]]:
    """
    Generate embeddings for a list of text chunks, in batches with per-batch retry.
    Returns vectors in the same order as ``texts``.
    """
    if not texts:
        return []
    batch_size = batch_size or EMBED_BATCH_SIZE
    max_retries = max_retries if max_retries is not None else EMBED_MAX_RETRIES

    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        vectors.extend(_embed_one_batch(batch, max_retries))
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]
