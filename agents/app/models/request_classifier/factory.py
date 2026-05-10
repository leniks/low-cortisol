from agents import Agent, set_default_openai_client, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_openai_client


CLASSIFIER_INSTRUCTIONS = """
You are a strict routing classifier for a data analysis assistant.

Your job is to decide whether the current user request needs retrieval from RAG/vector database,
or whether the necessary data/context is already present in the chat history.

Return ONLY valid JSON. Do not use markdown. Do not add explanations outside JSON.

Decision rules:
- "needs_rag": the user asks for new datasets, fresh facts, calculations, tables, files,
  indicators, periods, countries, companies, or any data that is not already present
  in the recent chat history.
- "use_existing_context": the user asks to clarify, reformat, explain, validate, modify,
  or continue working with data/results that are already present in the chat history.
- "no_data_needed": the user asks a general question, greeting, meta question, or asks
  about the system itself and no dataset retrieval is needed.

JSON schema:
{
  "decision": "needs_rag" | "use_existing_context" | "no_data_needed",
  "needs_rag": boolean,
  "reason": "short reason in Russian",
  "confidence": number
}

Keep confidence between 0 and 1.
Set needs_rag=true only when decision is "needs_rag".
""".strip()


def create_request_classifier_agent(settings: AgentSettings) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="RAG Routing Classifier",
        instructions=CLASSIFIER_INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(
            model=settings.yandex_classifier_model,
            openai_client=client,
        ),
    )
