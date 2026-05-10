from agents import Agent, set_default_openai_client, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_openai_client


CLARIFIER_INSTRUCTIONS = """
You check whether a user request has enough concrete information before data retrieval.

Return ONLY valid JSON. Do not use markdown. Do not add text outside JSON.

For data/dataset/indicator requests, require at least:
- metric/topic;
- geography/entity when relevant;
- period/time range.

If information is missing, ask exactly one concise question in Russian and provide 2-4 options.
For missing period, prefer these options:
- "За всё время" -> "за всё время"
- "2024" -> "за 2024 год"
- "2023-2024" -> "за 2023-2024 годы"
- "Ввести вручную" -> "manual"

If the request is already complete, or if it is a greeting/meta/general question, set is_complete=true.

JSON schema:
{
  "is_complete": boolean,
  "question": string | null,
  "missing_fields": ["period" | "geography" | "metric" | "other"],
  "options": [{"label": string, "value": string}],
  "reason": "short reason in Russian"
}
""".strip()


def create_request_clarifier_agent(settings: AgentSettings) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="Request Clarifier",
        instructions=CLARIFIER_INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(
            model=settings.yandex_clarifier_model,
            openai_client=client,
        ),
    )

