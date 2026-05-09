from dataclasses import dataclass, field
from typing import Any, Literal


EventType = Literal["thought", "tool_call", "tool_result", "final"]


@dataclass(frozen=True)
class UserRequest:
    message: str
    conversation_id: str | None = None
    history: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class EnrichedQuery:
    original: str
    enriched: str
    clarification_question: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParquetCandidate:
    dataset_id: str
    parquet_uri: str
    description: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HumanReadableFile:
    dataset_id: str
    file_uri: str
    title: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRoute:
    readable_files: tuple[HumanReadableFile, ...]
    parquets: tuple[ParquetCandidate, ...]


@dataclass(frozen=True)
class SqlReviewDraft:
    sql: str
    parquets: tuple[ParquetCandidate, ...]
    notes: str | None = None


@dataclass(frozen=True)
class PipelineEvent:
    type: EventType
    text: str
    tool: str | None = None
    payload: dict[str, Any] | None = None
    files: tuple[HumanReadableFile, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    answer: str
    sql: SqlReviewDraft | None = None
    files: tuple[HumanReadableFile, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

