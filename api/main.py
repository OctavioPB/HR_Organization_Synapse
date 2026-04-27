"""Org Synapse FastAPI application.

Start:
    uvicorn api.main:app --reload --port 8000

Docs:
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.tenant_middleware import TenantMiddleware

from api.routers import (
    admin, alerts, billing, compliance, connectors, graph, internal,
    knowledge, org_health, query, risk, succession, ws,
)

try:
    from prometheus_fastapi_instrumentator import Instrumentator as _PrometheusInstrumentator
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the Redis pub/sub subscriber on startup; cancel it on shutdown."""
    from api.ws.broadcaster import start_subscriber
    from api.ws.manager import manager

    subscriber_task = asyncio.create_task(start_subscriber(manager))
    logger.info("WebSocket alert subscriber started.")
    try:
        yield
    finally:
        subscriber_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await subscriber_task
        logger.info("WebSocket alert subscriber stopped.")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Org Synapse API",
    description=(
        "Organizational Network Analysis — collaboration graph metrics, "
        "SPOF risk scores, silo detection, What-If simulation, "
        "churn risk, knowledge risk, succession planning, and real-time alerts."
    ),
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────────────────────

_CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TenantMiddleware)   # extracts X-Tenant-ID + X-Api-Key → request.state

# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(graph.router)
app.include_router(risk.router)
app.include_router(alerts.router)
app.include_router(connectors.router)
app.include_router(knowledge.router)
app.include_router(succession.router)
app.include_router(admin.router)       # POST /admin/tenants (F6)
app.include_router(billing.router)     # GET /billing/usage, POST /billing/webhook (F6)
app.include_router(org_health.router)  # GET /org-health/* (F9)
app.include_router(query.router)       # POST /query/natural (F7)
app.include_router(compliance.router)  # GET /compliance/* (F8)
app.include_router(ws.router)          # WS /alerts/live
app.include_router(internal.router)   # POST /internal/alerts/broadcast

# ─── Prometheus metrics ───────────────────────────────────────────────────────
# Exposes GET /metrics (Prometheus text format) when the instrumentator is installed.
# Tracks: request count, request latency (p50/p95/p99), in-flight requests.

if _PROMETHEUS_AVAILABLE:
    _PrometheusInstrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health", "/"],
    ).instrument(app).expose(app, include_in_schema=False)


# ─── Health / root ────────────────────────────────────────────────────────────


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/", tags=["health"])
def root() -> dict:
    return {"status": "ok", "service": "org-synapse-api", "version": "0.2.0"}


@app.get("/health", tags=["health"])
def health() -> dict:
    """Liveness probe for container orchestrators.

    Includes cache, Prometheus availability, and WebSocket connection count.
    """
    from api.cache import health as cache_health
    from api.ws.manager import manager

    return {
        "status": "healthy",
        "cache": cache_health(),
        "metrics": "enabled" if _PROMETHEUS_AVAILABLE else "disabled",
        "websocket_connections": manager.connection_count,
    }
