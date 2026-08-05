"""Configuration base classes.

Services subclass :class:`ServiceSettings` and add their own fields. All settings come
from environment variables (12-factor), prefixed with ``MARSOOL_`` so that a shared
container environment cannot collide with unrelated variables.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment. Controls debug affordances and fail-fast checks."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        return self in (Environment.STAGING, Environment.PRODUCTION)


class ServiceSettings(BaseSettings):
    """Settings shared by every Marsool service."""

    model_config = SettingsConfigDict(
        env_prefix="MARSOOL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    service_name: str = "marsool-service"
    environment: Environment = Environment.LOCAL
    version: str = "0.1.0"

    # HTTP
    host: str = "0.0.0.0"
    port: int = 8000
    root_path: str = ""
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Logging / observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_traces_sample_ratio: float = Field(default=0.1, ge=0.0, le=1.0)

    # Data stores
    database_dsn: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://marsool:marsool@localhost:5432/marsool")
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_statement_timeout_ms: int = Field(default=10_000, ge=100)
    database_echo: bool = False
    redis_dsn: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    # Event bus. ``memory`` keeps local dev and tests hermetic; ``kafka`` is used in
    # staging/production. ``outbox`` writes to the transactional outbox table only.
    event_bus_backend: Literal["memory", "kafka", "noop"] = "memory"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str | None = None

    # Auth. HS256 with a shared secret keeps local dev simple; production uses RS256
    # with a private signing key in the auth service and public keys elsewhere.
    jwt_algorithm: Literal["HS256", "RS256"] = "HS256"
    jwt_secret: Annotated[str, Field(min_length=16)] = (
        "local-development-secret-change-me"  # noqa: S105 - local default, overridden in deploys
    )
    jwt_public_key: str | None = None
    jwt_private_key: str | None = None
    jwt_issuer: str = "marsool.auth"
    jwt_audience: str = "marsool.api"
    access_token_ttl_seconds: int = Field(default=900, ge=60)
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 30, ge=3600)

    # Rate limiting (per-identity token bucket applied at the gateway).
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=120, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string so K8s env vars stay simple."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def sync_database_dsn(self) -> str:
        """DSN using the sync driver, for Alembic migrations."""
        return str(self.database_dsn).replace("+asyncpg", "+psycopg2")

    @property
    def is_debug(self) -> bool:
        return not self.environment.is_production_like


@functools.cache
def get_settings(settings_class: type[ServiceSettings] = ServiceSettings) -> ServiceSettings:
    """Return a cached settings instance.

    Cached so that importing settings in dependencies does not re-read the
    environment on every request. Tests clear the cache via ``get_settings.cache_clear()``.
    """
    return settings_class()
