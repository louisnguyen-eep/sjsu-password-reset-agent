"""Ingest scraped SJSU docs as embeddings into a JSON file. Hardened version."""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import voyageai


SCRAPED_PATH = Path(__file__).parent / "scraped_docs.json"
EMBEDDINGS_PATH = Path(__file__).parent / "embeddings.json"
VOYAGE_MODEL = "voyage-3-lite"


def chunk(text, max_chars=500, overlap=80):
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap
    if step <= 0:
        step = max_chars
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + max_chars])
        start += step
        if len(chunks) > 1000:
            break
    return chunks


def ingest():
    print("step 1: loading scraped docs", flush=True)
    with SCRAPED_PATH.open() as f:
        docs = json.load(f)
    print(f"  loaded {len(docs)} docs", flush=True)

    print("step 2: building chunks", flush=True)
    chunks_data = []
    for doc in docs:
        for i, piece in enumerate(chunk(doc["content"])):
            chunks_data.append({
                "id": f"{doc['id']}_c{i}",
                "content": piece,
                "source": doc["source"],
                "doc_id": doc["id"],
            })
    print(f"  built {len(chunks_data)} chunks", flush=True)

    print("step 3: creating Voyage client", flush=True)
    voyage_key = os.environ.get("VOYAGE_API_KEY")
    if not voyage_key:
        print("ERROR: VOYAGE_API_KEY not set", flush=True)
        sys.exit(1)
    client = voyageai.Client(api_key=voyage_key)

    print("step 4: embedding in small batches", flush=True)
    texts = [c["content"] for c in chunks_data]
    all_embeddings = []
    batch_size = 8  # very small batches
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  batch {i // batch_size + 1}/{(len(texts) + batch_size - 1) // batch_size}", flush=True)
        result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
        all_embeddings.extend(result.embeddings)

    print("step 5: attaching embeddings", flush=True)
    for chunk_data, emb in zip(chunks_data, all_embeddings):
        chunk_data["embedding"] = emb

    print("step 6: writing JSON", flush=True)
    with EMBEDDINGS_PATH.open("w") as f:
        json.dump(chunks_data, f)

    print(f"DONE: saved {len(chunks_data)} embeddings to {EMBEDDINGS_PATH}", flush=True)


if __name__ == "__main__":
    ingest()