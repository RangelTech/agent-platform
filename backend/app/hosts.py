"""Computadores conectados (spec 11 §26): cliente do hub, chaves e variáveis injetadas no agente."""

from __future__ import annotations

import os
import re

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.crypto import decrypt

TIMEOUT = 8.0


def hub_url() -> str:
    return os.environ.get("CONECTOR_HUB_URL", "http://luar-conector-hub:8080").rstrip("/")


def _headers() -> dict:
    return {"Authorization": "Bearer " + os.environ.get("CONECTOR_HUB_TOKEN", "")}


def relay_addr() -> str:
    """host:porta do relay TCP do hub, como o ambiente da empresa o enxerga."""
    return os.environ.get("CONECTOR_RELAY_ADDR", "conector.rangeltech.net:2222")


def new_keypair() -> tuple[str, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    return priv, pub


def hub_pair(host_id: str, tenant_id: str, name: str, conn_pub: str) -> dict:
    r = httpx.post(
        hub_url() + "/internal/pair",
        headers=_headers(),
        json={"host_id": host_id, "tenant_id": tenant_id, "name": name, "conn_pub": conn_pub},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def hub_status(tenant_id: str) -> dict[str, dict]:
    try:
        r = httpx.get(
            hub_url() + "/internal/hosts",
            headers=_headers(),
            params={"tenant_id": tenant_id},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return {h["host_id"]: h for h in r.json()}
    except Exception:  # noqa: BLE001 - hub fora: tudo aparece offline, nunca derruba a tela
        return {}


def hub_revoke(host_id: str) -> None:
    try:
        httpx.delete(hub_url() + f"/internal/hosts/{host_id}", headers=_headers(), timeout=TIMEOUT)
    except Exception:  # noqa: BLE001
        pass


def relay_token(host_id: str, ttl: int = 3600) -> str | None:
    try:
        r = httpx.post(
            hub_url() + "/internal/relay-token",
            headers=_headers(),
            json={"host_id": host_id, "ttl": ttl},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["token"]
    except Exception:  # noqa: BLE001
        return None


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() or "PC"


def run_env(conn, tenant_id, template_id, system_key: str | None) -> dict[str, str]:
    """Variáveis dos computadores liberados a este agente (Sr Moon enxerga todos do tenant)."""
    if system_key == "moon":
        rows = conn.execute(
            "SELECT * FROM hosts WHERE tenant_id = %s AND NOT is_deleted ORDER BY name",
            (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT h.* FROM hosts h JOIN agent_hosts a ON a.host_id = h.id
                WHERE a.template_id = %s AND h.tenant_id = %s AND NOT h.is_deleted ORDER BY h.name""",
            (template_id, tenant_id),
        ).fetchall()
    out: dict[str, str] = {}
    names = []
    status = hub_status(str(tenant_id)) if rows else {}
    for h in rows:
        tok = relay_token(str(h["id"]))
        if not tok:
            continue
        base = f"PC_{slug(h['name'])}"
        host, _, port = relay_addr().partition(":")
        out[f"{base}_KEY"] = decrypt(h["conn_key_encrypted"])
        out[f"{base}_HUB"] = host
        out[f"{base}_PORT"] = port or "2222"
        out[f"{base}_TOKEN"] = tok
        out[f"{base}_OS"] = (status.get(str(h["id"])) or {}).get("os") or (h["os"] or "")
        names.append(f"{h['name']}={base}")
    if names:
        out["PC_HOSTS"] = ",".join(names)
    return out
