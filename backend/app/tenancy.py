"""Tenant scoping helpers.

Every non-master query is confined to the caller's tenant. The master may act
on any tenant, but must name it explicitly — there is no implicit global write.
"""

from fastapi import HTTPException


def resolve_target_tenant(user: dict, requested_tenant_id: str | None) -> str:
    """The tenant a write should land in.

    Non-master callers always write into their own tenant; a mismatching
    tenant_id in the payload is a permission error, not a silent override.
    """
    if user["is_master"]:
        if not requested_tenant_id:
            raise HTTPException(
                status_code=400, detail="Informe o tenant (tenant_id) para esta operação"
            )
        return requested_tenant_id

    if requested_tenant_id and str(requested_tenant_id) != str(user["tenant_id"]):
        raise HTTPException(status_code=403, detail="Permissão negada")
    return user["tenant_id"]
