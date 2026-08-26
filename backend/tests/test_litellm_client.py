"""Cliente do LiteLLM: Team, deployment e virtual key.

Mesma filosofia de `test_ai_router.py` — simula a instância real (aqui,
LiteLLM) com um servidor de verdade, e prova o comportamento do nosso cliente
contra o contrato observado (`infra-04-litellm-substitui-9router.md`), não
contra uma versão idealizada da API.
"""

import threading
import uuid

import pytest
import uvicorn
from app.litellm_client import (
    LiteLLMError,
    add_model_to_team,
    create_deployment,
    create_team,
    delete_deployment,
    delete_key,
    delete_team,
    generate_key,
    health,
    list_deployments,
)
from fastapi import FastAPI, HTTPException, Request

pytestmark = pytest.mark.integration

MASTER_KEY = "sk-test-master"


def _fake_litellm():
    """Instância de LiteLLM de mentira, com o contrato real observado no
    sandbox local da Fase A (infra-04): /team/new, /team/info, /team/update,
    /team/delete, /model/new, /model/info, /model/delete, /key/generate,
    /key/delete, /health/liveliness."""
    app = FastAPI()
    estado: dict = {"teams": {}, "models": [], "keys": {}}

    def _auth(request: Request):
        if request.headers.get("authorization") != f"Bearer {MASTER_KEY}":
            raise HTTPException(status_code=401)

    @app.get("/health/liveliness")
    async def liveliness():
        return {"status": "ok"}

    @app.post("/team/new")
    async def team_new(request: Request):
        _auth(request)
        corpo = await request.json()
        team_id = str(uuid.uuid4())
        estado["teams"][team_id] = {
            "team_id": team_id,
            "team_alias": corpo.get("team_alias"),
            "models": [],
        }
        return {"team_id": team_id, "team_alias": corpo.get("team_alias")}

    @app.get("/team/info")
    async def team_info(request: Request, team_id: str):
        _auth(request)
        team = estado["teams"].get(team_id)
        if not team:
            raise HTTPException(status_code=404)
        return {"team_info": team}

    @app.post("/team/update")
    async def team_update(request: Request):
        _auth(request)
        corpo = await request.json()
        team = estado["teams"][corpo["team_id"]]
        team["models"] = corpo["models"]
        return {"team_id": team["team_id"], "models": team["models"]}

    @app.post("/team/delete")
    async def team_delete(request: Request):
        _auth(request)
        corpo = await request.json()
        for tid in corpo["team_ids"]:
            estado["teams"].pop(tid, None)
        return {"deleted": corpo["team_ids"]}

    @app.post("/model/new")
    async def model_new(request: Request):
        _auth(request)
        corpo = await request.json()
        deployment = {
            "model_name": corpo["model_name"],
            "litellm_params": corpo["litellm_params"],
            "model_info": {"id": str(uuid.uuid4()), **corpo.get("model_info", {})},
        }
        estado["models"].append(deployment)
        # 26/08/2026, achado ao vivo (produto-08 §6): LiteLLM real devolve
        # 500 aqui mesmo quando a escrita deu certo, por causa do polling
        # entre pods (~30s, sem push) -- simula esse comportamento real
        # quando o model_name pedir explicitamente ("-simula-race-real").
        if "-simula-race-real" in corpo["model_name"]:
            model_id = deployment["model_info"]["id"]
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Model create was saved to the database, but the model id(s) ['{model_id}'] "
                    "are not live in this pod's router after the reload and are not being served "
                    "by this pod. Other pods reload on their own interval."
                ),
            )
        return deployment

    @app.get("/model/info")
    async def model_info(request: Request):
        _auth(request)
        return {"data": estado["models"]}

    @app.post("/model/delete")
    async def model_delete(request: Request):
        _auth(request)
        corpo = await request.json()
        estado["models"] = [m for m in estado["models"] if m["model_info"]["id"] != corpo["id"]]
        return {"deleted": corpo["id"]}

    @app.post("/key/generate")
    async def key_generate(request: Request):
        _auth(request)
        corpo = await request.json()
        team = estado["teams"].get(corpo["team_id"])
        if not team:
            raise HTTPException(status_code=400, detail="team inexistente")
        modelos_pedidos = corpo.get("models")
        if modelos_pedidos is not None:
            for model_name in modelos_pedidos:
                if model_name not in team["models"]:
                    raise HTTPException(
                        status_code=403, detail=f"team não tem acesso a {model_name}"
                    )
        key = f"sk-{uuid.uuid4().hex}"
        estado["keys"][key] = {
            "team_id": corpo["team_id"],
            "models": modelos_pedidos,
            "alias": corpo.get("key_alias"),
        }
        return {"key": key, "key_alias": corpo.get("key_alias")}

    @app.post("/key/delete")
    async def key_delete(request: Request):
        _auth(request)
        corpo = await request.json()
        for k in corpo["keys"]:
            estado["keys"].pop(k, None)
        return {"deleted": corpo["keys"]}

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    return server, server.servers[0].sockets[0].getsockname()[1], estado


@pytest.fixture
def litellm():
    server, porta, estado = _fake_litellm()
    yield f"http://127.0.0.1:{porta}", estado
    server.should_exit = True


@pytest.mark.asyncio
async def test_cria_team_e_devolve_team_id(litellm):
    base_url, _ = litellm
    resp = await create_team(base_url, MASTER_KEY, team_alias="tenant-x")
    assert resp["team_alias"] == "tenant-x"
    assert resp["team_id"]


@pytest.mark.asyncio
async def test_master_key_errada_falha_explicito(litellm):
    base_url, _ = litellm
    with pytest.raises(LiteLLMError):
        await create_team(base_url, "chave-errada", team_alias="tenant-x")


@pytest.mark.asyncio
async def test_deployment_so_aparece_pro_model_name_certo(litellm):
    base_url, _ = litellm
    await create_deployment(
        base_url,
        MASTER_KEY,
        model_name="tenant-x-gemini",
        provider_model="gemini/gemini-flash-latest",
        api_key="AIza-fake",
        tenant_id="tenant-x",
    )
    await create_deployment(
        base_url,
        MASTER_KEY,
        model_name="tenant-y-gemini",
        provider_model="gemini/gemini-flash-latest",
        api_key="AIza-fake-2",
        tenant_id="tenant-y",
    )
    deployments_x = await list_deployments(base_url, MASTER_KEY, model_name="tenant-x-gemini")
    assert len(deployments_x) == 1
    assert deployments_x[0]["model_info"]["tenant_id"] == "tenant-x"


@pytest.mark.asyncio
async def test_add_model_to_team_e_idempotente(litellm):
    base_url, estado = litellm
    team = await create_team(base_url, MASTER_KEY, team_alias="tenant-x")
    team_id = team["team_id"]

    await add_model_to_team(base_url, MASTER_KEY, team_id=team_id, model_name="tenant-x-gemini")
    await add_model_to_team(base_url, MASTER_KEY, team_id=team_id, model_name="tenant-x-gemini")

    assert estado["teams"][team_id]["models"] == ["tenant-x-gemini"]


@pytest.mark.asyncio
async def test_key_generate_falha_se_team_nao_tem_acesso_ao_model(litellm):
    """Achado de auditoria (infra-04/produto-06): nunca confiar em `tenant_id`
    solto — a garantia de isolamento é o Team só poder gerar key pros próprios
    model_names. Essa é a checagem que prova isso na prática, não só no papel."""
    base_url, _ = litellm
    team = await create_team(base_url, MASTER_KEY, team_alias="tenant-x")
    with pytest.raises(LiteLLMError):
        await generate_key(
            base_url,
            MASTER_KEY,
            team_id=team["team_id"],
            model_name="tenant-y-gemini",
            key_alias="vazamento",
        )


@pytest.mark.asyncio
async def test_gera_key_no_provisionamento_antes_de_ter_qualquer_model(litellm):
    """Caso real do provisionamento: o Team nasce vazio (tenant ainda não
    conectou conta própria nenhuma) e a plataforma precisa das 2 virtual
    keys (bridge/AI Assist) mesmo assim — sem `model_name`, a key herda o
    que o Team for ganhando depois via `add_model_to_team`."""
    base_url, _ = litellm
    team = await create_team(base_url, MASTER_KEY, team_alias="tenant-novo")
    bridge_key = await generate_key(
        base_url, MASTER_KEY, team_id=team["team_id"], key_alias="bridge"
    )
    ai_assist_key = await generate_key(
        base_url, MASTER_KEY, team_id=team["team_id"], key_alias="ai-assist"
    )
    assert bridge_key.startswith("sk-")
    assert ai_assist_key.startswith("sk-")
    assert bridge_key != ai_assist_key


@pytest.mark.asyncio
async def test_ciclo_completo_team_deployment_key(litellm):
    base_url, _ = litellm
    team = await create_team(base_url, MASTER_KEY, team_alias="tenant-x")
    team_id = team["team_id"]
    model_name = "tenant-x-gemini"

    await create_deployment(
        base_url,
        MASTER_KEY,
        model_name=model_name,
        provider_model="gemini/gemini-flash-latest",
        api_key="AIza-fake",
        tenant_id="tenant-x",
    )
    await add_model_to_team(base_url, MASTER_KEY, team_id=team_id, model_name=model_name)
    key = await generate_key(
        base_url, MASTER_KEY, team_id=team_id, model_name=model_name, key_alias="bridge"
    )

    assert key.startswith("sk-")


@pytest.mark.asyncio
async def test_delete_deployment_e_delete_team(litellm):
    base_url, estado = litellm
    team = await create_team(base_url, MASTER_KEY, team_alias="tenant-x")
    dep = await create_deployment(
        base_url,
        MASTER_KEY,
        model_name="tenant-x-gemini",
        provider_model="gemini/gemini-flash-latest",
        api_key="AIza-fake",
        tenant_id="tenant-x",
    )
    await delete_deployment(base_url, MASTER_KEY, deployment_id=dep["model_info"]["id"])
    assert estado["models"] == []

    await delete_team(base_url, MASTER_KEY, team_id=team["team_id"])
    assert team["team_id"] not in estado["teams"]


@pytest.mark.asyncio
async def test_delete_key(litellm):
    base_url, estado = litellm
    team = await create_team(base_url, MASTER_KEY, team_alias="tenant-x")
    await add_model_to_team(
        base_url, MASTER_KEY, team_id=team["team_id"], model_name="tenant-x-gemini"
    )
    key = await generate_key(
        base_url,
        MASTER_KEY,
        team_id=team["team_id"],
        model_name="tenant-x-gemini",
        key_alias="bridge",
    )
    await delete_key(base_url, MASTER_KEY, key=key)
    assert key not in estado["keys"]


@pytest.mark.asyncio
async def test_create_deployment_tolera_race_de_polling_entre_pods(litellm):
    """Achado ao vivo 26/08/2026 (produto-08 §6, contas reais Codex+Claude):
    LiteLLM devolve 500 "saved to the database, but not live in this pod's
    router" mesmo quando a escrita deu certo -- convergência entre pods é
    por polling (~30s, sem push), não dá pra checar "está vivo AGORA" logo
    depois do POST. Não é erro de verdade, `create_deployment` não deve
    propagar."""
    base_url, _ = litellm
    resultado = await create_deployment(
        base_url,
        MASTER_KEY,
        model_name="tenant-x-simula-race-real",
        provider_model="cc/claude-sonnet-5",
        api_key="access-token-fake",
        tenant_id="tenant-x",
    )
    assert resultado["model_name"] == "tenant-x-simula-race-real"


@pytest.mark.asyncio
async def test_health(litellm):
    base_url, _ = litellm
    assert await health(base_url) is True
    assert await health("http://127.0.0.1:1") is False
