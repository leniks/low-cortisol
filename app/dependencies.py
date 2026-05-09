from app.core.settings import Settings
from app.services.agent_service import AgentService
from app.services.dialog_store import DialogStore


_settings: Settings | None = None
_agent_service: AgentService | None = None
_dialog_store: DialogStore | None = None


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
        _dialog_store = DialogStore()
    return _dialog_store
