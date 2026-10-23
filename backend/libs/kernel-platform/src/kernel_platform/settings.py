"""Process configuration. Credential values belong in Docker secret files."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOTFLOW_", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"
    service: Literal["identity", "tasks", "automation"] = "identity"
    secrets_dir: Path = Path("/run/secrets")
    db_host: str | None = None
    db_port: int = 5432
    rabbitmq_host: str = "rabbitmq"
    redis_host: str = "redis"
    s3_endpoint: str = "http://minio:9000"
    s3_bucket: str = "botflow-audio"

    def database_url(self) -> URL:
        """Own-database URL; the password is read from the secret file here."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.service,
            password=self.secret(f"{self.service}_db_password").get_secret_value(),
            host=self.db_host or f"{self.service}-db",
            port=self.db_port,
            database=self.service,
        )

    def secret(self, name: str) -> SecretStr:
        """Read credentials only when opening a resource, never in model repr."""
        value = (self.secrets_dir / name).read_text().strip()
        if not value:
            raise ValueError(f"Empty secret file: {name}")
        return SecretStr(value)
