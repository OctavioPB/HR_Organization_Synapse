# Data Protection Impact Assessment & Threat Model
## Organizational Synapse — People Analytics Platform

**Document version:** 1.0  
**Classification:** Confidential — Data Governance  
**Author:** OPB · Octavio Pérez Bravo · Data & AI Strategy Architect  
**Review cadence:** Annual, or after any material change to data processing scope  
**Frameworks:** GDPR Art. 35, ISO/IEC 27005, NIST Privacy Framework 1.0, ENISA Threat Landscape

---

## Executive Summary

Organizational Synapse processes collaboration metadata from employed individuals within a customer organization. Because the data subjects are employees — a population with a structurally asymmetric relationship to their employer — the sensitivity ceiling for this processing is higher than for consumer analytics at comparable data volumes.

This document answers three questions an enterprise buyer, a DPO, or an employment lawyer will ask before signing a contract:

1. **What exactly do you hold?** A precise inventory with retention, sensitivity, and lawful basis for each data category.
2. **What can go wrong?** A structured threat model covering re-identification, misuse, technical exploitation, and model-specific harms.
3. **What have you built to stop it?** A controls map that traces every identified threat to a technical or organizational countermeasure implemented in the codebase.

The conclusion is not "this processing is risk-free." It is: "the risks are known, bounded, and mitigated to a residual level that is proportionate to the legitimate organizational interest being served." That specificity is what separates a compliance posture from a compliance performance.

---

## 1. Processing Overview

### 1.1 Roles and Jurisdiction

| Role | Party | Scope |
|---|---|---|
| **Data Controller** | Customer organization (employer) | Determines the purposes and means of processing employee data |
| **Data Processor** | Org Synapse / OPB | Processes data on the controller's documented instructions |
| **Data Subjects** | Employees of the customer organization | Have full Art. 15–22 GDPR rights exercisable via the employer |
| **DPO** | Appointed by the controller | Consulted before activation; receives quarterly compliance reports |

**Territorial scope:** GDPR applies wherever the data subject is located in the EU/EEA, regardless of where the processor's infrastructure runs. UK GDPR applies equivalently post-Brexit. CCPA applies to California-based employees of US-domiciled controllers. The platform's compliance module is designed to satisfy both frameworks simultaneously (`graph/compliance.py`, `_DATA_CATEGORIES` audit catalogue).

### 1.2 Processing Purposes

| Purpose | Description | Lawful Basis |
|---|---|---|
| Organizational network analysis | Compute graph topology metrics to detect structural risk (SPOF, silos) | Legitimate interests — Art. 6(1)(f) |
| Churn risk modelling | Predict voluntary departure probability to enable proactive retention | Legitimate interests — Art. 6(1)(f) |
| Knowledge risk assessment | Identify knowledge concentration and succession gaps | Legitimate interests — Art. 6(1)(f) |
| Org health monitoring | Aggregate health score for executive decision-making | Legitimate interests — Art. 6(1)(f) |
| Equity analytics | Detect structural centrality disparities by demographic group | Legitimate interests — Art. 6(1)(f); legal obligation where applicable |
| Data subject rights fulfillment | Export, consent management, retention enforcement | Legal obligation — Art. 6(1)(c) |
| Consent audit trail | Immutable log of all consent changes | Legal obligation — Art. 6(1)(c) |
| Employee master record | Identity anchor for all graph computation | Contract — Art. 6(1)(b) |

**Legitimate interests balancing test (abbreviated):** The processing uses metadata only — no communication content is ingested or stored. The organizational benefits (reducing unplanned turnover, preventing knowledge loss, improving cross-team collaboration) are concrete and documentable. The processing does not extend to monitoring individual productivity, sentiment, or loyalty. Employees are informed of the processing before it begins, hold individual consent flags, and can exercise portability and erasure rights at any time. The interference with reasonable privacy expectations is material but bounded.

---

## 2. Data Inventory

The following tables reflect the `_DATA_CATEGORIES` catalogue in `graph/compliance.py` and the schema used throughout `api/db.py` and `data/migrations/`.

### 2.1 Personal Data Categories

| Table | Description | Personal? | Sensitivity | Retention | Lawful Basis |
|---|---|---|---|---|---|
| `raw_events` | Collaboration metadata: `{source_id}→{target_id}`, channel, direction, timestamp, weight | Yes | Medium | **90 days**, then purged | Legitimate interests |
| `graph_snapshots` | Daily per-employee centrality metrics: betweenness, degree\_in/out, clustering, community\_id | Yes | Medium | **365 days**, then purged | Legitimate interests |
| `risk_scores` | SPOF score, entropy trend, anomaly score, flag per employee | Yes | **High** | **365 days** | Legitimate interests |
| `churn_risk_scores` | Churn probability, risk tier, model version per employee | Yes | **High** | **365 days** | Legitimate interests |
| `employee_knowledge` | Document contribution counts, knowledge domain, last contribution date | Yes | Low | **365 days** | Legitimate interests |
| `employees` | Name, department, role, active status, consent flag, hire date | Yes | Medium | **Employment duration** | Contract |
| `consent_audit_log` | Every consent change: who changed it, from/to, reason, timestamp | Yes | Low | **3 years** | Legal obligation |

### 2.2 What Is NOT Collected

This distinction is architecturally enforced, not merely policy:

| Category | Status | Enforcement |
|---|---|---|
| Message content (Slack, Teams, email bodies) | **Never ingested** | Producers extract only event metadata from webhook payloads; no content fields are mapped |
| Email subjects or bodies | **Never ingested** | Not in `CollaborationEvent` schema |
| File names or document content | **Never ingested** | `employee_knowledge` stores counts and domains, not file references |
| Calendar event titles or notes | **Never ingested** | Only: inviter → invitee, timestamp |
| Meeting transcripts or recordings | **Never ingested** | No integration with transcription APIs |
| Demographic attributes (age, gender, ethnicity) | **Stored separately** in `employee_demographics`; **never used as model input** | `MODEL_CARD_CHURN.md §4`: "What is NOT in the model" |
| Compensation, performance ratings | **Never ingested** | Not in schema |
| Health, biometric, financial data | **Never ingested** | Not in schema |

The graph edges encode exactly: **`{who} → {whom}`, `{channel}`, `{timestamp}`, `{weight}`**.

### 2.3 Data Flow

```
Collaboration Tools (Slack, Teams, Jira, GitHub, Calendar, Confluence, Notion)
        │  metadata webhook events only
        ▼
[Kafka topic: collaboration.events.raw]
        │  consumer filters: consent=true AND active=true (applied at SQL write)
        ▼
[PostgreSQL: raw_events]  ──── 90-day rolling purge (Airflow: compliance_dag)
        │
        ▼
[Airflow ETL] ─── graph_builder_dag (daily)
        │               ├── graph_snapshots  ──── 365-day purge
        │               ├── silo_alerts
        │               └── risk_scores     ──── 365-day purge
        │
        ├──── knowledge_risk_dag ─── employee_knowledge
        ├──── churn_risk_dag    ─── churn_risk_scores
        └──── org_health_dag    ─── org_health_scores
                                         │
                                         ▼
                              [FastAPI: role-gated endpoints]
                                         │
                              hr_admin ──┤── numeric scores, full export
                              manager ───┤── traffic-light tiers only
                              executive ─┤── department aggregates only
                              analyst ───┘── anonymized graph topology only
```

---

## 3. Necessity and Proportionality

### 3.1 Data Minimization Assessment

For each data category, the question is: *could the analytical purpose be achieved with less?*

**`raw_events` (90-day window):** The 90-day retention window is the minimum required to compute meaningful graph topology. Shannon entropy trends require at least 4 weekly snapshots (28 days). SPOF scores require sufficient edge volume to produce stable betweenness estimates; NetworkX betweenness is unstable at fewer than ~20 interactions per node. Shorter retention would produce structurally degenerate graphs for low-volume communicators. Longer retention is not required; the platform uses rolling windows, not cumulative lifetime graphs.

**`graph_snapshots` (365 days):** The churn model requires at least 3 quarterly cohorts of labelled departure events to produce non-degenerate AUROC (`MODEL_CARD_CHURN.md §7.2`). Training on 6 months of snapshots with an annual retention policy is the minimum viable window. Temporal risk scores (entropy trends, betweenness deltas) also require year-over-year comparison to distinguish seasonal patterns from genuine disengagement.

**`risk_scores` and `churn_risk_scores` (365 days):** Retention enables retrospective calibration and model auditability. A score stored today must be traceable to the model version and feature values that produced it (`model_version` field, `MODEL_CARD_CHURN.md §10`). Shorter retention would break this audit chain.

**`employees` (employment duration):** The identity anchor cannot be purged while the employee is active; doing so would make it impossible to honor ongoing data subject rights requests.

### 3.2 Purpose Limitation Controls

Technical controls prevent secondary use beyond declared purposes:

- **No external model training:** Customer data never leaves the customer's own infrastructure; Org Synapse is deployed as an on-premises or single-tenant SaaS, not a shared multi-tenant model training pool.
- **No data brokering:** The platform exposes no bulk export API to third parties. All exports are audit-logged.
- **Role-based purpose segregation:** The API enforces purpose at the endpoint level — managers cannot access numerical risk scores even if they attempt to call the `hr_admin` endpoints. Purpose is enforced by code, not only by policy.

---

## 4. Threat Model

### 4.1 Scope

This threat model covers:

- **Re-identification and inference attacks** on the processed data
- **Misuse vectors** arising from the HR context (organizational power asymmetry)
- **Technical exploitation** of the platform's API and data stores
- **Model-specific harms** unique to graph-based people analytics

It does not cover infrastructure threats (DDoS, cloud provider breach) that are handled by deployment-level controls outside this codebase.

### 4.2 Adversary Profiles

| Adversary | Motivation | Capability |
|---|---|---|
| **Curious manager** | Wants to know which team members are flagged as high-risk | Low technical capability; has authenticated API access at manager tier |
| **Insider HR analyst** | Wants to use churn scores to justify a pre-decided termination | Authenticated; has `hr_admin` access to numerical scores |
| **External attacker** | Wants to exfiltrate personal data for sale or extortion | Unauthenticated; targets public-facing API |
| **Subpoena / legal discovery** | Employer's adversary (plaintiff's counsel, regulator) seeks to use scores as evidence | Legal process; has standing to compel disclosure |
| **Algorithmic harm via design** | The platform itself surfaces scores that cause discriminatory outcomes without any human attacker | Not an adversary — an emergent harm from model properties |

---

### 4.3 Re-identification Risk

#### T-REID-01: Graph Topology Re-identification

**Description:** Even without names, a graph of `{node_id} → {node_id}` edges is not anonymous. Backstrom et al. (2007) demonstrated that an attacker with partial knowledge of a social network's structure (knowing that two specific individuals are connected) can re-identify nodes in an anonymized graph with high accuracy. An employee who knows their own communication partners can, in many cases, uniquely identify their own node in the anonymized graph.

**Applicable data:** `raw_events`, `graph_snapshots`, any graph visualization in the dashboard.

**Risk level:** High for individuals with distinctive structural positions (high betweenness, unique cross-department bridges). Low for individuals in large homogeneous communities.

**Controls:**
- `raw_events` are never exposed to any authenticated user via the API. The graph is processed server-side; raw edge lists are never returned to frontend clients. (`api/routers/graph.py` returns computed metrics, not raw edges.)
- The anonymized topology view (`analyst` tier) returns only aggregate community structures, not individual node IDs.
- Dashboard resolves employee names only for `hr_admin` role — all other roles see anonymized identifiers or department labels.

**Residual risk:** An `hr_admin` user with access to both the org chart and the risk dashboard can trivially re-identify any node. This is **intended and disclosed**: the `hr_admin` role by definition processes personal data and must be subject to the controller's internal access governance.

---

#### T-REID-02: Temporal Linkability Attack

**Description:** The `graph_snapshots` table retains 365 days of per-employee metric history. An attacker with access to two snapshots 30 days apart can link them by employee UUID, reconstructing a trajectory. UUID persistence creates a pseudo-identifier that is stable across the retention window.

**Risk level:** Medium. The UUID is not directly linked to a name in the snapshots table; name resolution requires joining to `employees`. The join requires `hr_admin` access.

**Controls:**
- The `graph_snapshots` table does not contain name, department, or any HR attribute. It contains only the UUID and metric values.
- Role-based access prevents any non-`hr_admin` role from performing or seeing the name join.
- The 365-day retention limit prevents indefinite tracking.

**Residual risk:** Low. The UUID is a necessary technical identifier for the retention audit trail and model versioning. Replacing it with per-snapshot random IDs would break GDPR Article 15 (right to access all one's data) and the `data-export` endpoint.

---

#### T-REID-03: Singling Out via Extreme Metric Values

**Description:** GDPR Recital 26 identifies "singling out" — the ability to distinguish one individual from others in a group — as sufficient to constitute personal data processing, even without a name. An employee who is the sole `critical`-flagged person in a 5-person department has effectively been singled out, even if names are suppressed.

**Risk level:** High at department-aggregate resolution for small teams (< 10 members).

**Controls:**
- Executive and manager dashboards suppress individual scores when department size falls below a configurable `k-anonymity threshold` (default: minimum 3 individuals per aggregate cell). This is implemented in `api/routers/equity.py` and enforced at query time.
- Silo alerts reference community labels and department names, not individual employee names.
- The churn risk API for managers returns only `{high, medium, low}` tiers — not probabilities — and only when the at-risk count in a team exceeds 2, to prevent trivial singling out in micro-teams.

**Residual risk:** Singling out cannot be fully eliminated when the data subject is, by definition, the organization's most structurally unique person (e.g., the only cross-department bridge). The platform discloses this limitation and recommends that HR treat all individual scores as triage inputs subject to qualitative review, not conclusive findings.

---

#### T-REID-04: Inference Attack — Sensitive Attribute from Graph Position

**Description:** Network position correlates with demographic attributes in documented ways. Research on gender and organizational networks (Burt, 2019; Brands & Kilduff, 2014) shows that women in certain industries occupy lower betweenness centrality positions due to structural barriers, not personal preferences. An attacker who knows a company's gender distribution by department can probabilistically infer gender from graph position with above-chance accuracy.

**Risk level:** Medium. The inference is probabilistic, not deterministic.

**Controls:**
- The platform's equity analytics (`etl/tasks/compute_equity.py`) explicitly monitor for centrality disparities by demographic group and surface them as investigative flags. This turns a potential harm into a disclosed, monitored risk.
- The equity disparity flag (`EQUITY_DISPARITY_RATIO = 0.80`) alerts the organization when a group's structural position is disproportionately low, enabling intervention rather than passive perpetuation.
- Model inputs never include demographic attributes (`MODEL_CARD_CHURN.md §4`). The fact that position correlates with demographics is a structural reality; the model does not use demographics to predict position.

**Residual risk:** Medium. Inference attacks on graph structure are a fundamental limitation of network analytics. The platform cannot prevent an analyst from drawing inferences; it can only ensure the platform itself does not make those inferences mechanically.

---

### 4.4 Misuse Vectors

#### T-MISUSE-01: Score Creep — Using Churn Scores for Termination

**Description:** The churn score predicts voluntary departure probability. An HR manager who sees a high-probability employee may rationalize a pre-existing decision to terminate them ("if they're leaving anyway, let's act first"). This converts a retention tool into a dismissal enabler — a legally and ethically distinct purpose for which the model was not designed and for which it lacks the appropriate validation.

**Risk level:** High. This is the most probable misuse vector in practice. The incentive is clear, the technical barrier is low (the `hr_admin` user already has access to the score), and the harm is direct (wrongful dismissal exposure, discriminatory outcome if the score's demographic bias is uncorrected).

**Controls:**
- The `MODEL_CARD_CHURN.md §2` explicitly and verbatim prohibits this use: *"The churn score must not be an input to compensation, promotion, performance review, role assignment, or termination decisions."*
- The manager tier receives only a `{high, medium, low}` tier, not a numeric probability, and only receives it in the context of the `GET /manager/team/{id}/suggestions` endpoint which frames output as retention actions.
- The platform does not provide any endpoint that could be used to request "all employees with high churn risk" for bulk review. Scores are accessible per-employee by `hr_admin` only.
- **This control relies on organizational policy enforcement.** Technical controls reduce the surface area but cannot prevent a determined `hr_admin` user from manually aggregating individual scores. The customer's DPA must include an explicit contractual prohibition on this use.

**Residual risk:** Medium-High. This is the gap between what the platform prevents technically and what requires organizational governance.

---

#### T-MISUSE-02: Self-Fulfilling Prediction — Manager Behavioral Change

**Description:** If a manager learns that an employee is classified as high departure risk and changes their management behavior (fewer opportunities, reduced mentorship, less trust), the manager may cause the departure the model predicted. The model's score becomes a contributing cause of the outcome it was predicting.

**Risk level:** Medium. Documented in behavioral economics literature; the mechanism is plausible and the harm (accelerated attrition, discriminatory treatment) is real.

**Controls:**
- Managers receive the score as a **retention input**, framed explicitly as: "this employee may benefit from a 1:1 check-in." The suggested action is positive engagement, not reduced investment.
- The numeric probability is withheld from managers. A `high` tier does not convey the magnitude of risk (0.61 vs. 0.95 trigger the same label), reducing the manager's ability to rank-order employees for differential treatment.
- The platform generates **AI-suggested talking points for 1:1 conversations** (`api/routers/manager.py`) that frame the interaction as retention-oriented.

**Residual risk:** Low-Medium. The framing intervention reduces but cannot eliminate behavioral feedback loops.

---

#### T-MISUSE-03: Surveillance Drift — From Aggregate Risk to Individual Monitoring

**Description:** The platform is designed to surface organizational patterns (silos, knowledge concentration, fragmentation). A motivated user could attempt to use the platform as a surveillance tool — monitoring specific individuals' score trajectories over time to detect disengagement or political behavior.

**Risk level:** Medium. The technical capability exists in the data (365-day history, individual UUID resolution for `hr_admin`). The question is whether the access controls and UX prevent it from becoming a default use pattern.

**Controls:**
- The primary dashboard is **organization-level**, not individual-level. The default views show health scores, silo alerts, and risk distributions — not individual employee timelines.
- Individual employee detail views require deliberate navigation and are accessible only to `hr_admin`.
- The GDPR data export endpoint (`GET /compliance/data-export/{id}`) provides employees with the ability to see what has been recorded about them — including all stored risk scores and the dates they were computed. This transparency is a structural deterrent against covert surveillance.
- All admin-level data access is logged. An audit trail exists for every `hr_admin` call to individual employee endpoints.

**Residual risk:** Low. Surveillance drift requires deliberate misuse by an authenticated `hr_admin` user. The access control, transparency, and audit trail reduce both the practical ease and the legal cover for this use pattern.

---

#### T-MISUSE-04: Legal Discovery — Scores Used as Evidence in Employment Litigation

**Description:** An employee who has been terminated may, in the context of wrongful dismissal or discrimination litigation, subpoena the organization's Org Synapse data. The existence of a `critical`-flagged risk score predating the termination could be used as evidence that the termination was premeditated, score-driven, or discriminatory (if the score's demographic correlations are demonstrated).

**Risk level:** High. This is a legal risk, not a technical vulnerability. It requires no attacker; the platform's own data becomes adversarial in a litigation context.

**Controls:**
- Retention limits (365 days for scores) reduce the window of exposure.
- The platform's documented prohibitions on using scores for employment decisions (`MODEL_CARD_CHURN.md §2`, this DPIA §4.4.T-MISUSE-01) can be offered as affirmative evidence that the organization was on notice that such use was prohibited.
- The consent audit log provides a documented record that data was processed with consent and that consent withdrawals were honored.
- The customer organization's legal counsel should review whether scores constitute "employment records" under applicable law and whether they are subject to mandatory disclosure obligations that differ from GDPR requests.

**Residual risk:** Medium. Legal discovery risk cannot be fully eliminated through technical controls. It is managed by the combination of retention limits, documented prohibitions, and organizational governance.

---

### 4.5 Technical Exploitation

#### T-TECH-01: Unauthenticated API Access

**Description:** The FastAPI application exposes endpoints containing personal data. An unauthenticated request to `/risk/scores` or `/graph/employee/{id}` should return 401/403, but a misconfigured dependency injection or middleware bypass could expose data.

**Controls:**
- All personal-data endpoints use `Depends(get_db)` which enforces authentication before yielding a connection.
- The tenant middleware (`api/middleware/tenant_middleware.py`) validates tenant context on every request.
- The admin API (`POST /compliance/purge`) uses `Depends(get_admin_db)` which enforces both the `ADMIN_SECRET_KEY` header check and a tenant-level DB connection. Missing `ADMIN_SECRET_KEY` env var returns 503 (disabled), not 200. Wrong key returns 403.
- Integration tests verify auth behaviors (`tests/unit/test_compliance.py::TestPurgeEndpoint`).

**Residual risk:** Low.

---

#### T-TECH-02: Horizontal Privilege Escalation (Role Bypass)

**Description:** An authenticated manager-role user attempts to call `hr_admin`-restricted endpoints by manipulating request headers or guessing endpoint paths.

**Controls:**
- Role enforcement is implemented as FastAPI dependencies (`api/deps.py::require_role()`), not as application-layer if-statements. A request that does not carry the correct role header fails at dependency resolution before the route handler executes.
- The tenant middleware ensures cross-tenant data access is architecturally impossible: each tenant's data is schema-isolated in PostgreSQL.

**Residual risk:** Low.

---

#### T-TECH-03: SQL Injection via Dynamic Queries

**Description:** The compliance module uses f-string interpolation for table names in `_count_table` and `run_retention_purge` (lines 137, 166 in `graph/compliance.py`). If a table name were user-supplied, this would be a SQL injection vector.

**Assessment:** The table names are drawn exclusively from `_DATA_CATEGORIES`, an internal constant defined in the same file. They are never user-supplied. The `# noqa: S608` comment documents the intentional bandit suppression with the reason. This is a code review concern, not an active vulnerability.

**Controls:**
- Table names are compile-time constants in `_DATA_CATEGORIES`. No user input reaches the f-string.
- Bandit (SAST) suppression is documented per-line with the justification comment.
- All user-supplied values in parameterized queries use `%s` placeholders with psycopg2's parameterization — never string interpolation.

**Residual risk:** Very Low.

---

#### T-TECH-04: Bulk Data Exfiltration via GDPR Export Endpoint

**Description:** The `GET /compliance/data-export/{id}` endpoint returns a complete personal data package for a given employee. A malicious insider with `hr_admin` access could iterate over all employee UUIDs and exfiltrate the entire dataset.

**Controls:**
- The endpoint requires an authenticated session with `hr_admin` role. Each request is logged.
- Rate limiting is a deployment-level control (reverse proxy / API gateway); it is not implemented in-application but must be configured by the customer in production.
- The endpoint returns data for one employee per request — no bulk export route exists in the platform.

**Residual risk:** Medium for a determined insider with `hr_admin` access. This is an organizational governance gap, not a code gap. The customer must enforce the principle of least privilege in who receives `hr_admin` role assignment.

---

### 4.6 Model-Specific Harms

#### T-MODEL-01: Demographic Bias Amplification via Structural Features

**Description:** Network centrality features (betweenness, degree) reflect structural position. Structural position correlates with demographic group membership due to documented organizational barriers (see `MODEL_CARD_CHURN.md §8.1`). A model trained on centrality features may produce systematically different predictions for different demographic groups even with identical behavior, because their structural position differs.

**Risk level:** High. This is not hypothetical — it is the documented mechanism of algorithmic discrimination in network-based models.

**Controls:**
- The equity analytics module (`etl/tasks/compute_equity.py`) computes centrality distributions by `gender_group`, `tenure_band`, and `level_band` and flags disparities below a configurable threshold (`EQUITY_DISPARITY_RATIO = 0.80`).
- The model card (`MODEL_CARD_CHURN.md §8.3`) explicitly requires slice-level AUROC evaluation by demographic group before production deployment, with a 10pp deviation threshold for investigation.
- The platform prohibits using scores for employment decisions — the harm pathway requires a human to act on a biased score, providing an intervention point.

**Residual risk:** Medium until slice-level evaluation is completed on the customer's actual population. This is documented as a known limitation and deployment prerequisite.

---

#### T-MODEL-02: Cold Start Vulnerability — New Hires

**Description:** Employees with fewer than 30 days of activity history receive `0.0` for all 8 graph-derived features (`MODEL_CARD_CHURN.md §7.4`). Their churn score is driven entirely by HR features (tenure, role level, PTO). Scores computed during this window are structurally unreliable.

**Controls:**
- The platform filters scores by `MIN_HISTORY_DAYS` (configurable, default 30 days) before surfacing them in the manager dashboard.
- Scores with insufficient history are flagged in `churn_scores` with a `data_quality` field indicating the feature coverage.

**Residual risk:** Low if the configurable filter is applied. Medium if the customer bypasses the filter.

---

#### T-MODEL-03: Consent Opt-Out Selection Bias

**Description:** Employees who withdraw consent are excluded from all computation. If the decision to opt out correlates with disengagement (a plausible assumption), the training population is more engaged on average than the full workforce. The model is calibrated on a selection-biased sample (`MODEL_CARD_CHURN.md §7.6`).

**Risk level:** Medium. The direction of bias is documentable (the model underestimates risk for the consenting population relative to the full population), but the magnitude is unknowable without external ground truth.

**Controls:**
- The bias direction is documented. HR can apply a conservative upward adjustment to thresholds when the opt-out rate exceeds ~10%.
- The consent audit log records opt-out patterns over time, enabling detection of systematic opt-out (e.g., an entire team opting out following a management change).

**Residual risk:** Low-Medium. This is an irreducible consequence of consent-based data collection and is disclosed.

---

## 5. Risk Assessment Matrix

| ID | Threat | Likelihood | Impact | Inherent Risk | Controls | Residual Risk |
|---|---|---|---|---|---|---|
| T-REID-01 | Graph topology re-identification | Medium | High | **High** | No raw edge API exposure; role-gated name resolution | **Low** |
| T-REID-02 | Temporal linkability via UUID | Low | Medium | Medium | Role-gated joins; 365-day retention limit | **Low** |
| T-REID-03 | Singling out in small teams | High | High | **High** | k-anonymity suppression; tier-only for managers | **Medium** |
| T-REID-04 | Demographic inference from position | Medium | High | **High** | Equity analytics; no demographic model inputs | **Medium** |
| T-MISUSE-01 | Score creep → termination | High | **Critical** | **Critical** | Documented prohibition; tier abstraction; no bulk score API | **Medium-High** |
| T-MISUSE-02 | Self-fulfilling prediction | Medium | Medium | Medium | Retention framing; tier abstraction; 1:1 suggestions | **Low-Medium** |
| T-MISUSE-03 | Surveillance drift | Medium | High | **High** | Org-level default views; audit logging; employee transparency | **Low** |
| T-MISUSE-04 | Legal discovery of scores | High | High | **High** | Retention limits; documented prohibitions; consent audit trail | **Medium** |
| T-TECH-01 | Unauthenticated API access | Low | **Critical** | **High** | Auth dependencies; tenant middleware; tested | **Low** |
| T-TECH-02 | Horizontal privilege escalation | Low | High | Medium | Role enforcement as dependency injection | **Low** |
| T-TECH-03 | SQL injection via table names | Very Low | High | Medium | Internal constants only; parameterized queries for user input | **Very Low** |
| T-TECH-04 | Bulk exfiltration via export API | Medium | High | **High** | Audit logging; no bulk endpoint; rate limiting (deployment) | **Medium** |
| T-MODEL-01 | Demographic bias amplification | High | **Critical** | **Critical** | Equity monitoring; slice-eval requirement; employment decision prohibition | **Medium** |
| T-MODEL-02 | Cold start unreliable scores | High | Medium | **High** | History-length filter; data quality flag | **Low** |
| T-MODEL-03 | Consent opt-out selection bias | Medium | Medium | Medium | Bias documented; direction known; audit trail | **Low-Medium** |

**Residual risk summary:**

- **High residual (action required before deployment):** T-MISUSE-01 (score creep) and T-MODEL-01 (demographic bias) require active organizational governance and slice-level evaluation respectively. Neither can be fully resolved by technical controls in the platform alone.
- **Medium residual (accepted with documented controls):** T-REID-03, T-REID-04, T-MISUSE-04, T-TECH-04, T-MODEL-03. These are bounded, monitored, and proportionate to the legitimate interest being served.
- **Low / Very Low residual:** All technical exploitation threats and most re-identification threats.

---

## 6. Controls Inventory

### 6.1 Technical Controls

| Control | Implementation | Threats Mitigated |
|---|---|---|
| Consent-gate at SQL layer | `WHERE consent = true AND active = true` in all graph computation queries | T-MISUSE-03, T-MODEL-03 |
| Role-based API access | `api/deps.py::require_role()` FastAPI dependency | T-TECH-01, T-TECH-02 |
| Tenant isolation | `api/middleware/tenant_middleware.py` schema-per-tenant PostgreSQL | T-TECH-01, T-TECH-02 |
| Admin key gating | `get_admin_db()` — 503 when `ADMIN_SECRET_KEY` unset; 403 on wrong key | T-TECH-01 |
| No raw edge API | No endpoint exposes `raw_events` rows to clients | T-REID-01, T-MISUSE-03 |
| k-anonymity suppression | Aggregate suppression below configurable threshold | T-REID-03 |
| Tier abstraction for managers | `{high, medium, low}` only — no probabilities | T-MISUSE-01, T-MISUSE-02, T-REID-03 |
| Retention enforcement | Airflow `compliance_dag` — automated DELETE on cutoff dates | T-MISUSE-04 |
| Consent audit log | Immutable record of every consent change with actor, reason, timestamp | T-MISUSE-03, T-MISUSE-04 |
| GDPR export endpoint | `GET /compliance/data-export/{id}` — full portability package | Employee rights, T-MISUSE-03 deterrence |
| Data audit catalogue | `build_data_audit()` — live row counts and retention status | Regulatory transparency |
| Equity monitoring | `etl/tasks/compute_equity.py` — centrality disparity detection | T-MODEL-01, T-REID-04 |
| History-length filter | Scores suppressed for employees with < `MIN_HISTORY_DAYS` activity | T-MODEL-02 |
| Parameterized SQL | `%s` placeholders for all user-supplied values | T-TECH-03 |
| Audit logging | All `hr_admin` access to individual scores logged | T-TECH-04, T-MISUSE-03 |

### 6.2 Organizational Controls (Required of the Controller)

These controls are outside the platform codebase. They are required as a condition of lawful processing.

| Control | Requirement | Threat Mitigated |
|---|---|---|
| DPA clause — no employment decisions | The customer's DPA must explicitly prohibit using any platform score in compensation, promotion, or termination decisions | T-MISUSE-01 |
| `hr_admin` role governance | The customer must designate and document who holds the `hr_admin` role; minimum-privilege access | T-TECH-04, T-MISUSE-03 |
| Employee transparency notice | Employees must be informed of the processing in the HRIS privacy notice before any data ingestion begins | GDPR Art. 13/14 |
| Slice-level model evaluation | Before production deployment, the customer must evaluate churn model performance by demographic group and investigate disparities > 10pp AUROC | T-MODEL-01 |
| Quarterly equity review | `GET /equity/centrality-distribution` results reviewed quarterly by HR + Legal | T-MODEL-01, T-REID-04 |
| Incident response plan | The customer must maintain a plan for responding to data subject rights requests, data breaches, and litigation discovery | T-MISUSE-04 |
| Rate limiting on export endpoint | The customer's infrastructure must configure rate limiting on `GET /compliance/data-export/{id}` to prevent automated iteration | T-TECH-04 |

---

## 7. GDPR Data Subject Rights Implementation

| Right | Article | Mechanism | Status |
|---|---|---|---|
| Right to access | Art. 15 | `GET /compliance/data-export/{id}` returns full personal data package | Implemented |
| Right to data portability | Art. 20 | Same endpoint; JSON format, machine-readable | Implemented |
| Right to withdraw consent | Art. 7(3) | `PATCH /compliance/consent/{id}` — removes from all future computation | Implemented |
| Right to erasure ("right to be forgotten") | Art. 17 | `run_retention_purge()` enforces deletion; consent withdrawal removes from active computation | Partial — automated purge implements systematic erasure; individual erasure request requires manual operation (known gap) |
| Right to rectification | Art. 16 | No mechanism for employee to correct score — **known gap**; scores are model outputs, not factual records | Not implemented — documented limitation |
| Right to object | Art. 21 | Consent withdrawal is the functional equivalent for this processing | Implemented via consent mechanism |
| Right not to be subject to solely automated decision-making | Art. 22 | All scores are triage inputs; all decisions require human review — **enforced by policy, not technical control** | Partial — policy-level only |

---

## 8. Special Considerations for US Employers

For customers domiciled in the United States, the following additional regulatory contexts apply:

**CCPA / CPRA (California):** Employees in California are covered as of the 2023 CPRA expansion. The platform's export endpoint and consent mechanism satisfy CPRA's access and opt-out requirements. The "sensitive personal information" provisions of CPRA may apply to inferred sensitive attributes (§4.3.T-REID-04).

**EEOC implications:** The equity disparity flag is modeled by analogy on the EEOC four-fifths rule (29 C.F.R. § 1607.4D). As documented in `MODEL_CARD_CHURN.md §8.2` and `planning/MODEL.md §11`, this is an **investigative heuristic**, not a legal standard or finding. A flag does not establish that a protected class has experienced adverse impact under Title VII. Legal counsel should be consulted before any equity disparity finding is used in an employment context.

**NLRA (National Labor Relations Act):** Monitoring of collaboration patterns between employees could, in some interpretations, constitute surveillance of protected concerted activity (e.g., employees discussing working conditions via Slack). The platform's metadata-only processing reduces but does not eliminate this risk. Legal counsel should assess whether the processing description satisfies the NLRA's constraints in the customer's jurisdiction and industry.

**State biometric laws (Illinois BIPA, Texas, Washington):** The platform does not collect biometric data. Collaboration metadata, graph centrality scores, and departure risk predictions do not constitute biometric information under any current state biometric privacy statute.

---

## 9. DPIA Conclusions and Sign-off

### 9.1 Is this processing high-risk under GDPR Art. 35?

The GDPR Article 35 DPIA trigger checklist:

| Criterion | Applies? | Basis |
|---|---|---|
| Systematic and extensive evaluation of personal aspects using automated processing | **Yes** | Risk scoring of employees is systematic, extensive, and automated |
| Processing produces decisions that produce legal or similarly significant effects | **Yes — risk** | Scores could influence employment decisions if misused; mitigated but not eliminated |
| Large-scale processing of sensitive data | No (for most deployments) | Collaboration metadata is not a special category under Art. 9 |
| Systematic monitoring of a publicly accessible area | No | Internal corporate environment |
| Use of new technologies | **Yes** | Graph neural networks and network analytics are not yet standardized in HR |
| Combination of datasets beyond reasonable expectation of data subject | **Yes** | Employees may not expect their communication metadata to generate departure risk scores |

**Conclusion:** A DPIA is required under GDPR Art. 35 before deployment. This document constitutes that DPIA.

### 9.2 Findings

1. The processing is **necessary** for the declared purposes; no less privacy-invasive alternative achieves equivalent organizational value.
2. The processing is **proportionate**: the data minimization (metadata only, no content), retention limits, and role-based access controls bound the interference to what is required.
3. **Two residual risks require organizational action before deployment:**
   - T-MISUSE-01: The customer must contractually prohibit use of scores in employment decisions.
   - T-MODEL-01: Slice-level demographic evaluation must be completed before production scoring begins.
4. All other identified risks are mitigated to a level proportionate to the legitimate interest, are disclosed to data subjects via the privacy notice, and are subject to ongoing monitoring.

### 9.3 DPO Consultation

Per GDPR Art. 35(2), the controller's DPO must be consulted before this processing begins. This document should be provided to the DPO as the basis for that consultation. The DPO's conclusions should be recorded in the organization's DPIA register.

### 9.4 Review Triggers

This DPIA must be reviewed and re-approved when any of the following occurs:

- A new data source is onboarded (new collaboration platform ingested)
- The churn model is updated with new features or architecture
- A new output type (new type of score or report) is added
- The customer organization undergoes a merger, acquisition, or significant headcount change
- A data breach or suspected misuse incident occurs
- Applicable law changes in a jurisdiction where the customer operates
- Twelve months have elapsed since the last review

---

## 10. Why This Document Is a Competitive Asset

People analytics platforms fail in enterprise sales in one of three ways: the procurement security review kills the deal, the legal team's privacy counsel finds no DPA-ready documentation, or an employment lawyer's question about algorithmic employment decisions creates a delay that outlasts the champion's tenure.

This document is written to preempt all three.

The difference between a platform that can answer "what data do you hold and what can go wrong with it" and one that cannot is not just regulatory compliance. It is the difference between a vendor that has **thought through the deployment implications** and one that has shifted the entire governance burden onto the customer. Enterprise HR buyers with any legal sophistication have started to recognize that difference, because they are the ones who bear the liability when it goes wrong.

The controls described in this document are not check-box compliance. They are design decisions made at the architecture level:

- Consent is enforced at the SQL `WHERE` clause, not at an application `if` statement that can be bypassed.
- Manager-tier outputs are architecturally incapable of returning numeric probabilities — the endpoint does not exist.
- The equity monitoring module surfaces the bias risk that every people analytics platform has but none document.
- The retention purge runs as a scheduled DAG, not as a manual process that can be skipped.
- The 100%-covered compliance module (`graph/compliance.py`) is tested at the exception branch level because a failed purge log entry is a compliance audit finding.

The goal is not to appear compliant. The goal is to make the product **genuinely harder to misuse than to use correctly** — and to be able to prove that in a vendor assessment, a procurement review, or a courtroom.

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*  
*Cross-reference: `MODEL_CARD_CHURN.md`, `graph/compliance.py`, `planning/MODEL.md §11`, `api/deps.py`, `etl/tasks/compute_equity.py`*  
*Regulatory references: GDPR Art. 6, 13, 15, 17, 20, 22, 35; CCPA/CPRA; EEOC 29 C.F.R. § 1607.4D; NLRA*  
*Academic references: Backstrom et al. (2007); Burt (2019); Brands & Kilduff (2014); Yang et al. (2022)*
