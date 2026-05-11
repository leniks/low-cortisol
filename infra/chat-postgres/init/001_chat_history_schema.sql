CREATE SCHEMA IF NOT EXISTS chat_history;

CREATE TABLE IF NOT EXISTS chat_history.conversations (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_history.messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES chat_history.conversations(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, position)
);

CREATE INDEX IF NOT EXISTS messages_conversation_order_idx
    ON chat_history.messages (conversation_id, position, id);

CREATE TABLE IF NOT EXISTS chat_history.app_state (
    scope TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
