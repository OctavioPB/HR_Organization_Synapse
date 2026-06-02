# Organizational Synapse & Knowledge Risk

A graph-based HR intelligence platform that ingests collaboration metadata, builds a live organizational network, and surfaces structural risk — before HR has any subjective signal.

---

## What it does

The system ingests collaboration metadata (Slack, Teams, Jira, GitHub, Confluence, Notion) as directed edges — who interacted with whom, on which channel, at what time — and builds a weighted organizational graph. Daily and weekly ML pipelines compute structural risk metrics, predict churn, and generate prescriptive outputs: cross-training plans, team compositions, departure impact reports, DEI equity analytics, and weekly AI-authored briefings.

**Core outputs**

| Output | Description |
|---|---|
| **SPOF score** | Single-point-of-failure risk per employee — betweenness centrality, cross-department bridging, clustering coefficient, entropy trend |
| **Knowledge risk** | Sole-expert fraction, document volume, domain breadth fused with SPOF score |
| **Churn risk** | GNN churn probability (90-day horizon) enriched with HRIS tenure, promotion, and PTO signals |
| **Silo alerts** | Departments whose internal/external edge ratio exceeds threshold |
| **Org health score** | Composite 0–100 weekly score with AI-generated executive narrative |
| **Succession plans** | Top-N cross-training candidates per SPOF employee, ranked by structural + domain compatibility |
| **Knowledge transfer plans** | 90-day three-phase action plan (introductions → documents → shadowing) for each succession pair |
| **What-If simulation** | Recalculates graph health after removing one or more employees; supports multi-operation reorg scenarios (remove / merge departments / move team) |
| **Onboarding integration tracker** | Daily integration score for new hires vs. tenure-matched cohort; fires alert at day 60 below 25th percentile |
| **Team composition optimizer** | Ranked team options scored by bridge coverage, domain coverage, structural load, and relationship density |
| **DEI structural equity analytics** | Centrality distributions by demographic group; succession homophily check; all outputs aggregate-only |
| **Departure impact report** | Automated post-departure analysis comparing predicted SPOF score against actual structural damage |
| **Manager self-service view** | Traffic-light engagement health for direct reports with AI-generated 1:1 suggestions — no raw scores exposed |
| **Weekly insights digest** | Monday email + Slack digest with Org Health Score, top risk signals, and one AI recommendation |
| **NL query interface** | Plain-English questions answered by a Claude agentic loop with tool-backed graph access |
| **Compliance reports** | GDPR/CCPA data audit, Article 20 export, consent management, retention purge, quarterly HTML report |

---

## Architecture

```
Collaboration tools (Slack, Teams, Jira, GitHub, Calendar, Confluence, Notion)
        │
        ▼
[Kafka] ── streaming metadata ingestion (KRaft mode, no Zookeeper)
        │  topics: collaboration.events.raw  │  {tenant}.collaboration.events.raw
        ▼
[Airflow DAGs] ── ETL + ML pipelines
   ├── graph_builder_dag      02:00 UTC daily   (sync_hris → build → metrics → onboarding → silos → risks)
   ├── churn_risk_dag         04:00 UTC Sundays
   ├── succession_dag         04:00 UTC Sundays (compute_succession → generate_transfer_plans)
   ├── equity_dag             04:30 UTC Sundays
   ├── org_health_dag         weekly
   ├── weekly_digest_dag      23:00 UTC Sundays  → email + Slack
   ├── departure_report_dag   06:00 UTC daily    → departure impact reports
   ├── knowledge_risk_dag     01:00 UTC Fridays
   ├── temporal_graph_dag     05:00 UTC daily
   ├── anomaly_detection_dag  03:00 UTC Mondays
   ├── neo4j_import_dag       06:00 UTC daily (optional)
   └── compliance_dag         quarterly
        │
        ▼
[PostgreSQL] ── schema-per-tenant isolation
   ├── employees              hire_date  manager_id  reporting_level  pto_days_ytd  hris_source
   ├── raw_events             90-day retention
   ├── graph_snapshots        365-day retention
   ├── risk_scores            knowledge_risk_scores  succession_recommendations
   ├── churn_scores           temporal_anomaly_scores
   ├── org_health_scores      onboarding_integration_scores
   ├── reorg_scenarios        knowledge_transfer_plans
   ├── departure_impact_reports
   ├── employee_demographics  structural_equity_scores
   ├── digest_config          consent_audit_log  data_retention_purges
   └── public.*               tenants  tenant_api_keys(role)  tenant_usage
        │
        ├──► [Neo4j] (optional) ── Cypher + Graph Data Science
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
| Ingestion | Apache Kafka 3.7 (KRaft) + kafka-python |
| Orchestration | Apache Airflow 2.9 (LocalExecutor) |
| Graph processing | NetworkX 3.x + optional Neo4j 5 + GDS |
| ML | scikit-learn Isolation Forest · PyTorch Geometric GNN (churn) · numpy polyfit (entropy) |
| HRIS connectors | Workday REST (OAuth 2.0) · BambooHR REST API |
| Knowledge connectors | Confluence Cloud · Notion API |
| Collaboration connectors | Slack Bot API · Microsoft Graph (Teams) · Jira REST · GitHub Webhooks |
| LLM | Anthropic Claude (`claude-sonnet-4-6`) — NL query, org health briefings, 1:1 suggestions, transfer plan narratives, departure reports, weekly digest |
| Notifications | SendGrid (email digest) · Slack Webhooks (Block Kit) |
| Backend | FastAPI + psycopg2 (RealDictCursor) + Redis |
| Frontend | React 18 + Vite 5 + Sigma.js 3 + Recharts + TanStack Query |
| Database | PostgreSQL 15 (schema-per-tenant) |
| Billing | Stripe webhooks |
| Observability | Prometheus + Grafana |
| Infrastructure | Docker Compose |

---

## Repository structure

```
org-synapse/
├── demo.ps1                    # One-command launcher (Windows/PowerShell)
├── docker-compose.yml
├── .env.example
│
├── ingestion/
│   ├── producers/              # Kafka producers (synthetic + real-API + multi-tenant)
│   ├── connectors/             # Confluence · Notion · HRIS (Workday / BambooHR)
│   ├── consumers/              # edge_consumer.py → raw_events
│   └── schemas/                # CollaborationEvent (Pydantic v2)
│
├── etl/
│   ├── dags/                   # 13 Airflow DAGs
│   │   ├── graph_builder_dag.py        # daily: sync_hris → build → onboarding → silos → risks
│   │   ├── churn_risk_dag.py
│   │   ├── succession_dag.py           # + generate_transfer_plans task
│   │   ├── equity_dag.py               # DEI structural equity (weekly)
│   │   ├── weekly_digest_dag.py        # email + Slack digest (Sundays 23:00)
│   │   ├── departure_report_dag.py     # daily departure detection
│   │   └── ...
│   ├── tasks/                  # Pure Python task functions (importable outside Airflow)
│   │   ├── compute_onboarding.py
│   │   ├── compute_equity.py
│   │   ├── generate_transfer_plans.py
│   │   └── generate_departure_report.py
│   └── templates/
│       └── digest_email.html   # BRAND.md-compliant HTML email template
│
├── graph/
│   ├── builder.py              # NetworkX DiGraph from edge list
│   ├── metrics.py              # Betweenness (exact + k-pivot), degree, clustering
│   ├── silo_detector.py        # Louvain community detection + silo alerting
│   ├── risk_scorer.py          # SPOF formula (α β γ δ env-configurable)
│   ├── succession.py           # Structural compatibility scoring
│   ├── scenario_simulator.py   # Multi-operation reorg What-If engine
│   ├── team_optimizer.py       # Greedy set-cover team composition
│   ├── org_health.py           # Composite 0–100 health score + Claude narrative
│   └── compliance.py           # GDPR audit, retention purge, Article 20 export
│
├── ml/
│   ├── gnn/
│   │   ├── feature_builder.py  # 11-feature node matrix (graph + HRIS fields)
│   │   └── model.py            # Graph Attention Network (PyTorch Geometric)
│   ├── anomaly/
│   │   └── isolation_forest.py
│   └── features/
│       └── feature_extractor.py
│
├── api/
│   ├── main.py                 # FastAPI app + all router registrations
│   ├── deps.py                 # get_db · get_tenant_db · require_role()
│   ├── tenant.py               # Multi-tenant provisioning + TenantContext(role)
│   └── routers/
│       ├── graph.py            # /graph/*
│       ├── risk.py             # /risk/*
│       ├── alerts.py           # /alerts/* + /alerts/departures
│       ├── succession.py       # /succession/* + transfer plan endpoints
│       ├── manager.py          # /manager/team  /manager/team/{id}/suggestions
│       ├── onboarding.py       # /onboarding/cohort  /onboarding/employee/{id}/history
│       ├── scenarios.py        # /scenarios CRUD + /scenarios/{id}/compute + /compare
│       ├── teams.py            # /teams/optimize  /teams/departments  /teams/domains
│       ├── equity.py           # /equity/centrality-distribution  /succession-check
│       ├── org_health.py       # /org-health/*
│       ├── query.py            # POST /query/natural
│       ├── compliance.py       # /compliance/* + /compliance/departure/{id}
│       ├── admin.py            # /admin/tenants  /admin/digest-config
│       ├── billing.py          # /billing/*
│       ├── connectors.py       # /connectors/*
│       ├── knowledge.py        # /knowledge/*
│       ├── internal.py         # POST /internal/alerts/broadcast
│       └── ws.py               # WS /alerts/live
│
├── frontend/src/
│   ├── components/
│   │   ├── OrgGraph.jsx             # Sigma.js WebGL graph
│   │   ├── CriticalNodePanel.jsx    # Top SPOF employees
│   │   ├── TeamMemberCard.jsx       # Manager view — traffic-light per direct report
│   │   ├── TransferPlanPanel.jsx    # 90-day transfer plan checklist
│   │   ├── DepartureReportModal.jsx # Post-departure impact report
│   │   ├── DigestConfigPanel.jsx    # Email + Slack digest configuration
│   │   ├── ChatPanel.jsx            # NL query chat UI
│   │   └── OrgHealthCard.jsx        # Org health score card
│   └── pages/
│       ├── Dashboard.jsx            # KPI cards + graph + critical panel
│       ├── EmployeeDetail.jsx       # Ego network + SPOF trend + What-If + transfer plan
│       ├── ManagerView.jsx          # /manager  — direct report risk view
│       ├── OnboardingTracker.jsx    # /onboarding — cohort scatter + at-risk table
│       ├── ScenarioPlanner.jsx      # /scenarios — reorg scenario builder + impact
│       ├── TeamOptimizer.jsx        # /teams — constraint-based team composition
│       ├── EquityDashboard.jsx      # /equity — structural equity by demographic group
│       └── AdminPanel.jsx           # /admin — tenant management + digest config
│
├── data/
│   ├── migrations/             # 016 SQL migrations (000 → 016)
│   └── synthetic/              # generate_org_data.py
│
└── scripts/
    └── seed_dev.py             # 13-step dev seed (graph + HRIS + onboarding + succession
                                #   + transfer plans + demographics + equity + org health
                                #   + departure simulation)
```

---

## Quick start

### Prerequisites

- Docker Desktop (running)
- Python 3.11+
- Node.js 20+

### Option A — one command (recommended)

```powershell
# Windows / PowerShell
.\demo.ps1
```

`demo.ps1` kills port conflicts, loads `.env`, starts all Docker services, waits for health checks, opens the API and Vite dev server in separate windows, then opens the browser.

After the browser opens, seed all demo data in a separate terminal:

```bash
python scripts/seed_dev.py --employees 120 --days 60
```

The seed runs 13 steps: graph build, HRIS enrichment, knowledge domains, onboarding scores, succession planning, transfer plans, employee demographics, equity scores, org health score, and a simulated departure with impact report.

### Option B — manual

**1. Environment**

```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY for NL queries, briefings, and digest narratives
```

**2. Infrastructure**

```bash
docker compose up -d
```

**3. Python dependencies**

```bash
pip install -r requirements.txt
```

**4. Apply database migrations**

```bash
for f in data/migrations/*.sql; do
  docker exec -i org-synapse-postgres-1 psql -U opb -d org_synapse < "$f"
done
```

**5. Seed data**

```bash
python scripts/seed_dev.py --employees 120 --days 60
```

**6. Start API**

```bash
uvicorn api.main:app --reload --port 8000
# Docs: http://localhost:8000/docs
```

**7. Start dashboard**

```bash
cd frontend && npm install && npm run dev
# http://localhost:5173
```

---

## Service URLs

| Service | URL | Credentials |
|---|---|---|
| **Dashboard** | http://localhost:5173 | — |
| **My Team** (manager view) | http://localhost:5173/manager | — |
| **Onboarding Tracker** | http://localhost:5173/onboarding | — |
| **Reorg Scenarios** | http://localhost:5173/scenarios | — |
| **Team Optimizer** | http://localhost:5173/teams | — |
| **DEI Equity** | http://localhost:5173/equity | — |
| **Admin Panel** | http://localhost:5173/admin | — |
| API (FastAPI) | http://localhost:8000 | — |
| API Docs (Swagger) | http://localhost:8000/docs | — |
| Airflow | http://localhost:8088 | admin / admin |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |
| Neo4j Browser | http://localhost:7474 | neo4j / changeme |
| Adminer (DB UI) | http://localhost:8081 | — |
| PostgreSQL | localhost:5433 | — |
| Redis | localhost:6380 | — |

---

## SPOF score formula

```
SPOF_score = α × betweenness_centrality
           + β × cross_department_edge_ratio
           + γ × (1 − clustering_coefficient)
           − δ × entropy_trend
```

Weights are configurable (`SPOF_ALPHA`, `SPOF_BETA`, `SPOF_GAMMA`, `SPOF_DELTA`). Default: 0.4 / 0.3 / 0.2 / 0.1. Thresholds: `normal` < 0.4 · `warning` 0.4–0.7 · `critical` > 0.7.

**Enhanced SPOF** fuses the graph score with the knowledge risk score (sole-expert fraction, document volume, domain breadth).

---

## Org health score

```
composite_risk = 0.20 × silo_risk
              + 0.35 × spof_risk
              + 0.20 × entropy_risk
              + 0.25 × fragmentation_risk

health_score = (1 − composite_risk) × 100   [0–100]
```

Tiers: `healthy` ≥ 80 · `caution` 60–79 · `at_risk` 40–59 · `critical` < 40.

A Monday morning digest delivers the score, top risk signals, and one Claude-authored recommendation via email and Slack. Configure recipients in Admin → Digest Config.

---

## API reference

### Graph & risk

| Method | Endpoint | Description |
|---|---|---|
| GET | `/graph/snapshot` | Full org graph for a snapshot date |
| GET | `/graph/employee/{id}` | 2-hop ego network |
| GET | `/graph/communities` | Louvain communities with silo flag |
| GET | `/risk/scores` | Top-N employees by SPOF score |
| GET | `/risk/critical-nodes` | Employees above SPOF threshold |
| GET | `/risk/employee/{id}/history` | 30-day SPOF trend |
| POST | `/risk/simulate` | What-If: single-employee removal |
| GET | `/alerts/silos` | Active silo alerts |
| GET | `/alerts/entropy` | Connectivity anomaly alerts |
| GET | `/alerts/departures` | Departure impact reports (ready) |
| GET | `/alerts/history` | All alerts in last N days |
| WS | `/alerts/live` | Real-time WebSocket stream |

### New feature endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/manager/team` | Traffic-light risk view for direct reports (`manager` role) |
| GET | `/manager/team/{id}/suggestions` | Claude 1:1 conversation suggestions |
| GET | `/onboarding/cohort` | New hire integration scores vs. tenure cohort |
| GET | `/onboarding/employee/{id}/history` | Integration score time series |
| POST | `/scenarios` | Create a reorg scenario |
| POST | `/scenarios/{id}/compute` | Run multi-operation structural simulation |
| GET | `/scenarios` | List saved scenarios |
| GET | `/scenarios/compare?ids=...` | Side-by-side impact comparison (up to 4) |
| GET | `/scenarios/{id}` | Full scenario + impact report |
| POST | `/teams/optimize` | Greedy team composition optimizer |
| GET | `/teams/departments` | Available departments for team builder |
| GET | `/teams/domains` | Available knowledge domains |
| GET | `/equity/centrality-distribution` | Centrality by demographic group (aggregates only) |
| GET | `/equity/succession-check/{id}` | Succession candidate diversity check |
| POST | `/equity/import-demographics` | Import anonymised group labels |
| GET | `/succession/{id}/transfer-plan` | 90-day knowledge transfer plan |
| GET | `/succession/{id}/transfer-plan/export.csv` | Export plan as CSV |
| GET | `/compliance/departure/{id}` | Full departure impact report + narrative |

### Intelligence

| Method | Endpoint | Description |
|---|---|---|
| GET | `/knowledge/scores` | Knowledge risk scores |
| GET | `/knowledge/employee/{id}` | Full knowledge profile |
| GET | `/succession/recommendations` | Succession candidates for top SPOF employees |
| GET | `/org-health/score` | Latest composite org health score |
| GET | `/org-health/trend` | Weekly trend |
| GET | `/org-health/briefing` | AI executive briefing |
| POST | `/query/natural` | Natural language query via Claude |

### Admin & billing

| Method | Endpoint | Description |
|---|---|---|
| POST | `/admin/tenants` | Provision tenant + API key |
| GET/PATCH/DELETE | `/admin/tenants/{id}` | Manage tenant |
| GET | `/admin/digest-config` | Digest delivery configuration |
| POST | `/admin/digest-config` | Update recipients + Slack webhook |
| GET | `/billing/usage` | Current-month event count |
| POST | `/billing/webhook` | Stripe HMAC-validated webhook |

Full interactive docs at `http://localhost:8000/docs`.

---

## Airflow pipelines

```
graph_builder_dag      02:00 UTC daily
  sync_hris → check_raw_events → build_graph → compute_metrics
    → compute_onboarding → detect_silos → score_risks → flag_spof
    → trigger_neo4j → broadcast_alerts

churn_risk_dag         04:00 UTC Sundays
  build_features → run_gnn → persist_scores → fire_alerts

succession_dag         04:00 UTC Sundays
  [waits: graph_builder_dag.compute_metrics]
  → compute_succession → generate_transfer_plans → log_summary

equity_dag             04:30 UTC Sundays
  [waits: graph_builder_dag.flag_spof_critical]
  → compute_equity

org_health_dag         weekly
  [waits: graph_builder_dag] → compute_health → generate_briefing

weekly_digest_dag      23:00 UTC Sundays  →  delivers Monday morning
  [waits: org_health_dag] → compile_digest_data → generate_narrative
    → send_email_digest ─┐
    → send_slack_digest  ┘  (parallel)

departure_report_dag   06:00 UTC daily
  detect_departures → generate_reports → broadcast_departure_alerts

knowledge_risk_dag     01:00 UTC Fridays
  ingest_confluence → ingest_notion → score_knowledge_risk

temporal_graph_dag     05:00 UTC daily
anomaly_detection_dag  03:00 UTC Mondays
compliance_dag         quarterly
```

---

## Roles

The `role` field on `tenant_api_keys` controls data visibility:

| Role | Access |
|---|---|
| `hr_admin` | Full access — individual scores, names, churn probabilities, transfer plans |
| `executive` | Department-level aggregates only — no individual names or scores |
| `analyst` | Anonymised graph topology — no names, no scores |
| `manager` | Direct reports only — traffic-light status and AI suggestions, no raw scores |

Provision role-specific keys via `POST /admin/tenants/{id}/api-keys`.

---

## Multi-tenant mode

Each tenant gets an isolated PostgreSQL schema (`tenant_{slug}`). `search_path` is switched per-request via `X-Tenant-ID` + `X-Api-Key` headers.

```bash
# Provision a tenant
curl -X POST http://localhost:8000/admin/tenants \
  -H "X-Admin-Key: $ADMIN_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"slug": "acme-corp", "name": "Acme Corp", "plan": "pro"}'
# → returns raw_api_key (shown once)

# Authenticate
curl http://localhost:8000/risk/scores \
  -H "X-Tenant-ID: <uuid>" \
  -H "X-Api-Key: <raw-key>"
```

---

## Privacy & compliance

- **Metadata only.** No message content, email bodies, or file contents are ever ingested — only `{who} → {whom}`, `{channel}`, `{timestamp}`.
- **Consent-gated.** `employees.consent = false` excludes an employee from all graph computation at the SQL level — data never reaches Python memory.
- **Aggregate-only DEI.** The equity analytics module produces group-level centrality distributions. No individual demographic attributes are accessible via any API endpoint.
- **Manager view abstraction.** Managers see traffic-light status only — numeric SPOF scores and churn probabilities are `hr_admin`-scoped and never travel over the wire for `manager` role keys.
- **GDPR Article 20.** `GET /compliance/data-export/{id}` returns a complete personal data package.
- **Retention.** `raw_events` purged after 90 days; `graph_snapshots` after 365 days.

---

## Running tests

```bash
pytest tests/ -v --tb=short

# Subsets
pytest tests/unit/test_graph_metrics.py -v
pytest tests/unit/test_compliance.py -v
pytest tests/unit/test_tenant.py -v
pytest tests/unit/test_nl_query.py -v
```

All unit tests mock the database via `dependency_overrides` — no live DB or Kafka required.

---

## Demo scenario

```bash
python scripts/seed_dev.py --employees 120 --days 60
```

Expected outputs:

- **SPOF scores:** connector employees surface in top 5; withdrawing employee enters `critical` in the final 15 days
- **Silo alerts:** HR and Sales fire; Engineering does not
- **Onboarding tracker:** ~10% of employees have `hire_date` < 90 days; 6 flagged below cohort 25th percentile
- **Succession + transfer plans:** top 10 SPOF employees get cross-training plans generated
- **DEI equity:** `group_c` employees show lower median centrality than org median (by seed design)
- **Departure report:** one SPOF employee is marked inactive; impact report compares t-90 prediction vs. t+30 structural change
- **What-If (reorg scenario):** remove both connectors → `new_isolated_components` > 0, `avg_path_length_delta_pct` > 25%
- **NL query:** "Who are the critical connectors between Engineering and Sales?" returns grounded tool-backed answer

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*
