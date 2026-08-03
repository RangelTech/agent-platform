"""Prova que o QR Code do PIX é pagável — não que "um artefato foi criado".

Todo o QA anterior parava em "kind == image": o harness via um artefato de
imagem e dava verde. Isso não prova nada sobre pagar. Uma imagem preta de 1x1
passaria igual.

Aqui a prova é fechada de ponta a ponta:

1. o agente gera uma cobrança de R$ 0,01 numa conversa de verdade;
2. o artefato publicado é baixado pela API, como o navegador do usuário baixaria;
3. os bytes são conferidos como PNG e **o QR é decodificado com OpenCV**;
4. o texto decodificado é comparado ao copia-e-cola que o Mercado Pago devolve
   para aquele mesmo payment_id.

Se os quatro fecharem, o que está na tela do cliente é o código que o banco
dele vai cobrar — e não uma imagem qualquer. Ao final a cobrança é cancelada,
porque a credencial disponível é de produção.

Uso:
    MP_SECRET_FILE=.../mercado_pago.json LICITA_FIXTURES=... \
        python scripts/provar_qr_pix.py
"""

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa_conversa_licita import BACKEND, EMAIL, SENHA, TEMPLATE, enviar  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MP = "https://api.mercadopago.com"


def ler_token() -> str:
    caminho = os.environ.get("MP_SECRET_FILE", "")
    if not caminho:
        raise SystemExit("defina MP_SECRET_FILE com o arquivo do Mercado Pago")
    with open(caminho, encoding="utf-8-sig") as arquivo:
        return json.load(arquivo)["access_token"]


def decodificar(png: bytes) -> str:
    """Lê o QR da imagem como um leitor de banco leria."""
    import cv2  # só este script depende disto; não vai para o runtime
    import numpy as np

    imagem = cv2.imdecode(np.frombuffer(png, dtype="uint8"), cv2.IMREAD_GRAYSCALE)
    if imagem is None:
        return ""
    texto, _pontos, _qr = cv2.QRCodeDetector().detectAndDecode(imagem)
    return texto or ""


def main() -> int:
    token_mp = ler_token()
    falhas = []

    with httpx.Client(base_url=BACKEND, timeout=600.0) as client:
        sessao = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": SENHA}
        ).json()["token"]
        cabecalho = {"Authorization": f"Bearer {sessao}"}
        templates = client.get("/api/templates", headers=cabecalho).json()
        tpl = next(t for t in templates if t["name"] == TEMPLATE)["id"]

        print("gerando a cobrança de R$ 0,01...")
        primeiro = enviar(
            client,
            sessao,
            "Gere uma cobrança PIX de R$ 0,01 para o cliente Porto Velho, "
            "referência PROVA-QR. Confirmo desde já, pode gerar.",
            tpl,
        )
        chat_id = primeiro.get("chat_id")
        artefatos = [a for a in primeiro["artifacts"] if a.get("kind") == "image"]
        if not artefatos:
            print("FALHA: nenhum artefato de imagem foi publicado no turno da cobrança")
            return 1

        artifact_id = artefatos[0]["artifact_id"]
        print(f"  artefato: {artifact_id}")

        # O ID do pagamento vem da própria plataforma, não do texto do modelo:
        # ler o número da resposta seria confiar em quem se quer verificar. A
        # rota de cobranças não expõe chat_id, então a referência do pedido é o
        # que liga esta conversa ao registro.
        cobrancas = client.get("/api/payments/charges", headers=cabecalho).json()
        cobrancas = cobrancas.get("items", cobrancas) if isinstance(cobrancas, dict) else cobrancas
        desta = [c for c in cobrancas if c.get("reference_id") == "PROVA-QR"]
        if not desta:
            print("FALHA: a cobrança não foi registrada na plataforma")
            return 1
        payment_id = desta[0]["external_id"]
        print(f"  payment_id: {payment_id}")

        # Como o navegador do usuário baixaria (307 -> storage assinado).
        png = client.get(
            f"/api/artifacts/{artifact_id}/download",
            headers=cabecalho,
            follow_redirects=True,
        ).content
        print(f"  imagem: {len(png)} bytes")

        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            falhas.append("o artefato baixado não é um PNG")

        lido = decodificar(png)
        if not lido:
            falhas.append("o QR Code não foi decodificado a partir da imagem")
        print(f"  QR decodificado: {lido[:60]}..." if lido else "  QR ilegível")

        pagamento = httpx.get(
            f"{MP}/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {token_mp}"},
            timeout=30.0,
        ).json()
        no_gateway = (
            (pagamento.get("point_of_interaction") or {}).get("transaction_data") or {}
        ).get("qr_code") or ""
        if lido and lido != no_gateway:
            falhas.append("o QR da tela não é o código que o gateway cobra")
        elif lido:
            print("  confere com o copia-e-cola do gateway")

        # Consultar de novo não pode criar cobrança: é o defeito que motivou a
        # correção em check_payment_status.
        antes = len(cobrancas)
        segundo = enviar(
            client, sessao, "Me mostre o QR Code dessa cobrança de novo.", tpl, chat_id
        )
        usadas = [t.get("tool") or t.get("name") for t in segundo["tools"]]
        depois = client.get("/api/payments/charges", headers=cabecalho).json()
        depois = depois.get("items", depois) if isinstance(depois, dict) else depois
        print(f"  reexibir usou: {usadas} | cobranças: {antes} -> {len(depois)}")
        if "generate_pix_charge" in usadas or len(depois) != antes:
            falhas.append("pedir o QR de novo criou outra cobrança pagável")

        print("cancelando...")
        resposta = client.put(
            f"{MP}/v1/payments/{payment_id}",
            json={"status": "cancelled"},
            headers={"Authorization": f"Bearer {token_mp}"},
        )
        print(f"  {payment_id} -> {resposta.json().get('status', resposta.status_code)}")

    if falhas:
        print("\nFALHOU:")
        for f in falhas:
            print(f"  - {f}")
        return 1
    print("\nQR Code do PIX provado: imagem na tela == código que o banco cobra.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
