from agents import Agent, set_default_openai_client, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from app.core.settings import AgentSettings


def create_openai_client(settings: AgentSettings) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.yandex_api_key,
        base_url=settings.yandex_llm_base_url,
        project=settings.yandex_folder_id,
    )


def create_main_agent(settings: AgentSettings) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="Data Analysis Agent",
        instructions=(
            "You are a data analysis agent. Build analyst-reviewable SQL, "
            "run calculations only on selected parquet inputs, and answer concisely."
        ),
        model=OpenAIChatCompletionsModel(
            model=settings.yandex_chat_model,
            openai_client=client,
        ),
    )

