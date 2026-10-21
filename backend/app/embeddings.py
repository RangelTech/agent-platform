"""Embeddings de texto direto no provedor (sem kernel-llm).

Mantém o contrato do kernel antigo: 768 dimensões (coluna pgvector), mesmos modelos
(`gemini-embedding-001`, `text-embedding-3-small`) e o provedor `stub` determinístico
(sem semântica, mas palavras em comum dão similaridade alta; serve aos testes).
"""

from __future__ import annotations

import hashlib
import math

import httpx

DIMENSIONS = 768
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_BASE = "https://api.openai.com/v1"
BATCH = 32


class EmbeddingError(RuntimeError):
    pass


def _stub_vector(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for token in text.lower().split():
        index = int(hashlib.md5(token.encode()).hexdigest(), 16) % DIMENSIONS
        vector[index] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


async def _gemini(client: httpx.AsyncClient, spec: dict, texts: list[str]) -> list[list[float]]:
    model = (spec.get("model") or "gemini/gemini-embedding-001").removeprefix("gemini/")
    body = {
        "requests": [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": DIMENSIONS,
            }
            for t in texts
        ]
    }
    r = await client.post(
        f"{GEMINI_BASE}/models/{model}:batchEmbedContents",
        headers={"x-goog-api-key": spec.get("api_key") or ""},
        json=body,
    )
    if r.status_code != 200:
        raise EmbeddingError(f"gemini {r.status_code}: {r.text[:200]}")
    return [e["values"] for e in r.json()["embeddings"]]


async def _openai(client: httpx.AsyncClient, spec: dict, texts: list[str]) -> list[list[float]]:
    model = spec.get("model") or "text-embedding-3-small"
    base = (spec.get("api_base") or OPENAI_BASE).rstrip("/")
    body: dict = {"model": model, "input": texts}
    if "text-embedding-3" in model:
        body["dimensions"] = DIMENSIONS
    r = await client.post(
        f"{base}/embeddings",
        headers={"Authorization": f"Bearer {spec.get('api_key') or ''}"},
        json=body,
    )
    if r.status_code != 200:
        raise EmbeddingError(f"openai {r.status_code}: {r.text[:200]}")
    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


async def embed_texts(
    spec: dict, texts: list[str], *, client: httpx.AsyncClient | None = None
) -> list[list[float]]:
    """spec: {provider, model, api_key}. provider 'stub' é determinístico."""
    if not texts:
        return []
    provider = spec.get("provider") or "stub"
    if provider == "stub":
        return [_stub_vector(t) for t in texts]
    send = {"gemini": _gemini, "openai": _openai}.get(provider)
    if send is None:
        raise EmbeddingError(f"provedor de embedding não suportado: {provider}")
    out: list[list[float]] = []
    own = client is None
    client = client or httpx.AsyncClient(timeout=60.0)
    try:
        for i in range(0, len(texts), BATCH):
            out.extend(await send(client, spec, texts[i : i + BATCH]))
    finally:
        if own:
            await client.aclose()
    return out
