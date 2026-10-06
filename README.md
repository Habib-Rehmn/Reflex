# Reflex

A System-1 / System-2 AI gateway that aims to reduce unnecessary GPT calls by
handling supported simple requests locally.

Module 1 provides a FastAPI API, validated request/response contracts, deterministic
routing, two FAQ examples, and injectable classifier/reasoning interfaces.
**Laya and OpenAI are not connected yet.** The bootstrap classifier is exact FAQ
matching, not a trained model or safety detector. Unknown prompts return HTTP 503
until a reasoning provider is implemented in module 2. No API key is needed now.

## Run locally

Install Python 3.12+ and uv, then from this directory:

```bash
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1
```

Open http://127.0.0.1:8000/docs for the interactive API. `GET /health` is a liveness
check; it does not assert that external providers are configured.

```bash
curl http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"What is Reflex?"}'
```

The response includes `answer`, `route`, `prediction`, `llm_used`, `request_id`,
and `latency_ms`. Supported FAQs are `What is Reflex?` and `What can you do?`
(case-insensitive, with surrounding whitespace ignored). Requests accept only a
`prompt` string of 1–8,000 characters after trimming; invalid input returns 422.

Risk 4–5 blocks and risk 3 returns a held review response. No review queue exists
yet. Other requests use System 2 if the classifier requires it, complexity exceeds
2, confidence is below 0.85, or no local handler exists. The development classifier
always assigns placeholder risk 0; safety behavior is tested with injected predictions.

## Check changes

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests exercise HTTP validation, FAQ responses, provider escalation, unavailable
providers, and safety precedence without calling external services. The HTTP tests
use HTTPX's ASGI transport to avoid network sockets and background threads.

Validation result: **21 tests passed**, Ruff lint passed, and formatting passed.

In the initial restricted workspace, package downloads were unavailable. Tests and
lint were run against locally cached packages; a clean `uv sync` remains to be
verified with network access. No dependency lockfile is claimed for this module.

## Commit workflow

Each numbered module gets one commit containing implementation, tests, and docs.
Pause after each commit for the owner to push. Do not start the next module until
the owner indicates they are ready. Project planning notes are kept locally and
excluded from version control.

Initial commit message: `feat: add API and routing foundation`

The initial workspace exposes an empty, read-only `.git` directory, so Git
initialization and committing must run from a terminal with normal repository
write access. No remote has been supplied.

## Next module

Identify the exact Laya library/service and its API, then connect Laya and OpenAI
with environment configuration, timeouts, and provider contract tests. Subsequent
modules add fast paths, guardrails, persistence, measured evaluation, and a dashboard.
No latency, quality, or cost-savings claims have been measured yet.
