"""Central settings, read from environment variables (prefix ``CSHUB_``) or ``.env``."""

from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CSHUB_", env_file=".env", extra="ignore")

    db_path: Path = ROOT / "data" / "cshub.db"
    port: int = 4701
    cors_origins: str = "http://localhost:4701"
    # /docs and /openapi.json answer without login and describe every route,
    # so they are off unless explicitly enabled (local development).
    expose_docs: bool = False

    # Pin "today" for demos and screenshots; empty = the real date in UTC-3.
    demo_today: date | None = None

    # Per-city cache of the live sync payload (ticketing + journeys). 0 disables.
    cache_ttl_seconds: int = 300
    # Usage warehouse answers. Its ETL runs once a day, so 15 min is plenty.
    usage_cache_ttl_seconds: int = 900
    # Session issued by POST /api/auth/login.
    session_ttl_seconds: int = 8 * 60 * 60

    # The fake ticketing API mimics the real one: only the N most recent tickets
    # come back per query. This is why the hub merges by id instead of replacing.
    ticketing_recent_limit: int = 50

    # Directory service account (searches the user's DN; never validates the
    # user's own password — that is a second bind with the DN found).
    directory_service_user: str = "svc-cshub"
    directory_service_password: str = "svc-demo-only"
    directory_identifier_attribute: str = "uid"

    # Optional advisor adapter. No provider ships in this repo: with an empty
    # provider (the default) the endpoint answers 503 "disabled".
    advisor_provider: str = ""
    advisor_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
