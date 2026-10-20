"""Desligamento por empresa dos agentes `graph` (Fase L-2): backup + quarentena.

Regra: nada é apagado. O desligamento faz, nesta ordem:

1. confere se há uso vivo que o desligamento quebraria (caixa de entrada ou rotina
   ativa apontando para template `structured`) e recusa, salvo `forcar`;
2. grava um backup em JSONL com tudo o que o `graph` da empresa produziu
   (templates e versões, conversas `llm`, mensagens, execuções, chamadas de ferramenta,
   artefatos, memórias e os checkpoints do LangGraph) mais um `manifest.json` com
   contagens e SHA-256 de cada arquivo, e relê o backup para provar que está inteiro;
3. só então liga a quarentena: `tenants.graph_enabled = false` e uma linha em
   `graph_shutdown_log`. Os dados continuam nas tabelas de origem, legíveis; apenas
   novas execuções/templates `graph` passam a ser recusados (403).

`reativar` desfaz a quarentena (a flag volta; nenhum dado foi movido).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from psycopg.types.json import Json

from app.db import get_connection

# nome -> (SQL de seleção; parâmetros: %(t)s tenant, %(chats)s chats llm (uuid[]),
# %(chats_txt)s mesmos ids como texto, %(templates)s ids de template, %(versions)s ids de versão)
_PLANO: list[tuple[str, str]] = [
    (
        "templates",
        "SELECT * FROM templates WHERE tenant_id = %(t)s AND agent_kind = 'structured'",
    ),
    (
        "template_versions",
        "SELECT * FROM template_versions WHERE template_id = ANY(%(templates)s)",
    ),
    (
        "template_agents",
        "SELECT * FROM template_agents WHERE version_id = ANY(%(versions)s)",
    ),
    (
        "chats",
        "SELECT * FROM chats WHERE tenant_id = %(t)s AND kernel = 'llm'",
    ),
    ("chat_messages", "SELECT * FROM chat_messages WHERE chat_id = ANY(%(chats)s)"),
    (
        "chat_runs",
        "SELECT * FROM chat_runs WHERE tenant_id = %(t)s AND kernel = 'llm'",
    ),
    (
        "tool_calls",
        "SELECT * FROM tool_calls WHERE tenant_id = %(t)s AND chat_id = ANY(%(chats_txt)s)",
    ),
    (
        "artifacts",
        "SELECT * FROM artifacts WHERE tenant_id = %(t)s AND chat_id = ANY(%(chats_txt)s)",
    ),
    ("memories", "SELECT * FROM memories WHERE tenant_id = %(t)s"),
    (
        "memory_extraction_state",
        "SELECT * FROM memory_extraction_state WHERE thread_id = ANY(%(chats_txt)s)",
    ),
    # Tabelas do LangGraph (criadas pelo kernel-llm; podem não existir em outros ambientes).
    ("checkpoints", "SELECT * FROM checkpoints WHERE thread_id = ANY(%(chats_txt)s)"),
    ("checkpoint_blobs", "SELECT * FROM checkpoint_blobs WHERE thread_id = ANY(%(chats_txt)s)"),
    ("checkpoint_writes", "SELECT * FROM checkpoint_writes WHERE thread_id = ANY(%(chats_txt)s)"),
]


class Bloqueio(RuntimeError):
    """Uso vivo que o desligamento quebraria."""


def _tenant(conn, tenant_key: str) -> dict:
    row = conn.execute(
        "SELECT id, tenant_key, name, graph_enabled FROM tenants WHERE tenant_key = %s",
        (tenant_key,),
    ).fetchone()
    if row is None:
        raise LookupError(f"empresa não encontrada: {tenant_key}")
    return row


def usos_vivos(conn, tenant_id) -> dict:
    """Quem ainda depende de um template `structured` da empresa."""
    caixas = conn.execute(
        """SELECT l.chatwoot_inbox_id FROM inbox_ai_links l
             JOIN templates t ON t.id = l.template_id
            WHERE l.tenant_id = %s AND t.agent_kind = 'structured' AND NOT t.is_deleted""",
        (tenant_id,),
    ).fetchall()
    rotinas = conn.execute(
        """SELECT j.id FROM scheduled_jobs j JOIN templates t ON t.id = j.template_id
            WHERE j.tenant_id = %s AND j.status = 'active' AND t.agent_kind = 'structured'""",
        (tenant_id,),
    ).fetchall()
    return {
        "caixas_de_entrada": [r["chatwoot_inbox_id"] for r in caixas],
        "rotinas_ativas": [str(r["id"]) for r in rotinas],
    }


def _existe(conn, tabela: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) AS r", (tabela,)).fetchone()["r"] is not None


def _json_default(value):
    if isinstance(value, bytes | memoryview):
        return {"__bytes_b64__": base64.b64encode(bytes(value)).decode()}
    return str(value)


def fazer_backup(conn, tenant: dict, destino: Path) -> dict:
    """Grava o backup em `destino/<tenant_key>-<carimbo>/` e devolve o manifesto."""
    carimbo = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    pasta = destino / f"{tenant['tenant_key']}-{carimbo}"
    pasta.mkdir(parents=True, exist_ok=False)

    t = tenant["id"]
    templates = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM templates WHERE tenant_id = %s AND agent_kind = 'structured'", (t,)
        ).fetchall()
    ]
    versions = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM template_versions WHERE template_id = ANY(%s)", (templates,)
        ).fetchall()
    ]
    chats = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chats WHERE tenant_id = %s AND kernel = 'llm'", (t,)
        ).fetchall()
    ]
    params = {
        "t": t,
        "templates": templates,
        "versions": versions,
        "chats": chats,
        "chats_txt": [str(c) for c in chats],
    }

    arquivos: dict[str, dict] = {}
    for nome, sql in _PLANO:
        if not _existe(conn, nome):
            arquivos[nome] = {"linhas": 0, "ausente": True}
            continue
        caminho = pasta / f"{nome}.jsonl.gz"
        linhas = 0
        with gzip.open(caminho, "wt", encoding="utf-8") as fh:
            cur = conn.execute(f"SELECT row_to_json(x) AS j FROM ({sql}) x", params)  # noqa: S608
            for row in cur:
                fh.write(json.dumps(row["j"], ensure_ascii=False, default=_json_default) + "\n")
                linhas += 1
        arquivos[nome] = {
            "linhas": linhas,
            "sha256": hashlib.sha256(caminho.read_bytes()).hexdigest(),
        }

    manifesto = {
        "tenant_key": tenant["tenant_key"],
        "tenant_id": str(t),
        "criado_em": datetime.now(UTC).isoformat(),
        "arquivos": arquivos,
    }
    (pasta / "manifest.json").write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    manifesto["pasta"] = str(pasta)
    manifesto["sha256"] = hashlib.sha256(json.dumps(arquivos, sort_keys=True).encode()).hexdigest()
    return manifesto


def verificar_backup(manifesto: dict) -> None:
    """Relê cada arquivo: hash e número de linhas têm de bater com o manifesto."""
    pasta = Path(manifesto["pasta"])
    for nome, info in manifesto["arquivos"].items():
        if info.get("ausente"):
            continue
        caminho = pasta / f"{nome}.jsonl.gz"
        if hashlib.sha256(caminho.read_bytes()).hexdigest() != info["sha256"]:
            raise RuntimeError(f"backup corrompido: {nome}")
        with gzip.open(caminho, "rt", encoding="utf-8") as fh:
            lidas = sum(1 for _ in fh)
        if lidas != info["linhas"]:
            raise RuntimeError(f"backup incompleto: {nome} ({lidas} != {info['linhas']})")


def desligar(
    tenant_key: str, destino: Path, *, forcar: bool = False, simular: bool = False
) -> dict:
    with get_connection() as conn:
        tenant = _tenant(conn, tenant_key)
        usos = usos_vivos(conn, tenant["id"])
        if (usos["caixas_de_entrada"] or usos["rotinas_ativas"]) and not forcar:
            raise Bloqueio(f"uso vivo de graph em {tenant_key}: {usos}")
        manifesto = fazer_backup(conn, tenant, destino)
        verificar_backup(manifesto)
        contagens = {n: i["linhas"] for n, i in manifesto["arquivos"].items()}
        if simular:
            return {"simulado": True, "usos": usos, "contagens": contagens, **manifesto}
        conn.execute(
            "UPDATE tenants SET graph_enabled = false, updated_at = now() WHERE id = %s",
            (tenant["id"],),
        )
        conn.execute(
            """INSERT INTO graph_shutdown_log (tenant_id, action, backup_path,
                      backup_sha256, counts)
               VALUES (%s, 'desligar', %s, %s, %s)""",
            (tenant["id"], manifesto["pasta"], manifesto["sha256"], Json(contagens)),
        )
    return {"simulado": False, "usos": usos, "contagens": contagens, **manifesto}


def reativar(tenant_key: str) -> dict:
    with get_connection() as conn:
        tenant = _tenant(conn, tenant_key)
        conn.execute(
            "UPDATE tenants SET graph_enabled = true, updated_at = now() WHERE id = %s",
            (tenant["id"],),
        )
        conn.execute(
            "INSERT INTO graph_shutdown_log (tenant_id, action) VALUES (%s, 'reativar')",
            (tenant["id"],),
        )
    return {"tenant_key": tenant_key, "graph_enabled": True}
