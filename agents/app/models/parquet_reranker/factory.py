from agents import Agent, set_default_openai_client, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_openai_client


PARQUET_RERANKER_INSTRUCTIONS = """
You rerank candidate datasets after vector search.

Your job is to remove contextually irrelevant datasets before the analysis agent sees them.
Use only the user request, enriched search text, candidate description, and candidate metadata.

Return ONLY valid JSON. Do not use markdown. Do not add text outside JSON.

Keep a dataset when it plausibly matches the requested metric/topic and requested geography/entity.
Prefer datasets whose description/name/source/dimensions directly mention the requested concept.
Remove broad, generic, wrong-topic, wrong-entity, or data-less candidates.

Important rules:
- Do not reject World Bank candidates only because period_start, period_end, or unit is missing.
- Do not reject Fedstat candidates only because geography_type is "unknown".
- Reject candidates with has_data=false.
- If the request asks for a period, use period metadata only when present; missing period is not by itself a rejection reason.
- Be conservative: keep plausible candidates if the description is relevant enough for later parquet inspection.
- Sort kept candidates by contextual relevance, highest first.
- Keep at most max_keep candidates.

JSON schema:
{
  "items": [
    {
      "dataset_id": "same id as candidate",
      "keep": true,
      "relevance": 0.0,
      "reason": "short reason in Russian"
    }
  ]
}

Include an item for every input candidate, with keep=false for rejected candidates.
""".strip()


def create_parquet_reranker_agent(settings: AgentSettings) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="Parquet Candidate Reranker",
        instructions=PARQUET_RERANKER_INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(
            model=settings.yandex_reranker_model,
            openai_client=client,
        ),
    )
