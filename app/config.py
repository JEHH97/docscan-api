"""Application configuration, loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings with safe development defaults."""

    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    app_env: str = "development"
    redis_url: str = "redis://localhost:6379/0"
    storage_backend: str = "local"
    local_storage_dir: str = "artifacts"
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    result_ttl_seconds: int = Field(default=60 * 60, gt=0)
    ocr_languages: str = "spa+eng"
    api_key: SecretStr = SecretStr("")
    webhooks_enabled: bool = True
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = Field(default=10, ge=0)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "demo-access"
    s3_secret_access_key: SecretStr = SecretStr("demo-secret")
    s3_bucket: str = "docscan-artifacts"
    s3_region: str = "us-east-1"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse the configured comma-separated CORS origin list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application configuration."""
    return Settings()
