"""Provider-independent routing and an intentionally limited development classifier."""

from dataclasses import dataclass, field
from typing import Protocol

from app.models import Prediction, Route

FAQ = {
    "what is reflex?": (
        "Reflex answers simple requests locally and routes deeper questions to GPT."
    ),
    "what can you do?": "Currently I can answer these FAQs. More fast paths and GPT are coming.",
}


class Classifier(Protocol):
    async def classify(self, prompt: str) -> Prediction: ...


class Reasoner(Protocol):
    async def answer(self, prompt: str) -> str: ...


class ProviderUnavailable(Exception):
    """The configured reasoning provider cannot currently serve requests."""


class RuleClassifier:
    """Bootstrap classifier, not Laya and not a safety detector."""

    async def classify(self, prompt: str) -> Prediction:
        known = prompt.casefold() in FAQ
        return Prediction(
            intent="faq" if known else "unknown",
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

    async def query(self, prompt: str) -> tuple[Prediction, Route, str, bool]:
        prediction = await self.classifier.classify(prompt)
        local_answer = FAQ.get(prompt.casefold()) if prediction.intent == "faq" else None
        route = select_route(prediction, fast_path_available=local_answer is not None)
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
            assert local_answer is not None
            return prediction, route, local_answer, False
        answer = await self.reasoner.answer(prompt)
        return prediction, route, answer, True
