from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MathMod DataAgent"
    agent_service_url: str = Field("http://localhost:8001", env="AGENT_SERVICE_URL")
    database_url: str | None = Field(None, env="DATABASE_URL")
    chat_database_url: str | None = Field(None, env="CHAT_DATABASE_URL")
    chat_db_schema: str = Field("chat_history", env="CHAT_DB_SCHEMA")
    artifacts_dir: str = Field("artifacts", env="ARTIFACTS_DIR")
    mock_mode: bool = Field(True, env="MOCK_MODE")

    @property
    def resolved_chat_database_url(self) -> str | None:
        return self.chat_database_url or self.database_url

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
