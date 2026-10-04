"""Configuration from the environment and `.env`. Secrets are never printed or logged."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    # `.env` sits at the repository root; commands run from there or from `backend/`.
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str | None = None  # overrides the POSTGRES_* values when set
    postgres_user: str = "productfoundry"
    postgres_password: str = "productfoundry"
    postgres_db: str = "productfoundry"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433

    # LLM providers. SecretStr keeps the values out of reprs and logs.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    # Web search for competitor discovery (decision D1: Tavily).
    tavily_api_key: SecretStr | None = None

    # The job queue (docker-compose.yml). Port 6380 on the host, like the database's 5433,
    # keeps clear of a Redis that may already run on the development machine.
    redis_url: str = "redis://127.0.0.1:6380/0"
    # The worker and the API run every stage as a stand-in: no search, store or LLM request.
    fake_stages: bool = False
    # Reviews the worker fetches per product per store in one run.
    review_cap: int = 2000

    # The browser that reads pricing pages: an installed one ("msedge", "chrome"), or
    # Playwright's own Chromium when unset (`playwright install chromium`).
    pricing_browser_channel: str | None = None

    def sqlalchemy_url(self) -> URL:
        if self.database_url:
            return make_url(self.database_url)
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
