"""HTTP application; use create_app to inject providers without global state."""

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import Settings
from app.gateway import Gateway, ProviderUnavailable
from app.models import QueryRequest, QueryResponse
from app.openai_provider import OpenAIReasoner


def create_app(gateway: Gateway | None = None, *, settings: Settings | None = None) -> FastAPI:
    if gateway is not None:
        service = gateway
    else:
        config = settings if settings is not None else Settings()
        service = Gateway()
        if config.system2_provider == "openai":
            service.reasoner = OpenAIReasoner(config)
    api = FastAPI(title="Reflex AI Gateway", version="0.1.0")

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post(
        "/query",
        response_model=QueryResponse,
        responses={503: {"description": "Provider unavailable"}},
    )
    async def query(body: QueryRequest) -> QueryResponse | JSONResponse:
        request_id = uuid4()
        started = perf_counter()
        try:
            prediction, route, answer, llm_used = await service.query(body.prompt)
        except ProviderUnavailable:
            return JSONResponse(
                status_code=503,
                content={
                    "request_id": str(request_id),
                    "error": "provider_unavailable",
                    "detail": "The AI provider is unavailable. Check configuration or retry later.",
                },
            )
        return QueryResponse(
            request_id=request_id,
            route=route,
            answer=answer,
            prediction=prediction,
            llm_used=llm_used,
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )

    return api


app = create_app()
