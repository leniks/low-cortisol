from app.core.settings import Settings
from app.services.agent_service import AgentService
from app.services.artifact_store import ArtifactStore, MemoryArtifactStore, PostgresArtifactStore
from app.services.dialog_store import DialogStore, MemoryDialogStore, PostgresDialogStore


_settings: Settings | None = None
_agent_service: AgentService | None = None
_dialog_store: DialogStore | None = None
_artifact_store: ArtifactStore | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_agent_service() -> AgentService:
    global _agent_service
    if _agent_service is None:
        settings = get_settings()
        _agent_service = AgentService(settings)
    return _agent_service


def get_dialog_store() -> DialogStore:
    global _dialog_store
    if _dialog_store is None:
        settings = get_settings()
        chat_database_url = settings.resolved_chat_database_url
        if chat_database_url:
            _dialog_store = PostgresDialogStore(chat_database_url, schema=settings.chat_db_schema)
        else:
            _dialog_store = MemoryDialogStore()
    return _dialog_store


def get_artifact_store() -> ArtifactStore:
    global _artifact_store
    if _artifact_store is None:
        settings = get_settings()
        chat_database_url = settings.resolved_chat_database_url
        if chat_database_url:
            _artifact_store = PostgresArtifactStore(
                chat_database_url,
                schema=settings.chat_db_schema,
                artifacts_dir=settings.artifacts_dir,
            )
        else:
            _artifact_store = MemoryArtifactStore(settings.artifacts_dir)
    return _artifact_store
