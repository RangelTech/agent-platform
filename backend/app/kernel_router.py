"""Escolha do kernel que atende uma conversa.

Dois tipos de agente convivem: `structured` (supervisor + especialistas, kernel
de sempre) e `skills` (pacote de arquivos Markdown, segundo kernel). Esta é a
única parte do backend que sabe qual URL, audiência e credencial pertence a
cada um; chats, API pública e tarefas assíncronas só perguntam aqui.

Vocabulário: `agent_kind` é o que a API expõe ('structured'|'skills'); `kind`
interno do roteador é 'graph'|'skills'; `chat_kernel` é o que fica gravado em
`chats.kernel` ('llm'|'harness').
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.config import settings

UNAVAILABLE = "Agentes por skills indisponíveis no momento"
NOT_ENABLED = "Recurso não habilitado para sua empresa"
SWITCH_CONFLICT = (
    "Este chat usa outro tipo de agente. Abra uma nova conversa para usar este agente."
)


@dataclass(frozen=True)
class KernelTarget:
    kind: str  # 'graph' | 'skills'
    chat_kernel: str  # 'llm' | 'harness'
    url: str
    audience: str
    static_token: str

    @property
    def runs_url(self) -> str:
        return f"{self.url.rstrip('/')}/v1/runs"


def kind_of(agent_kind: str | None) -> str:
    return "skills" if agent_kind == "skills" else "graph"


def chat_kernel_of(kind: str) -> str:
    return "harness" if kind == "skills" else "llm"


def kind_of_chat_kernel(kernel: str | None) -> str | None:
    if kernel == "harness":
        return "skills"
    if kernel == "llm":
        return "graph"
    return None


def _target(kind: str) -> KernelTarget:
    if kind == "skills":
        if not settings.kernel_harness_url:
            raise HTTPException(status_code=503, detail=UNAVAILABLE)
        return KernelTarget(
            "skills",
            "harness",
            settings.kernel_harness_url,
            settings.kernel_harness_audience,
            settings.kernel_harness_token,
        )
    return KernelTarget(
        "graph",
        "llm",
        settings.kernel_url,
        settings.kernel_audience,
        settings.kernel_internal_token,
    )


def resolve_kernel(template: dict | None) -> KernelTarget:
    """Kernel de um template (linha de `templates`) ou o padrão sem template."""
    return _target(kind_of((template or {}).get("agent_kind")))


def target_for_kind(kind: str) -> KernelTarget:
    return _target(kind)


def target_for_payload(kernel_payload: dict) -> KernelTarget:
    """Payloads do segundo kernel carregam `contract_version`."""
    return _target("skills" if "contract_version" in kernel_payload else "graph")


async def kernel_headers(target: KernelTarget) -> dict:
    if target.audience:
        from app.gcp_auth import id_token_for

        return {"Authorization": f"Bearer {await id_token_for(target.audience)}"}
    if target.static_token:
        return {"Authorization": f"Bearer {target.static_token}"}
    return {}


def template_row(conn, tenant_id, template_id) -> dict | None:
    if not template_id:
        return None
    return conn.execute(
        """SELECT id, tenant_id, agent_kind, active_version_id, system_key, name
             FROM templates WHERE id = %s AND tenant_id = %s AND NOT is_deleted""",
        (template_id, tenant_id),
    ).fetchone()


def tenant_skills_enabled(conn, tenant_id) -> bool:
    row = conn.execute("SELECT harness_enabled FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    return bool(row and row["harness_enabled"])


def ensure_skills_enabled(conn, tenant_id) -> None:
    if not tenant_skills_enabled(conn, tenant_id):
        raise HTTPException(status_code=403, detail=NOT_ENABLED)
