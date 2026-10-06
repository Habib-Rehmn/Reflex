"""Explicit local commands. Matching never executes a handler or performs I/O."""

import ast
import re
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext

FAQ = {
    "what is reflex?": "Reflex answers simple requests locally and routes deeper questions to GPT.",
    "what can you do?": (
        "I support FAQs, calc: arithmetic, support: department suggestions, "
        "lookup: catalog entries, and classify: sentiment. "
        "Other requests use System 2 when configured."
    ),
    "do you store my prompts?": (
        "Reflex stores request metadata, not prompts or answers. Escalated prompts go to OpenAI "
        "when configured; local handling does not call OpenAI."
    ),
}

# Public demonstration catalog, not live operational data or customer records.
CATALOG = {
    "service.api": "Reflex API: POST /query; interactive documentation at /docs.",
    "service.health": "API liveness: GET /health. This does not verify provider readiness.",
    "project.reflex": "Reflex is a System-1 / System-2 AI gateway.",
}

DEPARTMENTS = {
    "billing": {"billing", "invoice", "payment", "charged", "refund"},
    "technical": {"technical", "bug", "error", "crash", "outage"},
    "account": {"account", "login", "password", "signin"},
}
POSITIVE = {"good", "great", "excellent", "love", "happy", "helpful"}
NEGATIVE = {"bad", "awful", "terrible", "hate", "unhappy", "broken"}


@dataclass(frozen=True)
class LocalTask:
    intent: str
    value: str


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.casefold()))


def match_task(prompt: str) -> LocalTask | None:
    normalized = prompt.strip().casefold()
    if normalized in FAQ:
        return LocalTask("faq", normalized)
    if normalized.startswith("calc:"):
        return LocalTask("math", normalized[5:].strip())
    if re.fullmatch(r"[\d\s.+*/%()\-]+", normalized) and re.search(r"\d", normalized):
        return LocalTask("math", normalized)
    prefix, separator, value = normalized.partition(":")
    if not separator or not value.strip():
        return None
    value = value.strip()
    if prefix == "lookup":
        return LocalTask("lookup", value)
    if prefix == "support":
        tokens = words(value)
        matches = [name for name, keywords in DEPARTMENTS.items() if tokens & keywords]
        return LocalTask("support", matches[0]) if len(matches) == 1 else None
    if prefix == "classify":
        tokens = words(value)
        # Negation, contractions, and mixed polarity need contextual interpretation.
        if tokens & {"not", "no", "never", "hardly", "barely"} or "'" in value or "’" in value:
            return None
        positive, negative = bool(tokens & POSITIVE), bool(tokens & NEGATIVE)
        if positive != negative:
            return LocalTask("classification", "positive" if positive else "negative")
    return None


class ArithmeticRejected(ValueError):
    pass


def calculate(expression: str) -> str:
    """Interpret a small arithmetic AST with bounded input, work, and magnitude."""
    if not expression or len(expression) > 128:
        raise ArithmeticRejected("Use an expression of 1–128 characters.")
    if not re.fullmatch(r"[0-9\s.+*/%()\-]+", expression):
        raise ArithmeticRejected("Only numbers, parentheses, and + - * / // % are supported.")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError):
        raise ArithmeticRejected("Invalid arithmetic expression.") from None
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ArithmeticRejected("Use a simpler expression (at most 64 syntax nodes).")

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(expression, node)
            assert literal is not None
            result = Decimal(literal)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = evaluate(node.operand)
            result = operand if isinstance(node.op, ast.UAdd) else -operand
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
        ):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            else:
                # Floor division and modulo follow Python's sign convention.
                quotient = (left / right).to_integral_value(rounding="ROUND_FLOOR")
                result = quotient if isinstance(node.op, ast.FloorDiv) else left - quotient * right
        else:
            raise ArithmeticRejected("Unsupported arithmetic operation; powers are not supported.")
        if not result.is_finite() or abs(result) > Decimal("1e12"):
            raise ArithmeticRejected("Numbers and intermediate results must be within ±1 trillion.")
        return result

    try:
        with localcontext() as context:
            context.prec = 28
            result = evaluate(tree.body)
            return "0" if result == 0 else format(result.normalize(), "f")
    except DecimalException:
        raise ArithmeticRejected(
            "Undefined arithmetic, division by zero, or numeric limit exceeded."
        ) from None


def execute(task: LocalTask) -> str:
    if task.intent == "faq":
        return FAQ[task.value]
    if task.intent == "math":
        try:
            return calculate(task.value)
        except ArithmeticRejected as exc:
            return f"Cannot calculate: {exc}"
    if task.intent == "support":
        return f"Suggested department: {task.value}. No ticket has been created or sent."
    if task.intent == "lookup":
        return CATALOG.get(task.value, "No matching entry in the local demonstration catalog.")
    if task.intent == "classification":
        return f"Keyword sentiment: {task.value}. This is a rule-based label."
    raise ValueError("Unknown local task")
