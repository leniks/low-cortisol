from agents import Agent, set_default_openai_client, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_openai_client


QUERY_ENRICHER_INSTRUCTIONS = """
You prepare user requests for vector search over dataset metadata.

Return exactly one plain text search_text string.
Do not return JSON, markdown, bullets, explanations, labels, or quotes.

Rules:
- Preserve all numbers, periods, countries, regions, industries, metrics, and constraints from the user.
- Expand Russian abbreviations, for example "ВВП" -> "валовой внутренний продукт; GDP".
- Add common synonyms that improve recall, but do not add new facts or new filters.
- Include both the original wording and the most important expansions in the same text string.
- Add an English duplicate of the search intent into the same text string.
- The result will be embedded and used to select top datasets by description, so optimize for retrieval recall.
""".strip()


def create_query_enricher_agent(settings: AgentSettings) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="Query Enricher",
        instructions=QUERY_ENRICHER_INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(
            model=settings.yandex_query_enricher_model,
            openai_client=client,
        ),
    )
