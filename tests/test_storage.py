import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.config import Settings
from app.gateway import Gateway
from app.main import create_app
from app.openai_provider import OpenAIReasoner
from app.storage import RequestStore
from app.telemetry import ReasoningResult, TokenUsage, estimate_cost


def config(tmp_path, **kwargs):
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "ledger.db"),
        system2_provider="disabled",
        **kwargs,
    )


def event(index, **overrides):
    return (
        dict(
            request_id=str(index),
            created_at=datetime.now(UTC).isoformat(),
            status_code=200,
            route="fast_path",
            prediction=None,
            provider_attempted=0,
            model=None,
            latency_ms=index,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            estimated_cost_usd="0",
            pricing=None,
            error_code=None,
        )
        | overrides
    )


def test_ledger_reopen_pagination_retention_and_metrics(tmp_path):
    path = str(tmp_path / "ledger.db")
    store = RequestStore(path, retention_days=1)
    store.record(event(99, created_at=(datetime.now(UTC) - timedelta(days=2)).isoformat()))
    for i in range(1, 21):
        store.record(event(i))
    reopened = RequestStore(path, retention_days=1)
    assert len(reopened.recent()) == 20
    assert len(reopened.recent(3, 2)) == 3
    metrics = reopened.metrics()
    assert metrics["total_requests"] == 20
    assert metrics["average_latency_ms"] == 10.5
    assert metrics["p95_latency_ms"] == 19
    assert metrics["known_estimated_cost_usd"] == "0"
    assert metrics["unknown_cost_requests"] == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_unknowns_do_not_become_zero_and_errors_count(tmp_path):
    store = RequestStore(str(tmp_path / "ledger.db"))
    store.record(
        event(
            1,
            route="system2",
            status_code=503,
            provider_attempted=1,
            input_tokens=None,
            output_tokens=None,
            cached_input_tokens=None,
            estimated_cost_usd=None,
            error_code="provider_unavailable",
        )
    )
    metrics = store.metrics()
    assert metrics["failed_requests"] == metrics["provider_attempts"] == 1
    assert metrics["unknown_usage_requests"] == metrics["unknown_cost_requests"] == 1
    assert metrics["known_input_tokens"] is None
    assert metrics["routes"] == {"system2": 1}


def test_pricing_requires_exact_model_and_full_usage(tmp_path):
    settings = config(
        tmp_path,
        pricing_model="test-snapshot",
        pricing_date="2026-01-01",
        input_usd_per_million="2",
        output_usd_per_million="8",
        cached_input_usd_per_million="1",
    )
    usage = TokenUsage(input_tokens=1000, output_tokens=100, cached_input_tokens=200)
    assert estimate_cost(usage, "test-snapshot", settings) == Decimal("0.0026")
    assert estimate_cost(usage, "other-model", settings) is None
    assert estimate_cost(None, "test-snapshot", settings) is None
    assert (
        estimate_cost(TokenUsage(input_tokens=1, output_tokens=1), "test-snapshot", settings)
        is None
    )
    assert (
        estimate_cost(
            usage,
            "test-snapshot",
            settings.model_copy(update={"cached_input_usd_per_million": None}),
        )
        is None
    )


def test_partial_pricing_rejected(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, input_usd_per_million="2")


def test_metadata_api_records_local_failure_validation_and_never_text(tmp_path):
    settings = config(tmp_path)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(Gateway(), settings=settings)),
            base_url="http://test",
        ) as client:
            local = await client.post("/query", json={"prompt": "calc: 2+2"})
            assert local.json()["metadata_persisted"] is True
            assert local.json()["estimated_cost_usd"] == "0"
            await client.post("/query", json={"prompt": "private unique prompt"})
            invalid = await client.post("/query", json={"prompt": {"secret": "do-not-store"}})
            assert "do-not-store" not in invalid.text
            rows = (await client.get("/requests")).json()["requests"]
            assert {row["status_code"] for row in rows} == {200, 422, 503}
            assert all("prompt" not in row and "answer" not in row for row in rows)
            assert "private unique prompt" not in json.dumps(rows)
            assert "do-not-store" not in json.dumps(rows)
            metrics = (await client.get("/metrics")).json()
            assert metrics["total_requests"] == 3
            assert metrics["provider_attempts"] == 0
            assert (await client.get("/requests?limit=101")).status_code == 422

    asyncio.run(scenario())


def test_database_failure_preserves_answer(tmp_path):
    class BrokenStore:
        def record(self, event):
            raise sqlite3.OperationalError("private path")

        def metrics(self):
            raise sqlite3.OperationalError("private path")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(Gateway(), settings=config(tmp_path), store=BrokenStore())
            ),
            base_url="http://test",
        ) as client:
            response = await client.post("/query", json={"prompt": "calc: 2+2"})
            assert response.status_code == 200
            assert response.json()["answer"] == "4"
            assert response.json()["metadata_persisted"] is False
            response = await client.get("/metrics")
            assert response.status_code == 503
            assert "private path" not in response.text

    asyncio.run(scenario())


def test_concurrent_requests_keep_usage_separate(tmp_path):
    class Reasoner:
        async def answer(self, prompt):
            await asyncio.sleep(0)
            number = int(prompt.split()[-1])
            return ReasoningResult(
                "answer",
                "test",
                TokenUsage(input_tokens=number, output_tokens=number * 2, cached_input_tokens=0),
            )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(Gateway(reasoner=Reasoner()), settings=config(tmp_path))
            ),
            base_url="http://test",
        ) as client:
            responses = await asyncio.gather(
                *(client.post("/query", json={"prompt": f"task {i}"}) for i in range(1, 6))
            )
            assert [r.json()["usage"]["input_tokens"] for r in responses] == list(range(1, 6))
            rows = (await client.get("/requests")).json()["requests"]
            assert sorted(row["input_tokens"] for row in rows) == list(range(1, 6))

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_provider_usage_survives_success_and_incomplete_response(tmp_path, status):
    settings = config(tmp_path, openai_api_key="test-key", openai_model="test")
    body = {
        "status": status,
        "model": "test",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 2},
        },
    }
    provider = OpenAIReasoner(
        settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(Gateway(reasoner=provider), settings=settings)
            ),
            base_url="http://test",
        ) as client:
            response = await client.post("/query", json={"prompt": "Explain consensus"})
            assert response.status_code == (200 if status == "completed" else 503)
            row = (await client.get("/requests")).json()["requests"][0]
            assert row["input_tokens"] == 10
            assert row["output_tokens"] == 20
            assert row["cached_input_tokens"] == 2
            assert row["estimated_cost_usd"] is None

    asyncio.run(scenario())


def test_cache_write_usage_does_not_produce_underestimate(tmp_path):
    settings = config(
        tmp_path,
        pricing_model="test",
        pricing_date="2026-01-01",
        input_usd_per_million="2",
        output_usd_per_million="8",
    )
    usage = TokenUsage(
        input_tokens=100, output_tokens=1, cached_input_tokens=0, cache_write_input_tokens=50
    )
    assert estimate_cost(usage, "test", settings) is None


def test_unknown_schema_is_not_overwritten(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(sqlite3.DatabaseError, match="Unsupported"):
        RequestStore(str(path)).metrics()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_cost_and_pricing_snapshot_are_persisted(tmp_path):
    settings = config(
        tmp_path,
        pricing_model="test",
        pricing_date="2026-01-01",
        input_usd_per_million="2",
        output_usd_per_million="8",
    )

    class Reasoner:
        async def answer(self, prompt):
            return ReasoningResult(
                "answer",
                "test",
                TokenUsage(input_tokens=1000, output_tokens=100, cached_input_tokens=0),
            )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(Gateway(reasoner=Reasoner()), settings=settings)
            ),
            base_url="http://test",
        ) as client:
            response = await client.post("/query", json={"prompt": "Explain consensus"})
            assert Decimal(response.json()["estimated_cost_usd"]) == Decimal("0.0028")
            row = (await client.get("/requests")).json()["requests"][0]
            assert row["pricing"]["currency"] == "USD"
            assert row["pricing"]["date"] == "2026-01-01"
            assert row["pricing"]["model"] == "test"

    asyncio.run(scenario())
