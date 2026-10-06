"""Narrow, deterministic preflight rules; not a comprehensive safety classifier."""

import re

from app.models import Prediction

SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\b(?:api[_ -]?key|password|secret[_ -]?key)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
HARMFUL_INSTRUCTION = re.compile(
    r"\b(?:steal|harvest)\s+(?:(?:user|users|someone|their|other people's)[’']?s?\s+)?"
    r"(?:passwords|credentials)\b"
    r"|\b(?:create|write|build)\s+(?:a\s+)?phishing\s+(?:email|page|site)\b"
    r"|\b(?:make|build)\s+(?:a\s+)?bomb\b",
    re.IGNORECASE,
)


def preflight(prompt: str) -> Prediction | None:
    normalized = " ".join(prompt.split())
    if HARMFUL_INSTRUCTION.search(normalized):
        risk, intent = 4, "blocked_instruction_pattern"
    elif SECRET.search(normalized):
        risk, intent = 3, "possible_secret"
    else:
        return None
    return Prediction(
        intent=intent,
        complexity=1,
        requires_llm=False,
        risk=risk,
        confidence=1.0,
        classifier="rule_guardrails",
    )
