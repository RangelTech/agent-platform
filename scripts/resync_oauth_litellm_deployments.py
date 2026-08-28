"""Resincroniza deployments do LiteLLM cujo `api_key` vem de conta OAuth
(assinatura Claude/Codex) -- gap documentado em produto-12: `_chave_da_conta`
(ai_router.py) só renova o token na hora de CRIAR o combo; se o combo fica
parado (não é recriado) e o access_token expira de novo (Claude Code OAuth
dura ~1h), o deployment do LiteLLM fica com uma key morta até alguém salvar
o combo de novo -- sem job periódico, quebra sozinho a cada ~1h.

27/08/2026: rodado manualmente via /loop enquanto o job periódico de verdade
(Cloud Scheduler batendo nisto, ou equivalente) não é decidido/implementado
com o dono -- ver produto-12 e produto-08 §6.

Uso:
    python scripts/resync_oauth_litellm_deployments.py [--dry-run]

Pra cada `tenant_ai_combos` cujo modelo aponta pra uma conta OAuth
(auth_type='oauth', sem api_key própria): renova o token se precisar
(mesma lógica de `_chave_da_conta`), apaga o(s) deployment(s) LiteLLM
existentes daquele `router_combo_name`+provider e recria com a key fresca.
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import litellm_client, oauth_engine, router_catalog  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import get_connection  # noqa: E402
from app.installation_secrets import resolver as resolver_segredo  # noqa: E402
from app.routes.ai_router import _provider_model_e_extras  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("resync")


async def _chave_fresca(conta: dict) -> str:
    if conta["api_key_encrypted"]:
        return decrypt(conta["api_key_encrypted"])

    expira_em = conta["token_expires_at"]
    precisa_renovar = expira_em is None or datetime.now(UTC) >= expira_em - timedelta(seconds=60)
    if not precisa_renovar:
        return decrypt(conta["access_token_encrypted"])
    if not conta["refresh_token_encrypted"]:
        raise RuntimeError(f"conta {conta['id']} ({conta['provider']}) sem refresh_token, não dá pra renovar")

    resultado = await oauth_engine.renovar(
        conta["provider"], decrypt(conta["refresh_token_encrypted"]), conta["provider_data"]
    )
    novo_expira_em = (
        datetime.now(UTC) + timedelta(seconds=resultado.expires_in) if resultado.expires_in else None
    )
    novo_refresh = encrypt(resultado.refresh_token) if resultado.refresh_token else conta["refresh_token_encrypted"]
    with get_connection() as conn:
        conn.execute(
            """UPDATE tenant_ai_accounts
                   SET access_token_encrypted = %s, refresh_token_encrypted = %s,
                       token_expires_at = %s, token_last_refresh_error = NULL
                 WHERE id = %s""",
            (encrypt(resultado.access_token), novo_refresh, novo_expira_em, conta["id"]),
        )
    log.info("  renovado, novo token_expires_at=%s", novo_expira_em)
    return resultado.access_token


async def main(dry_run: bool) -> None:
    base_url = resolver_segredo("LITELLM_BASE_URL")
    master_key = resolver_segredo("LITELLM_MASTER_KEY")

    with get_connection() as conn:
        combos = conn.execute(
            "SELECT id, tenant_id, router_combo_name, models FROM tenant_ai_combos"
        ).fetchall()

    for combo in combos:
        nome_grupo = combo["router_combo_name"]
        modelos = combo["models"] if isinstance(combo["models"], list) else []
        with get_connection() as conn:
            contas = conn.execute(
                """SELECT * FROM tenant_ai_accounts
                       WHERE tenant_id = %s AND is_active AND auth_type = 'oauth'
                             AND access_token_encrypted IS NOT NULL""",
                (combo["tenant_id"],),
            ).fetchall()
        if not contas:
            continue
        conta_por_provider = {c["provider"]: c for c in contas}

        deployments_atuais = await litellm_client.list_deployments(base_url, master_key, model_name=nome_grupo)

        for modelo in modelos:
            provider = router_catalog.provedor_de_modelo(modelo)
            conta = conta_por_provider.get(provider)
            if conta is None:
                continue
            log.info("[%s] provider=%s conta=%s", nome_grupo, provider, conta["id"])
            try:
                chave = await _chave_fresca(conta)
            except Exception as exc:  # noqa: BLE001
                log.error("  FALHOU renovar: %s", exc)
                continue

            provider_model, extras = _provider_model_e_extras(modelo, conta)
            velhos = [
                d for d in deployments_atuais
                if d.get("litellm_params", {}).get("model") == provider_model
            ]
            if dry_run:
                log.info(
                    "  [dry-run] apagaria %d deployment(s) velho(s), recriaria com key nova",
                    len(velhos),
                )
                continue

            for d in velhos:
                dep_id = d.get("model_info", {}).get("id")
                if dep_id:
                    await litellm_client.delete_deployment(base_url, master_key, deployment_id=dep_id)
                    log.info("  apagado deployment velho %s", dep_id)

            await litellm_client.create_deployment(
                base_url,
                master_key,
                model_name=nome_grupo,
                provider_model=provider_model,
                api_key=chave,
                tenant_id=str(combo["tenant_id"]),
                **extras,
            )
            log.info("  recriado com key fresca")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
