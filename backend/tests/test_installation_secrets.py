"""Segredos de instalação: cadastro pela tela, cifrado, propagado ao Chatwoot.

O objetivo declarado é sair do Secret Manager: chave do app da Meta, Serper e
S3 deixam de morar em variável de ambiente por serviço e passam a viver aqui,
cifradas, editáveis sem redeploy.

O que estes testes protegem é o que separa isso de um cofre de brinquedo: o
valor não volta para ninguém, o bootstrap é recusado (guardar no banco a chave
que abre o banco é impossível por construção), e o estado da propagação é
visível em vez de presumido.
"""

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cofre_limpo():
    """A tabela é da instalação inteira — não há tenant para o TRUNCATE geral do
    conftest levar junto. Sem limpar, `version` acumula entre testes e o que
    falha é a asserção, não o código."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("DELETE FROM installation_secrets")
    # A rota de serviço exige o token da ponte; em teste ele costuma vir vazio,
    # e token vazio recusa qualquer chamada (inclusive a legítima).
    anterior = settings.bridge_admin_token
    settings.bridge_admin_token = "token-de-servico-de-teste"
    yield
    settings.bridge_admin_token = anterior


def test_o_valor_nunca_volta_nem_para_o_master(client, master_token):
    cabecalho = auth(master_token)
    criado = client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "FB_APP_SECRET", "value": "segredo-do-app", "targets": ["chatwoot"]},
    )
    assert criado.status_code == 201, criado.text
    assert "segredo-do-app" not in criado.text

    listagem = client.get("/api/installation-secrets", headers=cabecalho)
    assert "segredo-do-app" not in listagem.text
    assert any(s["name"] == "FB_APP_SECRET" for s in listagem.json())


def test_bootstrap_e_recusado(client, master_token):
    """Aceitar o cadastro criaria um valor que nada lê — e a falsa sensação de
    que o Secret Manager já pode ser desligado."""
    cabecalho = auth(master_token)
    for nome in ("DATABASE_URL", "ENCRYPTION_KEY"):
        resposta = client.post(
            "/api/installation-secrets",
            headers=cabecalho,
            json={"name": nome, "value": "x"},
        )
        assert resposta.status_code == 400
        assert "bootstrap" in resposta.json()["detail"]


def test_regravar_sobe_a_versao_e_reabre_a_propagacao(client, master_token):
    """Versão monotônica é o que deixa o sincronizador saber se o destino está
    atualizado sem comparar valores em claro."""
    cabecalho = auth(master_token)
    primeiro = client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "FB_APP_ID", "value": "111", "targets": ["chatwoot"]},
    ).json()
    assert primeiro["version"] == 1
    assert [s["status"] for s in primeiro["sync"]] == ["pending"]

    segundo = client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "FB_APP_ID", "value": "222", "targets": ["chatwoot"]},
    ).json()
    assert segundo["version"] == 2
    assert [s["status"] for s in segundo["sync"]] == ["pending"]


def test_destino_desconhecido_e_recusado(client, master_token):
    cabecalho = auth(master_token)
    resposta = client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "SERPER_API_KEY", "value": "x", "targets": ["tiktok"]},
    )
    assert resposta.status_code == 400


def test_o_job_do_chatwoot_le_o_valor_e_confirma(client, master_token):
    """O único lugar em que o valor sai em claro é este, para uma máquina
    autenticada — e a confirmação é o que separa "entreguei" de "está valendo"."""
    cabecalho = auth(master_token)
    client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "IG_VERIFY_TOKEN", "value": "token-da-meta", "targets": ["chatwoot"]},
    )

    servico = {"Authorization": f"Bearer {settings.bridge_admin_token}"}
    valores = client.get("/api/installation-secrets/for-chatwoot", headers=servico)
    assert valores.status_code == 200
    assert valores.json()["IG_VERIFY_TOKEN"] == "token-da-meta"

    ack = client.post(
        "/api/installation-secrets/chatwoot-ack",
        headers=servico,
        json={"applied": ["IG_VERIFY_TOKEN"], "skipped": []},
    )
    assert ack.status_code == 200

    # Buscar pelo nome, e não pela posição: a tabela é da instalação inteira e
    # guarda o que os outros testes cadastraram.
    depois = client.get("/api/installation-secrets", headers=cabecalho).json()
    sincronia = next(s for s in depois if s["name"] == "IG_VERIFY_TOKEN")["sync"][0]
    assert sincronia["status"] == "ok"
    assert sincronia["synced_version"] == 1


def test_config_travada_no_chatwoot_vira_erro_visivel(client, master_token):
    """`locked` é o operador do Chatwoot dizendo que decide aquele valor. A
    plataforma respeita — e mostra que não aplicou, em vez de fingir sucesso."""
    cabecalho = auth(master_token)
    client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "FB_VERIFY_TOKEN", "value": "x", "targets": ["chatwoot"]},
    )
    servico = {"Authorization": f"Bearer {settings.bridge_admin_token}"}
    client.post(
        "/api/installation-secrets/chatwoot-ack",
        headers=servico,
        json={"applied": [], "skipped": ["FB_VERIFY_TOKEN"]},
    )

    listagem = client.get("/api/installation-secrets", headers=cabecalho).json()
    sincronia = next(s for s in listagem if s["name"] == "FB_VERIFY_TOKEN")["sync"][0]
    assert sincronia["status"] == "error"
    assert "locked" in sincronia["detail"]


def test_rota_de_servico_recusa_token_errado(client):
    resposta = client.get(
        "/api/installation-secrets/for-chatwoot",
        headers={"Authorization": "Bearer token-errado"},
    )
    assert resposta.status_code == 401


def test_o_resolvedor_prefere_o_banco_ao_ambiente(client, master_token, monkeypatch):
    """Enquanto o ambiente vencer, um valor velho esquecido numa revisão do
    Cloud Run continua mandando e ninguém entende por que a chave nova não
    pegou."""
    from app.installation_secrets import resolver

    cabecalho = auth(master_token)
    monkeypatch.setenv("SERPER_API_KEY", "valor-velho-do-env")
    client.post(
        "/api/installation-secrets",
        headers=cabecalho,
        json={"name": "SERPER_API_KEY", "value": "valor-novo-do-banco"},
    )

    assert resolver("SERPER_API_KEY") == "valor-novo-do-banco"


def test_o_resolvedor_cai_no_ambiente_quando_o_banco_nao_tem(client, monkeypatch):
    from app.installation_secrets import resolver

    monkeypatch.setenv("CHAVE_QUE_SO_EXISTE_NO_ENV", "do-ambiente")
    assert resolver("CHAVE_QUE_SO_EXISTE_NO_ENV") == "do-ambiente"


def test_o_resolvedor_nunca_le_bootstrap_do_banco(client, monkeypatch):
    """DATABASE_URL no banco seria lido tarde demais para servir de qualquer
    coisa; garantir que ele vem do ambiente evita um caminho que só confunde."""
    from app.installation_secrets import resolver

    monkeypatch.setenv("ENCRYPTION_KEY", "do-ambiente")
    assert resolver("ENCRYPTION_KEY") == "do-ambiente"
