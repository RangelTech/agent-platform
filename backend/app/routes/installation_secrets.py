"""Segredos de instalação: cadastro pela tela, cifrado, só para o master.

São chaves da instalação inteira (app da Meta, Serper, S3), não de um tenant —
por isso master-only. Segredo de tenant continua em `/api/secrets`.

O valor NUNCA volta, nem para o master. Quem precisa dele é o serviço, não a
pessoa: a tela mostra nome, versão, data e o estado da propagação.
"""

import json
import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import require
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.installation_secrets import SOMENTE_AMBIENTE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/installation-secrets", tags=["installation-secrets"])

_NOME = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")

# Destinos conhecidos. Um segredo com `chatwoot` em `targets` é propagado para
# o `InstallationConfig` de lá; sem isso ele só vale para nós.
DESTINOS = {"chatwoot"}


class SegredoIn(BaseModel):
    name: str
    value: str = Field(min_length=1, max_length=20_000)
    targets: list[str] = Field(default_factory=list)
    description: str = ""


class SegredoUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=20_000)
    targets: list[str] | None = None
    is_active: bool | None = None


def _somente_master(user: dict) -> None:
    if not user.get("is_master"):
        raise HTTPException(status_code=403, detail="Somente o master gerencia estes segredos")


def _serialize(linha: dict, sincronia: list[dict]) -> dict:
    return {
        "id": str(linha["id"]),
        "name": linha["name"],
        "description": linha["description"],
        "targets": linha["targets"],
        "version": linha["version"],
        "is_active": linha["is_active"],
        "updated_at": linha["updated_at"].isoformat(),
        # Nunca o valor. Só se ele existe e para onde precisa ir.
        "sync": [
            {
                "target": s["target"],
                "status": s["status"],
                "detail": s["detail"],
                "synced_version": s["synced_version"],
            }
            for s in sincronia
            if s["secret_id"] == linha["id"]
        ],
    }


@router.get("")
def listar(user: dict = Depends(require("secrets", "view"))):
    _somente_master(user)
    with get_connection() as conn:
        linhas = conn.execute(
            "SELECT * FROM installation_secrets ORDER BY name"
        ).fetchall()
        sincronia = conn.execute("SELECT * FROM secret_sync_state").fetchall()
    return [_serialize(linha, sincronia) for linha in linhas]


@router.post("", status_code=201)
def criar(payload: SegredoIn, user: dict = Depends(require("secrets", "create"))):
    _somente_master(user)
    nome = payload.name.strip().upper()
    if not _NOME.match(nome):
        raise HTTPException(status_code=400, detail="Nome inválido (A-Z, 0-9, _)")
    if nome in SOMENTE_AMBIENTE:
        # Guardar aqui a chave que abre este banco é impossível por construção;
        # aceitar o cadastro criaria um valor que nada lê e a falsa sensação de
        # que o Secret Manager já pode ser desligado.
        raise HTTPException(
            status_code=400,
            detail=f"{nome} é segredo de bootstrap: só pode vir do ambiente",
        )
    desconhecidos = set(payload.targets) - DESTINOS
    if desconhecidos:
        raise HTTPException(status_code=400, detail=f"Destino desconhecido: {desconhecidos}")

    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO installation_secrets
                   (name, value_encrypted, targets, description, created_by)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE
                   SET value_encrypted = EXCLUDED.value_encrypted,
                       targets = EXCLUDED.targets,
                       description = EXCLUDED.description,
                       version = installation_secrets.version + 1,
                       is_active = TRUE,
                       updated_at = now()
               RETURNING *""",
            (
                nome,
                encrypt(payload.value),
                json.dumps(payload.targets),
                payload.description,
                user["id"],
            ),
        ).fetchone()
        _marcar_pendente(conn, linha)
        sincronia = conn.execute(
            "SELECT * FROM secret_sync_state WHERE secret_id = %s", (linha["id"],)
        ).fetchall()
    return _serialize(linha, sincronia)


@router.put("/{secret_id}")
def atualizar(
    secret_id: str, payload: SegredoUpdate, user: dict = Depends(require("secrets", "edit"))
):
    _somente_master(user)
    campos, valores = [], []
    if payload.value is not None:
        campos.append("value_encrypted = %s")
        valores.append(encrypt(payload.value))
        campos.append("version = version + 1")
    if payload.targets is not None:
        campos.append("targets = %s")
        valores.append(json.dumps(payload.targets))
    if payload.is_active is not None:
        campos.append("is_active = %s")
        valores.append(payload.is_active)
    if not campos:
        raise HTTPException(status_code=400, detail="Nada para atualizar")

    with get_connection() as conn:
        linha = conn.execute(
            f"UPDATE installation_secrets SET {', '.join(campos)}, updated_at = now() "
            "WHERE id = %s RETURNING *",
            (*valores, secret_id),
        ).fetchone()
        if linha is None:
            raise HTTPException(status_code=404, detail="Segredo não encontrado")
        _marcar_pendente(conn, linha)
        sincronia = conn.execute(
            "SELECT * FROM secret_sync_state WHERE secret_id = %s", (secret_id,)
        ).fetchall()
    return _serialize(linha, sincronia)


@router.delete("/{secret_id}", status_code=204)
def remover(secret_id: str, user: dict = Depends(require("secrets", "delete"))):
    _somente_master(user)
    with get_connection() as conn:
        conn.execute("DELETE FROM installation_secrets WHERE id = %s", (secret_id,))


def _marcar_pendente(conn, linha: dict) -> None:
    """Toda escrita reabre a propagação para os destinos do segredo.

    Marcar como pendente antes de tentar entregar é o que impede o pior caso:
    a entrega falhar e ninguém saber, porque o registro dizia que estava tudo
    certo desde a versão anterior.
    """
    for destino in linha["targets"] or []:
        conn.execute(
            """INSERT INTO secret_sync_state (secret_id, target, status, detail)
               VALUES (%s, %s, 'pending', '')
               ON CONFLICT (secret_id, target) DO UPDATE
                   SET status = 'pending', detail = ''""",
            (linha["id"], destino),
        )


def _autenticar_servico(authorization: str = Header(default="")) -> None:
    """Fronteira máquina a máquina, com a mesma credencial da camada omnichannel.

    Não é sessão de usuário: quem chama é um job do Chatwoot, sem navegador e
    sem tenant. Reusar o token da ponte evita inventar mais um segredo para
    guardar — e ele já é exatamente a credencial que liga estes dois lados.
    """
    esperado = settings.bridge_admin_token
    if not esperado or authorization != f"Bearer {esperado}":
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/for-chatwoot", dependencies=[Depends(_autenticar_servico)])
def para_o_chatwoot():
    """Nome -> valor dos segredos destinados ao Chatwoot.

    É o único lugar em que um valor sai daqui em claro, e sai para uma máquina
    dentro da nossa infraestrutura, autenticada. O job aplica no
    `InstallationConfig` e confirma em `/chatwoot-ack`.
    """
    with get_connection() as conn:
        linhas = conn.execute(
            """SELECT name, value_encrypted FROM installation_secrets
                WHERE is_active AND targets ? 'chatwoot'"""
        ).fetchall()
    return {linha["name"]: decrypt(linha["value_encrypted"]) for linha in linhas}


class AckIn(BaseModel):
    applied: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    error: str = ""


@router.post("/chatwoot-ack", dependencies=[Depends(_autenticar_servico)])
def confirmar_chatwoot(payload: AckIn):
    """O job diz o que conseguiu aplicar.

    Sem esta confirmação, "entreguei o valor" viraria sinônimo de "está valendo
    lá" — e a diferença entre as duas é exatamente onde mora a falha que ninguém
    percebe.
    """
    with get_connection() as conn:
        for nome in payload.applied:
            conn.execute(
                """UPDATE secret_sync_state s
                      SET status = 'ok', detail = '', attempted_at = now(),
                          synced_version = i.version
                     FROM installation_secrets i
                    WHERE s.secret_id = i.id AND i.name = %s AND s.target = 'chatwoot'""",
                (nome,),
            )
        for nome in payload.skipped:
            conn.execute(
                """UPDATE secret_sync_state s
                      SET status = 'error', attempted_at = now(),
                          detail = 'config travada no Chatwoot (locked)'
                     FROM installation_secrets i
                    WHERE s.secret_id = i.id AND i.name = %s AND s.target = 'chatwoot'""",
                (nome,),
            )
        if payload.error:
            conn.execute(
                """UPDATE secret_sync_state SET status = 'error', detail = %s,
                       attempted_at = now()
                    WHERE target = 'chatwoot' AND status <> 'ok'""",
                (payload.error[:500],),
            )
    return {"status": "ok"}
