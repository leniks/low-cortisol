import asyncio

from agents import Runner

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_main_agent


async def main():
    settings = AgentSettings.from_env()
    agent = create_main_agent(settings)
    history = []
    print("Пиши сообщения. Для выхода: exit, quit, q или пустая строка.")
    print(f"model={settings.yandex_chat_model}\n")

    while True:
        text = input("you> ").strip()
        if text.lower() in {"", "exit", "quit", "q"}:
            break

        result = await Runner.run(agent, history + [{"role": "user", "content": text}])
        print(f"agent> {result.final_output}\n")

        history = result.to_input_list()


asyncio.run(main())
