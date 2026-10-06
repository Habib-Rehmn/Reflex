import asyncio
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.gateway import Gateway, select_route
from app.main import create_app
from app.models import Prediction, Route


class TestClient:
    """Exercise the ASGI app directly without background threads or network sockets."""

    __test__ = False

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, method, path, **kwargs):
        async def send():
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def get(self, path):
        return self.request("GET", path)


def prediction(**overrides):
    return Prediction(
        **(
            dict(
                intent="faq",
                complexity=1,
                requires_llm=False,
                risk=0,
                confidence=1.0,
                classifier="test",
            )
            | overrides
        )
    )


@pytest.mark.parametrize(
    ("overrides", "available", "expected"),
    [
        ({}, True, Route.FAST_PATH),
        ({"confidence": 0.85}, True, Route.FAST_PATH),
        ({"confidence": 0.849}, True, Route.SYSTEM2),
        ({}, False, Route.SYSTEM2),
        ({"complexity": 3}, True, Route.SYSTEM2),
        ({"requires_llm": True}, True, Route.SYSTEM2),
        ({"risk": 3, "requires_llm": True}, True, Route.REVIEW),
        ({"risk": 4, "confidence": 0.0}, False, Route.BLOCK),
        ({"risk": 5}, True, Route.BLOCK),
    ],
)
def test_routing_precedence(overrides, available, expected):
    assert select_route(prediction(**overrides), fast_path_available=available) == expected


def test_faq_response():
    with TestClient(create_app()) as client:
        response = client.post("/query", json={"prompt": "  WHAT IS REFLEX?  "})
    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "fast_path"
    assert body["llm_used"] is False
    assert body["prediction"]["classifier"] == "bootstrap_rules"
    assert body["latency_ms"] >= 0
    assert "Reflex" in body["answer"]
    UUID(body["request_id"])


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"prompt": " "},
        {"prompt": 5},
        {"prompt": None},
        {"prompt": "x" * 8001},
        {"prompt": "hi", "risk": 0},
    ],
)
def test_invalid_input(body):
    with TestClient(create_app()) as client:
        assert client.post("/query", json=body).status_code == 422


def test_unconfigured_reasoning_returns_honest_error():
    with TestClient(create_app()) as client:
        result = client.post("/query", json={"prompt": "Explain distributed consensus"})
    assert result.status_code == 503
    assert result.json()["error"] == "provider_unavailable"


class FixedClassifier:
    def __init__(self, result):
        self.result = result

    async def classify(self, prompt):
        return self.result


class RecordingReasoner:
    def __init__(self):
        self.prompts = []

    async def answer(self, prompt):
        self.prompts.append(prompt)
        return "Test reasoning answer"


@pytest.mark.parametrize("risk,route", [(3, "review"), (4, "block"), (5, "block")])
def test_safety_routes_never_invoke_provider(risk, route):
    reasoner = RecordingReasoner()
    gateway = Gateway(FixedClassifier(prediction(risk=risk)), reasoner)
    with TestClient(create_app(gateway)) as client:
        body = client.post("/query", json={"prompt": "what is reflex?"}).json()
    assert body["route"] == route
    assert body["llm_used"] is False
    assert reasoner.prompts == []


def test_unknown_prompt_reaches_injected_provider():
    reasoner = RecordingReasoner()
    with TestClient(create_app(Gateway(reasoner=reasoner))) as client:
        body = client.post("/query", json={"prompt": "Explain consensus"}).json()
        assert client.get("/health").json() == {"status": "ok"}
    assert body["route"] == "system2"
    assert body["answer"] == "Test reasoning answer"
    assert body["llm_used"] is True
    assert reasoner.prompts == ["Explain consensus"]
