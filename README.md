# MathMod DataAgent

API-прослойка для стриминга к внешнему AgentService. Принимает сообщения от фронтенда, валидирует их Pydantic-схемами и проксирует к AgentService с SSE.

## Структура

- `backend/` — FastAPI API
- `backend/app/main.py` — FastAPI приложение и маршрутизация
- `backend/app/api/routes/invoke.py` — эндпоинты `/invoke`, `/invoke/stream`, `/invoke/clarify/stream`, `/invoke/upload_from_agent`
- `backend/app/services/agent_service.py` — прокси к внешнему AgentService
- `backend/app/schemas/invoke.py` — Pydantic-схемы для запроса/ответа
- `backend/app/core/settings.py` — конфигурация через env
- `agents/` — HTTP Agent Service с основным агентом
- `agents/app/main.py` — FastAPI приложение Agent Service
- `agents/main.py` — консольный entrypoint для ручной проверки основного агента
- `frontend/` — React + Tailwind фронтенд
- `frontend/src/` — UI чата; сессии загружаются и сохраняются через backend в Postgres
- `frontend/index.html` — Vite entrypoint
- `frontend/nginx.conf` — nginx-конфиг для отдачи фронтенда и проксирования API
- `infra/postgres/init/001_enable_pgvector.sql` — включение расширения `pgvector`
- `infra/chat-postgres/init/001_chat_history_schema.sql` — схема отдельной БД истории чатов
- `infra/postgres/backups/` — место для дампов/бэкапов Postgres
- `docker-compose.yml` — общий compose для frontend/backend/postgres/agents

## Docker

```bash
docker compose up
```

Для локальной настройки можно создать `.env` из примера:

```bash
cp .env.example .env
```

По умолчанию:

- фронтенд доступен на `http://localhost:8080`
- бэкенд доступен на `http://localhost:8000`
- RAG Postgres доступен на `localhost:5432`
- Chat Postgres доступен на `localhost:5434`
- фронтенд проксирует `/invoke` и `/health` в контейнер `backend`
- внутри Docker бэкенд подключается к Postgres по `DATABASE_URL=postgresql://matmod:matmod_password@postgres:5432/matmod_rag`
- внутри Docker история чатов пишется в отдельный `chat-postgres` по `CHAT_DATABASE_URL`
- внутри Docker бэкенд вызывает Agent Service по `AGENT_SERVICE_URL=http://agents:8001`

Порты и режим можно переопределить через переменные:

```bash
FRONTEND_PORT=3000 BACKEND_PORT=8000 POSTGRES_PORT=5433 AGENT_SERVICE_PORT=8001 MOCK_MODE=false docker compose up
```

Compose сам соберёт frontend/backend/agents образы на машине, где их ещё нет. Для принудительной пересборки после изменения кода можно использовать `docker compose up --build`.

Для реального основного агента нужен `YANDEX_API_KEY` в `.env`. Без него Agent Service поднимется, но запросы к агенту вернут ошибку конфигурации.

Агентский CLI для ручной проверки можно запустить так:

```bash
docker compose run --rm agents python main.py
```

Для CLI тоже нужен `YANDEX_API_KEY` в `.env`.

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

- `AGENT_SERVICE_URL` — URL Agent Service (в Docker по умолчанию `http://agents:8001`)
- `DATABASE_URL` — основная RAG Postgres БД для backend/agents в Docker
- `CHAT_DATABASE_URL` — отдельная Postgres БД для истории чатов; в Docker по умолчанию `postgresql://matmod_chat:matmod_chat_password@chat-postgres:5432/matmod_chat`, вне Docker если не задана, используется `DATABASE_URL`
- `CHAT_DB_SCHEMA` — схема для истории чатов и UI-состояния, по умолчанию `chat_history`
- `MOCK_MODE` — если `True`, backend работает в моковом режиме без вызова Agent Service
- `YANDEX_FOLDER_ID` — folder id Yandex Cloud для агентского CLI
- `YANDEX_API_KEY` — API key Yandex Cloud для Agent Service и CLI
- `YANDEX_LLM_BASE_URL` — OpenAI-compatible endpoint Yandex LLM
- `YANDEX_AI_BASE_URL` — endpoint Yandex Foundation Models API для embedding-запросов
- `YANDEX_CHAT_MODEL` — модель основного агента и SQL evidence-субагента
- `YANDEX_QUERY_ENRICHER_MODEL` — модель обязательного enrichment-шагa, который превращает запрос и историю в plain-text `search_text` перед pgvector
- `YANDEX_QUERY_EMBEDDING_MODEL` — модель эмбеддинга для поиска по описаниям датасетов
- `RAG_TOP_K` — сколько датасетов брать из pgvector после enrichment, по умолчанию `30`
- `RAG_RERANK_MAX_KEEP` — максимум датасетов после детерминированного отбора, по умолчанию `10`
- `MAIN_AGENT_MAX_ATTEMPTS` — максимум попыток основного агента, если после tool calls финальный текст пустой; по умолчанию `3`, жёсткий верхний предел `6`
- `RAG_VECTOR_TABLE`, `RAG_EMBEDDING_COLUMN` — таблица и колонка с векторами описаний датасетов; для перенесённого каталога из `matmod` дефолт `rag_embeddings.embedding`

## API

- `POST /invoke` — синхронный вызов агента
- `GET /invoke/stream?message=...` — SSE поток ответа по мере генерации модели
- Основной агент первым вызывает `submit_data_acquisition_plan`; план получения данных виден в trace и заменяет отдельный предварительный gate.
- Данные запрашиваются через tool `request_evidence`: внутри выполняется query enrichment, pgvector-поиск датасетов, технический отбор parquet-кандидатов и DuckDB-проверки SQL-субагентом.
- Если не хватает периода, географии, метрики или формулы, основной агент вызывает tool `request_user_clarification`, и frontend показывает варианты выбора в общем ходе выполнения.
- `GET /invoke/clarify/stream?message=...` — SSE поток уточнения (в конце перезаписывает последнюю пару)
- `GET /invoke/sessions`, `PUT /invoke/sessions` — загрузка и сохранение UI-состояния истории чатов
- `POST /invoke/upload_from_agent` — загрузка файла и отображение содержимого

### Диалог (переписка)

Backend хранит историю чатов в Postgres:

- каноническая переписка `user/assistant` пишется по `conversation_id` в `chat_history.conversations/messages`;
- UI-состояние чатов (список сессий, активный чат, trace, уточнения, чекпоинты) пишется в `chat_history.app_state`;
- в Docker для этого поднят отдельный сервис `chat-postgres`; для внешней базы задайте `CHAT_DATABASE_URL`.

- Если `conversation_id` не передан — сервер создаст новый и вернёт его.
- Для “уточнений” используйте `POST /invoke/clarify` — сервер **перезапишет последнюю пару** (вопрос+ответ) вместо добавления новой.
