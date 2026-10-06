"""Validated contracts shared by classifiers, routing, and HTTP clients."""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Route(StrEnum):
    FAST_PATH = "fast_path"
    SYSTEM2 = "system2"
    BLOCK = "block"
    REVIEW = "review"


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str
    complexity: Annotated[int, Field(strict=True, ge=1, le=5)]
    requires_llm: Annotated[bool, Field(strict=True)]
    risk: Annotated[int, Field(strict=True, ge=0, le=5)]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    classifier: str


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=8000)
    ]


class QueryResponse(BaseModel):
    request_id: UUID
    route: Route
    answer: str
    prediction: Prediction
    llm_used: bool
    latency_ms: float
    model: str | None = None
    usage: dict[str, int | None] | None = None
    estimated_cost_usd: str | None = None
    metadata_persisted: bool = False
