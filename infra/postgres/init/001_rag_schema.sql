CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    catalog_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    indicator_id TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_embeddings (
    catalog_id TEXT PRIMARY KEY REFERENCES rag_documents(catalog_id) ON DELETE CASCADE,
    model_uri TEXT NOT NULL,
    embedding vector(256) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS indicator_metadata (
    catalog_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    indicator_id TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT,
    frequency TEXT,
    period_start INTEGER,
    period_end INTEGER,
    geography_type TEXT,
    data_path TEXT,
    source_url TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rag_documents_source_idx
    ON rag_documents (source);

CREATE INDEX IF NOT EXISTS rag_documents_metadata_gin_idx
    ON rag_documents USING gin (metadata);

CREATE INDEX IF NOT EXISTS rag_embeddings_embedding_hnsw_idx
    ON rag_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS indicator_metadata_source_idx
    ON indicator_metadata (source);

CREATE INDEX IF NOT EXISTS indicator_metadata_geography_type_idx
    ON indicator_metadata (geography_type);

CREATE INDEX IF NOT EXISTS indicator_metadata_period_idx
    ON indicator_metadata (period_start, period_end);

