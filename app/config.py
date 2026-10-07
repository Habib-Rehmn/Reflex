"""Environment configuration; credentials never appear in settings representations."""

from datetime import date
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True, frozen=True
    )

    system1_provider: Literal["rules", "laya"] = "rules"
    laya_endpoint: str = "http://127.0.0.1:8001/v1/systemone"
    laya_api_key: SecretStr | None = None
    laya_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)

    @field_validator("laya_endpoint")
    @classmethod
    def validate_laya_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LAYA_ENDPOINT must be an HTTP(S) URL without credentials or query")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote Laya endpoints require HTTPS")
        return value

    system2_provider: Literal["disabled", "openai"] = "disabled"
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    provider_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    openai_max_output_tokens: int = Field(default=2048, ge=16, le=32768)
    database_path: str = "data/reflex.db"
    retention_days: int = Field(default=30, ge=1, le=3650)
    pricing_model: str | None = None
    pricing_date: date | None = None
    input_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_input_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("openai_model")
    @classmethod
    def trim_model(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_credentials(self) -> "Settings":
        rates = (
            self.input_usd_per_million,
            self.output_usd_per_million,
            self.cached_input_usd_per_million,
        )
        if any(rate is not None for rate in rates):
            if (
                self.input_usd_per_million is None
                or self.output_usd_per_million is None
                or not self.pricing_model
                or self.pricing_date is None
            ):
                raise ValueError(
                    "Pricing requires input/output rates, PRICING_MODEL and PRICING_DATE"
                )
        if not self.database_path.strip() or self.database_path == ":memory:":
            raise ValueError("DATABASE_PATH must be a persistent file path")
        if self.system2_provider == "openai":
            key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
            if not key or any(character.isspace() for character in key):
                raise ValueError("OpenAI requires a nonempty OPENAI_API_KEY without whitespace")
            if not self.openai_model:
                raise ValueError("OpenAI requires OPENAI_MODEL")
        return self
