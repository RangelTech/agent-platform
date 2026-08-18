from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Backend settings. Dev defaults only — production values come from the
    environment (Cloud Run) and, for anything sensitive, from the database or
    Secret Manager. No business config lives in env vars by design."""

    database_url: str = "postgresql://agent:agent@localhost:5433/agent_llm"
    kernel_url: str = "http://localhost:8080"
    port: int = 8090
    # Seconds to wait for a database connection before failing. Never unset:
    # an unreachable database must fail fast, not hang the process.
    db_connect_timeout: int = 5
    # Directory with the built SPA. Resolved relative to the repo in dev,
    # baked into the image in production.
    static_dir: str = ""

    # Sessions
    session_hours: int = 24
    session_idle_minutes: int = 120

    # Interim model config, replaced by per-tenant AI services in ticket 05.
    # provider "stub" (or an empty key) routes to the kernel's stub provider.
    ai_provider: str = "stub"
    # gemini-flash-latest: rolling alias — fixed models age out for new API
    # users (2.5-flash already returns 404 for keys created in 2026).
    ai_model: str = "gemini-flash-latest"
    ai_api_key: str = ""
    ai_system_prompt: str = (
        "Você é um assistente corporativo. Responda em português, de forma clara e objetiva."
    )
    # Shared secret for backend->kernel calls (dev; Cloud Run uses OIDC).
    kernel_internal_token: str = ""
    # When set (Cloud Run), the backend sends an OIDC ID token with this
    # audience on every kernel call. Usually the kernel's own URL.
    kernel_audience: str = ""
    # Fernet master key for secrets at rest (Secret Manager in production).
    encryption_key: str = ""
    # URL pública do backend (https://...). Só é necessária para montar o
    # notification_url que o gateway de pagamento chama de volta; vazio em dev
    # apenas desliga a confirmação por webhook, o polling continua funcionando.
    public_base_url: str = ""
    # Base da API do Mercado Pago; só muda em teste.
    mercado_pago_api: str = "https://api.mercadopago.com"

    # Camada omnichannel (Fase 2): a ponte que fala com o Chatwoot. Vazio
    # desliga a funcionalidade sem quebrar nada.
    bridge_url: str = ""
    bridge_admin_token: str = ""

    # Object storage (uploads). Priority: S3-compatible (MinIO) -> GCS -> local
    # dir. Must match the kernel's settings so both sides read the same paths.
    storage_backend: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_prefix: str = "agent-llm"
    s3_public_base_url: str = ""
    s3_presign_expires_seconds: int = 3600
    gcs_bucket: str = ""
    gcs_prefix: str = "agent llm"
    storage_local_dir: str = "../kernel/artifacts"
    # Max upload size for business files.
    max_upload_bytes: int = 50 * 1024 * 1024

    # Cloud Tasks queue path (projects/.../queues/...) for async work; empty
    # in dev, where a background task calls the kernel directly.
    cloud_tasks_queue: str = ""
    # Audience/SA for Cloud Tasks -> kernel OIDC calls.
    tasks_service_account: str = ""

    # Automatic 9Router provisioning on tenant creation (infra-06). Off by
    # default: it shells out to `scripts/provisionar_router.py`, which SSHes
    # into the VPS and touches DNS/TLS — must never fire in dev/tests unless
    # explicitly turned on where the VPS access actually exists.
    router_auto_provision_enabled: bool = False
    router_provision_timeout_seconds: int = 600

    # Master bootstrap. The password is only used when the master does not yet
    # exist; production must supply a real one via Secret Manager.
    # example.com is IANA-reserved for exactly this: a placeholder that is a
    # syntactically valid address nobody can receive mail at.
    master_email: str = "master@example.com"
    master_password: str = "admin123"
    master_name: str = "Master"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
