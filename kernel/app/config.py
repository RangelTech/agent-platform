from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Kernel settings. Dev defaults only; production values come from the
    environment (Cloud Run). Tenant/provider config lives in the database."""

    database_url: str = "postgresql://agent:agent@localhost:5433/agent_llm"
    port: int = 8080
    # Hard ceiling for one conversation turn, model call included.
    turn_timeout_seconds: float = 120.0
    # Default supervisor step budget per turn when the template omits it.
    max_steps_default: int = 6
    checkpoint_pool_size: int = 5
    # Shared secret for backend->kernel calls. Empty disables the check (dev).
    internal_token: str = ""
    # Exposes POST /stub/script so test suites can program the stub provider.
    enable_stub_control: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
