from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str | None = None
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "dr_reporting"
    postgres_user: str = "postgres"
    postgres_password: str | None = None
    postgres_connect_timeout: int = 3
    api_access_token: str | None = None
    sqlite_queue_url: str | None = None
    remote_api_url: str | None = None
    laptop_agent_poll_seconds: float = 3.0
    sync_poll_seconds: float = 2.0
    sync_batch_size: int = 50
    sync_max_backoff_seconds: int = 300
    cors_origins: str = "http://localhost:5173"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def permanent_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        from urllib.parse import quote_plus
        if not self.postgres_password:
            raise ValueError("POSTGRES_PASSWORD is required when DATABASE_URL is not set")
        return (f"postgresql+psycopg://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
                f"?connect_timeout={self.postgres_connect_timeout}")


settings = Settings()
