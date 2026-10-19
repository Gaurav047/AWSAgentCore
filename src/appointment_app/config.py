"""Runtime configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo


def load_env() -> str | None:
    """Load `.env` from the current directory (or the nearest parent that has one).

    Variables already set in the environment win, so `.env` only fills in gaps. Set
    APP_SKIP_DOTENV=1 to disable (the tests do this). Returns the loaded path, if any.
    """
    if os.environ.get("APP_SKIP_DOTENV"):
        return None
    from dotenv import find_dotenv, load_dotenv

    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)
    return path or None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    aws_region: str
    bedrock_model_id: str
    max_tokens: int
    timezone: str

    storage_backend: str  # "sqlite" | "dynamodb"
    sqlite_path: str
    dynamodb_table: str

    # Service endpoints. Defaults match `docker compose up` / `appointment-app <service>` locally.
    mcp_url: str
    mcp_bearer_token: str | None
    scheduler_a2a_url: str
    reminder_a2a_url: str
    a2a_bearer_token: str | None

    agentcore_memory_id: str | None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        aws_region=_env("AWS_REGION", "us-east-1"),
        # Bedrock model ID or inference-profile ID (e.g. a "us." / "global." prefixed profile).
        bedrock_model_id=_env("BEDROCK_MODEL_ID", "anthropic.claude-opus-5"),
        max_tokens=int(_env("BEDROCK_MAX_TOKENS", "16000")),
        timezone=_env("APP_TIMEZONE", "UTC"),
        storage_backend=_env("STORAGE_BACKEND", "sqlite"),
        sqlite_path=_env("SQLITE_PATH", "appointments.db"),
        dynamodb_table=_env("DYNAMODB_TABLE", "appointments"),
        mcp_url=_env("MCP_URL", "http://localhost:8000/mcp"),
        mcp_bearer_token=_env("MCP_BEARER_TOKEN"),
        scheduler_a2a_url=_env("SCHEDULER_A2A_URL", "http://localhost:9001"),
        reminder_a2a_url=_env("REMINDER_A2A_URL", "http://localhost:9002"),
        a2a_bearer_token=_env("A2A_BEARER_TOKEN"),
        agentcore_memory_id=_env("AGENTCORE_MEMORY_ID"),
    )
