"""Cancela no Mercado Pago as cobranças que o QA criou.

Existe porque a credencial disponível é de produção: cobrança de teste que fica
pendente é cobrança pagável esquecida na conta de alguém. Rodar isto faz parte
do teste, não é limpeza opcional.

A plataforma não expõe cancelamento — ela registra a cobrança e consulta status.
O cancelamento vai direto na API do Mercado Pago, que é quem manda no ciclo de
vida do pagamento.

Só cancela o que ainda está pendente: pagamento aprovado não se cancela (se
alguém pagou, o certo é estornar, e isso é decisão de quem recebeu, não deste
script).

Uso:
    MP_SECRET_FILE=.../mercado_pago.json python scripts/cancelar_cobrancas_qa.py
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
MP = "https://api.mercadopago.com"


def ler_token() -> str:
    caminho = os.environ.get("MP_SECRET_FILE", "")
    if not caminho:
        raise SystemExit("defina MP_SECRET_FILE com o arquivo do Mercado Pago")
    with open(caminho, encoding="utf-8-sig") as arquivo:
        return json.load(arquivo)["access_token"]


def main() -> int:
    token_mp = ler_token()

    with httpx.Client(timeout=120.0) as client:
        sessao = client.post(
            f"{BACKEND}/api/auth/login", json={"email": EMAIL, "password": SENHA}
        ).json()["token"]
        cobrancas = client.get(
            f"{BACKEND}/api/payments/charges",
            headers={"Authorization": f"Bearer {sessao}"},
        ).json()
        cobrancas = (
            cobrancas.get("items", cobrancas)
            if isinstance(cobrancas, dict)
            else cobrancas
        )

        pendentes = [c for c in cobrancas if c.get("status") == "pending"]
        print(f"cobranças registradas: {len(cobrancas)} | pendentes: {len(pendentes)}")

        cancelei = 0
        for cobranca in pendentes:
            externo = cobranca.get("external_id")
            if not externo:
                continue
            resposta = client.put(
                f"{MP}/v1/payments/{externo}",
                json={"status": "cancelled"},
                headers={"Authorization": f"Bearer {token_mp}"},
            )
            estado = (
                resposta.json().get("status")
                if resposta.status_code < 400
                else f"HTTP {resposta.status_code}"
            )
            print(f"  {externo} R$ {cobranca.get('amount')} -> {estado}")
            cancelei += resposta.status_code < 400

        pagas = [c for c in cobrancas if c.get("status") == "paid"]
        if pagas:
            print(f"ATENCAO: {len(pagas)} cobranca(s) ja paga(s) — estorno e decisao sua:")
            for c in pagas:
                print(f"  {c.get('external_id')} R$ {c.get('amount')}")

        print(f"canceladas: {cancelei}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
