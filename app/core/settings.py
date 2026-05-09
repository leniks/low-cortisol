from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MathMod DataAgent"
    agent_service_url: str = Field("http://localhost:8001", env="AGENT_SERVICE_URL")
    mock_mode: bool = Field(True, env="MOCK_MODE")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
