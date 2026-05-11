from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class AgentSettings:
    yandex_folder_id: str
    yandex_api_key: str
    yandex_llm_base_url: str
    yandex_ai_base_url: str
    yandex_chat_model: str
    yandex_query_enricher_model: str
    yandex_query_embedding_model: str
    vector_database_url: str | None = None
    rag_vector_table: str = "rag_embeddings"
    rag_embedding_column: str = "embedding"
    rag_top_k: int = 30
    rag_rerank_max_keep: int = 10
    rag_description_chars: int = 450
    main_agent_max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "AgentSettings":
        load_dotenv()

        folder_id = os.getenv("YANDEX_FOLDER_ID", "b1goa02eskrgbk1pg322")
        api_key = os.getenv("YANDEX_API_KEY", "")
        if not api_key:
            raise RuntimeError("YANDEX_API_KEY is required")

        chat_model = os.getenv("YANDEX_CHAT_MODEL", f"gpt://{folder_id}/qwen3.6-35b-a3b/latest")

        return cls(
            yandex_folder_id=folder_id,
            yandex_api_key=api_key,
            yandex_llm_base_url=os.getenv("YANDEX_LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1"),
            yandex_ai_base_url=os.getenv("YANDEX_AI_BASE_URL", "https://llm.api.cloud.yandex.net/foundationModels/v1"),
            yandex_chat_model=chat_model,
            yandex_query_enricher_model=os.getenv("YANDEX_QUERY_ENRICHER_MODEL", chat_model),
            yandex_query_embedding_model=os.getenv(
                "YANDEX_QUERY_EMBEDDING_MODEL",
                f"emb://{folder_id}/text-search-query/latest",
            ),
            vector_database_url=os.getenv("POSTGRES_DSN") or os.getenv("VECTOR_DATABASE_URL") or os.getenv("DATABASE_URL"),
            rag_vector_table=os.getenv("RAG_VECTOR_TABLE", "rag_embeddings"),
            rag_embedding_column=os.getenv("RAG_EMBEDDING_COLUMN", "embedding"),
            rag_top_k=_int_env("RAG_TOP_K", _int_env("SEARCH_TOP_K", 30)),
            rag_rerank_max_keep=_int_env("RAG_RERANK_MAX_KEEP", 10),
            rag_description_chars=_int_env("RAG_DESCRIPTION_CHARS", _int_env("SEARCH_DESCRIPTION_CHARS", 450)),
            main_agent_max_attempts=_int_env("MAIN_AGENT_MAX_ATTEMPTS", 3),
        )


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default
