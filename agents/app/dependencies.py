from app.core.settings import AgentSettings
from app.integrations.vector_db import PgVectorDatasetRepository
from app.integrations.yandex_embeddings import YandexEmbeddingClient
from app.models.query_enricher import QueryEnricher
from app.models.rag_retriever import PgVectorRagRetriever
from app.services.agent_service.main_chat import MainAgentChatService


_main_agent_chat_service: MainAgentChatService | None = None
_query_enricher: QueryEnricher | None = None
_rag_retriever: PgVectorRagRetriever | None = None


def get_query_enricher() -> QueryEnricher:
    global _query_enricher
    if _query_enricher is None:
        _query_enricher = QueryEnricher()
    return _query_enricher


def get_rag_retriever() -> PgVectorRagRetriever:
    global _rag_retriever
    if _rag_retriever is None:
        settings = AgentSettings.from_env()
        if not settings.vector_database_url:
            raise RuntimeError("POSTGRES_DSN, VECTOR_DATABASE_URL, or DATABASE_URL is required for RAG retrieval")

        embedder = YandexEmbeddingClient(
            api_key=settings.yandex_api_key,
            base_url=settings.yandex_ai_base_url,
            folder_id=settings.yandex_folder_id,
            model=settings.yandex_query_embedding_model,
        )
        repository = PgVectorDatasetRepository(
            dsn=settings.vector_database_url,
            table_name=settings.rag_vector_table,
            embedding_column=settings.rag_embedding_column,
            description_chars=settings.rag_description_chars,
        )
        _rag_retriever = PgVectorRagRetriever(
            embedder=embedder,
            repository=repository,
            top_k=settings.rag_top_k,
        )
    return _rag_retriever


def get_main_agent_chat_service() -> MainAgentChatService:
    global _main_agent_chat_service
    if _main_agent_chat_service is None:
        _main_agent_chat_service = MainAgentChatService(
            query_enricher=get_query_enricher(),
            rag_retriever_factory=get_rag_retriever,
        )
    return _main_agent_chat_service
