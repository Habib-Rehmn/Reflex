"""Provider-independent routing and an intentionally limited development classifier."""

from dataclasses import dataclass, field
from typing import Protocol

from app.fast_paths import execute, match_task
from app.guardrails import preflight
from app.models import Prediction, Route
from app.telemetry import Observation, ReasoningResult


class Classifier(Protocol):
    async def classify(self, prompt: str) -> Prediction: ...


class Reasoner(Protocol):
    async def answer(self, prompt: str) -> str | ReasoningResult: ...


class ProviderUnavailable(Exception):
    """The configured reasoning provider cannot currently serve requests."""

    result: ReasoningResult | None = None


class RuleClassifier:
    """Bootstrap classifier, not Laya and not a safety detector."""

    async def classify(self, prompt: str) -> Prediction:
        task = match_task(prompt)
        known = task is not None
        return Prediction(
            intent=task.intent if task else "unknown",
            complexity=1 if known else 3,
            requires_llm=not known,
            risk=0,
            confidence=1.0 if known else 0.0,
            classifier="bootstrap_rules",
        )


class UnconfiguredReasoner:
    async def answer(self, prompt: str) -> str:
        raise ProviderUnavailable("System 2 is not configured yet.")


def select_route(prediction: Prediction, *, fast_path_available: bool) -> Route:
    # Safety decisions always take precedence over confidence and intent.
    if prediction.risk >= 4:
        return Route.BLOCK
    if prediction.risk == 3:
        return Route.REVIEW
    if (
        prediction.requires_llm
        or prediction.complexity > 2
        or prediction.confidence < 0.85
        or not fast_path_available
    ):
        return Route.SYSTEM2
    return Route.FAST_PATH


@dataclass
class Gateway:
    classifier: Classifier = field(default_factory=RuleClassifier)
    reasoner: Reasoner = field(default_factory=UnconfiguredReasoner)

    async def query(
        self, prompt: str, observation: Observation | None = None
    ) -> tuple[Prediction, Route, str, bool]:
        observation = observation if observation is not None else Observation()
        prediction = preflight(prompt)
        if prediction is None:
            prediction = await self.classifier.classify(prompt)
        task = match_task(prompt)
        available = task is not None and task.intent == prediction.intent
        route = select_route(prediction, fast_path_available=available)
        observation.prediction = prediction
        observation.route = route
        if route == Route.BLOCK:
            return prediction, route, "This request was blocked by the routing policy.", False
        if route == Route.REVIEW:
            return (
                prediction,
                route,
                "This request requires review; no review queue is configured.",
                False,
            )
        if route == Route.FAST_PATH:
            assert task is not None
            return prediction, route, execute(task), False
        observation.provider_attempted = not isinstance(self.reasoner, UnconfiguredReasoner)
        try:
            answer = await self.reasoner.answer(prompt)
        except ProviderUnavailable as exc:
            if exc.result:
                observation.model, observation.usage = exc.result.model, exc.result.usage
            raise
        if isinstance(answer, ReasoningResult):
            observation.model, observation.usage = answer.model, answer.usage
            answer = answer.text
        return prediction, route, answer, True
