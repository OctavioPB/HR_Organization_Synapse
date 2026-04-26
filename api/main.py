"""Org Synapse FastAPI application.

Start:
    uvicorn api.main:app --reload --port 8000

Docs:
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import alerts, connectors, graph, knowledge, risk, succession

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

app = FastAPI(
    title="Org Synapse API",
    description=(
        "Organizational Network Analysis — collaboration graph metrics, "
        "SPOF risk scores, silo detection, and What-If simulation."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
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

# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(graph.router)
app.include_router(risk.router)
app.include_router(alerts.router)
app.include_router(connectors.router)
app.include_router(knowledge.router)
app.include_router(succession.router)

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


@app.get("/", tags=["health"])
def root() -> dict:
    return {"status": "ok", "service": "org-synapse-api", "version": "0.1.0"}


@app.get("/health", tags=["health"])
def health() -> dict:
    """Liveness probe for container orchestrators.

    Includes cache and Prometheus availability for observability dashboards.
    """
    from api.cache import health as cache_health
    return {
        "status": "healthy",
        "cache": cache_health(),
        "metrics": "enabled" if _PROMETHEUS_AVAILABLE else "disabled",
    }
