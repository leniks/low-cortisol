from app.services.agent_service.main_chat import MainAgentChatService
from app.models.request_clarifier import RequestClarifier
from app.models.request_classifier import RequestClassifier


_main_agent_chat_service: MainAgentChatService | None = None
_request_clarifier: RequestClarifier | None = None
_request_classifier: RequestClassifier | None = None


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


def get_main_agent_chat_service() -> MainAgentChatService:
    global _main_agent_chat_service
    if _main_agent_chat_service is None:
        _main_agent_chat_service = MainAgentChatService(classifier=get_request_classifier())
    return _main_agent_chat_service
