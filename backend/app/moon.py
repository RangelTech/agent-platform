"""Sr Moon: agente padrão do dono, um por tenant, atualizado por release (spec 11 §23).

Template de sistema (`system_key = 'moon'`): não editável pelo tenant, só por nós (este arquivo)
e pelo auto-aprendizado. Acesso só com `ai.moon.use` (`strict_scope` desliga o atalho de quem
edita agentes). Idempotente: cada release com pacote novo cria uma versão nova e ativa.
"""

from __future__ import annotations

import hashlib

from psycopg.types.json import Json

from app.db import get_connection
from app.skill_package import normalize_manifest, sha256_text, size_bytes

SYSTEM_KEY = "moon"
NAME = "Sr Moon"
DESCRIPTION = "Assistente padrão do dono da empresa. Atualizado pela LUAR."

AGENT_MD = """# Quem você é

Você é o Sr Moon, o assistente pessoal do dono desta empresa dentro da LUAR. Fale em português
claro e direto, como um braço direito de confiança.

## Como trabalhar

- Entenda o pedido antes de agir; se faltar informação, pergunte uma vez, de forma curta.
- Você age com as mesmas permissões da pessoa que conversa com você, nunca com mais.
- Você pode operar a própria LUAR (conversas, cartões, filas), usar o ambiente da empresa
  (servidor, arquivos, programas) e os computadores conectados, conforme o plano e as credenciais
  liberadas.
- Antes de ação destrutiva, financeira ou envio em massa, peça confirmação.
- Nunca mostre chaves, senhas ou dados de outra empresa.
- Aprenda as preferências da pessoa e use-as nas próximas conversas.
- Ao terminar, conte o que fez em poucas linhas.
"""

PACKAGE = [{"path": "AGENTE.md", "content": AGENT_MD}]
MANIFEST = normalize_manifest(
    {
        "tools": [],
        "environment": {"enabled": True},
        "limits": {
            "luar": True,
            "confirm_actions": True,
            "allowed_scopes": ["ai.moon.use"],
            "strict_scope": True,
        },
    }
)


def package_hash() -> str:
    h = hashlib.sha256()
    for f in PACKAGE:
        h.update(f"{f['path']}:{sha256_text(f['content'])}\n".encode())
    h.update(str(sorted(MANIFEST["limits"].items())).encode())
    return h.hexdigest()[:16]


def ensure_for_tenant(conn, tenant_id) -> str:
    template = conn.execute(
        "SELECT * FROM templates WHERE tenant_id=%s AND system_key=%s AND NOT is_deleted FOR UPDATE",
        (tenant_id, SYSTEM_KEY),
    ).fetchone()
    if template is None:
        template = conn.execute(
            """INSERT INTO templates (tenant_id, name, description, system_key, agent_kind)
               VALUES (%s, %s, %s, %s, 'skills') RETURNING *""",
            (tenant_id, NAME, DESCRIPTION, SYSTEM_KEY),
        ).fetchone()
    tag = f"moon:{package_hash()}"
    current = conn.execute(
        "SELECT notes FROM template_versions WHERE id = %s", (template["active_version_id"],)
    ).fetchone() if template["active_version_id"] else None
    if current and current["notes"] == tag:
        return str(template["id"])
    number = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS n FROM template_versions WHERE template_id = %s",
        (template["id"],),
    ).fetchone()["n"]
    version = conn.execute(
        """INSERT INTO template_versions (template_id, version_number, supervisor_prompt, notes, manifest)
           VALUES (%s, %s, '', %s, %s) RETURNING id""",
        (template["id"], number, tag, Json(MANIFEST)),
    ).fetchone()
    for f in PACKAGE:
        conn.execute(
            """INSERT INTO template_skill_files (version_id, path, kind, content, sha256, size_bytes)
               VALUES (%s, %s, 'agent', %s, %s, %s)""",
            (version["id"], f["path"], f["content"], sha256_text(f["content"]), size_bytes(f["content"])),
        )
    conn.execute(
        "UPDATE templates SET active_version_id=%s, updated_at=now() WHERE id=%s",
        (version["id"], template["id"]),
    )
    return str(template["id"])


def ensure_all_tenants() -> None:
    with get_connection() as conn:
        for row in conn.execute("SELECT id FROM tenants WHERE is_active").fetchall():
            ensure_for_tenant(conn, row["id"])
