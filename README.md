# Organizational Synapse & Knowledge Risk

A graph-based HR intelligence platform that analyzes collaboration metadata to detect knowledge silos, identify single points of failure, quantify organizational risk, and generate executive briefings — before HR has any subjective signal.

---

## What it does

The system ingests collaboration metadata (Slack, Teams, Jira, GitHub, Confluence, Notion) as a stream of directed edges — who interacted with whom, on which channel, at what time — and builds a live organizational network graph. From that graph it computes structural metrics per employee, runs daily and weekly ML pipelines, and surfaces the results through a REST API, an interactive React dashboard, and a natural language query interface powered by Claude.

**Core outputs:**

| Output | Description |
|---|---|
| **SPOF score** | Single-point-of-failure risk derived from betweenness centrality, cross-department bridging, clustering coefficient, and entropy trend |
| **Knowledge risk** | Sole-expert fraction, document volume, and domain breadth combined with SPOF to produce an enhanced risk score |
| **Silo alerts** | Communities whose internal/external edge ratio exceeds threshold (Louvain detection) |
| **Anomaly alerts** | Employees whose connectivity pattern deviates from their 30-day baseline (Isolation Forest) |
| **Churn risk** | GNN-based churn probability with `low / medium / high / critical` tiers |
| **Succession plans** | Top-N candidates for each critical SPOF employee, ranked by structural, clustering, and domain compatibility |
| **Org health score** | Composite 0–100 score with weekly trend and AI-generated executive briefing |
| **What-If simulation** | Recalculates graph health after removing a single employee; shows component fragmentation and betweenness deltas |
| **NL query interface** | Ask questions in plain English — a Claude agentic loop calls graph and risk tools and returns a grounded answer |
| **Compliance reports** | GDPR/CCPA data audit, Article 20 export, consent management, retention purge, quarterly HTML report |

---

## Architecture

```
Collaboration tools (Slack, Teams, Jira, GitHub, Calendar, Confluence, Notion)
        │
        ▼
[Kafka] ── streaming metadata ingestion
        │  topics: collaboration.events.raw | {tenant}.collaboration.events.raw
        ▼
[Airflow DAGs] ── ETL + ML pipelines
   ├── graph_builder_dag      02:00 UTC daily
   ├── anomaly_detection_dag  03:00 UTC Mondays
   ├── risk_scoring_dag       on-demand (triggered by anomaly)
   ├── churn_risk_dag         Sundays 04:00 UTC
   ├── temporal_graph_dag     daily 05:00 UTC
   ├── knowledge_risk_dag     Fridays 01:00 UTC
   ├── succession_dag         Saturdays 02:00 UTC
   ├── neo4j_import_dag       daily 06:00 UTC (optional)
   ├── org_health_dag         Mondays 06:00 UTC
   └── compliance_dag         Quarterly (Jan/Apr/Jul/Oct 1, 08:00 UTC)
        │
        ▼
[PostgreSQL] ── per-tenant schema isolation (F6 SaaS mode)
   ├── employees              consent flag enforced throughout
   ├── raw_events             90-day retention
   ├── graph_snapshots        365-day retention
   ├── risk_scores            knowledge_risk_scores  succession_recommendations
   ├── churn_risk_scores      temporal_anomaly_scores
   ├── org_health_scores      consent_audit_log  data_retention_purges
   └── public.*               tenants  tenant_api_keys  tenant_usage
        │
        ├──► [Neo4j] (optional) ── Cypher graph queries + PageRank
        │
        ├──► [Redis] ── API response cache (TTL 1 h)
        │
        ├──► [FastAPI] ── REST API (port 8000)
        │
        └──► [React Dashboard] ── Sigma.js ONA graph (port 5173)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Ingestion | Apache Kafka 3.7 (KRaft mode, no Zookeeper) + kafka-python |
| Orchestration | Apache Airflow 2.9 (LocalExecutor) |
| Graph processing | NetworkX 3.x (betweenness, clustering, Louvain); optional Neo4j 5 |
| ML | scikit-learn Isolation Forest · numpy polyfit entropy trend · PyTorch Geometric GNN (churn) |
| Knowledge connectors | Confluence Cloud API · Notion API (via `ingestion/connectors/`) |
| Collaboration connectors | Slack Bot API · Microsoft Graph (Teams) · Jira REST · GitHub Webhooks |
| LLM | Anthropic Claude (`claude-sonnet-4-6`) — NL query agent + org health briefings |
| Backend | FastAPI + psycopg2 (RealDictCursor) + Redis cache |
| Frontend | React 18 + Sigma.js 3 + graphology + Recharts |
| Database | PostgreSQL 15 (schema-per-tenant) |
| Billing | Stripe webhooks (HMAC-SHA256 verification, no SDK dependency) |
| Observability | Prometheus + Grafana (via `prometheus-fastapi-instrumentator`) |
| Infrastructure | Docker Compose |

---

## Repository structure

```
org-synapse/
├── docker-compose.yml
├── .env.example
│
├── ingestion/
│   ├── producers/           # Kafka producers: synthetic (Slack/Jira/Calendar)
│   │                        #   + real-API (Slack, Teams, Jira, GitHub)
│   │                        #   + TenantAwareProducer (F6 multi-tenant routing)
│   ├── connectors/          # Knowledge connectors: Confluence, Notion (F3)
│   ├── consumers/           # edge_consumer.py → writes to raw_events
│   └── schemas/             # CollaborationEvent (Pydantic v2)
│
├── etl/
│   └── dags/                # 10 Airflow DAGs (see Architecture above)
│
├── graph/
│   ├── builder.py           # NetworkX DiGraph from edge list
│   ├── metrics.py           # Betweenness (exact + approximate), degree, clustering
│   ├── silo_detector.py     # Louvain community detection + silo alerting
│   ├── risk_scorer.py       # SPOF score formula (α β γ δ configurable)
│   ├── org_health.py        # Composite 0–100 health score + Claude narrative (F9)
│   └── compliance.py        # Data audit, retention purge, GDPR export (F8)
│
├── ml/
│   ├── features/
│   │   └── feature_extractor.py   # 8-feature vector + entropy trend per employee
│   ├── anomaly/
│   │   └── isolation_forest.py    # Anomaly scoring + alert writing
│   └── churn/
│       └── gnn_model.py           # PyTorch Geometric GNN churn predictor (F2)
│
├── api/
│   ├── main.py              # FastAPI app + CORS + TenantMiddleware + Prometheus
│   ├── deps.py              # get_db() · get_tenant_db() · get_admin_db()
│   ├── db.py                # Thin query layer (all functions take explicit conn)
│   ├── cache.py             # Redis response cache
│   ├── tenant.py            # Multi-tenant provisioning + API key management (F6)
│   ├── middleware/
│   │   └── tenant_middleware.py   # X-Tenant-ID + X-Api-Key header extraction
│   ├── nl/
│   │   └── agent.py         # Claude agentic loop for NL query (F7)
│   ├── ws/
│   │   ├── manager.py       # WebSocket connection manager
│   │   └── broadcaster.py   # Redis pub/sub → WebSocket broadcast
│   └── routers/
│       ├── graph.py         # /graph/*
│       ├── risk.py          # /risk/*
│       ├── alerts.py        # /alerts/*
│       ├── connectors.py    # /connectors/*
│       ├── knowledge.py     # /knowledge/*
│       ├── succession.py    # /succession/*
│       ├── org_health.py    # /org-health/*  (F9)
│       ├── query.py         # POST /query/natural  (F7)
│       ├── compliance.py    # /compliance/*  (F8)
│       ├── admin.py         # /admin/tenants  (F6)
│       ├── billing.py       # /billing/*  (F6)
│       ├── internal.py      # POST /internal/alerts/broadcast
│       └── ws.py            # WS /alerts/live
│
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── OrgGraph.jsx         # Sigma.js force-directed graph
│       │   ├── CriticalNodePanel.jsx # Top SPOF employees with score bars
│       │   ├── SiloAlert.jsx        # Expandable alert accordion
│       │   ├── AlertToast.jsx       # Real-time WebSocket alert toasts
│       │   ├── ChatPanel.jsx        # Natural language query chat UI (F7)
│       │   └── OrgHealthCard.jsx    # Composite health score card (F9)
│       └── pages/
│           ├── Dashboard.jsx        # KPI cards + graph + critical panel + chat
│           ├── EmployeeDetail.jsx   # Ego network + SPOF trend + What-If
│           └── AdminPanel.jsx       # Tenant management dashboard (F6)
│
├── data/
│   ├── migrations/          # 009 SQL migrations (applied by Postgres init)
│   └── synthetic/           # generate_org_data.py — realistic synthetic dataset
│
└── tests/
    ├── unit/                # 19 test modules, 200+ tests — all DB calls mocked
    └── integration/         # DAG structure tests, ingestion pipeline
```

---

## Quick start

### Prerequisites

- Docker + Docker Compose
- Python 3.11+
- Node.js 18+

### 1. Environment

```bash
cp .env.example .env
# Required for NL query and org health briefings:
# ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Start infrastructure

```bash
docker-compose up -d
```

| Service | URL |
|---|---|
| Airflow UI | http://localhost:8088 (admin / admin) |
| Adminer (DB) | http://localhost:8081 |
| Grafana | http://localhost:3001 (admin / `GRAFANA_PASSWORD`) |
| Kafka | localhost:9092 |
| PostgreSQL | localhost:5432 |
| Neo4j | http://localhost:7474 (optional) |

### 3. Generate synthetic data

```bash
pip install -r requirements.txt
python data/synthetic/generate_org_data.py --employees 200 --days 90
```

### 4. Seed the graph

```bash
# Trigger via Airflow UI, or directly:
airflow dags trigger graph_builder_dag
```

### 5. Start the API

```bash
uvicorn api.main:app --reload --port 8000
# Swagger UI: http://localhost:8000/docs
```

### 6. Start the frontend

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

Weights are configurable via environment variables (`SPOF_ALPHA`, `SPOF_BETA`, `SPOF_GAMMA`, `SPOF_DELTA`). Scores are bucketed: `normal` (< 0.4), `warning` (0.4–0.7), `critical` (> 0.7).

**Enhanced SPOF** (F3): `(1 − δ) × graph_spof + δ × knowledge_score` where `knowledge_score` incorporates sole-expert fraction, document volume, and domain breadth.

---

## Org health score (F9)

```
composite_risk = 0.20 × silo_risk
              + 0.35 × spof_risk
              + 0.20 × entropy_risk
              + 0.25 × fragmentation_risk

health_score = (1 − composite_risk) × 100   [clamped 0–100]
```

Tiers: `healthy` (≥ 80) · `caution` (60–79) · `at_risk` (40–59) · `critical` (< 40).

A weekly executive briefing is generated every Monday at 06:00 UTC, with a Claude-authored narrative if `ANTHROPIC_API_KEY` is set (template fallback otherwise). Delivery via Slack or SendGrid is opt-in via environment variables.

---

## API reference

### Graph & risk

| Method | Endpoint | Description |
|---|---|---|
| GET | `/graph/snapshot` | Full org graph (nodes + edges) for a snapshot date |
| GET | `/graph/employee/{id}` | Ego network — direct collaborators + edges |
| GET | `/graph/communities` | Louvain communities with silo flag |
| GET | `/risk/scores` | Top-N employees by SPOF score |
| GET | `/risk/critical-nodes` | Employees above SPOF threshold |
| GET | `/risk/employee/{id}/history` | 30-day SPOF trend |
| POST | `/risk/simulate` | What-If: graph health delta after removing one employee |
| GET | `/alerts/silos` | Active silo alerts |
| GET | `/alerts/entropy` | Active connectivity anomaly alerts |
| GET | `/alerts/history` | All alerts in the last N days |
| WS | `/alerts/live` | Real-time WebSocket alert stream |

### Intelligence features

| Method | Endpoint | Description |
|---|---|---|
| GET | `/knowledge/scores` | Knowledge risk scores per employee |
| GET | `/knowledge/domains` | Sole-expert domains and contributor counts |
| GET | `/knowledge/employee/{id}` | Full knowledge profile for one employee |
| GET | `/succession/plans` | Succession candidates for top SPOF employees |
| GET | `/succession/employee/{id}` | Succession plan for one employee |
| GET | `/org-health/score` | Latest composite org health score (F9) |
| GET | `/org-health/trend` | Weekly health score trend |
| GET | `/org-health/briefing` | AI-generated executive briefing (F9) |
| POST | `/query/natural` | Natural language query via Claude agent (F7) |

### Compliance (F8)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/compliance/data-audit` | Full personal data inventory catalogue |
| GET | `/compliance/data-export/{id}` | GDPR Article 20 employee data package |
| PATCH | `/compliance/consent/{id}` | Update employee consent + write audit log |
| POST | `/compliance/purge` | Trigger data retention purge (admin key required) |
| GET | `/compliance/purge-history` | Recent purge run log |
| GET | `/compliance/report` | Quarterly HTML compliance report |

### Multi-tenant admin (F6)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/admin/tenants` | Provision a new tenant schema + API key |
| GET | `/admin/tenants` | List all tenants |
| GET | `/admin/tenants/{id}` | Tenant detail |
| PATCH | `/admin/tenants/{id}/plan` | Change plan tier |
| DELETE | `/admin/tenants/{id}` | Soft-deactivate tenant |
| GET | `/billing/usage` | Current-month event usage for authenticated tenant |
| POST | `/billing/webhook` | Stripe billing webhook (HMAC-validated) |

Interactive docs at `http://localhost:8000/docs`.

---

## Airflow pipelines

```
graph_builder_dag      02:00 UTC daily
  ingest_raw_events → build_graph → compute_metrics → detect_silos → score_risks → flag_spof

anomaly_detection_dag  03:00 UTC Mondays
  extract_features → run_isolation_forest → summarise → [triggers risk_scoring_dag]

risk_scoring_dag       on-demand
  resolve_snapshot → score_risks → flag_spof

churn_risk_dag         04:00 UTC Sundays
  build_features → run_gnn → persist_scores → fire_alerts

temporal_graph_dag     05:00 UTC daily
  build_temporal_graph → detect_temporal_anomalies → write_anomaly_scores

knowledge_risk_dag     01:00 UTC Fridays
  ingest_confluence → ingest_notion → score_knowledge_risk → fire_alerts

succession_dag         02:00 UTC Saturdays
  [waits for knowledge_risk_dag] → compute_succession → persist_plans

org_health_dag         06:00 UTC Mondays
  [waits for graph_builder_dag] → compute_health → generate_briefing → deliver

compliance_dag         08:00 UTC — Jan 1, Apr 1, Jul 1, Oct 1
  run_purge → gen_report → deliver_report
```

---

## Multi-tenant SaaS mode (F6)

Each tenant gets an isolated PostgreSQL schema (`tenant_{slug}`) provisioned on sign-up. The `search_path` is switched per-request based on `X-Tenant-ID` + `X-Api-Key` headers.

```bash
# Provision a tenant (admin key required)
curl -X POST http://localhost:8000/admin/tenants \
  -H "X-Admin-Key: $ADMIN_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"slug": "acme-corp", "name": "Acme Corp", "plan": "pro"}'
# → returns raw_api_key (shown once, not stored)

# Authenticate as a tenant
curl http://localhost:8000/risk/scores \
  -H "X-Tenant-ID: <tenant-uuid>" \
  -H "X-Api-Key: <raw-api-key>"
```

Kafka topics are namespaced per-tenant: `{slug}.collaboration.events.raw`.

---

## Natural language query (F7)

Send a plain-English question to `POST /query/natural`. A Claude agentic loop resolves it using graph and risk tools (up to 6 reasoning turns), returning a grounded answer with tool call transparency.

```bash
curl -X POST http://localhost:8000/query/natural \
  -H "Content-Type: application/json" \
  -d '{"question": "Who are the top 3 SPOF risks in Engineering and why?"}'
```

Requires `ANTHROPIC_API_KEY` in the environment.

---

## Compliance & privacy (F8)

1. **Metadata only.** No message content, email bodies, or file contents are ever ingested — only `{who} → {whom}`, `{channel}`, `{timestamp}`.
2. **Consent-gated computation.** The `employees.consent` column is enforced in every graph query: employees who opt out are excluded from all graph computations, risk scores, and analytics.
3. **Data retention.** `raw_events` are automatically purged after 90 days; `graph_snapshots` after 365 days. Each purge run is recorded in `data_retention_purges` for audit.
4. **GDPR Article 20.** `GET /compliance/data-export/{id}` returns a complete personal data package (raw events, graph snapshots, risk scores, knowledge entries, consent audit log).
5. **Consent audit trail.** Every consent change is recorded in `consent_audit_log` with timestamp, actor, and reason.
6. **Aggregate alerts.** Risk signals target organizational patterns — "Engineering ↔ Sales bridge is at risk" — not individuals under surveillance.

---

## Running tests

```bash
pytest tests/ -v --tb=short

# Subsets
pytest tests/unit/test_graph_metrics.py -v
pytest tests/unit/test_compliance.py -v
pytest tests/unit/test_tenant.py -v
pytest tests/unit/test_nl_query.py -v
pytest tests/unit/test_org_health.py -v
```

All unit tests mock the database entirely via `dependency_overrides` and `unittest.mock.patch` — no live DB or Kafka required.

---

## Demo scenario

```bash
python data/synthetic/generate_org_data.py \
  --employees 120 --days 90 --connectors 2 --withdrawal-days 30
```

Expected trajectory over 4 weeks:
- **Week 1–2:** both bridge employees appear in top 5 SPOF scores
- **Week 3:** the withdrawing connector enters `critical` flag; knowledge risk score rises
- **Week 4:** silo alert fires for Engineering; succession plan auto-generates for the connector
- **What-If:** removing the connector raises weakly connected components by 2–4 and average betweenness by ~15%
- **NL query:** "Who are the critical connectors between Engineering and Sales?" returns a grounded, tool-backed answer

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*
