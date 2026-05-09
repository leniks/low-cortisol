from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DatasetMetadataRecord:
    dataset_id: str
    description: str
    parquet_uri: str
    readable_file_uris: tuple[str, ...]
    embedding: tuple[float, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

