from app.core.settings import AgentSettings
from app.integrations.vector_db import PgVectorDatasetRepository
from app.integrations.yandex_embeddings import YandexEmbeddingClient
from app.models.parquet_reranker import ParquetReranker
from app.models.query_enricher import QueryEnricher
from app.models.rag_retriever import PgVectorRagRetriever
from app.services.agent_service.main_chat import MainAgentChatService
from app.models.request_clarifier import RequestClarifier
from app.models.request_classifier import RequestClassifier


_main_agent_chat_service: MainAgentChatService | None = None
_request_clarifier: RequestClarifier | None = None
_request_classifier: RequestClassifier | None = None
_query_enricher: QueryEnricher | None = None
_rag_retriever: PgVectorRagRetriever | None = None
_parquet_reranker: ParquetReranker | None = None


def get_request_clarifier() -> RequestClarifier:
    global _request_clarifier
    if _request_clarifier is None:
        _request_clarifier = RequestClarifier()
    return _request_clarifier


def get_request_classifier() -> RequestClassifier:
    global _request_classifier
    if _request_classifier is None:
        _request_classifier = RequestClassifier()
    return _request_classifier


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


def get_parquet_reranker() -> ParquetReranker:
    global _parquet_reranker
    if _parquet_reranker is None:
        _parquet_reranker = ParquetReranker()
    return _parquet_reranker


def get_main_agent_chat_service() -> MainAgentChatService:
    global _main_agent_chat_service
    if _main_agent_chat_service is None:
        _main_agent_chat_service = MainAgentChatService(
            classifier=get_request_classifier(),
            query_enricher=get_query_enricher(),
            rag_retriever_factory=get_rag_retriever,
            parquet_reranker=get_parquet_reranker(),
        )
    return _main_agent_chat_service
