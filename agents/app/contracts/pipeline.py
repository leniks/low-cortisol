from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UserRequest:
    message: str
    conversation_id: str | None = None
    history: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class EnrichedQuery:
    original: str
    enriched: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParquetCandidate:
    dataset_id: str
    parquet_uri: str
    description: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
