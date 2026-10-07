"""Run with python -m evaluation.benchmark; offline unless --live is explicit."""

import argparse
import asyncio
import hashlib
import json
import math
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.gateway import Gateway, ProviderUnavailable
from app.guardrails import preflight
from app.laya_provider import LayaClassifier
from app.models import QueryRequest, Route
from app.openai_provider import OpenAIReasoner
from app.telemetry import Observation, ReasoningResult, estimate_cost


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    prompt: str
    expected_route: Route
    expected_text: str | None = None
    live_eligible: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_prompt(self):
        self.prompt = QueryRequest(prompt=self.prompt).prompt
        if self.live_eligible and self.expected_route in (Route.BLOCK, Route.REVIEW):
            raise ValueError("Safety cases must remain offline")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case IDs")
        return self


async def measure(case, gateway, settings, *, baseline=False, offline=False):
    observation = Observation()
    answer, error = None, None
    start = perf_counter()
    try:
        if baseline:
            observation.provider_attempted = True
            observation.route = Route.SYSTEM2
            result = await gateway.reasoner.answer(case.prompt)
            if isinstance(result, ReasoningResult):
                answer = result.text
                observation.model, observation.usage = result.model, result.usage
            else:
                answer = result
        else:
            _, _, answer, _ = await gateway.query(case.prompt, observation)
    except ProviderUnavailable as exc:
        error = "not_executed_offline" if offline else "provider_unavailable"
        if exc.result:
            observation.model, observation.usage = exc.result.model, exc.result.usage
    except Exception:
        error = "internal_error"
    latency = (perf_counter() - start) * 1000
    cost = (
        Decimal(0)
        if not observation.provider_attempted
        else estimate_cost(observation.usage, observation.model, settings)
    )
    # An offline escalation is unmeasured, not a zero-cost GPT completion.
    if error == "not_executed_offline":
        cost = None
    return {
        "id": case.id,
        "route": observation.route,
        "error": error,
        "route_correct": observation.route == case.expected_route if not baseline else None,
        "exact_match": (
            answer.strip() == case.expected_text.strip()
            if answer is not None and case.expected_text is not None
            else None
        ),
        "latency_ms": latency,
        "provider_attempted": observation.provider_attempted,
        "model": observation.model,
        "usage": observation.usage.model_dump() if observation.usage else None,
        "estimated_cost_usd": str(cost) if cost is not None else None,
    }


def summarize(rows):
    latencies = sorted(row["latency_ms"] for row in rows)
    judged = [row["exact_match"] for row in rows if row["exact_match"] is not None]
    costs = [
        Decimal(row["estimated_cost_usd"]) for row in rows if row["estimated_cost_usd"] is not None
    ]
    return {
        "requests": len(rows),
        "errors": sum(row["error"] is not None for row in rows),
        "provider_attempts": sum(row["provider_attempted"] for row in rows),
        "mean_latency_ms": mean(latencies) if latencies else None,
        "p95_latency_ms": latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
        "exact_match_scored": len(judged),
        "exact_match_rate": sum(judged) / len(judged) if judged else None,
        "known_cost_usd": str(sum(costs, Decimal(0))),
        "unknown_cost_requests": len(rows) - len(costs),
        "known_input_tokens": sum(row["usage"]["input_tokens"] for row in rows if row["usage"]),
        "known_output_tokens": sum(row["usage"]["output_tokens"] for row in rows if row["usage"]),
        "unknown_provider_usage": sum(
            row["provider_attempted"] and row["usage"] is None for row in rows
        ),
    }


async def run(dataset, settings, *, live=False, repeats=1, seed=42, reasoner=None):
    if repeats < 1 or repeats > 100:
        raise ValueError("repeats must be between 1 and 100")
    gateway = Gateway()
    if live:
        if reasoner is None and settings.system2_provider != "openai":
            raise ValueError("Live evaluation requires SYSTEM2_PROVIDER=openai")
        gateway.reasoner = reasoner if reasoner is not None else OpenAIReasoner(settings)
        if settings.system1_provider == "laya":
            gateway.classifier = LayaClassifier(settings)
    cases = [case for case in dataset.cases if not live or case.live_eligible]
    if not cases:
        raise ValueError("No eligible evaluation cases")
    # Check the complete selection before either arm makes any paid requests.
    # Labels supplied by a custom dataset cannot bypass local preflight rules.
    if live and any(preflight(case.prompt) is not None for case in cases):
        raise ValueError("Live selection includes a prompt held or blocked by preflight")
    rng = random.Random(seed)
    gateway_rows, baseline_rows = [], []
    for _ in range(repeats):
        ordered = list(cases)
        rng.shuffle(ordered)
        for case in ordered:
            arms = [False, True] if live else [False]
            rng.shuffle(arms)
            for baseline in arms:
                row = await measure(case, gateway, settings, baseline=baseline, offline=not live)
                (baseline_rows if baseline else gateway_rows).append(row)
    gs, bs = summarize(gateway_rows), summarize(baseline_rows)
    costs_complete = live and not gs["unknown_cost_requests"] and not bs["unknown_cost_requests"]
    successful = live and not gs["errors"] and not bs["errors"]
    return {
        "schema_version": 1,
        "dataset_version": dataset.version,
        "dataset_sha256": hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "live" if live else "offline",
        "seed": seed,
        "repeats": repeats,
        "warmup_requests": 0,
        "selected_cases": len(cases),
        "excluded_cases": len(dataset.cases) - len(cases),
        "classifier": settings.system1_provider if live else "rules",
        "configured_model": settings.openai_model if live else None,
        "max_output_tokens": settings.openai_max_output_tokens if live else None,
        "timeout_seconds": settings.provider_timeout_seconds if live else None,
        "pricing": {
            "model": settings.pricing_model,
            "date": str(settings.pricing_date) if settings.pricing_date is not None else None,
            "input": (
                str(settings.input_usd_per_million)
                if settings.input_usd_per_million is not None
                else None
            ),
            "output": (
                str(settings.output_usd_per_million)
                if settings.output_usd_per_million is not None
                else None
            ),
            "cached_input": (
                str(settings.cached_input_usd_per_million)
                if settings.cached_input_usd_per_million is not None
                else None
            ),
        },
        "routing_accuracy": sum(row["route_correct"] for row in gateway_rows) / len(gateway_rows),
        "gateway": gs,
        "baseline": bs if live else None,
        "provider_call_reduction": (
            1 - gs["provider_attempts"] / bs["provider_attempts"]
            if successful and bs["provider_attempts"]
            else None
        ),
        "estimated_cost_saved_usd": (
            str(Decimal(bs["known_cost_usd"]) - Decimal(gs["known_cost_usd"]))
            if successful and costs_complete
            else None
        ),
        "gateway_rows": gateway_rows,
        "baseline_rows": baseline_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("dataset.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    parser.add_argument(
        "--live", action="store_true", help="Send eligible prompts to OpenAI; incurs cost"
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        if args.output.resolve() == args.dataset.resolve() or (
            args.output.exists() and args.dataset.exists() and args.output.samefile(args.dataset)
        ):
            raise ValueError("Output cannot overwrite the dataset")
        dataset = Dataset.model_validate_json(args.dataset.read_text())
        settings = (
            Settings() if args.live else Settings(_env_file=None, system2_provider="disabled")
        )
        report = asyncio.run(
            run(dataset, settings, live=args.live, repeats=args.repeats, seed=args.seed)
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    except (ValueError, OSError):
        parser.exit(2, "Evaluation failed: check dataset, configuration, and output path.\n")
    print(f"Saved {report['mode']} evaluation to {args.output}")
    print(f"Routing accuracy: {report['routing_accuracy']:.1%}")
    if args.live and (report["gateway"]["errors"] or report["baseline"]["errors"]):
        parser.exit(1, "Provider failures recorded; savings are unavailable.\n")


if __name__ == "__main__":
    main()
