from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceDatasetUsed(StrictSchemaModel):
    dataset_id: str = ""
    name: str = Field(min_length=1)
    source: str = ""
    unit: str = ""


class EvidenceCoverage(StrictSchemaModel):
    requested_indicators: tuple[str, ...] = ()
    requested_geographies: tuple[str, ...] = ()
    requested_period: str = ""
    found_slices: tuple[str, ...] = ()
    missing_slices: tuple[str, ...] = ()
    computable_from_parts: bool = False
    required_parts: tuple[str, ...] = ()
    next_action: Literal[
        "answer_directly",
        "request_more_evidence",
        "calculate_from_parts",
        "ask_clarification",
        "no_data",
    ]
    reason: str = Field(min_length=1)


class EvidenceSqlCheck(StrictSchemaModel):
    purpose: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    used_dataset_names: tuple[str, ...] = ()


class EvidencePack(StrictSchemaModel):
    status: Literal["ok", "no_relevant_dataset", "no_rows", "insufficient_data", "error"]
    reason: str = Field(min_length=1)
    coverage: EvidenceCoverage
    datasets_used: tuple[EvidenceDatasetUsed, ...] = ()
    facts: tuple[str, ...] = ()
    sql_checks: tuple[EvidenceSqlCheck, ...] = ()
    limitations: tuple[str, ...] = ()
    data_verdict: str = Field(min_length=1)
