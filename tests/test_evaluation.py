import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings
from app.gateway import ProviderUnavailable
from app.telemetry import ReasoningResult, TokenUsage
from evaluation.benchmark import Case, Dataset, run, summarize


def config():
    return Settings(_env_file=None, system2_provider="disabled")


def dataset():
    return Dataset(
        version="test",
        cases=[
            Case(
                id="math",
                prompt="calc: 2+2",
                expected_route="fast_path",
                expected_text="4",
                live_eligible=True,
            ),
            Case(
                id="reasoning",
                prompt="Explain consensus",
                expected_route="system2",
                live_eligible=True,
            ),
            Case(id="safety", prompt="api_key=synthetic", expected_route="review"),
        ],
    )


def test_offline_routes_without_fabricating_baseline():
    report = asyncio.run(run(dataset(), config()))
    assert report["routing_accuracy"] == 1
    assert report["baseline"] is None
    assert report["provider_call_reduction"] is None
    assert report["estimated_cost_saved_usd"] is None
    assert report["gateway"]["provider_attempts"] == 0
    assert report["gateway"]["exact_match_scored"] == 1
    assert report["gateway"]["errors"] == 1
    assert "api_key=synthetic" not in str(report)


class Reasoner:
    def __init__(self):
        self.prompts = []

    async def answer(self, prompt):
        self.prompts.append(prompt)
        return ReasoningResult(
            "4", "test", TokenUsage(input_tokens=10, output_tokens=1, cached_input_tokens=0)
        )


def test_live_pairs_excludes_safety_and_calculates_complete_costs():
    reasoner = Reasoner()
    settings = config().model_copy(
        update={
            "pricing_model": "test",
            "input_usd_per_million": Decimal(1),
            "output_usd_per_million": Decimal(2),
        }
    )
    report = asyncio.run(run(dataset(), settings, live=True, repeats=2, reasoner=reasoner))
    assert report["selected_cases"] == 2
    assert report["excluded_cases"] == 1
    assert report["gateway"]["requests"] == report["baseline"]["requests"] == 4
    assert report["provider_call_reduction"] == 0.5
    assert Decimal(report["estimated_cost_saved_usd"]) == Decimal("0.000024")
    assert len(reasoner.prompts) == 6
    assert all("api_key" not in prompt for prompt in reasoner.prompts)


def test_failed_provider_suppresses_savings():
    class Failed:
        async def answer(self, prompt):
            raise ProviderUnavailable("secret upstream details")

    report = asyncio.run(run(dataset(), config(), live=True, reasoner=Failed()))
    assert report["provider_call_reduction"] is None
    assert report["estimated_cost_saved_usd"] is None
    assert report["baseline"]["errors"] == 2
    assert "secret upstream details" not in str(report)


def test_unknown_prices_do_not_produce_cost_savings():
    report = asyncio.run(run(dataset(), config(), live=True, reasoner=Reasoner()))
    assert report["estimated_cost_saved_usd"] is None
    assert report["baseline"]["unknown_cost_requests"] == 2


def test_dataset_rejects_duplicates_and_live_safety():
    with pytest.raises(ValueError):
        Dataset(version="test", cases=[dataset().cases[0]] * 2)
    with pytest.raises(ValueError):
        Case(id="unsafe", prompt="test", expected_route="block", live_eligible=True)
    with pytest.raises(ValueError):
        Case(id="empty", prompt=" ", expected_route="fast_path")


def test_invalid_repeat_and_live_configuration():
    with pytest.raises(ValueError):
        asyncio.run(run(dataset(), config(), repeats=0))
    with pytest.raises(ValueError):
        asyncio.run(run(dataset(), config(), live=True))


def test_summary_nearest_rank_and_unknowns():
    rows = [
        dict(
            latency_ms=i,
            error=None,
            provider_attempted=False,
            exact_match=None,
            estimated_cost_usd=None,
            usage=None,
        )
        for i in range(1, 21)
    ]
    result = summarize(rows)
    assert result["p95_latency_ms"] == 19
    assert result["mean_latency_ms"] == 10.5
    assert result["unknown_cost_requests"] == 20
    assert result["exact_match_rate"] is None
    assert summarize([])["p95_latency_ms"] is None


def test_bundled_dataset_includes_honest_challenges():
    data = Dataset.model_validate_json(Path("evaluation/dataset.json").read_text())
    report = asyncio.run(run(data, config()))
    assert 0 < report["routing_accuracy"] < 1
    assert report["selected_cases"] == len(data.cases)
