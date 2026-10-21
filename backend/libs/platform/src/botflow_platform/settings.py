"""Process configuration. Credential values belong in Docker secret files."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOTFLOW_", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"
