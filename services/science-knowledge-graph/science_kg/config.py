from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    spacy_model_ru: str = "ru_core_news_sm"
    spacy_model_en: str = "en_core_sci_sm"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    neo4j_database: str = "neo4j"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    max_pdf_size_mb: int = 10

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.proxyapi.ru/openai/v1"
    openai_model: str = "gpt-4o-mini"


settings = Settings()
