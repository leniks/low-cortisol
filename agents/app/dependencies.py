from app.services.agent_service.main_chat import MainAgentChatService


_main_agent_chat_service: MainAgentChatService | None = None


def get_main_agent_chat_service() -> MainAgentChatService:
    global _main_agent_chat_service
    if _main_agent_chat_service is None:
        _main_agent_chat_service = MainAgentChatService()
    return _main_agent_chat_service

