"""Hostable ASGI app for the /v1 location API (NOT deployed with the static Cloudflare Pages site).

    DRIFT_CORS_ORIGINS="https://sih-hyperlocal-warning.pages.dev" uvicorn backend.api_app:app

Security defaults: read-only (GET/HEAD/OPTIONS), no credentials, and CORS is deny-by-default: only origins listed in
DRIFT_CORS_ORIGINS (comma-separated, exact match) receive CORS headers. Alert dispatch endpoints are not mounted here.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.location_api import router


def create_app(origins=None) -> FastAPI:
    app = FastAPI(title="DRIFT location API", version="1", docs_url="/v1/docs", openapi_url="/v1/openapi.json")
    allowed = [o.strip() for o in (os.environ.get("DRIFT_CORS_ORIGINS", "") if origins is None else ",".join(origins)).split(",") if o.strip()]
    if "*" in allowed:
        raise RuntimeError("wildcard CORS origin is not allowed for DRIFT")
    app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=False, allow_methods=["GET", "HEAD", "OPTIONS"], allow_headers=["Content-Type", "If-None-Match"], max_age=600)
    app.include_router(router)
    return app


app = create_app()
