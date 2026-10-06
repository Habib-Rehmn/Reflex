"""Per-request observations; never shared between concurrent requests."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Prediction, Route


class TokenUsage(BaseModel):
    model_config = ConfigDict(strict=True)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_cached(self):
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached tokens exceed input tokens")
        if self.cache_write_input_tokens > self.input_tokens:
            raise ValueError("Cache write tokens exceed input tokens")
        return self


@dataclass(frozen=True)
class ReasoningResult:
    text: str
    model: str | None = None
    usage: TokenUsage | None = None


@dataclass
class Observation:
    prediction: Prediction | None = None
    route: Route | None = None
    provider_attempted: bool = False
    model: str | None = None
    usage: TokenUsage | None = None


def estimate_cost(usage: TokenUsage | None, model: str | None, settings) -> Decimal | None:
    if (
        usage is None
        or usage.cached_input_tokens is None
        or usage.cache_write_input_tokens > 0
        or model != settings.pricing_model
        or settings.input_usd_per_million is None
    ):
        return None
    cached_rate = settings.cached_input_usd_per_million
    if usage.cached_input_tokens and cached_rate is None:
        return None
    return (
        Decimal(usage.input_tokens - usage.cached_input_tokens) * settings.input_usd_per_million
        + Decimal(usage.cached_input_tokens) * (cached_rate or Decimal(0))
        + Decimal(usage.output_tokens) * settings.output_usd_per_million
    ) / Decimal(1_000_000)
