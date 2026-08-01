"""Provisiona a instância dedicada de 9Router de um tenant.

Por que dedicada e não compartilhada: o 9Router escolhe qual conta atende uma
chamada por `(provider, isActive, priority)` e **não tem noção de dono**. Numa
instância compartilhada, a conta OAuth de um cliente atenderia a chamada de
outro — e nenhuma regra da plataforma impediria, porque a escolha acontece
dentro do 9Router. Com uma instância por tenant, o isolamento é uma
propriedade do deploy, não disciplina de código.

Custo medido em laboratório: 105 MB de RAM com 500 chaves e 200 combos;
listagem em 33 ms; banco de 4,4 MB. Uma instância de tenant real é bem menor.

O que este script faz, em ordem:
  1. cria o container e o volume do tenant na VPS;
  2. define a senha administrativa e a chave de consumo no primeiro boot;
  3. publica a instância no Traefik com hostname próprio e TLS;
  4. registra a instância na plataforma (`PUT /api/ai-router/instancias`).

Uso:
    python scripts/provisionar_router.py <tenant_key>
    python scripts/provisionar_router.py <tenant_key> --remover
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time

import httpx

VPS_HOST = os.environ.get("ROUTER_VPS_HOST", "66.94.101.153")
VPS_USER = os.environ.get("ROUTER_VPS_USER", "root")
SSH_KEY = os.environ.get("ROUTER_SSH_KEY", os.path.expanduser("~/.ssh/vps_rt_infra_ed25519_v2"))
DOMINIO = os.environ.get("ROUTER_DOMAIN", "rangeltech.net")
IMAGEM = os.environ.get("ROUTER_IMAGE", "decolua/9router:latest")
REDE = os.environ.get("ROUTER_NETWORK", "internal")
REDE_PUBLICA = os.environ.get("ROUTER_PUBLIC_NETWORK", "public")

BACKEND = os.environ.get("HOMOLOG_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")


def ssh(comando: str) -> str:
    resultado = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=25", f"{VPS_USER}@{VPS_HOST}", comando],
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"ssh falhou: {resultado.stderr[:400]}")
    return resultado.stdout.strip()


def nome_container(tenant_key: str) -> str:
    return f"9router-{tenant_key}"


def garantir_dns(subdominio: str) -> None:
    """Cria o registro A do subdomínio na Hostinger.

    Sem ele o Let's Encrypt não emite o certificado, e a instância fica de pé
    porém inalcançável. O `overwrite` afeta apenas o registro informado — os
    demais da zona permanecem.
    """
    chave = os.environ.get("HOSTINGER_API_KEY", "")
    if not chave:
        print(f"AVISO: HOSTINGER_API_KEY ausente — crie o registro A '{subdominio}' à mão")
        return
    resposta = httpx.put(
        f"https://developers.hostinger.com/api/dns/v1/zones/{DOMINIO}",
        headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json"},
        json={
            "overwrite": True,
            "zone": [
                {
                    "name": subdominio,
                    "type": "A",
                    "ttl": 300,
                    "records": [{"content": VPS_HOST}],
                }
            ],
        },
        timeout=60.0,
    )
    resposta.raise_for_status()
    # O certificado só sai depois que os resolvers enxergarem o registro; o
    # Traefik tenta de novo sozinho, então a instância pode levar alguns
    # minutos para responder em HTTPS.
    print(f"registro DNS {subdominio}.{DOMINIO} -> {VPS_HOST} solicitado")


def provisionar(tenant_key: str) -> dict:
    container = nome_container(tenant_key)
    host = f"ia-{tenant_key}.{DOMINIO}"
    senha = secrets.token_urlsafe(24)

    existente = ssh(f"docker ps -aq -f name=^{container}$")
    if existente:
        raise SystemExit(f"instância {container} já existe — remova antes de recriar")

    garantir_dns(f"ia-{tenant_key}")
    ssh(f"mkdir -p /opt/platform/data/{container}")
    # A instância não expõe porta: só o Traefik alcança, e ele publica com TLS.
    ssh(
        f"docker run -d --name {container} --restart unless-stopped "
        f"--network {REDE} "
        f"-v /opt/platform/data/{container}:/app/data "
        f"-e PORT=20128 -e INITIAL_PASSWORD='{senha}' "
        f"-l traefik.enable=true "
        f"-l 'traefik.http.routers.{container}.rule=Host(`{host}`)' "
        f"-l traefik.http.routers.{container}.entrypoints=websecure "
        f"-l traefik.http.routers.{container}.tls.certresolver=letsencrypt "
        f"-l traefik.http.services.{container}.loadbalancer.server.port=20128 "
        f"{IMAGEM}"
    )
    ssh(f"docker network connect {REDE_PUBLICA} {container} || true")

    # Espera a instância responder antes de declarar sucesso.
    for _ in range(30):
        saude = ssh(
            f"docker run --rm --network {REDE} curlimages/curl:latest -s -o /dev/null "
            f"-w '%{{http_code}}' http://{container}:20128/api/health || true"
        )
        if saude.endswith("200"):
            break
        time.sleep(3)
    else:
        raise SystemExit(f"instância {container} não respondeu ao health check")

    # A chave de consumo tem que ser criada pela API: a variável de ambiente
    # ROUTER_API_KEY serve ao subprocesso interno do 9Router, não registra uma
    # chave utilizável em /v1.
    chave = criar_chave_de_consumo(container, senha)
    return {"container": container, "base_url": f"https://{host}", "senha": senha, "chave": chave}


def criar_chave_de_consumo(container: str, senha: str) -> str:
    """Cria, pela API administrativa, a chave que a plataforma usa em /v1.

    A variável `ROUTER_API_KEY` não serve para isso: ela alimenta um
    subprocesso interno do 9Router e não registra uma chave utilizável.

    Login e criação acontecem no mesmo container de curl, com cookie jar —
    extrair o cookie com sed pelo SSH quebra no escape entre as camadas.
    """
    saida = ssh(
        f"docker run --rm --network {REDE} --entrypoint sh curlimages/curl:latest -c "
        f"\"curl -s -c /tmp/j -o /dev/null -X POST -H 'Content-Type: application/json' "
        f"-d '{{\\\"password\\\":\\\"{senha}\\\"}}' "
        f"http://{container}:20128/api/auth/login; "
        f"curl -s -b /tmp/j -X POST -H 'Content-Type: application/json' "
        f"-d '{{\\\"name\\\":\\\"plataforma\\\"}}' "
        f"http://{container}:20128/api/keys\""
    )
    try:
        dados = json.loads(saida)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"instância não devolveu chave de consumo: {saida[:200]}") from exc
    if not dados.get("key"):
        raise SystemExit(f"instância não devolveu chave de consumo: {saida[:200]}")
    return dados["key"]


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
            "/api/ai-router/instancias",
            headers=headers,
            json={
                "tenant_id": tenant["id"],
                "base_url": dados["base_url"],
                "admin_password": dados["senha"],
                "api_key": dados["chave"],
            },
        )
        resposta.raise_for_status()


def remover(tenant_key: str) -> None:
    container = nome_container(tenant_key)
    ssh(f"docker rm -f {container} || true")
    print(f"container {container} removido (o volume foi preservado em /opt/platform/data)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_key")
    parser.add_argument("--remover", action="store_true")
    args = parser.parse_args()

    if args.remover:
        remover(args.tenant_key)
        return

    dados = provisionar(args.tenant_key)
    registrar_na_plataforma(args.tenant_key, dados)
    # A senha aparece uma vez: a partir daqui ela vive cifrada na plataforma.
    print(json.dumps({k: v for k, v in dados.items() if k != "senha"}, indent=2))
    print("instância provisionada e registrada na plataforma")


if __name__ == "__main__":
    sys.exit(main())
