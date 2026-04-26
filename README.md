# Organizational Synapse & Knowledge Risk

A graph-based HR intelligence platform that analyzes collaboration metadata to detect knowledge silos, identify single points of failure, and quantify organizational risk — before HR has any subjective signal.

---

## What it does

The system ingests collaboration metadata (Slack, Jira, calendar invites) as a stream of directed edges — who interacted with whom, on which channel, at what time — and builds a live organizational network graph. From that graph it computes five structural metrics per employee, runs daily and weekly ML pipelines, and surfaces the results through a REST API and interactive React dashboard.

**Core outputs:**

- **SPOF score** — single-point-of-failure risk per employee, derived from betweenness centrality, cross-department bridging, clustering, and entropy trend
- **Silo alerts** — communities whose internal/external edge ratio exceeds threshold (Louvain detection)
- **Anomaly alerts** — employees whose connectivity pattern deviates from their own 30-day baseline (Isolation Forest)
- **What-If simulation** — recalculates graph health after removing a single employee; shows component fragmentation and betweenness deltas

---

## Architecture

```
Collaboration tools (Slack, Teams, Jira, Calendar)
        │
        ▼
[Kafka] ── streaming metadata ingestion
        │  topic: collaboration.events.raw
        ▼
[Airflow DAGs] ── daily ETL + weekly ML
   ├── graph_builder_dag      (02:00 UTC daily)
   ├── anomaly_detection_dag  (03:00 UTC Mondays)
   └── risk_scoring_dag       (triggered on anomaly completion)
        │
        ▼
[PostgreSQL] ── processed metrics + alerts
   ├── raw_events
   ├── employees
   ├── graph_snapshots
   ├── risk_scores
   └── alerts
        │
        ├──► [FastAPI] ── REST API (port 8000)
        │         /graph  /risk  /alerts
        │
        └──► [React Dashboard] ── Sigma.js ONA graph (port 5173)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Ingestion | Apache Kafka 3.6 + kafka-python |
| Orchestration | Apache Airflow 2.9 (LocalExecutor) |
| Graph processing | NetworkX 3.x (betweenness, clustering, Louvain communities) |
| ML | scikit-learn Isolation Forest + Shannon entropy trend (numpy polyfit) |
| Backend | FastAPI + psycopg2 (RealDictCursor) |
| Frontend | React 18 + Sigma.js 3 + graphology + Recharts + Tailwind CSS |
| Database | PostgreSQL 15 |
| Infrastructure | Docker Compose (Kafka, Zookeeper, Postgres, Airflow, Adminer) |

---

## Repository structure

```
org-synapse/
├── docker-compose.yml
├── .env.example
│
├── ingestion/
│   ├── producers/           # Synthetic Kafka producers (Slack, Jira, Calendar)
│   ├── consumers/           # edge_consumer.py → writes to raw_events
│   └── schemas/             # CollaborationEvent (Pydantic v2)
│
├── etl/
│   ├── dags/                # Airflow DAGs (graph builder, anomaly, risk scoring)
│   └── tasks/               # Task callables (build_graph, compute_centrality,
│                            #   detect_entropy, run_anomaly)
│
├── graph/
│   ├── builder.py           # NetworkX DiGraph from edge list
│   ├── metrics.py           # Betweenness, degree, clustering coefficient
│   ├── silo_detector.py     # Louvain community detection + silo alerting
│   └── risk_scorer.py       # SPOF score formula with configurable α β γ δ
│
├── ml/
│   ├── features/
│   │   └── feature_extractor.py   # 8-feature vector + entropy trend per employee
│   └── anomaly/
│       └── isolation_forest.py    # Anomaly scoring + alert writing
│
├── api/
│   ├── main.py              # FastAPI app + CORS
│   ├── deps.py              # get_db() dependency (psycopg2 per-request)
│   ├── db.py                # Thin query layer (all functions take explicit conn)
│   ├── routers/
│   │   ├── graph.py         # /graph/snapshot  /graph/employee/{id}  /graph/communities
│   │   ├── risk.py          # /risk/scores  /risk/critical-nodes  /risk/simulate
│   │   └── alerts.py        # /alerts/silos  /alerts/entropy  /alerts/history
│   └── models/schemas.py    # 14 Pydantic response models
│
├── frontend/
│   ├── src/
│   │   ├── lib/api.js       # Axios client for all 10 endpoints
│   │   ├── components/
│   │   │   ├── OrgGraph.jsx         # Sigma.js force-directed graph
│   │   │   ├── CriticalNodePanel.jsx # Top SPOF employees with score bars
│   │   │   └── SiloAlert.jsx        # Expandable alert accordion
│   │   └── pages/
│   │       ├── Dashboard.jsx        # KPI cards + graph + critical panel
│   │       └── EmployeeDetail.jsx   # Ego network + SPOF trend + What-If
│   └── package.json
│
├── data/
│   ├── migrations/          # SQL schema (applied automatically by Postgres init)
│   └── synthetic/           # generate_org_data.py — realistic synthetic dataset
│
└── tests/
    ├── unit/                # 60+ tests: graph metrics, ML, API (mocked DB)
    └── integration/         # DAG structure tests, ingestion pipeline
```

---

## Quick start

### 1. Prerequisites

- Docker + Docker Compose
- Python 3.11+ (for local development outside containers)
- Node.js 18+ (for the frontend)

### 2. Environment

```bash
cp .env.example .env
# Edit .env if you need non-default credentials
```

### 3. Start infrastructure

```bash
docker-compose up -d
```

Services after startup:

| Service | URL |
|---|---|
| Airflow UI | http://localhost:8088 (admin / admin) |
| Adminer (DB) | http://localhost:8081 |
| Kafka | localhost:9092 |
| PostgreSQL | localhost:5432 |

### 4. Generate synthetic data

```bash
pip install -r requirements.txt   # networkx, psycopg2-binary, kafka-python, pydantic, ...
python data/synthetic/generate_org_data.py --employees 200 --days 90
```

### 5. Seed the graph (manual trigger)

Run the Airflow DAG from the UI, or trigger it directly:

```bash
# Inside the airflow-scheduler container:
airflow dags trigger graph_builder_dag
```

### 6. Start the API

```bash
uvicorn api.main:app --reload --port 8000
# Swagger UI: http://localhost:8000/docs
```

### 7. Start the frontend

```bash
cd frontend
npm install
npm run dev
# Dashboard: http://localhost:5173
```

---

## SPOF score formula

```
SPOF_score = α × betweenness_centrality
           + β × cross_department_edge_ratio
           + γ × (1 − clustering_coefficient)
           − δ × entropy_trend
```

A negative entropy trend (declining interaction diversity) increases the score. Weights are configurable via environment variables:

```bash
SPOF_ALPHA=0.4   # weight on betweenness
SPOF_BETA=0.3    # weight on cross-department bridging
SPOF_GAMMA=0.2   # weight on structural hole (1 - clustering)
SPOF_DELTA=0.1   # weight on entropy withdrawal signal
```

Scores are bucketed: `normal` (< 0.4), `warning` (0.4–0.7), `critical` (> 0.7).

---

## API reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/graph/snapshot` | Full org graph (nodes + edges) for a snapshot date |
| GET | `/graph/employee/{id}` | Ego network — direct collaborators + edges |
| GET | `/graph/communities` | Louvain communities with silo flag |
| GET | `/risk/scores` | Top-N employees by SPOF score |
| GET | `/risk/critical-nodes` | Employees above SPOF threshold |
| GET | `/risk/employee/{id}/history` | 30-day SPOF score trend |
| POST | `/risk/simulate` | What-If: graph health delta after removing one employee |
| GET | `/alerts/silos` | Active (unresolved) silo alerts |
| GET | `/alerts/entropy` | Active connectivity anomaly alerts |
| GET | `/alerts/history` | All alerts in the last N days |

Interactive docs at `http://localhost:8000/docs`.

---

## Airflow pipelines

### `graph_builder_dag` — daily at 02:00 UTC

```
ingest_raw_events
    └─► build_graph_snapshot
            └─► compute_metrics
                    └─► detect_silos
                            └─► score_risks
                                    └─► flag_spof_critical
```

### `anomaly_detection_dag` — Mondays at 03:00 UTC

```
extract_features
    └─► run_isolation_forest
            └─► summarise_anomalies
                    └─► [triggers risk_scoring_dag]
```

### `risk_scoring_dag` — on-demand (triggered by anomaly DAG)

```
resolve_latest_snapshot
    └─► score_risks
            └─► flag_spof_critical
```

---

## Running tests

```bash
pytest tests/ -v --tb=short

# Subsets
pytest tests/unit/test_graph_metrics.py -v
pytest tests/unit/test_api.py -v
pytest tests/unit/test_ml_features.py -v
pytest tests/integration/test_dags.py -v
```

The unit tests mock the database entirely via `dependency_overrides` and `unittest.mock.patch` — no live DB required.

---

## Ethics & privacy constraints

1. **Metadata only.** No message content, no email bodies, no file contents are ingested. Only: `{who} → {whom}`, `{channel}`, `{timestamp}`.
2. **Anonymization at rest.** All graph computation uses UUIDs. Employee names exist only in the `employees` lookup table and are resolved at read time.
3. **Aggregate alerts.** Risk signals target organizational patterns, not individuals under surveillance. The system flags "Engineering ↔ Sales bridge is at risk" — not "watch John because he's quiet."
4. **Schema supports consent.** The `employees` table includes an `employee_consent` flag for future regulatory requirements.

---

## Demo scenario

Generate a synthetic org with the withdrawal pattern described in `CLAUDE.md`:

```bash
python data/synthetic/generate_org_data.py \
  --employees 120 \
  --days 90 \
  --connectors 2 \
  --withdrawal-days 30
```

Expected trajectory over 4 weeks:
- Week 1–2: both connectors appear in top 5 SPOF scores
- Week 3: withdrawing connector enters `critical` flag
- Week 4: silo alert fires for the Engineering community
- What-If simulation: removing the connector increases weakly connected components by 2–4 and raises average betweenness by ~15%

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*
