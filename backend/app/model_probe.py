"""Teste de credencial/modelo de um serviço de IA (BYOK), direto no provedor.

Faz uma chamada mínima (5 tokens) e devolve (ok, detalhe). Substitui o
`/v1/test-model` do kernel-llm; não usa biblioteca de provedor, só HTTP.
"""

from __future__ import annotations

import httpx

BASES = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
PROMPT = "responda: ok"


async def probe_model(spec: dict, *, client: httpx.AsyncClient | None = None) -> tuple[bool, str]:
    """spec: {provider, model, api_key, api_base}. Nunca levanta."""
    provider = spec.get("provider") or ""
    model = (spec.get("model") or "").strip()
    key = spec.get("api_key") or ""
    base = (spec.get("api_base") or "").rstrip("/")
    own = client is None
    client = client or httpx.AsyncClient(timeout=45.0)
    try:
        if provider == "stub":
            return True, ""
        if provider == "gemini":
            name = model.removeprefix("gemini/").removeprefix("models/")
            r = await client.post(
                f"{base or GEMINI_BASE}/models/{name}:generateContent",
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
                    "generationConfig": {"maxOutputTokens": 5},
                },
            )
        elif provider == "anthropic":
            r = await client.post(
                f"{base or ANTHROPIC_BASE}/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={
                    "model": model,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": PROMPT}],
                },
            )
        elif provider in ("openai", "deepseek", "groq", "openai-compatible"):
            endpoint = base or BASES.get(provider)
            if not endpoint:
                return False, "informe a URL base (api_base) do serviço compatível"
            r = await client.post(
                f"{endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": PROMPT}],
                },
            )
        else:
            return False, f"provedor não suportado: {provider}"
        if r.status_code == 200:
            return True, ""
        detail = f"{r.status_code}: {r.text[:400]}"
        return False, detail.replace(key, "***") if key else detail
    except httpx.HTTPError as exc:
        return False, f"provedor inacessível: {type(exc).__name__}"
    finally:
        if own:
            await client.aclose()
