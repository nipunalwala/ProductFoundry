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
