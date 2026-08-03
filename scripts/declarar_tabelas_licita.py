"""Declara na fonte `lake_mindlab` quais tabelas o template usa.

O dataset tem 180 tabelas e o template usa 15. Sem a declaração, o catálogo sai
em ordem alfabética com coluna só nas 50 primeiras — e as tabelas do SIOPE, que
são o assunto do template, caem na posição 75 em diante. O modelo então gasta o
turno perguntando o schema delas ao INFORMATION_SCHEMA: medido, 54 consultas e
mais de dez minutos num único turno, sem resposta.

Uso:
    python scripts/declarar_tabelas_licita.py
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
FONTE = "lake_mindlab"

TABELAS = [
    "obt_fnde_siope_dados_gerais_municipio_ano",
    "obt_fnde_siope_despesa_educacao_municipio_ano",
    "obt_fnde_siope_despesa_funcao_municipio_ano",
    "obt_fnde_siope_indicador_municipio_ano",
    "obt_fnde_siope_receita_municipio_ano",
    "obt_ibge_municipio",
    "obt_ibge_uf",
    "obt_pncp_atas",
    "obt_pncp_contratacoes",
    "obt_pncp_contratos",
    "obt_pncp_editais_semantico",
    "obt_pncp_pca",
    "obt_pncp_pca_itens",
    "obt_rreo_siope_municipio_ano",
    "obt_rreo_siope_municipio_bimestre",
]


def main() -> int:
    with httpx.Client(base_url=BACKEND, timeout=120.0) as client:
        token = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": SENHA}
        ).json()["token"]
        cabecalho = {"Authorization": f"Bearer {token}"}

        fontes = client.get("/api/datasources", headers=cabecalho).json()
        fontes = fontes.get("items", fontes) if isinstance(fontes, dict) else fontes
        fonte = next((f for f in fontes if f["name"] == FONTE), None)
        if fonte is None:
            print(f"fonte {FONTE} não encontrada")
            return 1

        # Mesclar, nunca substituir: `project` e `dataset` vivem no mesmo campo.
        config = dict(fonte.get("config") or {})
        config["tables"] = TABELAS

        resposta = client.put(
            f"/api/datasources/{fonte['id']}", json={"config": config}, headers=cabecalho
        )
        if resposta.status_code >= 400:
            print("erro:", resposta.status_code, resposta.text[:400])
            return 1
        print(f"fonte {FONTE}: {len(TABELAS)} tabelas declaradas")
        print(json.dumps(resposta.json().get("config", {}), ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
