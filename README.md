# MathMod DataAgent

API-прослойка для стриминга к внешнему AgentService. Принимает сообщения от фронтенда, валидирует их Pydantic-схемами и проксирует к AgentService с SSE.

## Структура

- `app/main.py` — FastAPI приложение и маршрутизация
- `app/api/routes/invoke.py` — эндпоинты `/invoke` и `/invoke/stream`, `/invoke/upload`
- `app/services/agent_service.py` — прокси к внешнему AgentService
- `app/schemas/invoke.py` — Pydantic-схемы для запроса/ответа
- `app/core/settings.py` — конфигурация через env
- `static/index.html` — локальный фронтенд с загрузкой файлов

## Запуск

```bash
cd mathmod-dataagent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Если у вас ранее `.venv` был создан под `root` (и `pip` ругается на `Permission denied` внутри `.venv/lib/...`), быстрее всего пересоздать окружение:

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Если видите ошибку `ModuleNotFoundError: No module named 'fastapi'`, почти всегда это означает, что запускается **системный** `uvicorn`/`python`, а не виртуальное окружение проекта. В этом случае:

```bash
source .venv/bin/activate
which python uvicorn
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

или запустите явно из окружения:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Конфигурация

- `AGENT_SERVICE_URL` — URL внешнего AgentService (по умолчанию `http://localhost:8001`)
- `MOCK_MODE` — если `True`, работает в моковом режиме без внешних вызовов

## API

- `POST /invoke` — синхронный вызов агента
- `GET /invoke/stream?message=...` — SSE поток ответа (чанками, для UI “печатает…”)
- `GET /invoke/clarify/stream?message=...` — SSE поток уточнения (в конце перезаписывает последнюю пару)
- `POST /invoke/upload` — загрузка файла и отображение содержимого

### Диалог (переписка)

`POST /invoke` хранит диалог на стороне сервера в формате `user/assistant` по `conversation_id` (без “истории сеанса” вроде thought/tool событий).

- Если `conversation_id` не передан — сервер создаст новый и вернёт его.
- Для “уточнений” используйте `POST /invoke/clarify` — сервер **перезапишет последнюю пару** (вопрос+ответ) вместо добавления новой.

