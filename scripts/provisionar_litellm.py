"""Provisiona o Team LiteLLM de um tenant (substitui `provisionar_router.py`).

Diferença central em relação ao 9Router: não existe mais infra por tenant.
O LiteLLM é 1 instância só, compartilhada (Cloud Run) — provisionar um tenant
novo é só criar 1 Team + 2 virtual keys, 3 chamadas HTTP, sem SSH, sem DNS,
sem container, sem espera de health check de infra nova subindo.

O Team nasce sem nenhum `model` — o tenant conecta a própria conta de
provider DEPOIS, pela tela "Contas de IA" (isso ainda usa o fluxo do
9Router hoje; a rota de contas ganhar o branch pro LiteLLM é passo separado,
ver `memoria.md`). As 2 virtual keys (bridge/kernel e AI Assist, nunca a
mesma — infra-04 seção 2c) existem desde já e herdam os models que o Team
for ganhando depois.

Uso:
    python scripts/provisionar_litellm.py <tenant_key>
    python scripts/provisionar_litellm.py <tenant_key> --remover
"""

import argparse
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app import litellm_client  # noqa: E402

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:14000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

BACKEND = os.environ.get("HOMOLOG_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")


async def provisionar(tenant_key: str) -> dict:
    if not LITELLM_MASTER_KEY:
        raise SystemExit("LITELLM_MASTER_KEY ausente — sem ela não dá pra falar com o LiteLLM")

    team = await litellm_client.create_team(LITELLM_BASE_URL, LITELLM_MASTER_KEY, team_alias=tenant_key)
    team_id = team["team_id"]

    # Fallback local (produto-07 seção 8a) liberado pra todo tenant desde o
    # provisionamento -- achado real 23/08/2026: sem isso, uma virtual key de
    # tenant real leva 403 (`team_model_access_denied`) ao tentar o fallback,
    # mesmo com o deployment registrado e funcionando pra quem tem master key.
    # `add_model_to_team` é idempotente, seguro de chamar mesmo antes do Team
    # ter algum outro model_name.
    await litellm_client.add_model_to_team(
        LITELLM_BASE_URL, LITELLM_MASTER_KEY, team_id=team_id, model_name="ragentes-local-fallback"
    )

    bridge_key = await litellm_client.generate_key(
        LITELLM_BASE_URL, LITELLM_MASTER_KEY, team_id=team_id, key_alias=f"{tenant_key}-bridge"
    )
    ai_assist_key = await litellm_client.generate_key(
        LITELLM_BASE_URL, LITELLM_MASTER_KEY, team_id=team_id, key_alias=f"{tenant_key}-ai-assist"
    )

    return {"team_id": team_id, "bridge_key": bridge_key, "ai_assist_key": ai_assist_key}


def registrar_na_plataforma(tenant_key: str, dados: dict) -> None:
    with httpx.Client(base_url=BACKEND, timeout=60.0) as client:
        token = client.post(
            "/api/auth/login", json={"email": MASTER_EMAIL, "password": MASTER_PASSWORD}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        tenants = client.get("/api/tenants", headers=headers).json()
        tenant = next((t for t in tenants if t["tenant_key"] == tenant_key), None)
        if tenant is None:
            raise SystemExit(f"tenant '{tenant_key}' não existe na plataforma")
        resposta = client.put(
            "/api/ai-router/instancias-litellm",
            headers=headers,
            json={
                "tenant_id": tenant["id"],
                "litellm_team_id": dados["team_id"],
                "bridge_key": dados["bridge_key"],
                "ai_assist_key": dados["ai_assist_key"],
            },
        )
        resposta.raise_for_status()


async def remover(tenant_key: str) -> None:
    """Não apaga nada no LiteLLM por padrão — só desativa na plataforma.

    Diferente do 9Router (onde remover = `docker rm`), o Team/keys do
    LiteLLM não custam nada parados; apagar de vez é ação separada e
    deliberada (`litellm_client.delete_team`), não o caminho padrão daqui."""
    print(f"tenant '{tenant_key}': remoção de Team LiteLLM não é automática por padrão")
    print("use litellm_client.delete_team(...) direto se quiser apagar de vez")


async def _main_async() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_key")
    parser.add_argument("--remover", action="store_true")
    args = parser.parse_args()

    if args.remover:
        await remover(args.tenant_key)
        return

    dados = await provisionar(args.tenant_key)
    registrar_na_plataforma(args.tenant_key, dados)
    # As keys aparecem uma vez: a partir daqui vivem cifradas na plataforma.
    print(json.dumps({"team_id": dados["team_id"]}, indent=2))
    print("Team provisionado e registrado na plataforma")


def main() -> None:
    import asyncio

    asyncio.run(_main_async())


if __name__ == "__main__":
    sys.exit(main())
