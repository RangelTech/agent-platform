from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    token: str
    # Sessão sincronizada RAgentes<->RAtende (produto-05 seção 6c): link de
    # SSO de uso único pro frontend estampar a sessão do RAtende num iframe
    # oculto. None quando não há tenant/RAtende configurado (ex. master).
    chatwoot_sso_url: str | None = None


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tenant_key: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permissions: dict[str, list[str]] = Field(default_factory=dict)
    # Master only: which tenant the profile belongs to.
    tenant_id: str | None = None


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    permissions: dict[str, list[str]] | None = None
    is_active: bool | None = None


class UserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    profile_id: str | None = None
    # Master only: which tenant the user belongs to.
    tenant_id: str | None = None


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    profile_id: str | None = None
    is_active: bool | None = None
