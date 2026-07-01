# Organizational Synapse
### A People Analytics Platform for HR Leaders Who Need to See Around Corners

---

## The Problem You Already Know

Every HR team has experienced this: a senior engineer, a project manager, or a sales operations analyst submits their resignation — and the organization only then discovers how much invisible structural weight that person was carrying. Three departments lose their primary communication bridge. A critical compliance process belongs entirely to one person's memory. Two teams that had been coordinating smoothly fall silent.

The SHRM estimates replacing a mid-level employee costs 50–200% of their annual salary. For an employee who is an informal network hub — one whose relationships and institutional knowledge are not documented anywhere — the real cost is higher because the network itself cannot be transferred. It must be rebuilt from scratch, typically over 6–18 months.

**HR functions have historically had no systematic way to identify these employees before they leave.**

Exit interviews happen after the decision is made. Engagement surveys are self-reported, quarterly at best, and cannot distinguish an employee who is genuinely engaged from one who is professionally performing while privately job-hunting. Performance reviews measure individual output, not structural contribution to organizational connectivity. The 9-box grid identifies leadership potential, not the staff engineer who is the only bridge between Engineering and Compliance.

---

## The Signal That Already Exists in Your Organization

Every modern organization continuously generates collaboration metadata: who messages whom on Slack, who assigns tickets to whom in Jira, who reviews whose code on GitHub, who invites whom to calendar events. This is not message content — it is the *structure* of interactions, the pattern of edges in a network of human relationships.

This signal is sufficient to answer questions your current tools cannot:

- Which employees sit on the most communication paths between other employees?
- Which departments have quietly stopped talking to each other?
- Which employees are progressively narrowing their interaction surface — a behavioral withdrawal signal that precedes resignation by 4–8 weeks?
- Which employees are the sole documented experts in a knowledge domain?

This signal exists. It is generated every day. It has never been packaged into an HR-accessible, privacy-respecting analytical system — until now.

---

## What Organizational Synapse Does

Organizational Synapse ingests collaboration metadata from up to six sources simultaneously — Slack, Microsoft Teams, Jira, GitHub, Confluence, and Notion — and constructs a live network model of your organization. Every node is an employee. Every edge is a pattern of interaction, weighted by frequency and channel depth. The graph is recomputed every day at 02:00 UTC.

No message content is ever ingested. The system is architecturally incapable of storing what was said. Only the structure of who interacted with whom, and how often, enters the model.

From this daily graph, the platform computes four classes of intelligence:

---

### 1. Structural Risk: Who Is Your Organization Most Dependent On?

Each employee receives a daily **SPOF (Single Point of Failure) Score** between 0 and 1, derived from four observable behavioral properties:

| Component | Weight | What It Measures |
|---|---|---|
| **Network centrality** | 40% | How often this person sits on the shortest communication path between other employees — how many structural holes they bridge |
| **Cross-department ratio** | 30% | What fraction of their interactions cross departmental boundaries — the hallmark of an irreplaceable connector |
| **Neighbor self-sufficiency** | 20% | How much their direct collaborators depend on them specifically, rather than being connected to each other |
| **Engagement trend** | 10% | Whether their interaction diversity is growing or contracting over the past 30 days |

An employee with a SPOF score above **0.7** is flagged **critical** — their unplanned departure would create immediate, measurable disruption to organizational connectivity. A score of 0.5–0.7 is a **warning**: cross-training investment is warranted now, before risk becomes urgency.

The model also applies a sensitivity check: an employee is only designated `robust_critical` if they exceed the threshold across multiple statistical weight configurations, not just the central estimate. This prevents acting on borderline classifications.

**In plain terms:** The platform tells you which employees are structurally irreplaceable — not which employees have the most impressive job titles.

---

### 2. Early Departure Detection: 4–8 Weeks Before a Resignation Is Submitted

Research in organizational behavior (Lee & Mitchell's Unfolding Model of Voluntary Turnover, 1994) shows that the most common departure pathway for high-value employees is not a sudden shock — it is progressive withdrawal. Before submitting a resignation, an employee typically narrows their collaboration network: fewer voluntary contributions to cross-team conversations, fewer responses to after-hours messages, fewer new connections initiated.

The platform captures this through **Shannon entropy** — a measure of how diverse and unpredictable an employee's communication pattern is. A declining entropy slope (more than -0.05 per week over a 30-day window) is the earliest behavioral signal of disengagement. It appears in the data before it appears in survey responses, 1-on-1 conversations, or manager intuition.

A **Graph Attention Network** (a machine learning model that evaluates each employee in the context of their collaboration neighborhood, not in isolation) produces a 90-day departure probability for every active employee. The model trains weekly and scores daily. Critically, it captures network contagion: employees whose direct collaborators have recently departed are at elevated churn risk, because the perceived cost of leaving decreases when their relational anchor points have already gone.

**The output is three risk tiers:**

- **High churn risk (>0.7):** Simultaneous retention effort and knowledge transfer preparation. Decision is likely forming.
- **Medium churn risk (0.4–0.7):** Highest-value intervention window. Targeted 1-on-1 investment has the highest return here.
- **Low churn risk (<0.4) + high SPOF:** No departure risk, but structural dependency risk. Cross-training, not retention.

---

### 3. Succession Planning: Structural Compatibility, Not Just Leadership Potential

Traditional succession planning identifies candidates based on manager assessment and the 9-box grid. This approach has a structural blind spot: it measures the individual in isolation, not the individual in context.

The platform's succession module identifies, for every employee with a SPOF score above 0.3, up to five internal candidates who could absorb their structural role. Candidates are ranked by:

| Dimension | Weight | Meaning |
|---|---|---|
| **Network overlap** | 40% | How much of the at-risk employee's collaboration network the candidate already shares — the harder gap to close |
| **Knowledge domain coverage** | 35% | How many of the at-risk employee's documented expertise areas the candidate already covers |
| **Community embeddedness** | 25% | How embedded the candidate already is in the relevant organizational community |

The result is not a hiring recommendation. It is a **prioritized cross-training roadmap**: where to invest in relationship-building and knowledge transfer *before* a critical departure occurs. For each top succession candidate, the platform generates a 90-day transfer plan — relationship introductions, document review, and structural shadowing — trackable in the dashboard and exportable to Jira or Asana.

Industry research (Gartner, CEB) consistently shows that internally sourced successors perform better in their first 18 months and cost approximately 50% less to onboard into structural roles compared to external hires who must rebuild the departing employee's network from scratch.

---

### 4. Organizational Health: A Weekly Score for the Whole

Every Sunday, the platform computes an **Org Health Score** from 0 to 100, composed of four structural indicators:

| Component | Weight | What It Detects |
|---|---|---|
| **SPOF concentration** | 35% | How much structural load the organization is placing on a small number of people |
| **Network fragmentation** | 25% | Whether any part of the organization is structurally disconnected from the rest — the most acute form of organizational risk |
| **Entropy risk** | 20% | The fraction of employees showing behavioral withdrawal signals |
| **Silo risk** | 20% | Whether any department's internal communication volume is disproportionately exceeding its external communication (scaled to your organization's structure, not a fixed threshold) |

**Score tiers:** Healthy (≥80) · Caution (60–79) · At-Risk (40–59) · Critical (<40)

Every Monday morning, this score — along with the week's top three risk signals and an AI-generated recommended action — is delivered to configured recipients by email and Slack, before the first meeting of the week. No dashboard login required.

---

## What You Can Do With It

The platform addresses ten specific workflows that HR leaders currently have no systematic tool for:

1. **Know who is critical before they leave, not after.** SPOF risk scoring identifies structural dependencies daily.
2. **Detect disengagement before a resignation is submitted.** Entropy trend alerts fire 4–8 weeks ahead of formal departure.
3. **Make a structurally defensible restructuring decision.** The What-If Simulator removes any employee from the model and shows the impact: how many isolated clusters would form, what percentage of cross-department paths would be severed, by how much average communication distance would increase.
4. **Know where knowledge is concentrated and undocumented.** The Knowledge Risk Score tracks sole-expert concentration — employees who are the only documented source for a specific domain.
5. **Demonstrate responsible data practices to regulators.** GDPR Article 20 data export, consent management, and quarterly compliance reports are built into the platform.
6. **Prove that departures were predicted and quantify their actual impact.** The Departure Impact Report compares the employee's pre-departure risk score against post-departure graph changes (diameter increase, new silos, recovery trajectory) — a board-ready artifact that closes the prediction loop.
7. **Know whether new hires are connecting into the network at the expected rate.** The Onboarding Integration Tracker alerts when a new hire is in the bottom quartile of their tenure cohort at day 60.
8. **Form project teams that will actually work.** The Team Composition Optimizer returns ranked team options scored by bridge coverage, domain coverage, structural load, and existing relationship density.
9. **Know whether structural positions are equitably distributed.** The DEI Structural Equity module surfaces whether employees from different demographic groups occupy systematically different positions in the collaboration network — a dimension that headcount representation metrics cannot reach.
10. **Stay informed without logging into a dashboard.** The weekly digest delivers the Org Health Score and recommended action before Monday standup.

---

## Privacy and Compliance: Built Into the Architecture, Not Added as a Feature

The platform was designed from the ground up for organizations operating under GDPR and equivalent regulations.

**Metadata only.** The event schema is architecturally incapable of holding message content, subject lines, or file contents. The constraint is enforced at the data model level, not by policy.

**Consent gate.** Only employees who have actively consented are included in graph computation. Consent is filtered at the database query level — non-consenting employees' data never enters Python memory, never influences centrality scores, and never appears in any output. The system does not exclude them after loading; it never loads them.

**Role-based access.** Individual SPOF scores and churn probabilities are visible only to users with `hr_admin` access. Executives see department-level aggregates — elevated risk in Engineering, not John Smith's specific score. Line managers see a traffic-light status (green/amber/red) and AI-generated conversation suggestions, with no exposure to numeric scores. This is not a UX decision; it is a regulatory constraint.

**GDPR compliance infrastructure.** The platform provides automated data export for Article 20 (data portability), data retention purge with audit trail for Article 17 (right to erasure), and a data audit endpoint that produces the inputs required for a Data Protection Impact Assessment (Article 35).

**EU AI Act alignment.** The churn risk model, SPOF score, and succession recommendations are all classified as inputs to HR decisions under the EU AI Act's high-risk category. The platform's human-in-the-loop design (all outputs are recommendations requiring human review, never automated decisions), consent mechanism, model version and accuracy transparency, and role-based access control are compliance architecture, not retrofitted features.

---

## The Technology Stack (For Your IT and Security Teams)

The platform is built entirely on open-source, auditable components:

| Layer | Technology | Why |
|---|---|---|
| **Event streaming** | Apache Kafka | Durable, replayable event log; survives consumer downtime without data loss |
| **Primary database** | PostgreSQL 15 | Schema-per-tenant isolation; no cross-tenant data leakage possible |
| **Graph engine** | NetworkX + Neo4j | In-memory metric computation; optional graph traversal acceleration |
| **Caching** | Redis 7 | Sub-millisecond API response for cached graph snapshots |
| **Pipeline orchestration** | Apache Airflow | Visible DAG execution history; cross-pipeline dependencies enforced |
| **ML framework** | PyTorch Geometric | Graph Attention Network for churn prediction |
| **API** | FastAPI + Python 3.11 | Type-safe, async, OpenAPI-documented |
| **Dashboard** | React 18 + Vite + Sigma.js (WebGL) | 60 fps graph rendering; handles 500+ node graphs without DOM performance degradation |
| **Observability** | Prometheus + Grafana | Request latency p95/p99, error rates, and pipeline health in a pre-built dashboard |

**API keys are stored as SHA-256 hashes** — a database breach exposes no usable credentials. **Multi-tenancy is enforced at the schema level** — your organization's data is in an isolated PostgreSQL schema; a misconfiguration cannot leak it to another tenant. The system connects to your HRIS (Workday, BambooHR) via OAuth to enrich churn model features with tenure, promotion recency, and compensation band data.

---

## Deployment and Adoption Path

| Tier | Employees | Events/Month | Typical Buyer |
|---|---|---|---|
| **Free** | Up to 50 | 10,000 | Department-level proof of concept |
| **Starter** | Up to 200 | 100,000 | Single-site or small company deployment |
| **Pro** | Up to 1,000 | 1,000,000 | Mid-market full organizational coverage |
| **Enterprise** | Unlimited | Unlimited | Large enterprise with custom support and SLA |

A Free deployment is production-ready for a 50-person team. Connecting your first data source and generating your first graph snapshot with risk scores takes under 30 minutes. No professional services engagement required to start.

---

## The Business Argument in Three Sentences

**Organizations have a structural risk problem that is invisible to them.** The employees most critical to organizational continuity are often not the most senior, and they are almost never identified through formal HR processes. When they leave, the disruption is disproportionate, expensive, and entirely predictable in retrospect — because the signal was there all along.

**Organizational Synapse makes that signal visible, daily, in the tools your HR team already uses.**

---

## For Your First Conversation

When you evaluate this platform, the first thing to generate is not a report — it is a **What-If simulation**. Take any employee from your mental list of "people we can't afford to lose." Submit their ID. See by how much cross-department communication would contract if they left tomorrow.

If the number surprises you, that is the problem the platform solves.

---

*Organizational Synapse · Passive Organizational Network Analysis · B2B SaaS · [Free tier available for proof of concept]*
