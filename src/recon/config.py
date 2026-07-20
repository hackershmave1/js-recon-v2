"""Runtime configuration, read once from the environment.

Everything tunable at the platform level lives here so no dynamic fact is
hardcoded elsewhere. Provider/LLM config is deliberately NOT here — that is
user-supplied at runtime per-run (REQ-L1) and arrives in a later slice.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECON_", env_file=".env", extra="ignore"
    )

    env: str = "local"
    log_level: str = "INFO"

    # The app/workers connect as a NON-superuser role so row-level security is
    # actually enforced (a Postgres superuser bypasses RLS). Migrations and
    # bootstrap use the owning admin role.
    database_url: str = "postgresql+psycopg2://recon_app:recon_app@localhost:5432/recon"
    database_admin_url: str = "postgresql+psycopg2://recon:recon@localhost:5432/recon"
    redis_url: str = "redis://localhost:6379/0"

    # Largest JS upload the API will store per run. Bounds worker memory (REQ-Q5),
    # since the analyze stage reads the whole blob into memory. This is an
    # application cap, not an ingress body limit — see runs_router upload NOTE.
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MiB

    # Object storage — blobs are referenced by key, never stored in a row (REQ-D2).
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "recon-artifacts"
    s3_region: str = "us-east-1"

    # Realtime / durability (REQ-R2, REQ-R3).
    heartbeat_interval_seconds: float = 5.0
    heartbeat_stall_threshold_seconds: float = 30.0
    event_stream_maxlen: int = 10_000

    # Queue retry policy (REQ-Q2).
    retry_max_attempts: int = 5
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
