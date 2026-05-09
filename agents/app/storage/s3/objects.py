from dataclasses import dataclass


@dataclass(frozen=True)
class S3ObjectRef:
    uri: str
    content_type: str
    size: int | None = None

