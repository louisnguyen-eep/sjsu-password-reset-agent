"""Retriever — loads embeddings from JSON, does cosine similarity in NumPy.

Lightweight RAG implementation. For 4 documents and ~50 chunks, a full
vector database is unnecessary overhead.
"""
import json
import os
from pathlib import Path

import numpy as np
import voyageai

from config import CONFIG


EMBEDDINGS_PATH = Path(__file__).parent / "embeddings.json"
VOYAGE_MODEL = "voyage-3-lite"


_chunks = None
_matrix = None
_voyage_client = None


def _load():
    """Load embeddings into memory once on first query."""
    global _chunks, _matrix, _voyage_client
    if _chunks is not None:
        return

    if not EMBEDDINGS_PATH.exists():
        raise RuntimeError(
            f"{EMBEDDINGS_PATH} not found. Run: python3.14 -m knowledge.ingest"
        )

    with EMBEDDINGS_PATH.open() as f:
        _chunks = json.load(f)

    embeddings = np.array([c["embedding"] for c in _chunks], dtype=np.float32)
    # Pre-normalize so similarity = simple dot product
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    _matrix = embeddings / np.clip(norms, 1e-9, None)

    _voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def retrieve(query: str, k: int = None) -> list[dict]:
    """Return [{content, source, score}] for top-k matches."""
    _load()
    k = k or CONFIG.retrieval_k

    # Embed the query
    result = _voyage_client.embed([query], model=VOYAGE_MODEL, input_type="query")
    query_vec = np.array(result.embeddings[0], dtype=np.float32)
    query_vec = query_vec / max(np.linalg.norm(query_vec), 1e-9)

    # Cosine similarity = dot product since both sides are normalized
    scores = _matrix @ query_vec

    # Top k
    top_idx = np.argsort(-scores)[:k]

    return [
        {
            "content": _chunks[i]["content"],
            "source": _chunks[i]["source"],
            "score": float(scores[i]),
        }
        for i in top_idx
    ]