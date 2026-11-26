# Lessons Learned — Organizational Synapse

What building this platform taught us, in modeling, engineering, product, and process. Each lesson names the evidence and what changed (or still needs to change) because of it. The newest items come from a full review in November 2026, when these docs were written and the screenshots were captured against a live, seeded instance.

Status: **✅ applied** · **🟡 partially applied** · **🔴 open**

---

## 1. Modeling

### 1.1 Normalize before you weight ✅
**What happened:** The v1 SPOF score combined raw betweenness, cross-department ratio, clustering, and entropy slope linearly. Betweenness is extremely right-skewed, so it dominated the score whatever weight it was given. α = 0.4 was really closer to 0.9.
**Lesson:** In a linear composite, nominal weights mean nothing unless the inputs share a scale. Rank-percentile each component first.
**Changed:** [MODEL.md §5.1](MODEL.md#51-full-formula), [`graph/risk_scorer.py`](../graph/risk_scorer.py).

### 1.2 A threshold on an uncalibrated score needs an uncertainty band ✅
**What happened:** The weight ordering (α > β > γ > δ) is defensible; the exact magnitudes are not. Some employees crossed 0.7 only under the central weights.
**Lesson:** When a score can trigger a personnel conversation, report whether the classification survives reasonable perturbations, not just the point estimate.
**Changed:** `critical` now means critical under every weight set; `critical_uncertain` requires human review and does **not** auto-alert.

### 1.3 Accumulated weights encode tenure, not fit ✅
**What happened:** Succession similarity used raw edge weights, which grow with time at the company. Senior people looked like better successors because their numbers were bigger.
**Lesson:** Compare interaction *shares*, not totals, whenever populations differ in exposure time.
**Changed:** Normalized weighted Jaccard ([MODEL.md §8.1](MODEL.md#81-weighted-jaccard--corrected)).

### 1.4 Community IDs are labels, not identities ✅
**What happened:** Onboarding "community stability" compared Louvain IDs week over week. Louvain is non-deterministic and its labels are arbitrary, so the metric was mostly noise.
**Lesson:** To compare clusterings across runs, compare membership sets.
**Changed:** `CS_overlap` = Jaccard of community members ([MODEL.md §9.1](MODEL.md#91-community-stability--corrected)).

### 1.5 Detect silos on the unit people can act on ✅
**What happened:** Louvain sometimes merged a siloed department into a hub's community, so the silo disappeared.
**Lesson:** Algorithmic communities are good for discovery. Alerts should run on the organizational unit someone owns (department).
**Changed:** [`graph/silo_detector.py`](../graph/silo_detector.py) groups by department. *Follow-on:* see 3.3.

### 1.6 Make risk scale with the organization, and make the first break hurt ✅
**What happened:** Silo risk had a fixed denominator of 10 silos, and fragmentation risk was linear in components ÷ n. In a 200-person org, splitting into two disconnected halves scored 0.005.
**Lesson:** Pick functional forms that match the severity curve: relative denominators, and a convex penalty for the first disconnection.
**Changed:** `max(⌊d/3⌋, 2)` silo denominator, `1 − e^(−1.5·(WCC−1))` fragmentation ([MODEL.md §12](MODEL.md#12-org-health-score)).

### 1.7 The fancy model lost, so ship the baseline ✅
**What happened:** At N = 300 with 30 positive labels, a 3-feature logistic regression beat the graph model by 10.6 pp AUROC.
**Lesson:** Graph contagion signals need data volume to show up. Publish the comparison and deploy what wins today. The GNN stays as an upgrade path, gated at roughly N > 500 labeled across 3 or more cohorts.
**Changed:** README and [MODEL_CARD_CHURN.md](MODEL_CARD_CHURN.md) state it plainly.

### 1.8 Leading indicators look wrong if you score them as damage meters ✅
**What happened:** The withdrawing archetype ranked #1 on SPOF but #3 on removal damage. It fires about 15 days before departure, and by then the graph has partly adapted.
**Lesson:** Validate each signal against the question it answers. SPOF answers "who should we worry about", not "how much will it hurt today".

### 1.9 Some risks need their own score ✅
**What happened:** Sole experts with no cross-department ties maxed out at SPOF 0.63, below critical.
**Lesson:** Don't stretch one formula to cover two mechanisms. Knowledge risk is separate and gets blended in deliberately (`SPOF⁺`).

### 1.10 Add new signals beside the model, not inside it ✅
**What happened:** The literature shows turnover contagion (peers leaving) is a strong predictor, and the GAT had no such feature.
**Lesson:** A rule layer (`peer_churn_rate > 0.30`) ships the signal immediately without invalidating trained checkpoints.

### 1.11 Borrowed legal thresholds aren't legal thresholds ✅
**What happened:** The equity module cited the EEOC 4/5ths rule for its 0.8 cutoff on centrality.
**Lesson:** Centrality isn't a selection rate. Call the cutoff an investigation trigger in code, docs, and UI so nobody reads it as a legal finding.

### 1.12 Don't average a rank ✅
**What happened:** Org health's largest component (35 %) was mean SPOF. SPOF is a weighted sum of rank percentiles, and the mean percentile of any population is ≈0.5, so mean SPOF ≈ 0.4 for every organization. On the demo data it moved 0.427–0.433 across eight weeks, and with the other components saturated, the health score sat at 64.9 ± 0.1.
**Lesson:** Rank transforms are good for combining individual signals and useless for comparing populations. Organization-level metrics need absolute, graph-level quantities.
**Changed:** spof_risk is now brokerage concentration, the share of all betweenness held by the top 5 %. The score now spans 63.8–75.7 over the same weeks.

### 1.13 Every "share" needs a baseline ✅
**What happened:** Silo detection used internal ÷ outgoing edges with one threshold. Under random mixing that ratio is (n−1)/(N−n), about 1.0 for a department holding half the org and 0.2 for a small one. On the demo data the largest department ranked most siloed and the most inward one, relative to its size, ranked least.
**Lesson:** Before thresholding a proportion, ask what it would be by chance. Use a null-model-corrected index (here Coleman homophily) so one threshold means the same thing for every group.
**Changed:** department silos use H = (p − q)/(1 − q). A follow-on finding: p has to be measured on interaction *volume*, because unique edges saturate inside small departments and break the random baseline.

### 1.14 Fix the estimator before the threshold ✅
**What happened:** Fixing the entropy SQL produced real slopes for the first time. A 4-point OLS slope is noisy: the 10th–90th percentile range is −0.48 to +0.36 bits/week. The old "any negative slope" rule flagged 51 % of *stable* simulated employees as withdrawing, and 53 of 118 on the demo data.
**Lesson:** A threshold is only as meaningful as the estimate it cuts. Make the flag a statement of confidence ("P(slope < −0.05) ≥ 0.9"), not a cut on a noisy number.
**Changed:** Theil-Sen slope + empirical-Bayes shrinkage + probabilistic flag (MATH_MODEL §4.1). Slope RMSE fell 53 % and false positives went from 51 % to 2.5 %. Sudden drops get a separate change-point alert (§4.2).

### 1.16 Validate the pieces of an estimator on the data you'll run it on ✅
**What happened:** The first shrinkage version passed its own simulation, then collapsed on the demo database: every slope was shrunk onto the median and nobody could ever be flagged. Two causes. The per-week variance formula over-predicts true noise by 1.5–14× in isolation (the prototype used the same formula, so the simulation couldn't see it). And the method-of-moments τ² estimate breaks when noise varies by orders of magnitude between employees.
**Lesson:** Check calibration of each component (predicted vs. empirical variance) separately, and run on real data before trusting a simulation that shares the model's assumptions. For heterogeneous known variances, use the meta-analysis estimator built for it (DerSimonian–Laird).
**Changed:** DL τ², plus a 4-variant bake-off across 3 seeds to pick the variance formula. A regression test covers the collapse.

### 1.15 A "fix" that matches the spec can still be wrong 🟡
**What happened:** Decisions.md says betweenness should be undirected, and the code was directed. Making it undirected halved validation Precision@critical (100 % → 50 %). Undirected betweenness is tiny but nonzero for nearly everyone, and the rank-percentile step stretches those negligible differences into the full 0–1 range.
**Lesson:** Run the validation suite on every spec reconciliation, and expect two documented choices to interact. Directed betweenness stays until the BC term gets a tail-aware normalization.

---

## 2. Engineering

### 2.1 Unbounded dependency ranges break silently ✅
**What happened:** `requirements*.txt` set only lower bounds (`fastapi>=0.110.0`, `prometheus-fastapi-instrumentator>=6.1.0`). In November 2026 a fresh environment resolved FastAPI 0.165 with instrumentator 7.1, and **every HTTP request returned 500** (`'_IncludedRouter' object has no attribute 'path'`). The same clash fails most API unit tests locally. CI installs the same ranges, so it is likely affected too.
**Lesson:** Pin upper bounds or use a lock file for anything that touches framework internals. A soft import only protects you from a missing package, not a broken one. Smoke-test a *routed* endpoint: `/` and `/health` are app-level routes and passed even on the broken install.
**Changed:** instrumentator `>=8.1.0,<9`, FastAPI capped at the newest version tested (`<0.166`), and [`tests/unit/test_app_smoke.py`](../tests/unit/test_app_smoke.py) boots the real app with instrumentation and calls an `include_router` endpoint. That test fails on 7.1 and passes on 8.1.

### 2.2 Tenant DDL drifted from migrations ✅
**What happened:** New tenant schemas are created from an inline `_TENANT_TABLES_SQL` constant in [`api/tenant.py`](../api/tenant.py), not from `data/migrations/`. That constant defines 11 tables. It has none of migration 017's columns (`spof_score_lo`, `spof_score_hi`, `weight_robust`), none of the tables added by later features (onboarding scores, reorg scenarios, transfer plans, demographics and equity, departure reports, digest config), and it names the churn table `churn_risk_scores` where the README says `churn_scores`. A freshly provisioned tenant can run only the original core pipeline.
This was worse than missing features. The request-time `search_path` is `tenant, public`, so a query against a table missing from the tenant schema silently read the **public** (default-org) table.
**Lesson:** One schema, one source of truth. Provision tenants by running the migration set inside the new schema.
**Changed:** `apply_tenant_migrations()` in [`api/tenant.py`](../api/tenant.py) runs `data/migrations/*.sql` (except the control-plane files 000 and 008) with `search_path` set to the tenant schema only. Against Postgres, the resulting schema matched the default schema (23 tables, every column) and a second run was a no-op. [`scripts/migrate_tenants.py`](../scripts/migrate_tenants.py) repairs tenants provisioned before the fix.

### 2.3 Know how your DB driver shapes rows ✅
**What happened:** `build_graph()` unpacked rows as tuples. With psycopg2's `RealDictRow`, unpacking yields the *column names*, and the edge weight silently became the string `'weight'`.
**Lesson:** Accept both shapes explicitly, or standardize one cursor factory per layer. Silent type coercion is worse than a crash.
**Changed:** [`graph/builder.py`](../graph/builder.py) handles both.

### 2.4 Pure core, thin shell, paid for itself ✅
**What happened:** Separating math from I/O let a 30-test ground-truth suite run in about 12 s with no infrastructure. Every model correction in §1 was caught or verified there.
**Lesson:** Keep scoring code DB-free. The cost is low coverage on persistence paths (~35 % overall), which needs integration tests, not more mocks.

### 2.5 Seeds should be idempotent like everything else 🔴
**What happened:** DAG writes are UPSERTs, but `seed_dev.py` isn't. A failed run followed by a retry doubled the org (120 → 240 → 360).
**Lesson:** Demo tooling is production for the person giving the demo. Make it re-runnable.

### 2.6 Environment loading should not be tribal knowledge 🟡
**What happened:** `api/deps.py` reads `os.environ` with no `load_dotenv()`. Starting uvicorn in a fresh shell connects to Postgres on 5432 instead of 5433 and returns 500s. Vite reads `frontend/.env`, not the repo root.
**Lesson:** Either load `.env` in the app or fail fast with a clear message when required variables are missing.
**Changed:** `demo.ps1` loads `.env`; the app itself still doesn't.

### 2.7 Windows leaves orphaned sockets 🟡
**What happened:** After a crashed uvicorn, port 8000 stayed in LISTEN with no owning process.
**Workaround:** API dev port moved to 8001 and `demo.ps1` clears ports. The README still documents 8000.

### 2.8 Excluded tests rot 🔴
**What happened:** `test_tenant.py` and `test_compliance.py` were excluded from CI after a `TenantContext` change broke their fixtures. They still fail. Meanwhile the README reports 100 % coverage for `graph/compliance.py`, which comes from that excluded file.
**Lesson:** An `--ignore` needs an owner and a date. Otherwise it's permanent.

---

## 3. Product and UI

Observed while capturing the screenshots in these docs (seeded demo, 120 employees).

### 3.1 The riskiest person displayed as "normal" ✅
**What happened:** On the employee profile, the badge reads `employee.spof_flag ?? employee.flag ?? "normal"`. `/graph/employee/{id}` returns neither field, so **every profile shows "normal"**. That includes Carl Jackson, the #1 SPOF in the org at 0.90, shown as critical in the roster (see [employee-detail.png](screenshots/employee-detail.png)). The badge component also has no style for `critical_uncertain` or `elevated`.
**Lesson:** Never default a risk label to the safe value. Show "unknown" when the data is missing.
**Changed:** the endpoint returns the latest `spof_score`, `flag` and `risk_scored_at` for the centre node, and a shared [`spofFlag.js`](../frontend/src/lib/spofFlag.js) styles all six flags, with "unknown" when none is on record. The same problem showed up in the ego-network chart, which colored nodes by *betweenness* under a "SPOF risk" legend, so nearly everyone looked green. Neighbours now carry their own latest SPOF score from the same scoring run, and `OrgGraph` draws unscored people in neutral grey with a "No score" legend entry instead of green "Low".

### 3.2 Two "engagement" signals that disagree 🟡
**What happened:** The roster shows many direct reports with **High** leave risk (derived from entropy trend). The manager view for the same team shows all 34 as **Engaged** ([manager-view.png](screenshots/manager-view.png)).
**Lesson:** If two screens describe the same person's risk, they need the same source, or labels that make the difference obvious.

### 3.3 "0 silos detected" above three silo alerts 🔴
**What happened:** The Communities KPI counts Louvain communities flagged as silos, while the alert registry, right below it, lists three department-level silo alerts ([dashboard.png](screenshots/dashboard.png)). This is the visible consequence of lesson 1.5.
**Lesson:** When you change the unit of analysis, update every surface that counts it.

### 3.4 Empty states need a reason 🟡
**What happened:** Onboarding showed 0 new hires and Risk Trend showed "Insufficient history" on a freshly seeded instance, so those screens weren't captured. The transfer-plan panel does this well ("Run the succession_dag to generate one").
**Lesson:** Every empty state should say what's missing and how to fill it. The seed should cover every screen.

### 3.5 A privacy promise the code doesn't keep ✅
**What happened:** The equity page says *"aggregated by group (minimum 5 members per group)"*, but neither `compute_equity.py` nor `/equity/*` enforces a minimum. A tenure band with 2 people would expose their centrality.
**Lesson:** Privacy guarantees belong in the query (`HAVING COUNT(*) >= 5`), with a test, not in UI copy.
**Changed:** `EQUITY_MIN_GROUP_SIZE` (default 5, floor 2) is enforced in SQL when scores are computed **and** again in the router, which also covers rows stored before the fix. The response reports `suppressed_groups` without labels or statistics, and [`test_equity_privacy.py`](../tests/unit/test_equity_privacy.py) checks that a suppressed group's label never appears.

---

## 4. Documentation and process

### 4.1 Docs drifted from code 🟡
**What happened:** A code-level review for [MATH_MODEL.md](MATH_MODEL.md) found eight places where MODEL.md and the implementation differ. The largest: the channel × direction weights (GitHub 3.0, Calendar 2.5, …) and their `CHANNEL_WEIGHT_*` env vars are **documented but never applied**. [PITCH.md](PITCH.md) also says the GNN "captures network contagion", when contagion is a separate rule layer.
**Lesson:** Formulas should live next to the code that implements them, or be checked by a test that imports the documented defaults. MATH_MODEL §12 is now the drift ledger.
**Changed (Nov 2026):** eight ledger rows resolved, including channel weights, now applied in every edge loader. Four remain open, each with a stated reason. Every resolved row is pinned by a test in `test_math_model_corrections.py`.

### 4.2 "Unchanged from original" loses the original 🔴
**What happened:** MODEL.md v2/v3 kept only changed sections and replaced the rest with "[unchanged — see original]". The v1 file isn't in the repo, so the channel-weight thesis, centrality and entropy definitions, knowledge risk and the GAT spec became unreadable.
**Lesson:** Version documents the way you version code: full text at every version, diffs in the changelog.
**Partly fixed:** MATH_MODEL.md reconstructs those sections from the source.

### 4.3 Placeholders ship 🟡
**What happened:** README badges pointed at `YOUR_ORG/…` until November 2026. The README still documents API port 8000 while dev runs on 8001.
**Lesson:** Grep for placeholders (`YOUR_`, `TODO`, `changeme`) in the release checklist ([ENGINEERING §7](ENGINEERING.md#7-release-checklist)).

### 4.4 Synthetic validation is necessary, not sufficient 🟡
**What happened:** Planted-archetype tests proved the formulas behave as designed and caught real bugs. They can't prove the designs match reality.
**Lesson:** The departure impact report is the most valuable feature for the *model*, because every real exit becomes a labeled example. Calibration on real outcomes is the next milestone ([BUSINESS_VALUE §6](BUSINESS_VALUE.md#6-proving-it-a-90-day-value-pilot)).

---

## 5. Open items, ranked

Resolved in November 2026: dependency pins and boot smoke test (2.1), tenants provisioned from migrations (2.2), profile risk badge and ego-network chart coloring (3.1), equity small-group suppression (3.5).

| # | Item | Lesson | Effort |
|---|---|---|---|
Also resolved in November 2026: channel weights applied (4.1), org-health components made absolute (1.12), size-corrected silo detection (1.13), entropy trend computed for the first time and made robust (1.14), sudden-withdrawal detection added.

| # | Item | Lesson | Effort |
|---|---|---|---|
| 1 | Run `scripts/migrate_tenants.py` against any environment with tenants provisioned before the 2.2 fix | 2.2 | S |
| 2 | **Privacy:** add the consent/active filter to the dashboard edge queries (`fetch_graph_edges`, ego-network edges in `api/db.py`) | 3.5 | S |
| 3 | Re-run the graph, risk and health DAGs (or re-seed) so stored scores reflect the corrected math | 1.12–1.14 | S |
| 4 | Apply migration 018 (`sudden_withdrawal` alert type) to existing databases; run `scripts/migrate_tenants.py` for tenant schemas | 1.14 | S |
| 5 | Tail-aware normalization of the BC term, then adopt undirected betweenness (tracks B/D) | 1.15 | M |
| 6 | Fix and re-enable `test_tenant.py`, `test_compliance.py` | 2.8 | S |
| 7 | Isolate cache unit tests from a live local Redis (two tests fail when one is running on :6379) | 2.8 | S |
| 8 | Unify the dashboard silo KPI with department-level alerts | 3.3 | S |
| 9 | Make `seed_dev.py` idempotent; seed recent hires and score history | 2.5, 3.4 | M |

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*
