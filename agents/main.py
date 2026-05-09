import os
import asyncio

from agents import Agent, Runner, set_default_openai_client, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from dotenv import load_dotenv


load_dotenv()

FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "b1goa02eskrgbk1pg322")
API_KEY = os.getenv("YANDEX_API_KEY")
BASE_URL = os.getenv("YANDEX_LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
MODEL = os.getenv("YANDEX_CHAT_MODEL", f"gpt://{FOLDER_ID}/qwen3.6-35b-a3b/latest")

if not API_KEY:
    raise RuntimeError("Нужен YANDEX_API_KEY в .env")

client = AsyncOpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    project=FOLDER_ID,
)

set_default_openai_client(client, use_for_tracing=False)
set_tracing_disabled(True)

agent = Agent(
    name="Норм агентик",
    instructions="Ты полезный ассистент. Отвечай кратко и по делу.",
    model=OpenAIChatCompletionsModel(
        model=MODEL,
        openai_client=client,
    ),
)


async def main():
    history = []
    print("Пиши сообщения. Для выхода: exit, quit, q или пустая строка.")
    print(f"model={MODEL}\n")

    while True:
        text = input("you> ").strip()
        if text.lower() in {"", "exit", "quit", "q"}:
            break

        result = await Runner.run(agent, history + [{"role": "user", "content": text}])
        print(f"agent> {result.final_output}\n")

        history = result.to_input_list()


asyncio.run(main())
