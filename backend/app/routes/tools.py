"""Catálogo de ferramentas que o manifesto de um agente por skills pode listar."""

from fastapi import APIRouter, Depends

from app.auth import require
from app.tool_catalog import catalog

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("/catalog")
def tools_catalog(user: dict = Depends(require("templates", "view"))):
    return catalog()
