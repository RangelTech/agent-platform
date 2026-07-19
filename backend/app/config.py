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

    # Master bootstrap. The password is only used when the master does not yet
    # exist; production must supply a real one via Secret Manager.
    # example.com is IANA-reserved for exactly this: a placeholder that is a
    # syntactically valid address nobody can receive mail at.
    master_email: str = "master@example.com"
    master_password: str = "admin123"
    master_name: str = "Master"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
