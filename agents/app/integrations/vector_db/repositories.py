from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.contracts import EnrichedQuery, ParquetCandidate


class DatasetVectorRepository(Protocol):
    async def search(
        self,
        query: EnrichedQuery,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ParquetCandidate, ...]:
        """Search pgvector dataset metadata and return parquet candidates."""


class PgVectorDatasetRepository:
    def __init__(
        self,
        *,
        dsn: str,
        table_name: str,
        embedding_column: str,
        description_chars: int,
    ) -> None:
        self._dsn = dsn
        self._table_name = table_name
        self._embedding_column = embedding_column
        self._description_chars = description_chars

    async def search(
        self,
        query: EnrichedQuery,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ParquetCandidate, ...]:
        return await asyncio.to_thread(self._search_sync, embedding, limit)

    def _search_sync(self, embedding: tuple[float, ...], limit: int) -> tuple[ParquetCandidate, ...]:
        vector_literal = _to_pgvector_literal(embedding)
        statement = (
            _default_catalog_statement(self._embedding_column)
            if self._table_name == "rag_embeddings"
            else _generic_vector_statement(self._table_name, self._embedding_column)
        )

        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                cursor.execute(statement, (vector_literal, vector_literal, limit))
                rows = cursor.fetchall()

        candidates: list[ParquetCandidate] = []
        for index, row in enumerate(rows, start=1):
            candidate = self._row_to_candidate(index, row)
            if candidate:
                candidates.append(candidate)
        return tuple(candidates)

    def _row_to_candidate(self, index: int, row: dict[str, Any]) -> ParquetCandidate | None:
        metadata = _metadata_from_row(row)
        dataset_id = _first_text(
            row.get("dataset_id"),
            row.get("dataset"),
            row.get("catalog_id"),
            row.get("indicator_id"),
            row.get("id"),
            metadata.get("dataset_id"),
            metadata.get("catalog_id"),
            metadata.get("indicator_id"),
            metadata.get("id"),
        )
        parquet_uri = _first_text(
            row.get("parquet_uri"),
            row.get("parquet_path"),
            row.get("data_path"),
            row.get("s3_parquet_uri"),
            row.get("object_uri"),
            metadata.get("parquet_uri"),
            metadata.get("parquet_path"),
            metadata.get("data_path"),
            metadata.get("s3_parquet_uri"),
            _first_from_collection(metadata.get("parquets")),
            _first_from_collection(metadata.get("parquet_uris")),
        )
        description = _first_text(
            row.get("description"),
            row.get("dataset_description"),
            row.get("text"),
            row.get("title"),
            row.get("name"),
            row.get("content"),
            metadata.get("description"),
        )

        if not dataset_id:
            dataset_id = f"dataset_{index}"
        if not parquet_uri:
            return None

        distance = _float(row.get("distance"), default=1.0)
        score = max(0.0, 1.0 - distance)
        description = _truncate(description or "", self._description_chars)

        metadata.update(
            {
                "rag_distance": distance,
                "rag_score": score,
                "source_row": _safe_row_metadata(row),
            }
        )

        return ParquetCandidate(
            dataset_id=dataset_id,
            parquet_uri=parquet_uri,
            description=description,
            score=score,
            metadata=metadata,
        )


def _generic_vector_statement(table_name: str, embedding_column: str) -> sql.Composed:
    return sql.SQL(
        """
        SELECT *, ({embedding} <=> %s::vector) AS distance
        FROM {table}
        ORDER BY {embedding} <=> %s::vector
        LIMIT %s
        """
    ).format(
        table=_identifier(table_name),
        embedding=sql.Identifier(embedding_column),
    )


def _default_catalog_statement(embedding_column: str) -> sql.Composed:
    return sql.SQL(
        """
        SELECT
            e.catalog_id,
            d.source,
            d.indicator_id,
            d.title,
            d.text,
            d.metadata AS document_metadata,
            m.name,
            m.unit,
            m.frequency,
            m.period_start,
            m.period_end,
            m.geography_type,
            m.data_path,
            m.source_url,
            m.metadata AS indicator_metadata,
            (e.{embedding} <=> %s::vector) AS distance
        FROM rag_embeddings e
        LEFT JOIN rag_documents d ON d.catalog_id = e.catalog_id
        LEFT JOIN indicator_metadata m ON m.catalog_id = e.catalog_id
        ORDER BY e.{embedding} <=> %s::vector
        LIMIT %s
        """
    ).format(embedding=sql.Identifier(embedding_column))


def _identifier(name: str) -> sql.Composed:
    parts = [part for part in name.split(".") if part]
    if not parts:
        raise ValueError("RAG vector table name is empty")
    return sql.SQL(".").join(sql.Identifier(part) for part in parts)


def _to_pgvector_literal(embedding: tuple[float, ...]) -> str:
    if not embedding:
        raise ValueError("Embedding is empty")
    return "[" + ",".join(f"{value:.10g}" for value in embedding) + "]"


def _metadata_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    metadata.update(_parse_metadata_value(row.get("document_metadata")))
    metadata.update(_parse_metadata_value(row.get("indicator_metadata")))
    metadata.update(_parse_metadata_value(row.get("metadata")))
    return metadata


def _parse_metadata_value(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"metadata_raw": raw}
        return dict(parsed) if isinstance(parsed, dict) else {"metadata_raw": parsed}
    return {}


def _safe_row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"embedding", "distance", "metadata"} and _is_json_scalar(value)
    }


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_from_collection(value: Any) -> Any:
    if isinstance(value, list | tuple) and value:
        return value[0]
    return None


def _float(value: Any, *, default: float) -> float:
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return f"{value[:max_chars].rstrip()}..."
