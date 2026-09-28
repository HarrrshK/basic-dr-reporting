from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str | None = None
    api_access_token: str | None = None
    remote_api_url: str | None = None
    backup_database_url: str | None = None
    backup_interval_seconds: int = 300
    backup_batch_size: int = 500
    backup_local_dir: str = "backups"
    backup_provider: str | None = None
    backup_bucket: str | None = None
    backup_prefix: str = "field-reports"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str | None = None
    aws_endpoint_url: str | None = None
    cors_origins: str = "http://localhost:5173"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        value = self.database_url
        if not value:
            raise ValueError("DATABASE_URL is required for the application database")
        return self.normalize_postgres_url(value)

    @property
    def backup_sqlalchemy_url(self) -> str:
        if not self.backup_database_url:
            raise ValueError("BACKUP_DATABASE_URL is required for the laptop backup agent")
        return self.normalize_postgres_url(self.backup_database_url, require_ssl=False)

    @staticmethod
    def normalize_postgres_url(value: str, require_ssl: bool = True) -> str:
        from sqlalchemy.engine import make_url

        if value.startswith("postgres://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgres://")
        elif value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
        url = make_url(value)
        if require_ssl and url.drivername.startswith("postgresql") and "sslmode" not in url.query:
            url = url.update_query_dict({"sslmode": "require"})
        return url.render_as_string(hide_password=False)

    @property
    def external_backup_configured(self) -> bool:
        return bool(
            self.backup_provider == "s3"
            and self.backup_bucket
            and self.aws_access_key_id
            and self.aws_secret_access_key
            and self.aws_region
        )


settings = Settings()
