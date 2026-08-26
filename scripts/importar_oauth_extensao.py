"""Ponte pontual (produto-08 §6): copia credenciais OAuth de assinatura
(Claude Code/Codex CLI) já capturadas pela extensão RAtende Connector
(produto-15, `tenant_unofficial_connections`) pra `tenant_ai_accounts`,
onde o LiteLLM/`_criar_combo_litellm` realmente lê.

Não é rota nova nem mecanismo permanente -- é um script de import de
1 uso, porque os dois sistemas nasceram separados na mesma mega-spec
sem se falar (produto-15 é genérico, produto-08 é quem sabe virar
deployment LiteLLM). Ver produto-08 §6 pra contexto completo.

Uso:
    python scripts/importar_oauth_extensao.py <tenant_key>
    python scripts/importar_oauth_extensao.py <tenant_key> --dry-run
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import get_connection  # noqa: E402

# tenant_unofficial_connections (produto-15) -> tenant_ai_accounts (produto-08)
MAPA_PROVIDER = {
    "claude_code": "claude",
    "codex_cli": "codex",
}


def _buscar_tenant_id(tenant_key: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM tenants WHERE tenant_key = %s", (tenant_key,)
        ).fetchone()
    if row is None:
        raise SystemExit(f"tenant '{tenant_key}' não existe")
    return str(row["id"])


def _conexoes_oauth_pendentes(tenant_id: str) -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM tenant_unofficial_connections
                WHERE tenant_id = %s AND is_active
                  AND provider IN ('claude_code', 'codex_cli')
                ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()


def _ja_importada(tenant_id: str, provider_destino: str, origem_id: str) -> bool:
    """Idempotência: cada linha de tenant_unofficial_connections só deve virar
    1 linha em tenant_ai_accounts -- guarda o id de origem em provider_data
    pra não duplicar se o script rodar de novo."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT provider_data FROM tenant_ai_accounts
                WHERE tenant_id = %s AND provider = %s""",
            (tenant_id, provider_destino),
        ).fetchall()
    return any((r["provider_data"] or {}).get("importado_de") == origem_id for r in rows)


def _registrar_conta_ai(tenant_id: str, provider_destino: str, label: str, tokens: dict, origem_id: str) -> str:
    expira_em = None
    if tokens.get("expires_in") is not None:
        expira_em = datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))

    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO tenant_ai_accounts
                   (tenant_id, provider, auth_type, label, access_token_encrypted,
                    refresh_token_encrypted, token_expires_at, provider_data)
               VALUES (%s, %s, 'oauth', %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                tenant_id,
                provider_destino,
                label,
                encrypt(tokens["access_token"]),
                encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None,
                expira_em,
                json.dumps({"importado_de": origem_id, "origem": "ratende_connector_extensao"}),
            ),
        ).fetchone()
    return str(linha["id"])


def importar(tenant_key: str, dry_run: bool) -> None:
    tenant_id = _buscar_tenant_id(tenant_key)
    conexoes = _conexoes_oauth_pendentes(tenant_id)
    if not conexoes:
        print(f"tenant '{tenant_key}': nenhuma conexão claude_code/codex_cli ativa em tenant_unofficial_connections")
        return

    for conexao in conexoes:
        provider_origem = conexao["provider"]
        provider_destino = MAPA_PROVIDER[provider_origem]
        origem_id = str(conexao["id"])

        if _ja_importada(tenant_id, provider_destino, origem_id):
            print(f"[skip] {provider_origem} ({origem_id}) já foi importado antes")
            continue

        tokens = json.loads(decrypt(conexao["cookies_encrypted"]))
        if "access_token" not in tokens:
            print(f"[skip] {provider_origem} ({origem_id}): não tem access_token (não é conexão OAuth?)")
            continue

        if dry_run:
            print(f"[dry-run] importaria {provider_origem} -> {provider_destino}, label='{conexao['label']}'")
            continue

        novo_id = _registrar_conta_ai(tenant_id, provider_destino, conexao["label"], tokens, origem_id)
        print(f"[ok] {provider_origem} -> tenant_ai_accounts.{provider_destino} (id={novo_id})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_key")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    importar(args.tenant_key, args.dry_run)


if __name__ == "__main__":
    main()
