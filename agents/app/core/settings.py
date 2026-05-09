from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class AgentSettings:
    yandex_folder_id: str
    yandex_api_key: str
    yandex_llm_base_url: str
    yandex_chat_model: str
    database_url: str | None = None
    vector_database_url: str | None = None
    s3_bucket: str | None = None

    @classmethod
    def from_env(cls) -> "AgentSettings":
        load_dotenv()

        folder_id = os.getenv("YANDEX_FOLDER_ID", "b1goa02eskrgbk1pg322")
        api_key = os.getenv("YANDEX_API_KEY", "")
        if not api_key:
            raise RuntimeError("YANDEX_API_KEY is required")

        return cls(
            yandex_folder_id=folder_id,
            yandex_api_key=api_key,
            yandex_llm_base_url=os.getenv("YANDEX_LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1"),
            yandex_chat_model=os.getenv(
                "YANDEX_CHAT_MODEL",
                f"gpt://{folder_id}/qwen3.6-35b-a3b/latest",
            ),
            database_url=os.getenv("DATABASE_URL"),
            vector_database_url=os.getenv("VECTOR_DATABASE_URL") or os.getenv("DATABASE_URL"),
            s3_bucket=os.getenv("S3_BUCKET"),
        )

