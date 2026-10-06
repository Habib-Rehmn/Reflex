"""Small asynchronous adapter for the OpenAI Responses HTTP API."""

import asyncio

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings
from app.gateway import ProviderUnavailable


class Content(BaseModel):
    type: str
    text: str | None = None
    refusal: str | None = None


class OutputItem(BaseModel):
    type: str
    role: str | None = None
    content: list[Content] = []


class ResponsePayload(BaseModel):
    model_config = ConfigDict(strict=True)

    status: str
    output: list[OutputItem]


def parse_answer(payload: object) -> str:
    response = ResponsePayload.model_validate(payload)
    if response.status != "completed":
        raise ProviderUnavailable("The provider did not complete the response.")
    parts = []
    for item in response.output:
        if item.type != "message" or item.role != "assistant":
            continue
        for content in item.content:
            if content.type == "output_text" and content.text:
                parts.append(content.text)
            elif content.type == "refusal" and content.refusal:
                parts.append(content.refusal)
    answer = "\n".join(parts).strip()
    if not answer:
        raise ProviderUnavailable("The provider returned no usable text.")
    return answer


class OpenAIReasoner:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def answer(self, prompt: str) -> str:
        key = self.settings.openai_api_key
        if key is None:
            raise ProviderUnavailable("OpenAI is not configured.")
        try:
            # A total deadline also bounds slow responses that keep sending bytes.
            async with asyncio.timeout(self.settings.provider_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=self.settings.provider_timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                        json={
                            "model": self.settings.openai_model,
                            "input": prompt,
                            "max_output_tokens": self.settings.openai_max_output_tokens,
                            "store": False,
                        },
                    )
                    response.raise_for_status()
                    return parse_answer(response.json())
        except (httpx.HTTPError, TimeoutError, ValueError, ValidationError):
            # Do not leak upstream response bodies, prompts, or credentials.
            raise ProviderUnavailable("OpenAI could not complete the request.") from None
