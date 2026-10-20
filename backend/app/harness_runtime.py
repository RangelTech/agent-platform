"""Payload de uma run do kernel de agentes por skills (contrato v1, spec 04 §4).

Equivalente de `template_runtime.build_run_payload` para o tipo `skills`. As
credenciais seguem em claro no payload (decisão D-57), no mesmo formato do
fluxo estruturado; o valor só vive na memória da run do outro lado.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.credential_env import derive as derive_env
from app.crypto import decrypt
from app.db import get_connection
from app.run_tokens import issue_run_token
from app.skill_package import default_manifest, normalize_manifest, output_format_of
from app.template_access import ensure_can_run
from app.tool_catalog import credential_kinds

CONTRACT_VERSION = 1
INLINE_PACKAGE_LIMIT = 700 * 1024
USER_SKILLS_INLINE_LIMIT = 256 * 1024
HISTORY_MESSAGES = 20
DEFAULT_MODEL_ALIAS = "agente-padrao"
DEFAULT_LIMITS = {"max_turns": 60, "turn_timeout_s": 540, "idle_timeout_s": 120}


def package_sha(files: list[dict]) -> str:
    digest = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["path"]):
        digest.update(f"{f['path']}:{f['sha256']}\n".encode())
    return digest.hexdigest()


def version_package(conn, version_id, *, include_content: bool = True) -> list[dict]:
    """Arquivos da versão. `include_content=False` só lê metadados (dimensionar sem carregar bytes).

    Texto vem em `content` (UTF-8). Binário (arquivos/) vem em `content` como base64 e
    `encoding = "base64"`; `size_bytes` é sempre o tamanho real do arquivo.
    """
    rows = conn.execute(
        """SELECT path, kind, sha256, size_bytes, is_binary, mime,
                  CASE WHEN %s THEN content ELSE NULL END AS content,
                  CASE WHEN %s AND is_binary THEN content_bin ELSE NULL END AS content_bin
             FROM template_skill_files WHERE version_id = %s ORDER BY path""",
        (include_content, include_content, version_id),
    ).fetchall()
    out = []
    for r in rows:
        f = dict(r)
        raw = f.pop("content_bin", None)
        if f["is_binary"]:
            f["encoding"] = "base64"
            f["content"] = base64.b64encode(bytes(raw)).decode("ascii") if raw is not None else None
        else:
            f["encoding"] = "utf-8"
        out.append(f)
    return out


def company_skill_files(conn, tenant_id, taken: set[str], *, include_content: bool) -> list[dict]:
    """Skills compartilhadas da empresa, no formato dos arquivos do pacote do agente.

    Entram em todo agente por skills do tenant; se o pacote já traz uma skill com o mesmo
    caminho, a do pacote vence.
    """
    rows = conn.execute(
        """SELECT name, content FROM tenant_skills
            WHERE tenant_id = %s AND status = 'active' ORDER BY name""",
        (tenant_id,),
    ).fetchall()
    out = []
    for r in rows:
        path = f"skills/{r['name']}/SKILL.md"
        if path in taken:
            continue
        data = r["content"].encode("utf-8")
        out.append(
            {
                "path": path,
                "kind": "skill",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "is_binary": False,
                "mime": "text/markdown",
                "content": r["content"] if include_content else None,
                "encoding": "utf-8",
            }
        )
    return out


def package_files(conn, version_id, tenant_id, *, include_content: bool = True) -> list[dict]:
    """Arquivos do pacote da versão mais as skills compartilhadas da empresa."""
    files = version_package(conn, version_id, include_content=include_content)
    taken = {f["path"] for f in files}
    return sorted(
        files + company_skill_files(conn, tenant_id, taken, include_content=include_content),
        key=lambda f: f["path"],
    )


def wire_size(f: dict) -> int:
    """Bytes que o arquivo ocupa no JSON (base64 incha 4/3)."""
    return -(-f["size_bytes"] * 4 // 3) if f.get("is_binary") else f["size_bytes"]


def wire_file(f: dict) -> dict:
    """Formato do contrato v1: `encoding` só aparece quando o conteúdo é base64."""
    out = {"path": f["path"], "sha256": f["sha256"], "content": f["content"]}
    if f.get("is_binary"):
        out["encoding"] = "base64"
        out["mime"] = f.get("mime") or "application/octet-stream"
    return out


LUAR_CTX_TTL_S = 900


def _luar_access(conn, manifest: dict, tenant_id, user_id, run_id: str) -> dict | None:
    """Contexto assinado (aud=luar): o terminal do ambiente chama a API do Luar como o usuário."""
    if not user_id or (manifest.get("limits") or {}).get("luar") is not True:
        return None
    row = conn.execute(
        """SELECT t.chatwoot_account_id AS account_id, u.chatwoot_user_id AS cw_user
             FROM tenants t JOIN users u ON u.tenant_id = t.id
            WHERE t.id = %s AND u.id = %s""",
        (tenant_id, user_id),
    ).fetchone()
    if not row or not row["account_id"] or not row["cw_user"] or not settings.public_base_url:
        return None
    from app.ui_context import sign_context

    token = sign_context(
        {
            "aud": "luar",
            "account_id": row["account_id"],
            "user_id": row["cw_user"],
            "run_id": run_id,
            "exp": time.time() + LUAR_CTX_TTL_S,
        }
    )
    return {
        "url": settings.public_base_url.rstrip("/") + "/rt_luar",
        "token": token,
        "account_id": row["account_id"],
    }


def _credentials(conn, tenant_id) -> dict:
    from app.routes.email_accounts import accounts_for_run_config
    from app.routes.google_accounts import accounts_for_run_config as google_accounts
    from app.routes.microsoft_accounts import accounts_for_run_config as microsoft_accounts
    from app.template_runtime import _embedding_spec, _payment_spec

    secret_rows = conn.execute(
        """SELECT name, value_encrypted, kind, meta FROM secrets
            WHERE tenant_id = %s AND is_active AND NOT is_deleted""",
        (tenant_id,),
    ).fetchall()
    datasource_rows = conn.execute(
        "SELECT * FROM datasources WHERE tenant_id = %s AND is_active AND NOT is_deleted",
        (tenant_id,),
    ).fetchall()
    datasources = [
        {
            "name": d["name"],
            "kind": d["kind"],
            "config": d["config"],
            "secret": decrypt(d["secret_encrypted"]) if d["secret_encrypted"] else None,
        }
        for d in datasource_rows
    ]
    email = accounts_for_run_config(conn, tenant_id)
    google = google_accounts(conn, tenant_id)
    microsoft = microsoft_accounts(conn, tenant_id)
    return {
        # segredos cadastrados + SSH/GCP/banco/e-mail/OAuth como variáveis nomeadas (H-5, H-7)
        "secrets": derive_env(secret_rows, datasources, email, google, microsoft, decrypt),
        "datasources": datasources,
        "payment": _payment_spec(conn, tenant_id),
        "email_accounts": email,
        "google_accounts": google,
        "microsoft_accounts": microsoft,
        "embedding": _embedding_spec(conn, tenant_id),
    }


def _catalog(credentials: dict) -> list[dict]:
    """Só rótulos: o agente sabe o que existe, nunca o valor."""
    out: list[dict] = []
    payment = credentials.get("payment") or {}
    if payment:
        label = "Mercado Pago" + (" (teste)" if payment.get("sandbox") else "")
        out.append({"kind": "mercado_pago", "label": label})
    for acc in credentials.get("email_accounts") or []:
        out.append({"kind": "email", "label": str(acc.get("label") or "E-mail")})
    for acc in credentials.get("google_accounts") or []:
        out.append(
            {
                "kind": "google",
                "label": str(acc.get("email_address") or acc.get("label") or "Google"),
            }
        )
    for acc in credentials.get("microsoft_accounts") or []:
        out.append(
            {
                "kind": "microsoft",
                "label": str(acc.get("email_address") or acc.get("label") or "Outlook"),
            }
        )
    for ds in credentials.get("datasources") or []:
        out.append({"kind": "datasource", "label": str(ds.get("name"))})
    for name in credentials.get("secrets") or {}:
        out.append({"kind": "secret", "label": str(name)})
    return out


def active_user_skills(conn, tenant_id, user_id, template_id) -> list[dict]:
    if not user_id:
        return []
    rows = conn.execute(
        """SELECT name, description, content, revision FROM user_skills
            WHERE tenant_id = %s AND user_id = %s AND status = 'active'
              AND (template_id IS NULL OR template_id = %s)
            ORDER BY name""",
        (tenant_id, user_id, template_id),
    ).fetchall()
    out, used = [], 0
    for r in rows:
        size = len(r["content"].encode("utf-8"))
        inline = used + size <= USER_SKILLS_INLINE_LIMIT
        used += size if inline else 0
        out.append(
            {
                "name": r["name"],
                "description": r["description"],
                "content": r["content"] if inline else None,
                "revision": r["revision"],
            }
        )
    return out


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _history(conn, chat_id: str) -> list[dict]:
    """Mensagens anteriores à atual (a última do usuário já foi gravada)."""
    rows = conn.execute(
        """SELECT role, content FROM (
               SELECT role, content, created_at FROM chat_messages
                WHERE chat_id = %s ORDER BY created_at DESC LIMIT %s
           ) t ORDER BY created_at""",
        (chat_id, HISTORY_MESSAGES + 1),
    ).fetchall()
    rows = list(rows)
    if rows and rows[-1]["role"] == "user":
        rows = rows[:-1]
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def prepare_harness_run(
    *,
    tenant_id,
    template_id,
    chat_id: str,
    message: str,
    user: dict | None = None,
    attachments: list[dict] | None = None,
    transcription: dict | None = None,
    channel: str = "web",
    actor: dict | None = None,
    message_id: str | None = None,
) -> dict:
    """Monta o payload v1, registra a run e emite o run_token.

    Devolve o payload (dict pronto para POST /v1/runs) com `run_id`.
    """
    run_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    user_id = str(user["id"]) if user else None
    with get_connection() as conn:
        version = conn.execute(
            """SELECT v.id, v.manifest, v.max_steps FROM templates t
                 JOIN template_versions v ON v.id = t.active_version_id
                WHERE t.id = %s AND t.tenant_id = %s AND NOT t.is_deleted
                  AND t.agent_kind = 'skills'""",
            (template_id, tenant_id),
        ).fetchone()
        if version is None:
            raise HTTPException(
                status_code=409, detail="Este agente ainda não tem uma versão publicada"
            )
        manifest = normalize_manifest(version["manifest"] or default_manifest())
        if user:
            ensure_can_run(manifest, user)  # quem pode usar este agente (spec 10, A-7)
        files = package_files(conn, version["id"], tenant_id, include_content=False)
        sha = package_sha(files)
        inline = sum(wire_size(f) for f in files) <= INLINE_PACKAGE_LIMIT
        if inline:
            files = package_files(conn, version["id"], tenant_id)
        base = settings.public_base_url.rstrip("/")
        package = {
            "sha256": sha,
            "files": [wire_file(f) for f in files] if inline else None,
            "url": None if inline else f"{base}/internal/runs/{run_id}/package",
        }
        grants = [
            {"kind": g["kind"], "id": str(g["ref_id"]) if g["ref_id"] else None}
            for g in conn.execute(
                "SELECT kind, ref_id FROM template_version_credentials WHERE version_id = %s",
                (version["id"],),
            ).fetchall()
        ]
        # Concessões explícitas (tabela) + as implícitas das ferramentas do manifesto.
        for kind in credential_kinds(manifest["tools"]):
            if not any(g["kind"] == kind for g in grants):
                grants.append({"kind": kind, "id": None})
        credentials = _credentials(conn, tenant_id)
        luar = _luar_access(conn, manifest, tenant_id, user_id, run_id)
        if luar:
            credentials["luar"] = luar  # o agente opera o Luar como o usuário do chat (spec 10 H-6)
        catalog = _catalog(credentials)
        if attachments:
            credentials["transcription"] = transcription or {}
        user_skills = active_user_skills(conn, tenant_id, user_id, template_id)
        first_run = (
            conn.execute(
                "SELECT 1 FROM chat_runs WHERE chat_id = %s AND kernel = 'harness' LIMIT 1",
                (chat_id,),
            ).fetchone()
            is None
        )
        # Conversas de canal externo (ext-...) não têm linhas em chat_messages.
        in_chats = _is_uuid(chat_id)
        history = _history(conn, chat_id) if first_run and in_chats else None
        if message_id is None and in_chats:
            row = conn.execute(
                """SELECT id FROM chat_messages WHERE chat_id = %s AND role = 'user'
                    ORDER BY created_at DESC LIMIT 1""",
                (chat_id,),
            ).fetchone()
            message_id = str(row["id"]) if row else None
        conn.execute(
            """INSERT INTO chat_runs (run_id, tenant_id, user_id, chat_id, template_id,
                                      version_id, kernel, idempotency_key)
               VALUES (%s, %s, %s, %s, %s, %s, 'harness', %s)""",
            (run_id, tenant_id, user_id, chat_id, template_id, version["id"], idempotency_key),
        )

    limits = {**DEFAULT_LIMITS, **(manifest.get("limits") or {})}
    model = manifest.get("model") or {}
    payload = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "tenant_id": str(tenant_id),
        "user_id": user_id,
        "actor": actor
        or {
            "type": "member",
            "name": (user or {}).get("name") or "",
            "role": "member",
        },
        "chat_id": chat_id,
        "channel": channel,
        "inbox_id": None,
        "message_id": message_id,
        "template": {"id": str(template_id), "version_id": str(version["id"]), "kind": "skills"},
        "message": message,
        "attachments": attachments or [],
        "model": {
            "alias": model.get("alias") or settings.harness_default_model or DEFAULT_MODEL_ALIAS,
            "reasoning_effort": "medium",
        },
        "skills": {
            "system_rev": settings.harness_system_skills_rev,
            "tenant_package": package,
            "user_skills": user_skills,
        },
        "manifest": {
            "tools": manifest["tools"],
            "credential_grants": grants,
            "environment": manifest["environment"],
            "limits": limits,
            "output_format": output_format_of(manifest),
        },
        "credentials": credentials,
        "credentials_catalog": catalog,
        "memory": {"recall": []},
        "run_token": issue_run_token(
            run_id=run_id,
            tenant_id=str(tenant_id),
            user_id=user_id,
            chat_id=chat_id,
            template_id=str(template_id),
            version_id=str(version["id"]),
        ),
    }
    if history is not None:
        payload["history"] = history
    return payload
