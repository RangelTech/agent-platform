from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import authenticate, current_user, revoke_session
from app.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    token = authenticate(payload.email, payload.password)
    if token is None:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    return LoginResponse(token=token)


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
            "theme": user.get("brand_theme") or "dark",
        },
    }


@router.post("/logout")
def logout(request: Request, user: dict = Depends(current_user)):
    revoke_session(request.headers["authorization"].split(" ", 1)[1])
    return {"status": "ok"}
