from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any

import duckdb
from agents import function_tool


MAX_RESULT_ROWS = 500
DEFAULT_RESULT_ROWS = 100
PARQUET_VIEW_NAME = "parquet_data"

_ALLOWED_SQL_PREFIXES = ("select", "with", "describe", "show", "summarize")
_BLOCKED_SQL_TOKENS = re.compile(
    r"\b("
    r"attach|call|copy|create|delete|detach|drop|export|insert|install|load|"
    r"pragma|set|update|vacuum"
    r")\b",
    re.IGNORECASE,
)


def _clean_parquet_path(parquet_path: str) -> str:
    path = parquet_path.strip()
    if not path:
        raise ValueError("parquet_path is required")
    return path


def _clean_sql(sql: str) -> str:
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise ValueError("sql is required")

    lowered = statement.lstrip().lower()
    if not lowered.startswith(_ALLOWED_SQL_PREFIXES):
        raise ValueError(
            "Only read-only DuckDB SQL is allowed. Start the query with SELECT, WITH, DESCRIBE, SHOW, or SUMMARIZE."
        )

    blocked = _BLOCKED_SQL_TOKENS.search(statement)
    if blocked:
        raise ValueError(f"Read-only DuckDB SQL cannot contain '{blocked.group(1)}'.")

    return statement


def _row_limit(max_rows: int) -> int:
    if max_rows < 1:
        return DEFAULT_RESULT_ROWS
    return min(max_rows, MAX_RESULT_ROWS)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _open_connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(database=":memory:")
    connection.execute("SET threads TO 4")
    return connection


def _register_parquet_view(connection: duckdb.DuckDBPyConnection, parquet_path: str) -> None:
    relation = connection.read_parquet(_clean_parquet_path(parquet_path))
    relation.create_view(PARQUET_VIEW_NAME, replace=True)


@function_tool
def get_parquet_columns(parquet_path: str) -> dict[str, object]:
    """Return column names and DuckDB types for a parquet file.

    Args:
        parquet_path: Local path, glob, or URI that DuckDB can read.
    """

    return get_parquet_columns_impl(parquet_path)


@function_tool
def query_parquet_with_duckdb(sql: str, parquet_path: str, max_rows: int) -> dict[str, object]:
    """Run a read-only DuckDB query against parquet data.

    If parquet_path is provided, the tool registers it as a temporary DuckDB view named parquet_data.
    The SQL can then use: SELECT ... FROM parquet_data.

    If parquet_path is empty, the SQL must include its own read_parquet('path') call.

    Args:
        sql: Read-only DuckDB SQL. Use SELECT/WITH/DESCRIBE/SHOW/SUMMARIZE only.
        parquet_path: Local path, glob, or URI that DuckDB can read. Pass an empty string if SQL uses read_parquet('path').
        max_rows: Maximum rows to return. Use 100 by default; values above 500 are clamped.
    """

    return query_parquet_with_duckdb_impl(sql=sql, parquet_path=parquet_path, max_rows=max_rows)


def get_parquet_columns_impl(parquet_path: str) -> dict[str, object]:
    with _open_connection() as connection:
        relation = connection.read_parquet(_clean_parquet_path(parquet_path))
        columns = [
            {"name": name, "type": str(relation.types[index]) if index < len(relation.types) else None}
            for index, name in enumerate(relation.columns)
        ]

    return {
        "parquet_path": parquet_path,
        "column_count": len(columns),
        "columns": columns,
    }


def query_parquet_with_duckdb_impl(
    *,
    sql: str,
    parquet_path: str = "",
    max_rows: int = DEFAULT_RESULT_ROWS,
) -> dict[str, object]:
    statement = _clean_sql(sql)
    limit = _row_limit(max_rows)

    with _open_connection() as connection:
        if parquet_path.strip():
            _register_parquet_view(connection, parquet_path)

        cursor = connection.execute(statement)
        column_names = [description[0] for description in (cursor.description or [])]
        raw_rows = cursor.fetchmany(limit + 1)

    returned_rows = raw_rows[:limit]
    rows = [
        {column_names[index]: _jsonable(value) for index, value in enumerate(row)}
        for row in returned_rows
    ]

    return {
        "parquet_path": parquet_path or None,
        "sql": statement,
        "columns": column_names,
        "row_count": len(rows),
        "truncated": len(raw_rows) > limit,
        "max_rows": limit,
        "rows": rows,
    }
