"""HTTP API and metadata-only request ledger."""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.gateway import Gateway, ProviderUnavailable
from app.models import QueryRequest, QueryResponse
from app.openai_provider import OpenAIReasoner
from app.storage import RequestStore
from app.telemetry import Observation, TokenUsage, estimate_cost

logger = logging.getLogger(__name__)


def create_app(
    gateway: Gateway | None = None,
    *,
    settings: Settings | None = None,
    store: RequestStore | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    ledger = (
        store if store is not None else RequestStore(config.database_path, config.retention_days)
    )
    service = gateway if gateway is not None else Gateway()
    if gateway is None and config.system2_provider == "openai":
        service.reasoner = OpenAIReasoner(config)
    api = FastAPI(title="Reflex AI Gateway", version="0.5.0")

    static_dir = Path(__file__).with_name("static")
    api.mount("/static", StaticFiles(directory=static_dir), name="static")

    @api.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(static_dir / "index.html")

    async def record(request_id, observation, status, latency, error=None):
        usage = observation.usage
        # Requests that never invoked System 2 have known zero System-2 usage.
        local = not observation.provider_attempted
        if local:
            usage = TokenUsage(input_tokens=0, output_tokens=0, cached_input_tokens=0)
        cost = 0 if local else estimate_cost(usage, observation.model, config)
        pricing = None
        if cost is not None and not local:
            pricing = json.dumps(
                {
                    "model": config.pricing_model,
                    "date": str(config.pricing_date),
                    "currency": "USD",
                    "input_per_million": str(config.input_usd_per_million),
                    "output_per_million": str(config.output_usd_per_million),
                    "cached_input_per_million": (
                        str(config.cached_input_usd_per_million)
                        if config.cached_input_usd_per_million is not None
                        else None
                    ),
                }
            )
        event = dict(
            request_id=str(request_id),
            created_at=datetime.now(UTC).isoformat(),
            status_code=status,
            route=observation.route,
            prediction=observation.prediction.model_dump_json() if observation.prediction else None,
            provider_attempted=int(observation.provider_attempted),
            model=observation.model,
            latency_ms=latency,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            cache_write_input_tokens=usage.cache_write_input_tokens if usage else None,
            estimated_cost_usd=str(cost) if cost is not None else None,
            pricing=pricing,
            error_code=error,
        )
        try:
            ledger.record(event)
            return True, usage, event["estimated_cost_usd"]
        except (sqlite3.Error, OSError):
            logger.warning("Request metadata persistence failed")
            return False, usage, event["estimated_cost_usd"]

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        request_id = uuid4()
        persisted = False
        if request.url.path == "/query" and request.method == "POST":
            persisted, _, _ = await record(request_id, Observation(), 422, None, "invalid_request")
        return JSONResponse(
            status_code=422,
            content={
                "request_id": str(request_id),
                "error": "invalid_request",
                "detail": [
                    {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                    for e in exc.errors()
                ],
                "metadata_persisted": persisted,
            },
        )

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/requests")
    async def requests(
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
    ):
        try:
            return {"requests": ledger.recent(limit, offset)}
        except (sqlite3.Error, OSError):
            return JSONResponse(status_code=503, content={"error": "storage_unavailable"})

    @api.get("/metrics")
    async def metrics():
        try:
            return ledger.metrics()
        except (sqlite3.Error, OSError):
            return JSONResponse(status_code=503, content={"error": "storage_unavailable"})

    @api.post(
        "/query",
        response_model=QueryResponse,
        responses={503: {"description": "Provider unavailable"}},
    )
    async def query(body: QueryRequest) -> QueryResponse | JSONResponse:
        request_id = uuid4()
        started = perf_counter()
        observation = Observation()
        error, status = None, 200
        try:
            prediction, route, answer, llm_used = await service.query(body.prompt, observation)
        except ProviderUnavailable:
            error, status = "provider_unavailable", 503
        except Exception:
            logger.error("Unexpected gateway failure")
            error, status = "internal_error", 500
        latency = round((perf_counter() - started) * 1000, 3)
        persisted, usage, cost = await record(request_id, observation, status, latency, error)
        if error:
            return JSONResponse(
                status_code=status,
                content={
                    "request_id": str(request_id),
                    "error": error,
                    "detail": "Request failed. Check configuration or retry later.",
                    "metadata_persisted": persisted,
                },
            )
        return QueryResponse(
            request_id=request_id,
            route=route,
            answer=answer,
            prediction=prediction,
            llm_used=llm_used,
            latency_ms=latency,
            model=observation.model,
            usage=usage.model_dump() if usage else None,
            estimated_cost_usd=cost,
            metadata_persisted=persisted,
        )

    return api


app = create_app()
