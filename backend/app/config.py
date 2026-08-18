from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    warehouse_url: str = "postgresql://warehouse_ro:warehouse@localhost:5433/semantic"
    app_url: str = "postgresql://app_rw:appsecret@localhost:5433/semantic"
    admin_url: str = "postgresql://postgres:postgres@localhost:5433/semantic"

    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5"

    layer_dir: Path = Path(__file__).parent / "layer" / "definitions"
    default_ttl_seconds: int = 900
    max_rows: int = 10_000


settings = Settings()
