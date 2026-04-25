# PLAN.md — Organizational Synapse & Knowledge Risk
## Sprint Roadmap

---

## Conventions

- **Sprint duration:** 1 week (5 working days)
- **Definition of Done (DoD):** Code committed, tested locally, and manually verified against the demo scenario defined in `CLAUDE.md`.
- **Branching:** `main` (stable) → `dev` (integration) → `feature/<sprint>-<task>` (work branches)
- **Sprint prefix in commits:** `[S0]`, `[S1]`, `[S2]`, etc.
- **Status legend:** 🔲 Pending · 🔄 In Progress · ✅ Done · ⏸ Blocked

---

## Milestone Map

```
S0  ──── S1  ──── S2  ──── S3  ──── S4  ──── S5
│        │        │        │        │        │
Setup   Ingest   Graph    ML      API+UI   Demo
                 Engine   Layer   Layer    Ready
                                           │
                               MVP COMPLETE ▲

S6  ──── S7  ──── S8  ──── S9  ──── S10 ──── S11+
│        │        │        │        │        │
Auth &   Neo4j   Connectors What-If  Perf    Future
RBAC    Migrate  (Real APIs) Engine  & Scale Impl.
```

---

## SPRINT 0 — Project Bootstrap
**Goal:** Zero-to-running local environment. No business logic yet.  
**Duration:** Week 0 (pre-dev)

### Tasks

#### Infrastructure
- [ ] Initialize git repo: `org-synapse/`, commit `CLAUDE.md`, `PLAN.md`, `.gitignore`, `.env.example`
- [ ] Write `docker-compose.yml` with:
  - Kafka + Zookeeper (bitnami/kafka)
  - PostgreSQL 15
  - Airflow 2.x (LocalExecutor)
  - Adminer (DB UI for dev convenience)
- [ ] Verify all services start: `docker-compose up -d && docker-compose ps`
- [ ] Create `.env` from `.env.example`, document every variable

#### Database Schema (migrations)
- [ ] `data/migrations/001_initial_schema.sql`:
  ```sql
  employees (id UUID PK, name TEXT, department TEXT, role TEXT, active BOOL, consent BOOL)
  raw_events (id UUID PK, source_id UUID, target_id UUID, channel TEXT, direction TEXT, ts TIMESTAMPTZ, weight FLOAT)
  graph_snapshots (id UUID PK, snapshot_date DATE, employee_id UUID, betweenness FLOAT, degree FLOAT, clustering FLOAT, community_id INT)
  risk_scores (id UUID PK, scored_at TIMESTAMPTZ, employee_id UUID, spof_score FLOAT, entropy_trend FLOAT, flag TEXT)
  alerts (id UUID PK, fired_at TIMESTAMPTZ, type TEXT, severity TEXT, affected_entities JSONB, details TEXT)
  ```
- [ ] Run migrations against Postgres container, verify schema

#### Python Project Setup
- [ ] `pyproject.toml` / `requirements.txt`: kafka-python, airflow, networkx, scikit-learn, pyod, fastapi, uvicorn, pydantic, psycopg2-binary, python-louvain, pytest
- [ ] `ingestion/schemas/collaboration_event.py`: Pydantic `CollaborationEvent` model

### Sprint 0 Exit Criteria
- `docker-compose up -d` runs without errors
- All 5 DB tables exist and accept inserts
- `CollaborationEvent` schema validates correctly in a unit test

---

## SPRINT 1 — Synthetic Data & Ingestion Pipeline
**Goal:** Realistic fake data flows from producers → Kafka → Postgres. No graph logic yet.

### Tasks

#### Synthetic Data Generator
- [ ] `data/synthetic/generate_org_data.py`
  - CLI args: `--employees N`, `--days D`, `--departments dept1,dept2,...`
  - Generates realistic edge distribution: power-law degree distribution (most employees talk to few, connectors talk to many)
  - Embeds 2 synthetic "connectors" with cross-department edges
  - Embeds 1 synthetic "withdrawing" employee: edges decay 70% in the last 15 days
  - Outputs: CSV `edges.csv` + `employees.csv` for local use
  - Writes directly to `raw_events` and `employees` tables

#### Kafka Producers
- [ ] `ingestion/producers/slack_producer.py`: reads from `edges.csv`, publishes `CollaborationEvent` JSON to topic `collaboration.events.raw` with 100ms delay between events
- [ ] `ingestion/producers/jira_producer.py`: same structure, different channel/direction values
- [ ] `ingestion/producers/calendar_producer.py`: bidirectional events (invite = two edges)

#### Kafka Consumer
- [ ] `ingestion/consumers/edge_consumer.py`:
  - Subscribes to `collaboration.events.raw`
  - Validates each message against `CollaborationEvent` schema
  - Writes to `raw_events` table (batch insert every 50 events or 2s)
  - Dead-letter logging for invalid events

#### Integration Smoke Test
- [ ] `tests/integration/test_ingestion_pipeline.py`:
  - Publish 100 synthetic events → verify 100 rows in `raw_events`
  - Verify schema validation rejects malformed events

### Sprint 1 Exit Criteria
- Run `generate_org_data.py --employees 200 --days 90` → 200 rows in `employees`, ~18,000 rows in `raw_events`
- Kafka consumer processes all events with 0 errors
- The synthetic "withdrawing" employee has visibly fewer edges in the last 15 days (verify via SQL query)

---

## SPRINT 2 — Graph Engine
**Goal:** Build and query the corporate network graph. Core analytics logic.

### Tasks

#### Graph Builder
- [ ] `graph/builder.py`:
  - Reads `raw_events` for a given `--date` (builds graph from last N days, default 30)
  - Constructs `nx.DiGraph` (directed: A→B and B→A are different)
  - Edge weight = sum of interaction `weight` field for the pair in the window
  - Returns serializable adjacency data

#### Graph Metrics
- [ ] `graph/metrics.py`:
  - `compute_betweenness(G)` → dict `{employee_id: float}`
  - `compute_degree_centrality(G)` → in-degree and out-degree separately
  - `compute_clustering(G)` → undirected projection for clustering coefficient
  - `compute_community(G)` → Louvain community IDs
  - Writes all results to `graph_snapshots` table for the given snapshot date

#### Silo Detector
- [ ] `graph/silo_detector.py`:
  - `detect_silos(G, threshold)` → list of `SiloAlert` dataclasses
  - Computes inter/intra community edge ratio per Louvain community
  - Flags communities where ratio > `SILO_THRESHOLD` env var
  - Writes alerts to `alerts` table with `type='silo'`

#### Risk Scorer
- [ ] `graph/risk_scorer.py`:
  - `compute_spof_score(employee_id, metrics_dict)` → float
  - Formula: `α*betweenness + β*cross_dept_edges + γ*(1-clustering) + δ*entropy_trend`
  - Weights from env vars
  - Writes to `risk_scores` table

#### Unit Tests
- [ ] `tests/unit/test_graph_metrics.py`:
  - Star graph: center node must have betweenness = 1.0
  - Clique: all nodes betweenness = 0
  - Bridge graph: bridge node must rank highest in betweenness
  - Two isolated cliques → silo detector must fire for both

### Sprint 2 Exit Criteria
- `python graph/builder.py --date 2025-04-25` runs without error
- `python graph/metrics.py --snapshot-date 2025-04-25` writes correct rows to `graph_snapshots`
- The synthetic "connector" employees rank #1 and #2 in betweenness centrality
- All unit tests pass

---

## SPRINT 3 — Airflow ETL Orchestration
**Goal:** Replace manual script execution with scheduled, observable DAGs.

### Tasks

#### DAG: Graph Builder (Daily)
- [ ] `etl/dags/graph_builder_dag.py`:
  - Schedule: `@daily` at 02:00 UTC
  - Tasks (chain):
    1. `check_raw_events` → fail if < 100 events for prior day
    2. `build_graph` → calls `graph/builder.py` for yesterday
    3. `compute_metrics` → calls `graph/metrics.py`
    4. `detect_silos` → calls `graph/silo_detector.py`
    5. `score_risks` → calls `graph/risk_scorer.py`
  - On failure: write alert to `alerts` table with `type='pipeline_failure'`

#### DAG: Anomaly Detection (Weekly)
- [ ] `etl/dags/anomaly_detection_dag.py`:
  - Schedule: `@weekly` (Monday 03:00 UTC)
  - Tasks:
    1. `extract_features` → calls `ml/features/feature_extractor.py` for 30-day window
    2. `run_isolation_forest` → calls `ml/anomaly/isolation_forest.py`
    3. `flag_anomalies` → writes anomaly flags to `risk_scores`

#### DAG: Risk Alert (Sensor-triggered)
- [ ] `etl/dags/risk_scoring_dag.py`:
  - Triggered externally (REST API or after anomaly DAG completes)
  - Re-computes top-N risk scores immediately
  - Sends alert rows to `alerts` table with `type='spof_critical'` for scores > threshold

#### Airflow Config
- [ ] Configure LocalExecutor in `docker-compose.yml` Airflow service
- [ ] Mount DAGs folder into container
- [ ] Add Airflow connections for Postgres via UI or env vars

#### Observability
- [ ] Add task-level logging: number of edges processed, graph node count, top-3 SPOF scores logged per run
- [ ] `tests/integration/test_dags.py`: import all DAGs, verify no import errors, verify task count

### Sprint 3 Exit Criteria
- All 3 DAGs visible in Airflow UI with no import errors
- Manual trigger of `graph_builder_dag` completes all 5 tasks successfully
- `alerts` table populates correctly after DAG run on synthetic data
- The withdrawing synthetic employee appears in `risk_scores` with a flagged trend

---

## SPRINT 4 — ML Anomaly Detection Layer
**Goal:** Add the intelligence layer that detects non-obvious patterns.

### Tasks

#### Feature Extractor
- [ ] `ml/features/feature_extractor.py`:
  - Input: `graph_snapshots` for last 30 days per employee
  - Output: tabular feature matrix `(employee_id, date, features...)`
  - Features:
    - `betweenness_mean_30d`, `betweenness_delta_7d`, `betweenness_delta_30d`
    - `degree_mean_30d`, `degree_delta_7d`
    - `cross_dept_edge_ratio` (edges outside own department / total edges)
    - `community_stability` (did community membership change in last 7 days?)
    - `entropy_score` (Shannon entropy of interaction partner distribution)
    - `entropy_trend` (linear regression slope of entropy over 30 days)

#### Isolation Forest
- [ ] `ml/anomaly/isolation_forest.py`:
  - Trains on 60-day baseline features
  - Scores each employee daily
  - Anomaly threshold: contamination = 0.05 (top 5% flagged)
  - Saves model to `ml/models/isolation_forest.pkl`
  - Output: anomaly score + binary flag per employee

#### Entropy Trend Model
- [ ] Simple linear regression on `entropy_score` over rolling 30-day window per employee
  - Negative slope > `ENTROPY_SLOPE_THRESHOLD` → flag `withdrawing`
  - Writes slope value to `risk_scores.entropy_trend`

#### Model Versioning
- [ ] `ml/models/` directory: save models with `{model_name}_{YYYYMMDD}.pkl`
- [ ] Keep last 5 versions, auto-delete older

#### Tests
- [ ] `tests/unit/test_feature_extractor.py`: verify feature shapes and entropy calculation on known distribution
- [ ] `tests/unit/test_isolation_forest.py`: inject synthetic anomalous employee, verify it's flagged

### Sprint 4 Exit Criteria
- Feature matrix generated for all 200 synthetic employees over 90-day period
- Isolation Forest flags the synthetic "withdrawing" employee within top 5% anomaly scores
- `entropy_trend` is negative for withdrawing employee, positive for stable connectors

---

## SPRINT 5 — FastAPI Backend
**Goal:** Expose all computed data through a clean, documented REST API.

### Tasks

#### API Foundation
- [ ] `api/main.py`: FastAPI app with lifespan events (DB pool init/teardown)
- [ ] `api/models/schemas.py`: Pydantic response models for all endpoints
- [ ] Postgres connection pool (asyncpg or psycopg2)
- [ ] CORS config: allow `localhost:5173` (Vite dev server)
- [ ] Auto-generated docs at `/docs` (Swagger UI)

#### Router: /graph
- [ ] `GET /graph/snapshot?date=YYYY-MM-DD` → full graph adjacency list + node metrics for that snapshot
- [ ] `GET /graph/employee/{employee_id}` → individual ego-network (2-hop neighborhood)
- [ ] `GET /graph/communities?date=YYYY-MM-DD` → list of communities with member IDs and silo flag

#### Router: /risk
- [ ] `GET /risk/scores?date=YYYY-MM-DD&top=N` → top-N SPOF employees with scores and component breakdown
- [ ] `GET /risk/critical-nodes` → employees with SPOF > configurable threshold
- [ ] `GET /risk/employee/{employee_id}/history` → 30-day score trend for one employee
- [ ] `POST /risk/simulate` → body: `{remove_employee_id: UUID}` → returns recalculated graph metrics without that employee (What-If)

#### Router: /alerts
- [ ] `GET /alerts/silos` → active silo alerts (unfired/unresolved)
- [ ] `GET /alerts/entropy` → employees flagged as withdrawing
- [ ] `GET /alerts/history?days=30` → all alerts in last N days

#### Tests
- [ ] `tests/unit/test_api.py`: FastAPI TestClient for all endpoints with fixture data
- [ ] Verify `POST /risk/simulate` recalculates betweenness correctly on star graph removal

### Sprint 5 Exit Criteria
- All endpoints return correct data for synthetic dataset
- `POST /risk/simulate` removing the synthetic connector increases graph diameter by > 30%
- Swagger UI accessible at `http://localhost:8000/docs`
- API response times < 500ms for all GET endpoints on 200-node graph

---

## SPRINT 6 — React Dashboard (Core)
**Goal:** Minimum viable UI. The graph is visible and interactive.

### Tasks

#### Project Setup
- [ ] `cd frontend && npm create vite@latest . -- --template react`
- [ ] Install: `d3`, `sigma`, `graphology`, `react-query`, `tailwindcss`, `recharts`, `axios`
- [ ] Configure Tailwind, global CSS vars matching SPOF severity palette (green/yellow/orange/red)

#### Component: OrgGraph
- [ ] `OrgGraph.jsx` using Sigma.js + graphology:
  - Force-directed layout (ForceAtlas2)
  - Node size = betweenness centrality
  - Node color = SPOF score quartile (green → red)
  - Edge opacity = interaction weight
  - On hover: tooltip with employee name, department, top-3 metrics
  - On click: navigate to `EmployeeDetail` page

#### Component: CriticalNodePanel
- [ ] Right sidebar: top 10 SPOF employees
- [ ] Each row: avatar placeholder, name, department, SPOF score bar, trend arrow (↑↓)
- [ ] Click row → highlight node in graph

#### Component: SiloAlert
- [ ] Top banner: active silo count with severity badge
- [ ] Expandable list: community name, affected departments, isolation ratio, days active

#### Page: Dashboard
- [ ] Layout: `OrgGraph` (70% width) + `CriticalNodePanel` (30% width) + `SiloAlert` (top bar)
- [ ] Date picker: select snapshot date, reload graph data via React Query

#### Page: EmployeeDetail
- [ ] Ego-network visualization (2-hop from selected employee)
- [ ] 30-day SPOF score trend (Recharts LineChart)
- [ ] Metric breakdown table: betweenness, degree, clustering, entropy
- [ ] "What-If" button → calls `POST /risk/simulate`, shows recalculated graph health overlay

### Sprint 6 Exit Criteria
- Dashboard renders with 200-node synthetic org
- Clicking a connector node opens EmployeeDetail with correct ego-network
- "What-If" simulation visually shows isolated clusters when connector is removed
- Zero console errors on load

---

## SPRINT 7 — Auth, RBAC & Hardening
**Goal:** The system is multi-user safe with role-based data visibility.

### Tasks

#### Authentication
- [ ] Add JWT authentication to FastAPI (`python-jose`, `passlib`)
- [ ] `POST /auth/token` → returns JWT given username/password
- [ ] Protect all `/risk`, `/graph`, `/alerts` routes with `Depends(get_current_user)`

#### Role-Based Access Control
- [ ] Roles: `hr_admin`, `executive`, `analyst`
- [ ] `hr_admin`: full access including individual employee names and scores
- [ ] `executive`: department-level aggregates only, no individual names
- [ ] `analyst`: anonymized graph topology, no names, no scores
- [ ] Implement via response filtering in each router based on `current_user.role`

#### Frontend Auth
- [ ] Login page with JWT storage (memory only, no localStorage)
- [ ] Axios interceptor: attach `Authorization: Bearer <token>` to all requests
- [ ] Role-aware UI: `CriticalNodePanel` shows names only for `hr_admin`

#### API Hardening
- [ ] Rate limiting: 100 req/min per user (slowapi)
- [ ] Input validation: all query params validated via Pydantic
- [ ] `POST /risk/simulate`: limit to 1 concurrent simulation per user
- [ ] Add structured logging (structlog or loguru) for all API requests

#### Ethics Compliance Audit
- [ ] Code review checklist: verify no message content ever reaches any endpoint
- [ ] Verify `analyst` role returns anonymized UUIDs, not resolvable names
- [ ] Add a `ETHICS.md` doc with data governance decisions and their rationale

### Sprint 7 Exit Criteria
- Unauthenticated requests return 401
- `executive` role cannot see individual employee names (verified via API test)
- All routes have structured logs with user ID, role, and latency
- `ETHICS.md` written and committed

---

## SPRINT 8 — Neo4j Migration (Graph Persistence)
**Goal:** Move graph storage from in-memory NetworkX snapshots to Neo4j for richer querying.

### Tasks

#### Neo4j Setup
- [ ] Add Neo4j service to `docker-compose.yml` (neo4j:5.x community)
- [ ] Python driver: `neo4j` package
- [ ] `graph/neo4j_client.py`: connection pool, query helper

#### Graph Import DAG
- [ ] `etl/dags/neo4j_import_dag.py`:
  - After `graph_builder_dag` completes, import today's graph into Neo4j
  - Node label: `Employee` with properties: `id`, `name`, `department`, `spof_score`
  - Relationship: `INTERACTED_WITH` with properties: `weight`, `channel`, `date`
  - Use `MERGE` to upsert (avoid duplicates)

#### Cypher Queries
- [ ] Replace NetworkX betweenness with Neo4j Graph Data Science (GDS) plugin:
  - `gds.betweenness.stream()` for large graphs (>500 nodes)
- [ ] New query: shortest path between any two employees
- [ ] New query: all employees reachable from a given node within 2 hops
- [ ] New query: "knowledge islands" — weakly connected components with < 3 members

#### API Updates
- [ ] `GET /graph/path?from={id}&to={id}` → shortest collaboration path between two employees
- [ ] `GET /graph/reachability/{employee_id}` → employees reachable within 2 hops

### Sprint 8 Exit Criteria
- Neo4j browser accessible at `localhost:7474`
- GDS betweenness results match NetworkX results within 1% tolerance on synthetic data
- Shortest path query runs in < 200ms on 500-node graph

---

## SPRINT 9 — Real API Connectors (Optional / Production Path)
**Goal:** Replace synthetic producers with real collaboration tool integrations.

> ⚠️ **Ethics gate:** All connectors must be approved by legal/compliance before production use. Connectors must only request metadata scopes.

### Tasks

#### Slack Connector
- [ ] `ingestion/producers/slack_real_producer.py`:
  - Uses Slack Events API (OAuth, `message.channels` scope — metadata only)
  - Captures: `{user_id} sent message in channel {channel_id}` → no message body
  - Required scopes: `channels:read`, `users:read` (no `messages:read`)

#### Microsoft Teams Connector
- [ ] `ingestion/producers/teams_producer.py`:
  - Microsoft Graph API: `CallRecords.Read.All` for meeting metadata
  - Captures: who was in a meeting with whom, duration
  - No transcript or content access

#### Jira Connector
- [ ] `ingestion/producers/jira_real_producer.py`:
  - Jira REST API v3: capture `{assignee}` and `{reporter}` on issue events
  - Captures: mentions in comments as edges (user ID only, not comment text)

#### GitHub Connector (Bonus)
- [ ] `ingestion/producers/github_producer.py`:
  - GitHub webhook: PR review requests, review approvals
  - Captures: `{reviewer} reviewed {author}'s PR` as directed edge

#### Connector Framework
- [ ] Abstract `BaseProducer` class with `connect()`, `stream_events()`, `disconnect()`
- [ ] All connectors inherit from `BaseProducer` and publish `CollaborationEvent`
- [ ] Connector health endpoint: `GET /connectors/status`

### Sprint 9 Exit Criteria
- Each connector can be individually enabled via env flag (`ENABLE_SLACK=true`)
- End-to-end test: real Slack workspace (dev workspace) produces events in Kafka within 5s
- Zero message content appears in any DB table (verified by automated content scan)

---

## SPRINT 10 — Performance, Scalability & Monitoring
**Goal:** The system handles production-scale orgs (1,000–10,000 employees).

### Tasks

#### Graph Performance
- [ ] Profile betweenness centrality on 1,000-node graph — switch to approximate betweenness (`k` parameter in NetworkX) if > 5s
- [ ] Parallelize community detection with `joblib`
- [ ] Add Redis cache for graph snapshots: `GET /graph/snapshot` serves from cache (TTL 1 hour)

#### Kafka Scaling
- [ ] Increase partitions on `collaboration.events.raw` to 6
- [ ] Add consumer group with 3 consumers for parallel processing
- [ ] Add Kafka lag monitoring metric

#### Airflow Scaling
- [ ] Switch to CeleryExecutor for parallel DAG task execution
- [ ] Add retry logic (3 retries with exponential backoff) on all tasks

#### Observability Stack
- [ ] Add Prometheus metrics endpoint to FastAPI (`prometheus-fastapi-instrumentator`)
- [ ] Add Grafana service to `docker-compose.yml` with pre-built dashboard:
  - Graph processing time per snapshot
  - API request rate + latency p95
  - Kafka consumer lag
  - Active SPOF alerts count

#### Load Testing
- [ ] `tests/performance/load_test.py` using `locust`:
  - Simulate 50 concurrent users on `/graph/snapshot` and `/risk/scores`
  - Target: p95 latency < 800ms at 50 RPS

### Sprint 10 Exit Criteria
- 1,000-node graph processed in < 30s (metrics computation)
- API p95 latency < 500ms under 50 concurrent users
- Grafana dashboard operational with all 4 panels

---

## FUTURE IMPLEMENTATIONS

> These are post-MVP capabilities. Sequencing depends on business priorities.

---

### F1 — Graph Neural Network (GNN) for Churn Risk Prediction
**Complexity:** High · **Business Value:** Very High

Move beyond anomaly detection to supervised churn risk. Train a GNN (Graph Attention Network) on historical data where ground truth is: "employee left within 90 days."

- Feature graph: node features = HR metrics (tenure, role level, recent promo, PTO usage) + graph metrics (betweenness trend)
- Model: GAT (Graph Attention Network) via PyTorch Geometric
- Target: binary classification — will this employee leave in the next 90 days?
- Requires: HR system integration for ground truth labels (HRIS API)
- Output: churn probability score per employee, surfaced in dashboard

**Why this matters:** Transforms from reactive anomaly detection to proactive, time-bounded risk prediction.

---

### F2 — Temporal Graph Analysis (Knowledge Flow Over Time)
**Complexity:** High · **Business Value:** High

Move from static daily snapshots to a temporal graph model where the graph is a time series object.

- Use `torch_geometric_temporal` or `DyGNN`
- Model evolving relationships: not just "who talks to whom today" but "how has this relationship changed over 6 months"
- Enables: early pattern recognition (3-week leading indicator instead of 3-day)
- Storage: graph snapshots as time-indexed tensors in a time-series DB (TimescaleDB or InfluxDB)

---

### F3 — "Knowledge Risk" Quantification
**Complexity:** Medium · **Business Value:** Very High

Attach domain knowledge to nodes. If a connector between Engineering and Sales is the only person who understands a critical system, the risk is not just social — it's epistemic.

- Integrate with Confluence/Notion API: map documents authored/edited per employee
- Build a "knowledge graph overlay": nodes inherit document tags as knowledge domains
- SPOF score becomes: `graph_spof + knowledge_concentration_score`
- Dashboard: "If Maria leaves, 3 departments lose their only expert in payment systems"

---

### F4 — Succession Planning Recommendations
**Complexity:** Medium · **Business Value:** High

From risk detection to prescriptive action. Instead of just flagging "Maria is a SPOF," recommend who should be cross-trained.

- Algorithm: for each critical connector, find employees in neighboring communities with high clustering coefficient who could absorb bridge relationships
- Output: "Suggested knowledge transfer pairs" with compatibility score
- Integration: export to HR system as "succession plan" action items

---

### F5 — Real-Time Alert Engine (WebSockets)
**Complexity:** Medium · **Business Value:** Medium

Replace polling-based dashboard with push-based alerts.

- FastAPI WebSocket endpoint: `WS /alerts/live`
- Airflow DAG completion triggers alert broadcast
- Frontend: toast notifications for new SPOF alerts without page reload
- Mobile-ready: PWA push notification support

---

### F6 — Multi-Tenant SaaS Architecture
**Complexity:** Very High · **Business Value:** Very High (product path)

Transform from single-company deployment to a multi-tenant SaaS platform.

- Schema-per-tenant isolation in PostgreSQL (or separate DB per tenant)
- Tenant-aware Kafka topics: `{tenant_id}.collaboration.events.raw`
- API: `X-Tenant-ID` header routing
- Billing integration: Stripe for usage-based pricing (events ingested/month)
- Admin panel: tenant onboarding, connector configuration, RBAC management
- This is the path to productizing Org Synapse as a standalone HR Intelligence SaaS

---

### F7 — Natural Language Interface (Claude Integration)
**Complexity:** Low–Medium · **Business Value:** High

Allow HR managers to query the organizational graph in plain language.

- FastAPI endpoint: `POST /query/natural` → `{question: str}`
- Uses Anthropic Claude API (claude-sonnet) with tool use
- Tools exposed to Claude: `get_graph_snapshot`, `get_risk_scores`, `get_silo_alerts`, `simulate_removal`
- Example queries:
  - "Who are the 5 people most critical to Engineering-Sales communication?"
  - "If the entire Payments team went on leave, which departments would be cut off?"
  - "Show me who has been increasingly isolated over the last month"
- Frontend: chat panel alongside graph visualization

---

### F8 — Compliance & Regulatory Reporting
**Complexity:** Medium · **Business Value:** High (enterprise requirement)

Generate audit-ready reports for HR compliance frameworks (GDPR, CCPA, SOC 2).

- `GET /compliance/data-audit` → what data is stored per employee, retention period
- Automated data retention: purge `raw_events` > 90 days, `graph_snapshots` > 12 months
- Employee data export: `GET /employees/{id}/data-export` → full data package (GDPR Article 20)
- Consent management: employees opt-out via `employee_consent = false` → excluded from graph computation
- Quarterly compliance PDF report generated by Airflow DAG

---

### F9 — Org Health Score & Executive Briefing
**Complexity:** Low · **Business Value:** Very High (C-level adoption)

Synthesize all graph metrics into a single executive-facing "Organizational Health Score."

- Composite score (0–100): `f(silo_count, avg_spof_score, entropy_trend, community_fragmentation)`
- Weekly trend: is the organization getting more or less connected?
- Automated briefing: Airflow generates a PDF/email every Monday morning with:
  - Org Health Score vs prior week
  - Top 3 risk nodes (anonymized for executive view)
  - Active silo count and trend
  - Recommended actions
- Delivery: email via SendGrid or internal Slack channel

---

## Dependency Map

```
S0 → S1 → S2 → S3 → S4 → S5 → S6   (Core MVP, sequential)
                              ↓
                    S7 → S8 → S9 → S10  (Production hardening)
                    ↓
          F1   F2   F3   F4   F5   F6   F7   F8   F9
          (Future implementations, can be parallelized by priority)
```

---

## Effort & Priority Summary

| Item | Effort | Business Value | Recommended Order |
|---|---|---|---|
| S0–S6 (MVP) | 6 weeks | Critical | First |
| S7 (Auth/RBAC) | 1 week | High | Before any real data |
| S8 (Neo4j) | 1 week | Medium | After MVP |
| S9 (Real connectors) | 2 weeks | Critical (prod) | After S7 |
| S10 (Scale) | 1 week | High (prod) | Before launch |
| F3 (Knowledge Risk) | 2 weeks | Very High | First future |
| F7 (Claude NL) | 1 week | High | Quick win |
| F9 (Exec Briefing) | 1 week | Very High | Quick win |
| F4 (Succession) | 2 weeks | High | After F3 |
| F1 (GNN Churn) | 3 weeks | Very High | Requires ground truth data |
| F5 (WebSockets) | 1 week | Medium | When users demand it |
| F2 (Temporal GNN) | 4 weeks | High | Research phase |
| F8 (Compliance) | 2 weeks | High (enterprise) | When selling to regulated industries |
| F6 (Multi-tenant SaaS) | 6+ weeks | Very High (product) | Strategic decision point |
