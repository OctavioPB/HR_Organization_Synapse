# Model Card — ChurnGAT: Employee Voluntary Departure Risk

**Version:** `churn_gat_{snapshot_date}` (checkpoint filename convention)  
**Type:** Binary classifier — voluntary departure probability, 90-day horizon  
**Architecture:** Graph Attention Network (PyTorch Geometric `GATConv`)  
**Source files:** `ml/gnn/model.py`, `ml/gnn/trainer.py`, `ml/gnn/scorer.py`, `ml/gnn/feature_builder.py`

---

## 1. Intended use

| | |
|---|---|
| **Primary purpose** | Surface employees at elevated risk of voluntary departure within 90 days so HR can initiate retention conversations, succession planning, or workload reviews — before the departure occurs. |
| **Decision type** | Triage input. The score surfaces a signal; a human HR professional decides whether and how to act. |
| **Who sees the score** | `hr_admin` role only. Managers see a traffic-light (`high / medium / low`) via `GET /manager/team/{id}/suggestions` — no numeric probability. Executives see department-level aggregates, not individuals. |
| **Prediction horizon** | 90 days from the scoring date. |
| **Update cadence** | Weekly (Sundays, `churn_risk_dag`). |

---

## 2. Out-of-scope use

**These uses are prohibited and the system does not support them technically or ethically:**

- **Employment decisions.** The churn score must not be an input to compensation, promotion, performance review, role assignment, or termination decisions. A high churn score means the model predicts the employee may leave; it does not mean the employee should be let go first.

- **Surveillance.** The score is not a productivity, sentiment, or loyalty metric. It does not measure whether an employee is working hard, whether they are satisfied, or whether they are a good employee.

- **Sharing with the scored employee.** The score is computed from the employee's own collaboration patterns. Showing them a number derived from that data without their informed understanding of the model constitutes a harm this system is not designed to prevent.

- **Contractor and gig workers.** The model was designed for full-time employees with stable collaboration patterns in the platforms ingested. Application to contractors will systematically underpredict risk for those with low system presence and overpredict for those who compensate with higher digital volume.

- **Employees with fewer than 30 days of activity history.** Graph features default to 0.0 for employees without a prior snapshot (see `feature_builder.py`, `_safe_div`). Early scores for new hires are driven almost entirely by HR features (tenure, role level, PTO); they are not structurally meaningful and should not inform retention decisions.

- **Treating the score as a deterministic outcome.** A churn probability of 0.65 means the model assigns a 65% likelihood based on historical structural patterns. It is not a statement that the employee has decided to leave.

---

## 3. Model architecture

```
Input: x ∈ ℝ^(N × 11)   edge_index ∈ ℤ^(2 × E)   edge_weight ∈ ℝ^E

GATConv(11 → 64, heads=4, concat=True, dropout=0.3)   → ELU   → (N × 256)
GATConv(256 → 64, heads=1, concat=False, dropout=0.3) → ELU   → (N × 64)
Linear(64 → 32) → ReLU → Dropout(0.3) → Linear(32 → 1) → sigmoid → p ∈ [0, 1]
```

**Training objective:** `BCEWithLogitsLoss(pos_weight = n_neg / n_pos)` — compensates for the typical 95/5 voluntary churn imbalance.

**Optimizer:** Adam, lr=5e-3, weight_decay=1e-4.

**Early stopping:** monitors validation AUROC; stops when AUROC has not improved by ≥ 0.001 for 20 consecutive epochs (max 200 epochs).

**Temporal split** (no label leakage):
- Train: label_date < (latest − 44 days)
- Validation: last 30–44 days
- Test: last 14 days

**Risk tiers** (`ml/gnn/scorer.py`, env-configurable):

| Tier | Threshold |
|---|---|
| `high` | churn_prob ≥ 0.60 |
| `medium` | churn_prob ≥ 0.30 |
| `low` | churn_prob < 0.30 |

---

## 4. Input features

All features are normalized to [0, 1] before entering the network. Missing HR data defaults to 0.0 (same as a new hire with no history).

| # | Feature | Source | Notes |
|---|---|---|---|
| 0 | `tenure_days_norm` | HRIS `hire_date` | days / 3650; capped at 1.0 (10 yr) |
| 1 | `role_level_norm` | HRIS `role_level` | 1=IC1 … 7=C-Suite, divided by 7.0 |
| 2 | `pto_days_norm` | HRIS `pto_days_ytd` | days / 90.0; capped at 1.0 |
| 3 | `betweenness` | `graph_snapshots` | normalised betweenness centrality |
| 4 | `degree_in` | `graph_snapshots` | normalised in-degree centrality |
| 5 | `degree_out` | `graph_snapshots` | normalised out-degree centrality |
| 6 | `clustering` | `graph_snapshots` | local clustering coefficient |
| 7 | `betweenness_delta_7d` | `graph_snapshots` | 7-day change in betweenness |
| 8 | `degree_out_delta_7d` | `graph_snapshots` | 7-day change in out-degree |
| 9 | `entropy_current` | `raw_events` | Shannon entropy of partner distribution (current week), capped at 1.0 |
| 10 | `entropy_trend` | `raw_events` | linear slope of weekly entropy (4 weeks), clipped ±1 |

**What is NOT in the model:**
- Message content, email text, file names, document content
- Demographic attributes (age, gender, ethnicity, disability status)
- Compensation data or performance ratings
- Calendar content or meeting notes

The graph edges encode `{who} → {whom}`, `{channel}`, `{timestamp}`, and `{weight}` only.

**Explainability:** The GAT's first-layer attention coefficients identify the top-3 neighbours whose collaboration patterns most influence each employee's score. These are stored in `churn_scores.influence_neighbors` (JSON). They explain which edges drive the score, not the decision of whether to act.

---

## 5. Training data requirements

**Minimum viable dataset** for the model to train without degenerate loss:
- ≥ 2 labelled employees (enforced in `trainer.py`; logs a warning and aborts below this threshold)
- Positive class (churned): at least 1 example in the training split

**Practical minimum for deployment:**
- ≥ 500 labelled employees across ≥ 3 quarterly observation cohorts
- See Section 7 (Limitations) for why N=300 is insufficient

**Label definition:** `churn_labels.churned = true` when an employee's `active` flag is set to `false` and the departure was voluntary (not restructuring, not retirement, not leave of absence). Label assignment is manual; the model cannot distinguish voluntary from involuntary departure unless the HR team maintains this distinction in `churn_labels`.

**Edge data:** 30-day rolling window from `raw_events`, filtered to consenting active employees (`consent = true`, `active = true`). The SQL filter is applied at the source in `build_graph_data()` — employees who withdrew consent are never loaded into Python memory.

**Data sources:** Slack, Microsoft Teams, Jira, GitHub, calendar invitations, Confluence, Notion. Employees who communicate primarily via phone, SMS, or in-person are structurally underrepresented in the graph.

---

## 6. Evaluation results

All numbers below are from `tests/validation/churn_model_evaluator.py` (seed=42). The evaluation uses synthetic data — see Section 7 for implications.

**Dataset:** 300 synthetic employees, stratified temporal split (210 train / 90 test), 14.3% train churn rate, 18.9% test churn rate, 30 training positives.

**Comparison:** GraphMLP (GraphSAGE-style surrogate for ChurnGAT; `torch_geometric` not installed in this environment) vs. LogisticRegression on 3 tabular features (tenure, degree_out, entropy_trend).

| Metric | LogReg baseline | GraphMLP (graph model) | Δ |
|---|---|---|---|
| AUROC | 0.722 | 0.616 | −0.106 |
| PR-AUC | 0.414 | 0.334 | −0.080 |
| AP@10 | 0.610 | 0.589 | −0.021 |
| F1 (optimal threshold) | 0.522 | 0.414 | −0.108 |
| Precision at optimal threshold | 0.414 | 0.500 | +0.086 |
| Recall at optimal threshold | 0.706 | 0.353 | −0.353 |

**Random floor:** AUROC 0.50. **Trivial PR-AUC floor** (predict churn_rate for all): 0.189.

Both models beat the random floor. At N=300, the 3-feature logistic regression outperforms the graph model by 10.6pp AUROC. This is the current deployment recommendation: **use the baseline until the conditions in Section 7 are met.**

No slice-level evaluation (by department, tenure band, role level, or demographic group) has been conducted on real data. This is a known gap — see Section 8.

---

## 7. Limitations

### 7.1 Synthetic validation only

The evaluation in Section 6 uses a synthetic dataset with planted churn labels constructed from known rules. The model has **never been evaluated against real organizational churn data with verified ground-truth labels.** The synthetic results establish that the model architecture is not degenerate, but they do not establish that the model generalizes to any specific real organization.

Before deployment: collect ≥ 6 months of departure history with voluntary/involuntary labels, train on the organization's data, and evaluate on a held-out cohort with the methodology in `ml/gnn/trainer.py`.

### 7.2 Graph model does not outperform baseline at N=300

At 300 employees with 30 training positives, the 3-feature logistic regression (tenure, degree_out, entropy_trend) outperforms the GNN by 10.6pp AUROC. The GNN's structural advantage — capturing social contagion through neighbor aggregation — is not decisive when individual features are strong and the training set is small.

**Current deployment recommendation:** LogisticRegression on tabular features. Revisit ChurnGAT when:
- Labelled churn history covers N > 500 unique employees
- Labels span ≥ 3 quarterly observation cohorts
- `torch_geometric` is installed

### 7.3 90-day horizon and voluntary churn only

The model predicts voluntary departure in the next 90 days. It does not predict:
- Involuntary termination (layoffs, performance-based)
- Retirement
- Leave of absence
- Departure beyond 90 days

Applying the score to predict involuntary departures will produce spuriously high scores for employees who are structurally underutilized (and likely to be laid off) but not personally disengaged — a conflation that risks discriminatory outcomes.

### 7.4 Cold start for new hires

Employees without a prior `graph_snapshots` entry receive 0.0 for features 3–10 (`betweenness`, `degree_in`, `degree_out`, `clustering`, and all deltas). Their score is driven by HR features alone (tenure, role level, PTO). Early churn in the first 30–60 days of employment is precisely the period where the graph signal is absent; the model is least useful when prediction would be most valuable.

### 7.5 Channel representation bias

The model ingests collaboration from 7 platforms. Employees who communicate primarily through channels not ingested (phone, in-person, other tools) appear structurally isolated. Their betweenness and degree features will be low regardless of their actual organizational engagement, leading to systematically elevated risk scores.

### 7.6 Consent opt-out selection

Employees with `consent = false` are excluded from all computation at the SQL layer. If the decision to opt out correlates with disengagement (a plausible assumption), the model's training data systematically underrepresents employees at high departure risk. This creates optimistic bias: the model is trained on a population that is more engaged than average.

### 7.7 Label quality dependency

The model is only as good as the churn labels. If `churn_labels.churned` conflates voluntary and involuntary departures, the model learns a mixed signal. If HR does not record departures promptly, there will be label leakage (an employee marked as not churned who was actually mid-departure at scoring time). Both errors degrade the model in ways that cannot be detected without external ground truth.

### 7.8 Distribution shift

Collaboration patterns can shift rapidly: a merger, a remote-work policy change, or a new communication tool can change the graph topology organization-wide. A model trained before such a shift may score the entire population anomalously after it. The model should be retrained after any significant organizational or tooling change.

---

## 8. Equity and fairness

### 8.1 Known risk: structural position as a proxy

Network centrality (betweenness, degree) is not a neutral feature. Structural barriers — being assigned to a marginalized team, being excluded from key meetings, working in a department with lower cross-functional visibility — can produce low centrality that reflects the organization's treatment of the employee, not the employee's own disengagement. A model that uses centrality as a churn predictor will produce systematically different scores for employees in structurally disadvantaged positions, regardless of their actual departure intent.

This is not a hypothetical. It is the same mechanism documented in gender-gap research showing that women in certain industries have lower brokerage centrality due to structural access barriers, not preference.

### 8.2 Equity monitoring

The platform includes structural equity analytics (`etl/tasks/compute_equity.py`, `api/routers/equity.py`) that compute centrality distributions by `gender_group`, `tenure_band`, and `level_band`. A disparity ratio below 0.80 (configurable via `EQUITY_DISPARITY_RATIO`) between a group's median centrality and the organization median triggers a `below_org_median` flag.

**This flag is an investigative heuristic, not a legal finding.** It is adapted by analogy from the EEOC four-fifths rule (29 C.F.R. § 1607.4D), which applies to employment selection rates — not graph centrality. A flag means: "this group's structural position is far enough below the median to warrant qualitative investigation of whether barriers exist." It does not establish discrimination or legal liability.

### 8.3 No demographic slice evaluation conducted

The evaluation in Section 6 does not include slice-level performance analysis by demographic group. This is a gap. Before deployment:

- Compute AUROC and PR-AUC separately for each demographic group available in `employee_demographics`
- Confirm that no group has an AUROC below the organization's overall AUROC by more than 10pp
- Investigate the cause before deployment if that threshold is exceeded

### 8.4 Self-fulfilling prediction risk

If a manager is informed that an employee is high-risk and changes their behavior toward that employee (fewer opportunities, less mentorship, reduced responsibilities), the manager's behavior may cause the departure the model predicted. The system mitigates this by:

- Giving managers only a traffic-light status and AI-generated 1:1 suggestions (not the numeric score)
- Scoping the numeric probability to `hr_admin` only
- Framing all outputs as retention inputs, not exit conclusions

This mitigation relies on organizational policy enforcement, not technical controls alone.

---

## 9. Access controls and data governance

| Data | Stored in | Sensitivity | Access |
|---|---|---|---|
| `churn_prob` (numeric) | `churn_scores` | high | `hr_admin` role only |
| `risk_tier` (high/medium/low) | `churn_scores` | medium | `hr_admin`; traffic-light abstraction for `manager` |
| `influence_neighbors` (attention neighbors) | `churn_scores` | high | `hr_admin` only |
| Raw collaboration events | `raw_events` | medium | 90-day retention then purged |
| Graph snapshots | `graph_snapshots` | medium | 365-day retention then purged |
| Churn scores | `churn_scores` | high | 365-day retention |

**Consent:** `employees.consent = false` gates all computation at the SQL level in `build_graph_data()`. The SQL `WHERE consent = true` filter is applied before any Python processing; non-consenting employees never enter the feature matrix.

**GDPR Article 20 export:** `GET /compliance/data-export/{id}` returns the employee's complete personal data package, including all stored churn scores and the dates they were computed.

**Data minimisation:** The model ingests `{who} → {whom}`, `{channel}`, `{timestamp}`, and `{weight}`. It does not ingest and cannot reconstruct message content, email bodies, file content, calendar event titles, or meeting notes.

---

## 10. Maintenance

| Activity | Trigger | Owner |
|---|---|---|
| Model retraining | Quarterly, or after AUROC drops ≥ 5pp on a fresh holdout | Data team |
| Threshold review | After any org-wide change in churn rate ≥ 3pp | HR analytics |
| Equity audit | Quarterly, using `GET /equity/centrality-distribution` | HR + Legal |
| Feature drift check | Monthly — monitor mean and std of each of the 11 features vs. training distribution | Data team |
| Label quality review | After each quarterly cohort is labeled | HR + Data team |

**Versioning:** each checkpoint is named `churn_gat_{snapshot_date}.pt`. The `model_version` field in `churn_scores` records which checkpoint produced each row. This allows backward attribution: a score stored today can be traced to the training snapshot that produced it.

**Rollback:** to revert to a prior checkpoint, pass `--checkpoint checkpoints/churn_gat_{prior_date}.pt` to `scorer.py`. The UPSERT in `_write_scores` overwrites today's scores idempotently.

---

## 11. Contact and escalation

**Model questions and bug reports:** open an issue in this repository.

**Suspected discriminatory outcomes:** escalate to HR Legal and the Data team simultaneously; do not wait for the next quarterly equity audit.

**Employee rights:** employees subject to this model have the right to request the data on which their score was computed (`GET /compliance/data-export/{id}`) and the right to withdraw consent (`PATCH /compliance/consent`). Withdrawal removes them from all future graph computation; it does not retroactively purge historical scores (see retention policy above).

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*  
*Reference: Mitchell et al., "Model Cards for Model Reporting," FAccT 2019.*
