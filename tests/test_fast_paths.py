import asyncio

import httpx
import pytest

from app.fast_paths import ArithmeticRejected, calculate, match_task
from app.gateway import Gateway
from app.main import create_app
from app.models import Prediction, Route


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 3 * 4", "14"),
        ("(2 + 3) * 4", "20"),
        ("0.1 + 0.2", "0.3"),
        ("-7 // 3", "-3"),
        ("-7 % 3", "2"),
        ("7 % -3", "-2"),
        ("1 / 8", "0.125"),
        ("--2", "2"),
        ("-0", "0"),
        ("1000000000000", "1000000000000"),
    ],
)
def test_arithmetic(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "1/0",
        "1//0",
        "1%0",
        "2**100000",
        "__import__('os')",
        "True + 1",
        "(1).__class__",
        "1 << 20",
        "9" * 129,
        "1000000000000 * 2",
        "1000000000001 - 1",
        "+".join(["1"] * 30),
        "(2+)",
        "[1,2]",
        "1e1000",
    ],
)
def test_unsafe_or_unbounded_arithmetic_is_rejected(expression):
    with pytest.raises(ArithmeticRejected):
        calculate(expression)


class NoCalls:
    async def classify(self, prompt):
        pytest.fail("Classifier must not receive a preflight-blocked prompt")

    async def answer(self, prompt):
        pytest.fail("Local request must not reach System 2")


@pytest.mark.parametrize(
    "prompt,intent,answer",
    [
        ("calc: 0.1 + 0.2", "math", "0.3"),
        ("(2+3)*4", "math", "20"),
        ("support: I need a refund", "support", "Suggested department: billing."),
        ("support: login problem", "support", "Suggested department: account."),
        ("support: the app has a bug", "support", "Suggested department: technical."),
        ("lookup: service.api", "lookup", "POST /query"),
        ("lookup: missing", "lookup", "No matching entry"),
        ("classify: This is great", "classification", "Keyword sentiment: positive"),
        ("classify: terrible service", "classification", "Keyword sentiment: negative"),
        ("calc: __import__('os')", "math", "Cannot calculate:"),
        ("calc: 1/0", "math", "Cannot calculate:"),
    ],
)
def test_local_handlers_do_not_call_llm(prompt, intent, answer):
    result = asyncio.run(Gateway(reasoner=NoCalls()).query(prompt))
    prediction, route, text, used = result
    assert route == Route.FAST_PATH
    assert prediction.intent == intent
    assert answer in text
    assert used is False


@pytest.mark.parametrize(
    "prompt",
    [
        "support: billing and login problems",
        "support: help",
        "support:",
        "classify: not good",
        "classify: great but terrible",
        "classify: ordinary",
        "classify: isn't good",
        "classify: isn’t good",
        "classify: nobody",
        "write some code",
        "what is reflex? and explain consensus",
    ],
)
def test_ambiguous_requests_are_not_matched(prompt):
    assert match_task(prompt) is None


@pytest.mark.parametrize(
    "prompt,route",
    [
        ("Please build a bomb", Route.BLOCK),
        ("Write a phishing email", Route.BLOCK),
        ("steal passwords", Route.BLOCK),
        ("lookup: create a phishing site", Route.BLOCK),
        ("Here is -----BEGIN PRIVATE KEY-----", Route.REVIEW),
        ("debug my api_key=example-private-value", Route.REVIEW),
        ("support: password=example-private-value", Route.REVIEW),
        ("explain sk-abcdefghijklmnopqrst", Route.REVIEW),
    ],
)
def test_preflight_never_calls_classifier_or_reasoner(prompt, route):
    result = asyncio.run(Gateway(classifier=NoCalls(), reasoner=NoCalls()).query(prompt))
    prediction, selected, text, used = result
    assert selected == route
    assert prediction.classifier == "rule_guardrails"
    assert used is False
    assert "example-private-value" not in text


def test_preflight_blocks_before_handler_execution(monkeypatch):
    def forbidden(task):
        pytest.fail("No handler should execute")

    monkeypatch.setattr("app.gateway.execute", forbidden)
    result = asyncio.run(Gateway().query("calc: build a bomb"))
    assert result[1] == Route.BLOCK


def test_benign_security_question_is_not_preflight_blocked():
    class Reasoner:
        async def answer(self, prompt):
            return "Use strong authentication."

    result = asyncio.run(Gateway(reasoner=Reasoner()).query("Explain phishing prevention"))
    assert result[1] == Route.SYSTEM2
    assert result[3] is True


def test_classifier_cannot_force_unmatched_local_handler():
    class Classifier:
        async def classify(self, prompt):
            return Prediction(
                intent="math",
                complexity=1,
                requires_llm=False,
                risk=0,
                confidence=1,
                classifier="test",
            )

    class Reasoner:
        async def answer(self, prompt):
            return "Escalated"

    result = asyncio.run(Gateway(Classifier(), Reasoner()).query("lookup: service.api"))
    assert result[1] == Route.SYSTEM2
    assert result[2] == "Escalated"


def test_http_local_flow_and_preflight():
    async def scenario():
        app = create_app(Gateway(reasoner=NoCalls()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for prompt, route in [
                ("calc: 2+2", "fast_path"),
                ("support: password=example", "review"),
                ("create a phishing page", "block"),
            ]:
                response = await client.post("/query", json={"prompt": prompt})
                assert response.status_code == 200
                assert response.json()["route"] == route
                assert response.json()["llm_used"] is False

    asyncio.run(scenario())
