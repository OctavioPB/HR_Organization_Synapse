.PHONY: help up down seed graph api frontend test test-unit test-integration lint

PYTHON = python
UVICORN = uvicorn

help:
	@echo ""
	@echo "Org Synapse — available targets"
	@echo "────────────────────────────────────────────────────────"
	@echo "  make up           Start all infrastructure (Docker)"
	@echo "  make down         Stop and remove containers"
	@echo "  make seed         Generate synthetic data (200 employees, 90 days)"
	@echo "  make seed-demo    Generate demo scenario (120 employees, 90 days)"
	@echo "  make graph        Build graph snapshot for today"
	@echo "  make api          Start FastAPI backend (port 8000)"
	@echo "  make frontend     Start React dev server (port 5173)"
	@echo "  make test         Run all unit tests"
	@echo "  make test-unit    Run unit tests only (no Docker required)"
	@echo "  make test-int     Run integration tests (requires docker-compose up)"
	@echo "  make lint         Check Python syntax with py_compile"
	@echo "────────────────────────────────────────────────────────"
	@echo ""

# ─── Infrastructure ───────────────────────────────────────────────────────────

up:
	docker-compose up -d
	@echo "Waiting for services to be healthy…"
	@sleep 10
	@echo "Done. Airflow UI: http://localhost:8088 | Adminer: http://localhost:8081"

down:
	docker-compose down

# ─── Data ─────────────────────────────────────────────────────────────────────

seed:
	$(PYTHON) data/synthetic/generate_org_data.py \
		--employees 200 \
		--days 90 \
		--seed 42

seed-demo:
	$(PYTHON) data/synthetic/generate_org_data.py \
		--employees 120 \
		--days 90 \
		--departments "Engineering:0.5,Sales:0.33,HR:0.17" \
		--seed 42
	@echo "Demo dataset generated."
	@echo "Connectors and withdrawing employee logged above."

# ─── Graph ────────────────────────────────────────────────────────────────────

graph:
	$(PYTHON) graph/builder.py --date $(shell date +%Y-%m-%d)

# ─── API ──────────────────────────────────────────────────────────────────────

api:
	$(UVICORN) api.main:app --reload --port 8000

# ─── Frontend ─────────────────────────────────────────────────────────────────

frontend:
	cd frontend && npm run dev

# ─── Tests ────────────────────────────────────────────────────────────────────

test: test-unit

test-unit:
	pytest tests/unit/ -v

test-int:
	pytest tests/integration/ -m integration -v

# ─── Lint ─────────────────────────────────────────────────────────────────────

lint:
	$(PYTHON) -m py_compile \
		graph/builder.py \
		graph/metrics.py \
		graph/silo_detector.py \
		graph/risk_scorer.py \
		ml/features/feature_extractor.py \
		ml/anomaly/isolation_forest.py \
		api/main.py \
		api/db.py \
		api/routers/graph.py \
		api/routers/risk.py \
		api/routers/alerts.py
	@echo "Syntax OK."
