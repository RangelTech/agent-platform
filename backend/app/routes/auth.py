from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import authenticate, current_user, resolve_session, revoke_session
from app.schemas import LoginRequest, LoginResponse
from app.services import chatwoot_sso

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    token = authenticate(payload.email, payload.password, client=payload.client)
    if token is None:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")

    # Sessão sincronizada RAgentes<->RAtende (produto-05 seção 6c): usuário
    # master não tem tenant/conta no RAtende, fica None nesse caso.
    chatwoot_sso_url = None
    user = resolve_session(token)
    if user and user["tenant_id"]:
        chatwoot_sso_url = await chatwoot_sso.login_url(str(user["tenant_id"]), str(user["id"]))

    return LoginResponse(token=token, chatwoot_sso_url=chatwoot_sso_url)


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "name": user["name"],
        "is_master": user["is_master"],
        "tenant_id": str(user["tenant_id"]) if user["tenant_id"] else None,
        "permissions": user["permissions"] or {},
        "branding": {
            "name": user.get("brand_name") or user.get("tenant_name") or "",
            "tenant_key": user.get("tenant_key") or "",
            "has_logo": bool(user.get("brand_logo_url")),
            "color": user.get("brand_color") or "",
            "theme": user.get("brand_theme") or "light",
        },
    }


@router.post("/heartbeat", status_code=204)
def heartbeat(user: dict = Depends(current_user)):
    """Renew the current session after verified human activity in the SPA.

    `current_user` resolves the bearer token and performs the atomic sliding
    renewal. The endpoint deliberately has no business payload or side effect.
    """
    return None


@router.post("/logout")
async def logout(request: Request, user: dict = Depends(current_user)):
    revoke_session(request.headers["authorization"].split(" ", 1)[1])
    if user["tenant_id"]:
        await chatwoot_sso.logout(str(user["tenant_id"]), str(user["id"]))
    return {"status": "ok"}
