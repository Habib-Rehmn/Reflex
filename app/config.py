"""Environment configuration; credentials never appear in settings representations."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True, frozen=True
    )

    system2_provider: Literal["disabled", "openai"] = "disabled"
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    provider_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    openai_max_output_tokens: int = Field(default=2048, ge=16, le=32768)

    @field_validator("openai_model")
    @classmethod
    def trim_model(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_credentials(self) -> "Settings":
        if self.system2_provider == "openai":
            key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
            if not key or any(character.isspace() for character in key):
                raise ValueError("OpenAI requires a nonempty OPENAI_API_KEY without whitespace")
            if not self.openai_model:
                raise ValueError("OpenAI requires OPENAI_MODEL")
        return self
