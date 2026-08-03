"""Cadastra a credencial do Mercado Pago no tenant de QA.

O token NUNCA aparece no código nem na saída: vem de um arquivo apontado por
`MP_SECRET_FILE`, e o que se imprime é só o sufixo.

**A credencial disponível é de produção** (prefixo `APP_USR-`; sandbox começa
com `TEST-`). Logo, cobrança criada aqui é pagável de verdade. O QA usa R$ 0,01
e cancela tudo ao final com `scripts/cancelar_cobrancas_qa.py`.

Uso:
    MP_SECRET_FILE=.../mercado_pago.json python scripts/credencial_pix_qa.py
"""

import json
import os
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = os.environ.get(
    "REGRESSAO_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app"
)
EMAIL = os.environ.get("LICITA_EMAIL", "dono@licita.com")
SENHA = os.environ.get("LICITA_SENHA", "licita-senha-forte-123")


def ler_token() -> str:
    caminho = os.environ.get("MP_SECRET_FILE", "")
    if not caminho:
        raise SystemExit("defina MP_SECRET_FILE com o arquivo do Mercado Pago")
    # utf-8-sig: o arquivo de origem vem com BOM.
    with open(caminho, encoding="utf-8-sig") as arquivo:
        return json.load(arquivo)["access_token"]


def main() -> int:
    token_mp = ler_token()
    producao = token_mp.startswith("APP_USR-")

    with httpx.Client(base_url=BACKEND, timeout=120.0) as client:
        sessao = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": SENHA}
        ).json()["token"]
        cabecalho = {"Authorization": f"Bearer {sessao}"}

        resposta = client.put(
            "/api/payments/credentials",
            json={
                "access_token": token_mp,
                # `sandbox` precisa refletir o token de verdade: marcar uma
                # credencial de produção como sandbox esconde no registro que a
                # cobrança é real.
                "sandbox": not producao,
                "is_active": True,
            },
            headers=cabecalho,
        )
        if resposta.status_code >= 400:
            print("erro:", resposta.status_code, resposta.text[:300])
            return 1

        corpo = resposta.json()
        print(
            f"credencial cadastrada | token ...{token_mp[-4:]} | "
            f"sandbox={corpo.get('sandbox')} | ativa={corpo.get('is_active')}"
        )
        if producao:
            print("ATENCAO: token de PRODUCAO — cobranca gerada aqui e pagavel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
