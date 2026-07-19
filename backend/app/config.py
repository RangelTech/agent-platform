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

    # Object storage (uploads). GCS when bucket set, local dir otherwise —
    # must match the kernel's settings so both sides read the same paths.
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

    # Master bootstrap. The password is only used when the master does not yet
    # exist; production must supply a real one via Secret Manager.
    # example.com is IANA-reserved for exactly this: a placeholder that is a
    # syntactically valid address nobody can receive mail at.
    master_email: str = "master@example.com"
    master_password: str = "admin123"
    master_name: str = "Master"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
