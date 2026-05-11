CREATE TABLE IF NOT EXISTS chat_history.runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS runs_conversation_idx
    ON chat_history.runs (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS chat_history.artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    file_path TEXT,
    filename TEXT,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS artifacts_conversation_idx
    ON chat_history.artifacts (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS chat_history.checkpoints (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    title TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS checkpoints_conversation_idx
    ON chat_history.checkpoints (conversation_id, created_at);
