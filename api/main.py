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

from api.routers import alerts, connectors, graph, risk

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


# ─── Health / root ────────────────────────────────────────────────────────────


@app.get("/", tags=["health"])
def root() -> dict:
    return {"status": "ok", "service": "org-synapse-api", "version": "0.1.0"}


@app.get("/health", tags=["health"])
def health() -> dict:
    """Liveness probe for container orchestrators."""
    return {"status": "healthy"}
