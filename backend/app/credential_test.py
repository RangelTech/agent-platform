"""Teste de credenciais no backend do motor (spec 11 §7). Nunca no navegador; timeout curto;
destinos privados, de loopback ou de metadados da nuvem são recusados (SSRF)."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import time

import httpx

TIMEOUT = 10.0


def public_host(host: str) -> str | None:
    """Devolve o IP público do host, ou None se resolver para rede privada/reservada."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return None
    return infos[0][4][0] if infos else None


async def test_ssh(meta: dict) -> tuple[bool, str]:
    host, port = str(meta.get("host") or ""), int(meta.get("port") or 22)
    ip = await asyncio.to_thread(public_host, host)
    if ip is None:
        return False, "Servidor não encontrado ou em rede privada"
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), TIMEOUT)
        banner = (await asyncio.wait_for(reader.readline(), TIMEOUT)).decode(errors="replace")
        writer.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"Não foi possível conectar: {type(exc).__name__}"
    if not banner.startswith("SSH-"):
        return False, "A porta respondeu, mas não é um servidor SSH"
    return True, "Servidor SSH alcançado (a chave é conferida no primeiro uso)"


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


async def test_gcp(value: str) -> tuple[bool, str]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    sa = json.loads(value)
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = _b64(
        json.dumps(
            {
                "iss": sa["client_email"],
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 300,
            }
        ).encode()
    )
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = _b64(key.sign(head + b"." + claim, padding.PKCS1v15(), hashes.SHA256()))
    jwt = (head + b"." + claim + b"." + sig).decode()
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt},
        )
    if r.status_code == 200:
        return True, f"Autenticado como {sa['client_email']}"
    return False, f"O Google recusou a credencial ({r.status_code})"


def _aws_sync(value: str) -> tuple[bool, str]:
    import boto3

    d = json.loads(value)
    sts = boto3.client(
        "sts",
        aws_access_key_id=d["access_key_id"],
        aws_secret_access_key=d["secret_access_key"],
        region_name=d.get("region") or "us-east-1",
    )
    who = sts.get_caller_identity()
    return True, f"Conta AWS {who['Account']} ({who['Arn'].split('/')[-1]})"


async def test_aws(value: str) -> tuple[bool, str]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_aws_sync, value), TIMEOUT + 5)
    except Exception as exc:  # noqa: BLE001
        return False, f"A AWS recusou a credencial ({type(exc).__name__})"


async def test_azure(value: str) -> tuple[bool, str]:
    d = json.loads(value)
    url = f"https://login.microsoftonline.com/{d['tenant_id']}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            url,
            data={
                "client_id": d["client_id"],
                "client_secret": d["client_secret"],
                "grant_type": "client_credentials",
                "scope": "https://management.azure.com/.default",
            },
        )
    if r.status_code == 200:
        return True, "Autenticado no Azure"
    return False, f"O Azure recusou a credencial ({r.status_code})"


async def run_test(kind: str, value: str, meta: dict) -> tuple[bool, str]:
    try:
        if kind == "ssh":
            return await test_ssh(meta)
        if kind == "gcp":
            return await test_gcp(value)
        if kind == "aws":
            return await test_aws(value)
        if kind == "azure":
            return await test_azure(value)
        return True, "Guardada. Este tipo não tem teste de conexão."
    except Exception as exc:  # noqa: BLE001
        return False, f"Falha no teste: {type(exc).__name__}"
