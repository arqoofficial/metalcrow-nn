from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    REDIS_URL: str = "redis://redis:6379/0"
    POSTGRES_SERVER: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "app"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""

    SERVICE_NAME: str = "parse_docling"
    SERVICE_VERSION: str = "0.1.0"

    PARSER_URL: str = "http://parser-main:8114"
    PARSER_ENFORCE: bool = False
    PARSER_TIMEOUT_S: float = 60.0
    PARSER_POLL_INTERVAL_S: float = 3.0
    PARSER_POLL_TIMEOUT_S: float = 900.0
    PARSER_UPLOAD_WAIT_S: float = 30.0


settings = Settings()


def postgres_dsn() -> str:
    return (
        f"host={settings.POSTGRES_SERVER} "
        f"port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} "
        f"user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )
