"""Rotinas agendadas do usuário (pelo agente ou pela tela): criar, editar, pausar, apagar."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app import scheduler
from app.auth import current_user
from app.permissions import require_scope_for_chatwoot

router = APIRouter(prefix="/api", tags=["schedules"])


@router.get("/schedules")
def list_schedules(user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.routines.view")
    if user["tenant_id"] is None:
        return {"schedules": []}
    return scheduler.op_list(user["tenant_id"], user["id"])


@router.post("/schedules/{schedule_id}/pause")
def pause(schedule_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.routines.manage")
    return scheduler.op_set_status(user["tenant_id"], user["id"], schedule_id, "paused")


@router.post("/schedules/{schedule_id}/resume")
def resume(schedule_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.routines.manage")
    return scheduler.op_set_status(user["tenant_id"], user["id"], schedule_id, "active")


@router.delete("/schedules/{schedule_id}")
def delete(schedule_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.routines.manage")
    return scheduler.op_delete(user["tenant_id"], user["id"], schedule_id)


@router.post("/schedules", status_code=201)
def create(body: dict = Body(...), user: dict = Depends(current_user)):
    """Cria uma rotina pela tela: horário em cron ou em frequência amigável."""
    require_scope_for_chatwoot(user, "ai.routines.manage")
    if user["tenant_id"] is None:
        raise HTTPException(status_code=403, detail="Rotinas exigem uma empresa")
    return scheduler.op_create_from_ui(user["tenant_id"], user["id"], body)


@router.put("/schedules/{schedule_id}")
def update(schedule_id: str, body: dict = Body(...), user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.routines.manage")
    return scheduler.op_update(user["tenant_id"], user["id"], schedule_id, body)


@router.get("/schedules/{schedule_id}/runs")
def runs(schedule_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.routines.view")
    return scheduler.op_runs(user["tenant_id"], user["id"], schedule_id)
