import json
import uuid

import pytest
from app import credential_env, oauth_engine
from app.config import settings
from app.db import get_connection
from tests.conftest import auth

GCP_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "sa@p.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    }
)
SSH_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"


# ---------------------------------------------------------------- unidade (sem banco)


def test_url_de_banco_com_senha_codificada_e_ssl():
    ds = {
        "kind": "postgresql",
        "config": {
            "host": "db.exemplo.com",
            "port": 5432,
            "database": "loja",
            "user": "app",
            "ssl": True,
        },
        "secret": "p@ss:w/rd",
    }
    assert credential_env.database_url(ds) == (
        "postgresql://app:p%40ss%3Aw%2Frd@db.exemplo.com:5432/loja?sslmode=require"
    )


def test_url_de_banco_por_tipo():
    mongo = {"kind": "mongodb", "config": {"host": "m", "port": 27017, "user": "u"}, "secret": "s"}
    assert credential_env.database_url(mongo) == "mongodb://u:s@m:27017"
    sqlite = {"kind": "sqlite", "config": {"path": "/d/a.db"}}
    assert credential_env.database_url(sqlite) == "sqlite:////d/a.db"
    assert credential_env.database_url({"kind": "bigquery", "config": {"project": "p"}}) is None


def test_derive_expoe_ssh_gcp_banco_email_e_oauth():
    rows = [
        {"name": "TOKEN_X", "value_encrypted": "v1", "kind": "secret", "meta": {}},
        {
            "name": "SSH_DO_SERVIDOR",
            "value_encrypted": "chave",
            "kind": "ssh",
            "meta": {"host": "10.0.0.5", "port": 2222, "user": "root"},
        },
        {"name": "GCP_SA_JSON", "value_encrypted": "{}", "kind": "gcp", "meta": {}},
    ]
    email = {
        "label": "Contato",
        "email_address": "a@b.com",
        "username": "a",
        "password": "pw",
        "smtp_host": "s",
        "smtp_port": 587,
        "imap_host": "i",
        "imap_port": 993,
    }
    out = credential_env.derive(
        rows,
        [{"name": "loja pg", "kind": "postgresql", "config": {"host": "h"}, "secret": None}],
        [email],
        [{"label": "Dr. Fulano", "access_token": "gtok", "email_address": "f@x.com"}],
        [{"label": "Outlook", "access_token": "mtok", "email_address": "o@x.com"}],
        decrypt=lambda v: v,
    )
    assert out["TOKEN_X"] == "v1"
    assert out["SSH_DO_SERVIDOR"] == "chave"
    assert (out["SSH_DO_SERVIDOR_HOST"], out["SSH_DO_SERVIDOR_PORT"]) == ("10.0.0.5", "2222")
    assert out["SSH_DO_SERVIDOR_USER"] == "root"
    assert out["GCP_SA_JSON"] == "{}"
    assert out["DB_LOJA_PG_URL"] == "postgresql://h"
    assert out["EMAIL_CONTATO_PASSWORD"] == "pw"
    assert out["GOOGLE_DR_FULANO_TOKEN"] == "gtok"
    assert out["MICROSOFT_OUTLOOK_TOKEN"] == "mtok"


# ---------------------------------------------------------------- integração


@pytest.mark.integration
def test_ssh_vira_segredo_nomeado_sem_devolver_a_chave(client, tenant_admin):
    h = auth(tenant_admin["token"])
    r = client.post(
        "/api/secrets",
        json={
            "name": "do servidor",
            "kind": "ssh",
            "value": SSH_KEY,
            "meta": {"host": "10.1.1.1", "user": "deploy", "lixo": "x"},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "SSH_DO_SERVIDOR"
    assert body["meta"] == {"host": "10.1.1.1", "port": 22, "user": "deploy"}
    assert "PRIVATE KEY" not in r.text
    assert "PRIVATE KEY" not in client.get("/api/secrets", headers=h).text


@pytest.mark.integration
def test_ssh_exige_chave_host_e_usuario(client, tenant_admin):
    h = auth(tenant_admin["token"])
    sem_chave = client.post(
        "/api/secrets",
        headers=h,
        json={"name": "a", "kind": "ssh", "value": "senha", "meta": {"host": "h", "user": "u"}},
    )
    sem_host = client.post(
        "/api/secrets",
        headers=h,
        json={"name": "b", "kind": "ssh", "value": SSH_KEY, "meta": {"user": "u"}},
    )
    assert sem_chave.status_code == 400
    assert sem_host.status_code == 400


@pytest.mark.integration
def test_gcp_valida_json_e_usa_nome_padrao(client, tenant_admin):
    h = auth(tenant_admin["token"])
    ruim = client.post(
        "/api/secrets", headers=h, json={"name": "sa", "kind": "gcp", "value": "{nao json"}
    )
    sem_chave = client.post(
        "/api/secrets", headers=h, json={"name": "sa", "kind": "gcp", "value": '{"a": 1}'}
    )
    ok = client.post(
        "/api/secrets", headers=h, json={"name": "sa", "kind": "gcp", "value": GCP_JSON}
    )
    assert (ruim.status_code, sem_chave.status_code) == (400, 400)
    assert ok.status_code == 201, ok.text
    assert ok.json()["name"] == "GCP_SA_JSON"
    assert "private_key" not in ok.text


@pytest.mark.integration
def test_token_mostra_so_o_final_e_substituir_troca_o_valor(client, tenant_admin):
    h = auth(tenant_admin["token"])
    nome = f"TOK_{uuid.uuid4().hex[:6]}"
    criado = client.post(
        "/api/secrets", headers=h, json={"name": nome, "value": "abcdefghijkl1234"}
    ).json()
    assert criado["hint"] == "1234" and criado["kind"] == "secret"
    novo = client.put(
        f"/api/secrets/{criado['id']}", headers=h, json={"value": "zzzzzzzzzzzz9999"}
    ).json()
    assert novo["hint"] == "9999"
    curto = client.post("/api/secrets", headers=h, json={"name": nome + "C", "value": "curto"})
    assert curto.json()["hint"] is None


@pytest.mark.integration
def test_remover_libera_o_nome_e_para_de_injetar(client, tenant_admin):
    h = auth(tenant_admin["token"])
    nome = f"DEL_{uuid.uuid4().hex[:6]}"
    s = client.post("/api/secrets", headers=h, json={"name": nome, "value": "v1"}).json()
    assert client.delete(f"/api/secrets/{s['id']}", headers=h).status_code == 200
    again = client.post("/api/secrets", headers=h, json={"name": nome, "value": "v2"})
    assert again.status_code == 201
    from app.harness_runtime import _credentials

    with get_connection() as conn:
        creds = _credentials(conn, tenant_admin["user"]["tenant_id"])
    assert creds["secrets"][nome] == "v2"
    assert not any("~del~" in k for k in creds["secrets"])


@pytest.mark.integration
def test_banco_mongodb_e_aceito_e_vira_url_para_o_agente(client, tenant_admin):
    h = auth(tenant_admin["token"])
    r = client.post(
        "/api/datasources",
        headers=h,
        json={
            "name": "clientes",
            "kind": "mongodb",
            "config": {"host": "mongo.exemplo.com", "port": 27017, "user": "app"},
            "secret": "sen ha",
        },
    )
    assert r.status_code == 201, r.text
    assert "sen ha" not in r.text
    from app.harness_runtime import _credentials

    with get_connection() as conn:
        creds = _credentials(conn, tenant_admin["user"]["tenant_id"])
    assert creds["secrets"]["DB_CLIENTES_URL"] == "mongodb://app:sen%20ha@mongo.exemplo.com:27017"


def test_escopos_de_credenciais_cobrem_bancos_e_pagamento():
    from app.permissions import scopes_to_permissions

    manage = scopes_to_permissions(["ai.credentials.manage"])
    assert set(manage["datasources"]) == {"view", "create", "edit", "delete"}
    assert "payments" in manage
    assert "datasources" not in scopes_to_permissions(["ai.knowledge.manage"])


@pytest.mark.integration
def test_oauth_google_fecha_pelo_servidor_sem_navegador_de_origem(
    client, tenant_admin, monkeypatch
):
    h = auth(tenant_admin["token"])
    monkeypatch.setattr(settings, "public_base_url", "https://motor.exemplo.com")
    monkeypatch.setitem(oauth_engine.GOOGLE_WORKSPACE, "client_id", "cid")

    ini = client.post(
        "/api/credentials/oauth/google/iniciar", headers=h, json={"label": "Agenda do Dr. Fulano"}
    )
    assert ini.status_code == 200, ini.text
    state = ini.json()["state"]
    assert "accounts.google.com" in ini.json()["auth_url"]

    async def fake(provider, code, redirect_uri, verifier):
        assert redirect_uri == "https://motor.exemplo.com/oauth/callback"
        return oauth_engine.ResultadoToken(
            access_token="at", refresh_token="rt", expires_in=3600, email="f@x.com"
        )

    monkeypatch.setattr(oauth_engine, "concluir_redirect", fake)
    fim = client.post("/api/oauth/finalizar", json={"state": state, "code": "abc"})
    assert fim.status_code == 201, fim.text
    contas = client.get("/api/google-accounts", headers=h).json()
    assert [c["label"] for c in contas] == ["Agenda do Dr. Fulano"]
    # o state é de uso único
    again = client.post("/api/oauth/finalizar", json={"state": state, "code": "abc"})
    assert again.status_code == 404


@pytest.mark.integration
def test_oauth_sem_client_id_explica_e_state_desconhecido_da_404(client, tenant_admin, monkeypatch):
    h = auth(tenant_admin["token"])
    monkeypatch.setattr(settings, "public_base_url", "https://motor.exemplo.com")
    monkeypatch.setitem(oauth_engine.MICROSOFT_GRAPH, "client_id", "")
    r = client.post("/api/credentials/oauth/microsoft/iniciar", headers=h, json={})
    assert r.status_code == 503
    dropbox = client.post("/api/credentials/oauth/dropbox/iniciar", headers=h, json={})
    assert dropbox.status_code == 404
    desconhecido = client.post("/api/oauth/finalizar", json={"state": "x" * 20, "code": "c"})
    assert desconhecido.status_code == 404


@pytest.mark.integration
def test_email_substituir_mantem_senha_quando_em_branco_e_lista_variaveis(client, tenant_admin):
    h = auth(tenant_admin["token"])
    criada = client.post(
        "/api/email-accounts",
        headers=h,
        json={
            "label": "Contato",
            "email_address": "contato@loja.com",
            "smtp_host": "smtp.loja.com",
            "imap_host": "imap.loja.com",
            "username": "contato",
            "password": "senha-antiga",
        },
    )
    assert criada.status_code == 201, criada.text
    assert "senha-antiga" not in criada.text
    assert "EMAIL_CONTATO_PASSWORD" in criada.json()["env_vars"]
    conta = criada.json()["id"]

    novo_host = client.put(f"/api/email-accounts/{conta}", headers=h, json={"smtp_host": "smtp2"})
    assert novo_host.status_code == 200 and novo_host.json()["smtp_host"] == "smtp2"
    nova_senha = client.put(f"/api/email-accounts/{conta}", headers=h, json={"password": "nova"})
    assert nova_senha.status_code == 200
    vazio = client.put(f"/api/email-accounts/{conta}", headers=h, json={})
    assert vazio.status_code == 400

    from app.harness_runtime import _credentials

    with get_connection() as conn:
        creds = _credentials(conn, tenant_admin["user"]["tenant_id"])
    assert creds["secrets"]["EMAIL_CONTATO_PASSWORD"] == "nova"
    assert creds["secrets"]["EMAIL_CONTATO_SMTP_HOST"] == "smtp2"
