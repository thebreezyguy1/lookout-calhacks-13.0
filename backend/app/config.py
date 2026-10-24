"""Settings loaded from environment variables (and a local .env file)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    nessie_api_key: str = ""
    nessie_base_url: str = "http://api.nessieisreal.com"

    crs_username: str = ""
    crs_password: str = ""
    crs_base_url: str = "https://api-sandbox.stitchcredit.com:443/api"

    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    risk_window_hours: int = 72


settings = Settings()
