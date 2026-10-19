"""Rotinas agendadas do usuário (criadas pelo agente): ver, pausar, retomar, apagar."""

from __future__ import annotations

from fastapi import APIRouter, Depends

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
