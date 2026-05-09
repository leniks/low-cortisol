# MathMod DataAgent

API-прослойка для стриминга к внешнему AgentService. Принимает сообщения от фронтенда, валидирует их Pydantic-схемами и проксирует к AgentService с SSE.

## Структура

- `backend/` — FastAPI API
- `backend/app/main.py` — FastAPI приложение и маршрутизация
- `backend/app/api/routes/invoke.py` — эндпоинты `/invoke`, `/invoke/stream`, `/invoke/upload_from_agent`
- `backend/app/services/agent_service.py` — прокси к внешнему AgentService
- `backend/app/schemas/invoke.py` — Pydantic-схемы для запроса/ответа
- `backend/app/core/settings.py` — конфигурация через env
- `agents/` — CLI-сервис с агентом
- `agents/main.py` — консольный агент через OpenAI Agents SDK и Yandex LLM-compatible API
- `frontend/` — статический фронтенд
- `frontend/index.html` — UI с чатом и загрузкой файлов
- `frontend/nginx.conf` — nginx-конфиг для отдачи фронтенда и проксирования API
- `infra/postgres/init/001_enable_pgvector.sql` — включение расширения `pgvector`
- `infra/postgres/backups/` — место для дампов/бэкапов Postgres
- `docker-compose.yml` — общий compose для frontend/backend/postgres/agents

## Docker

```bash
docker compose up --build
```

Для локальной настройки можно создать `.env` из примера:

```bash
cp .env.example .env
```

По умолчанию:

- фронтенд доступен на `http://localhost:8080`
- бэкенд доступен на `http://localhost:8000`
- Postgres доступен на `localhost:5432`
- фронтенд проксирует `/invoke` и `/health` в контейнер `backend`
- внутри Docker бэкенд подключается к Postgres по `DATABASE_URL=postgresql://matmod:matmod_password@postgres:5432/matmod_rag`

Порты и режим можно переопределить через переменные:

```bash
FRONTEND_PORT=3000 BACKEND_PORT=8000 POSTGRES_PORT=5433 MOCK_MODE=false AGENT_SERVICE_URL=http://host.docker.internal:8001 docker compose up --build
```

Агентский CLI запускается отдельным compose-сервисом:

```bash
docker compose --profile agents run --rm agents
```

Для него нужен `YANDEX_API_KEY` в `.env`.

## Postgres и вектора

`infra/postgres/data/` — служебные файлы Postgres, туда руками ничего класть не нужно.

Вектора хранятся не как отдельные файлы в папке, а в таблицах Postgres через тип `vector`. SQL-инициализацию для пустой базы можно класть в `infra/postgres/init/*.sql`; эти скрипты выполняются только при первом создании базы. Дампы и ручные бэкапы удобно хранить в `infra/postgres/backups/`.

## Локальный запуск бэкенда

```bash
cd backend
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
- `YANDEX_FOLDER_ID` — folder id Yandex Cloud для агентского CLI
- `YANDEX_API_KEY` — API key Yandex Cloud для агентского CLI
- `YANDEX_LLM_BASE_URL` — OpenAI-compatible endpoint Yandex LLM
- `YANDEX_CHAT_MODEL` — модель для `agents/main.py`

## API

- `POST /invoke` — синхронный вызов агента
- `GET /invoke/stream?message=...` — SSE поток ответа (чанками, для UI “печатает…”)
- `GET /invoke/clarify/stream?message=...` — SSE поток уточнения (в конце перезаписывает последнюю пару)
- `POST /invoke/upload_from_agent` — загрузка файла и отображение содержимого

### Диалог (переписка)

`POST /invoke` хранит диалог на стороне сервера в формате `user/assistant` по `conversation_id` (без “истории сеанса” вроде thought/tool событий).

- Если `conversation_id` не передан — сервер создаст новый и вернёт его.
- Для “уточнений” используйте `POST /invoke/clarify` — сервер **перезапишет последнюю пару** (вопрос+ответ) вместо добавления новой.
