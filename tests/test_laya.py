import asyncio
import copy
import json

import httpx
import pytest

from app.config import Settings
from app.gateway import Gateway, ProviderUnavailable
from app.laya_provider import QUESTIONS, LayaClassifier, prediction_from_response
from app.main import create_app
from app.models import Route


def payload(**overrides):
    choices = dict(intent="math", complexity="simple", requires_llm="no", risk="allow") | overrides
    return {
        "answers": {
            name: {
                "type": "choice",
                "choice": selected,
                "probabilities": {
                    label: 0.99
                    if label == selected
                    else 0.01 / (len(QUESTIONS[name]["criteria"]) - 1)
                    for label in QUESTIONS[name]["criteria"]
                },
            }
            for name, selected in choices.items()
        }
    }


def config(**overrides):
    return Settings(_env_file=None, system2_provider="disabled", **overrides)


def test_http_contract_and_gateway_local_result():
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:8001/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["state"] == "calc: 2+2"
        assert set(body["questions"]) == set(QUESTIONS)
        assert body["model"] == "multilingual"
        assert body["max_len"] == 8192
        return httpx.Response(200, json=payload())

    classifier = LayaClassifier(
        config(laya_api_key="test-key"), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(Gateway(classifier=classifier).query("calc: 2+2"))
    assert result[0].classifier == "laya_http"
    assert result[1:] == (Route.FAST_PATH, "4", False)


@pytest.mark.parametrize("risk,expected", [("allow", 0), ("review", 3), ("block", 4)])
def test_risk_mapping(risk, expected):
    assert prediction_from_response(payload(risk=risk)).risk == expected


def test_uncertain_safety_holds_while_uncertain_intent_escalates():
    body = payload()
    body["answers"]["risk"]["probabilities"] = dict(allow=0.6, review=0.3, block=0.1)
    assert prediction_from_response(body).risk == 3
    body = payload()
    body["answers"]["intent"]["low_confidence"] = True
    result = prediction_from_response(body)
    assert result.risk == 0
    assert result.confidence == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.clear(),
        lambda body: body["answers"].pop("risk"),
        lambda body: body["answers"]["intent"].update(choice="invented"),
        lambda body: body["answers"]["risk"].update(probabilities={"allow": 1}),
        lambda body: body["answers"]["risk"].update(
            probabilities=dict(allow=True, review=0, block=0)
        ),
        lambda body: body["answers"]["risk"].update(
            probabilities=dict(allow=float("nan"), review=0, block=0)
        ),
        lambda body: body["answers"]["risk"].update(
            probabilities=dict(allow=0.1, review=0.8, block=0.1)
        ),
        lambda body: body["answers"]["risk"].update(
            probabilities=dict(allow=0.5, review=0.1, block=0.1)
        ),
    ],
)
def test_malformed_predictions_are_rejected(mutation):
    body = copy.deepcopy(payload())
    mutation(body)
    with pytest.raises(ValueError):
        prediction_from_response(body)


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_provider_failure_does_not_call_gpt(status):
    class NoGPT:
        async def answer(self, prompt):
            pytest.fail("Classifier failure must not bypass to GPT")

    classifier = LayaClassifier(
        config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="private upstream details")
        ),
    )
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(Gateway(classifier, NoGPT()).query("hello"))
    assert "private" not in str(exc.value)


def test_preflight_prevents_laya_call():
    def handler(request):
        pytest.fail("Secret must not reach Laya")

    classifier = LayaClassifier(config(), transport=httpx.MockTransport(handler))
    assert asyncio.run(Gateway(classifier=classifier).query("api_key=example"))[1] == Route.REVIEW


def test_total_timeout():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=payload())

    classifier = LayaClassifier(
        config(laya_timeout_seconds=0.01), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderUnavailable):
        asyncio.run(classifier.classify("hello"))


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/laya",
        "http://remote.example/v1/systemone",
        "https://user:pass@example.com",
        "https://example.com?key=test",
    ],
)
def test_invalid_endpoint(url):
    with pytest.raises(ValueError):
        config(laya_endpoint=url)


def test_application_wires_selected_classifier(monkeypatch):
    class FakeLaya:
        def __init__(self, settings):
            assert settings.system1_provider == "laya"

        async def classify(self, prompt):
            return prediction_from_response(payload())

    monkeypatch.setattr("app.main.LayaClassifier", FakeLaya)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings=config(system1_provider="laya"))),
            base_url="http://test",
        ) as client:
            response = await client.post("/query", json={"prompt": "calc: 2+2"})
            assert response.status_code == 200
            assert response.json()["prediction"]["classifier"] == "laya_http"

    asyncio.run(scenario())
