"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routes import catalog, jobs
from config.settings import API_KEYS


def create_app() -> FastAPI:
    app = FastAPI(title="Football Highlights API", version="1.0.0")

    @app.middleware("http")
    async def api_key_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Skip auth for health check
        if request.url.path == "/api/v1/health":
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if API_KEYS and api_key not in API_KEYS:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {"code": "unauthorized", "message": "Invalid or missing API key"}
                },
            )
        return await call_next(request)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")

    return app


app = create_app()
