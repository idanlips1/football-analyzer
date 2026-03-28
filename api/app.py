"""FastAPI application factory."""

from __future__ import annotations

import html as html_module

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.dependencies import get_job_store
from api.routes import catalog, jobs
from config.settings import API_KEYS
from models.job import JobStatus


def create_app() -> FastAPI:
    app = FastAPI(title="Football Highlights API", version="1.0.0")

    @app.middleware("http")
    async def api_key_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Skip auth for health check and browser-facing watch page
        if request.url.path == "/api/v1/health" or request.url.path.startswith("/watch/"):
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

    @app.get("/watch/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    async def watch_job(job_id: str) -> HTMLResponse:
        store = get_job_store()
        job = store.get(job_id)
        if job is None or job.status != JobStatus.COMPLETED or not job.result:
            return HTMLResponse("<h1>Not ready yet — check back soon.</h1>", status_code=404)

        sas_url = html_module.escape(job.result.download_url, quote=True)
        title = html_module.escape(job.match_id)
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title} — Highlights</title>
  <style>
    body {{ margin: 0; background: #000; display: flex; flex-direction: column;
           align-items: center; justify-content: center; min-height: 100vh;
           font-family: sans-serif; }}
    h1   {{ color: #fff; font-size: 1rem; margin-bottom: 1rem; opacity: 0.8; }}
    video {{ max-width: 960px; width: 100%; outline: none; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <video src="{sas_url}" controls preload="metadata"></video>
</body>
</html>"""
        return HTMLResponse(body)

    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")

    return app


app = create_app()
