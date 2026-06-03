# Organizational Synapse & Knowledge Risk

[![CI](https://github.com/YOUR_ORG/HR_Organization_Synapse/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/HR_Organization_Synapse/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/YOUR_ORG/HR_Organization_Synapse/branch/main/graph/badge.svg)](https://codecov.io/gh/YOUR_ORG/HR_Organization_Synapse)

A graph-based HR intelligence platform that surfaces structural risk from collaboration metadata — before HR has any subjective signal.

---

## The problem

HR gets subjective signals last. By the time a manager notices someone is "disengaged," or an exit interview flags a knowledge gap, the structural damage has been accumulating for months. Standard HR tooling — surveys, performance reviews, HRIS exports — captures how employees feel about their work, not how the organization structurally depends on them.

Three failure modes repeat across companies:

**Key-person departure.** A senior engineer leaves and two teams lose their only cross-functional connector. Nobody knew the dependency existed until the graph collapsed.

**Silent siloing.** Two departments stop collaborating six months before anyone files a complaint. By the time it surfaces as a project delay, it's baked into team culture.

**Onboarding blindness.** New hires integrate below cohort median at day 60. HR finds out at the 90-day review — or not at all.

These risks are structural, not behavioral. They live in the collaboration graph, not in 1:1 feedback.

---

## Core mechanism

Synapse ingests collaboration metadata (Slack, Teams, Jira, GitHub, Confluence, Notion) as directed edges — `{who} → {whom}`, `{channel}`, `{timestamp}`. No message content, no email bodies, no file contents. Only structure.

Daily and weekly ML pipelines derive:

| Signal | Method |
|---|---|
| **SPOF score** | Betweenness centrality + cross-department bridging + clustering coefficient + entropy trend (formula below) |
| **Knowledge risk** | Sole-expert fraction × document volume × domain breadth, fused with SPOF score |
| **Churn probability** | Graph Attention Network (11-feature node matrix, 90-day horizon) enriched with HRIS tenure, promotion, and PTO signals — see [MODEL_CARD_CHURN.md](MODEL_CARD_CHURN.md) |
| **Silo detection** | Louvain community detection; internal/external edge ratio vs. configurable threshold |
| **Org health** | Composite 0–100 weekly score with AI executive briefing |

Outputs are prescriptive: succession cross-training plans, 90-day knowledge transfer schedules, reorg what-if simulations, onboarding cohort alerts, departure impact forecasts.

---

## Validation & Results

Each model is evaluated against a controlled synthetic dataset with planted ground truth. All numbers below are the **direct output of the test suite** (`tests/validation/`) — not targets or predictions. The suite runs without a database or Kafka: 30 tests, ~12 seconds.

---

### SPOF score — structural identification

**Setup:** 100-employee org, planted archetypes with known structural roles (seed=42). Scoring uses the production `score_all_with_bands()` pipeline with rank-percentile transform.

| Archetype | n | SPOF score | Rank | Flag | Robust critical |
|---|---|---|---|---|---|
| BRIDGE | 2 | 0.84 / 0.80 | 2 / 3 | critical | yes |
| WITHDRAWING | 1 | 0.89 | 1 | critical | yes |
| SOLE_EXPERT | 2 | 0.63 / 0.46 | 9 / 37 | warning / elevated | no |
| SILO (max) | 10 | 0.49 | 26 | elevated | no |

| Metric | Result | Threshold |
|---|---|---|
| Precision@critical (score ≥ 0.70) | 100% | ≥ 80% |
| Recall — BRIDGE employees | 100% | 100% |
| Recall — WITHDRAWING (score ≥ 0.50) | 100% | 100% |
| SOLE_EXPERT max score | 0.63 | < 0.70 |
| SILO max score | 0.49 | < 0.50 |
| Spearman ρ (5 archetype medians) | 0.800 | ≥ 0.60 |

The SOLE_EXPERT boundary (max score 0.63, below the 0.70 critical threshold) is a deliberate design property: knowledge depth without cross-department bridging is a different risk signal — scored separately in `graph/knowledge_risk.py`.

---

### SPOF score — what-if structural impact

**Setup:** Each of the 100 employees removed one at a time via `scenario_simulator.apply_operations()`. Damage = avg-path-length increase % + new isolated components × 20.

| Archetype | Mean damage score | SPOF rank | Damage rank |
|---|---|---|---|
| BRIDGE | 1.502 | 2–3 | 1–2 |
| WITHDRAWING | 0.865 | 1 | 3 |
| SOLE_EXPERT | 0.209 | 9 | 5 |
| SILO | 0.102 | 26–67 | 70–95 |
| NORMAL (mean) | −0.035 | 4–100 | — |

| Metric | Result | Threshold |
|---|---|---|
| Spearman ρ — individual (N=100) | 0.349 | ≥ 0.30 |
| Spearman ρ — archetype medians (N=5) | 0.900 | ≥ 0.80 |
| Precision@5 | 60% | ≥ 40% |
| BRIDGE damage > SOLE_EXPERT damage | 1.502 > 0.209 | required |
| BRIDGE damage > SILO damage | 1.502 > 0.102 | required |

**WITHDRAWING gap (SPOF rank 1, damage rank 3):** the model fires 15 days before departure because it reads the withdrawal signal from the entropy trend, not from current network load. By the time the employee actually leaves, the graph has already partially adapted. This is intended behavior — SPOF is a leading indicator, not a real-time damage meter.

---

### Churn model — graph vs. tabular baseline

**Setup:** 300-employee synthetic org, stratified temporal split (210 train / 90 test, seed=42). Baseline: `LogisticRegression` on 3 tabular features (tenure, degree_out, entropy_trend). Graph model: `GraphMLP` (GraphSAGE-style, node features + mean-aggregated neighbor features) — surrogate for `ChurnGAT` since `torch_geometric` is not installed.

| Metric | LogReg (3 features) | GraphMLP (11+11 features) | Δ |
|---|---|---|---|
| AUROC | 0.722 | 0.616 | −0.106 |
| PR-AUC | 0.414 | 0.334 | −0.080 |
| AP@10 | 0.610 | 0.589 | −0.021 |
| F1 (optimal threshold) | 0.522 | 0.414 | −0.108 |
| Precision (optimal) | 0.414 | 0.500 | +0.086 |
| Recall (optimal) | 0.706 | 0.353 | −0.353 |

**Finding:** at N=300 with 30 training positives, the 3-feature logistic regression outperforms the graph model by 10.6pp AUROC. Individual features (tenure, activity decay, entropy trend) are strong enough that the graph's social-contagion signal is not decisive at this scale. Both models exceed the random floor (AUROC 0.50) and the trivial PR-AUC floor (churn rate = 18.9%). The GNN architecture is expected to show advantage when churn labels cover N > 500 employees across ≥ 3 observation cohorts. Until then: deploy the baseline.

---

### Anomaly detection — Isolation Forest

**Setup:** 100 noisy normal employees + 8 injected anomalies (4 archetypes × 2 severity levels), evaluated at oracle contamination (7.4% = 8/108) and production contamination (5%).

| Archetype | Severity | Anomaly score | Rank / 108 | Oracle (7.4%) | Production (5%) |
|---|---|---|---|---|---|
| Activity spike | severe | 1.000 | 1 | detected | detected |
| Sudden dropout | severe | 0.910 | 2 | detected | detected |
| Activity spike | moderate | 0.736 | 3 | detected | detected |
| Sudden dropout | moderate | 0.675 | 4 | detected | detected |
| Entropy collapse | severe | 0.642 | 5 | detected | detected |
| Bridge collapse | severe | 0.626 | 6 | detected | detected |
| Entropy collapse | moderate | 0.413 | 7 | detected | missed |
| Bridge collapse | moderate | 0.376 | 8 | detected | missed |

| Metric | Result | Threshold |
|---|---|---|
| Recall@severe — oracle contamination | 100% | 100% |
| Recall@all — oracle contamination | 100% | ≥ 62.5% |
| Precision — oracle contamination | 100% | ≥ 62.5% |
| Recall@severe — production (5%) | 75% (3/4) | ≥ 50% |
| Severity order: severe score > moderate (all archetypes) | yes | required |
| Spearman ρ — 3 group medians (normal / moderate / severe) | 1.000 | ≥ 0.90 |

Group medians: normal 0.118 · moderate 0.544 · severe 0.776.

**Hardest archetype: bridge collapse** (severe score 0.626, rank 6). The current betweenness and degree values are moderate — the anomaly lives in the 7-day delta features (`betweenness_delta_7d = −0.54`). At production contamination (5%, ~5 flags), entropy-collapse-moderate and bridge-collapse-moderate are missed. Raising contamination to 8% recovers both; the trade-off is more false positives from the normal tail.

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
│       └── digest_email.html   # HTML email template
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

### Prescriptive outputs

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

## Roadmap

**Near-term**

- Production OAuth flows for real Slack, Teams, Jira, and GitHub connectors (currently: API-backed synthetic producers)
- Temporal GNN: replace static node features with time-series embeddings so the churn model sees velocity, not snapshots
- Headless mode: decouple the Airflow DAGs from Docker Compose so the pipeline can run in any orchestration environment (Kubernetes, Prefect, Dagster)

**Medium-term**

- Real-time graph updates: bypass the daily DAG batch cycle for high-frequency events (Slack + GitHub) using the Kafka consumer to write graph deltas directly
- Fine-tuned SPOF thresholds: per-tenant calibration based on historical departure data rather than fixed global defaults
- Manager nudge system: proactive Slack DMs to managers when a direct report's SPOF score crosses a threshold — not just in the weekly digest

**Long-term**

- Federated deployment: each business unit runs its own graph pipeline with aggregated cross-unit visibility at the executive tier
- Comparative benchmarks: anonymised cross-tenant percentile ranks so an org health score of 72 is interpretable against industry peers
- Active intervention tracking: close the loop by recording whether a suggested knowledge transfer plan was executed and whether it moved the SPOF score

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*
