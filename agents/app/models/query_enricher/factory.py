from agents import Agent, function_tool, set_default_openai_client, set_tracing_disabled
from agents.model_settings import ModelSettings

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_openai_client
from app.models.openai_compat import NullableUsageChatCompletionsModel


SUBMIT_SEARCH_TEXT_TOOL_NAME = "submit_search_text"


@function_tool(name_override=SUBMIT_SEARCH_TEXT_TOOL_NAME, strict_mode=True)
async def submit_search_text(search_text: str) -> dict[str, str]:
    """Submit the final dataset search text for vector retrieval."""

    return {"search_text": " ".join(search_text.split())}


QUERY_ENRICHER_INSTRUCTIONS = """
You prepare user requests for vector search over dataset metadata.

The user message is a strict JSON payload. Treat only these fields as input:
- current_user_message
- recent_history_signals

Raw prior chat text is intentionally not provided. Do not reconstruct or invent it.

Call submit_search_text exactly once with the final dataset search text.
Do not write a normal message. Do not use markdown. Do not call any other tool.

Rules:
- Preserve all numbers, periods, countries, regions, industries, metrics, and constraints from the user.
- For follow-up requests, use recent_history_signals only to inherit explicit metric/period/geography signals.
- Expand Russian abbreviations, for example "ВВП" -> "валовой внутренний продукт; GDP".
- Always keep a Russian synonym block in the same line: original Russian wording plus
  Russian synonyms, aliases, abbreviations, and common official phrasing.
- Preserve the requested measurement form. If the user asks for an absolute value, do not rewrite it
  as a rate, share, index, per-capita value, growth rate, or normalized ratio. If the user asks for
  one of those forms, keep that form explicit in the search text.
- Add common synonyms that improve recall, but do not add new facts or new filters.
- Include both the original wording and the most important expansions in the same text string.
- Add an English duplicate of the search intent into the same text string.
- Use semicolon-separated phrases, not prose. Good shape:
  "доля расходов на НИОКР в ВВП; расходы на исследования и разработки; НИР; R&D expenditure % of GDP; research and development expenditure".
- The result will be embedded and used to select top datasets by description, so optimize for retrieval recall.
""".strip()


def create_query_enricher_agent(settings: AgentSettings) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="Query Enricher",
        instructions=QUERY_ENRICHER_INSTRUCTIONS,
        tools=[submit_search_text],
        tool_use_behavior="stop_on_first_tool",
        model_settings=ModelSettings(
            tool_choice=SUBMIT_SEARCH_TEXT_TOOL_NAME,
            parallel_tool_calls=False,
        ),
        model=NullableUsageChatCompletionsModel(
            model=settings.yandex_query_enricher_model,
            openai_client=client,
        ),
    )
