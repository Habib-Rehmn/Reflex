import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.gateway import Gateway, ProviderUnavailable
from app.main import create_app
from app.openai_provider import OpenAIReasoner


def settings(**overrides):
    return Settings(
        _env_file=None,
        **(
            dict(
                system2_provider="openai",
                openai_api_key="test-secret",
                openai_model="test-model",
                provider_timeout_seconds=1,
                openai_max_output_tokens=100,
            )
            | overrides
        ),
    )


def payload(content=None, **overrides):
    return dict(
        status="completed",
        output=[
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": content or [{"type": "output_text", "text": "A useful answer"}],
            },
        ],
        **overrides,
    )


def test_request_contract_and_text_extraction():
    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {
            "model": "test-model",
            "input": "Explain consensus",
            "max_output_tokens": 100,
            "store": False,
        }
        return httpx.Response(
            200,
            json=payload(
                [
                    {"type": "output_text", "text": "First"},
                    {"type": "output_text", "text": "Second"},
                ]
            ),
        )

    provider = OpenAIReasoner(settings(), transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.answer("Explain consensus")).text == "First\nSecond"


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500, 503])
def test_http_errors_are_sanitized_without_retries(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private upstream body test-secret")

    provider = OpenAIReasoner(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(provider.answer("private prompt"))
    assert str(exc.value) == "OpenAI could not complete the request."
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"status": "completed", "output": []},
        {"status": "incomplete", "output": []},
        {"status": "failed", "output": []},
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "   "}],
                }
            ],
        },
        {"status": "completed", "output": [{"type": "message", "content": "invalid"}]},
    ],
)
def test_unusable_responses_fail(body):
    provider = OpenAIReasoner(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.answer("hello"))


def test_non_json_response_fails():
    provider = OpenAIReasoner(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not JSON")),
    )
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.answer("hello"))


def test_refusal_is_preserved():
    provider = OpenAIReasoner(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=payload([{"type": "refusal", "refusal": "I cannot help with that request."}]),
            )
        ),
    )
    assert asyncio.run(provider.answer("hello")).text == "I cannot help with that request."


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_failures(error):
    def handler(request):
        raise error("private connection details", request=request)

    provider = OpenAIReasoner(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.answer("hello"))


def test_total_deadline():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=payload())

    provider = OpenAIReasoner(
        settings(provider_timeout_seconds=0.01), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.answer("hello"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"system2_provider": "typo"},
        {"openai_api_key": ""},
        {"openai_api_key": "key with spaces"},
        {"openai_model": " "},
        {"provider_timeout_seconds": 0},
        {"provider_timeout_seconds": float("nan")},
        {"openai_max_output_tokens": 0},
    ],
)
def test_invalid_configuration(overrides):
    with pytest.raises(ValidationError) as exc:
        settings(**overrides)
    assert "test-secret" not in str(exc.value)


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("SYSTEM2_PROVIDER=disabled\nOPENAI_MODEL=file-model\n")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    result = Settings(_env_file=path, system2_provider="disabled")
    assert result.openai_model == "env-model"
    assert "test-secret" not in repr(settings())


@pytest.mark.parametrize("upstream_status,expected_status", [(200, 200), (429, 503)])
def test_http_gateway_with_real_adapter(upstream_status, expected_status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(upstream_status, json=payload())

    provider = OpenAIReasoner(settings(), transport=httpx.MockTransport(handler))
    app = create_app(Gateway(reasoner=provider))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            faq = await client.post("/query", json={"prompt": "What is Reflex?"})
            assert faq.status_code == 200
            assert calls == []
            response = await client.post("/query", json={"prompt": "Explain consensus"})
            assert response.status_code == expected_status
            assert "test-secret" not in response.text
            if expected_status == 200:
                assert response.json()["answer"] == "A useful answer"
                assert response.json()["llm_used"] is True
            else:
                assert response.json()["error"] == "provider_unavailable"
            assert len(calls) == 1

    asyncio.run(scenario())


def test_app_wires_configured_provider(monkeypatch):
    captured = []

    class FakeReasoner:
        def __init__(self, config):
            captured.append(config.openai_model)

        async def answer(self, prompt):
            return "Configured provider"

    monkeypatch.setattr("app.main.OpenAIReasoner", FakeReasoner)
    app = create_app(settings=settings())

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/query", json={"prompt": "unknown"})
            assert response.json()["answer"] == "Configured provider"

    asyncio.run(scenario())
    assert captured == ["test-model"]
