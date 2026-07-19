from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Kernel settings. Dev defaults only; production values come from the
    environment (Cloud Run). Tenant/provider config lives in the database."""

    database_url: str = "postgresql://agent:agent@localhost:5433/agent_llm"
    port: int = 8080

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
