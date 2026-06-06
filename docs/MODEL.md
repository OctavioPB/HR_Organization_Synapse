# MODEL.md — Mathematical Foundations of Organizational Synapse
# Octavio Pérez Bravo · Data & AI Strategy Architect

This document records every quantitative decision in the platform: why each formula was chosen, what alternatives were considered, where the weights came from, and what the model cannot capture. It is written for a technical reviewer who needs to reproduce, critique, or extend the risk calculations.

**CHANGELOG v2.0 (Mathematical corrections):**
- §5: SPOF score — added mandatory sensitivity analysis; rank normalization before combination
- §5.3: Weight justification — added robustness condition; confidence interval specification
- §6: Knowledge Risk — no corrections required
- §8.1: Weighted Jaccard — fixed absolute-weight bias; normalized by employee interaction volume
- §9: Onboarding Integration Score — Community Stability replaced with neighborhood overlap metric
- §12: Org Health Score — silo_risk denominator made organization-relative; fragmentation_risk replaced with convex function
- §11: Equity Analytics — EEOC 4/5 threshold reframed as investigative heuristic, not legal standard
- §13: Limitations — updated to reflect corrections

**CHANGELOG v3.0 (Literature update — empirical grounding):**
- §2.2.1: Channel Weight Motivation — added hybrid-work empirical constraint (Yang et al., 2022)
- §5.3: SPOF Weight Justification — added centrality / market-visibility tension (Younis et al., 2023)
- §7: Churn Model — added turnover contagion feature gap (AlKetbi et al., 2025) and XAI note (Ghanem et al., 2024)
- §12: Org Health Score — added post-disruption network resilience note (Moore, 2023)
- §13: Limitations — three new: hybrid-work channel drift, turnover contagion blind spot, discrete-snapshot GNN
- §15: References — five new peer-reviewed citations

---

## 1. Notation

| Symbol | Meaning |
|---|---|
| G = (V, E) | Directed weighted graph: V = employees, E = collaboration events |
| w(u, v) | Accumulated edge weight between employees u and v |
| w_norm(u, v) | Normalized edge weight: w(u,v) / Σ_{x} w(u,x); proportion of u's interactions directed at v |
| w_inv(u, v) | Inverse weight: 1 / w(u, v); used for shortest-path algorithms |
| σ(s, t) | Number of shortest paths from s to t in G |
| σ(s, t \| v) | Number of those paths passing through node v |
| N(v) | Neighbourhood of v: all nodes with a direct edge to/from v |
| d(u, v) | Effective graph distance (weighted shortest path) |
| H(v, t) | Shannon entropy of v's interaction distribution at time t |
| C | Set of collaboration channels: {github, calendar, jira, slack, email, teams} |
| D | Set of interaction directions: {reviewed, assigned, mentioned, invited, sent} |
| R(x, P) | Rank percentile of value x within population P; maps any metric to [0,1] |

---

## 2. Graph Construction and Edge Semantics

### 2.1 Raw edge accumulation

For each ingested `CollaborationEvent` e = (source, target, channel, direction, weight):

```
w(source, target) ← w(source, target) + weight × ω(channel, direction)
```

The base `weight` field in the event schema (default 1.0) captures event-level importance. The channel-direction multiplier `ω(c, d)` applies semantic depth — described in section 2.2.

The graph is directed (A→B and B→A are separate edges) to preserve asymmetric interaction patterns. It is converted to undirected for betweenness centrality.

---

### 2.2 Channel Weight Thesis

[Sections 2.2.1 through 2.2.6 unchanged — see original MODEL.md]

The channel weights (ω) remain theoretically motivated as specified in the original document. Their empirical validation status is logged in §13 (Limitations). The sensitivity analysis in §5.4 quantifies the effect of weight uncertainty on SPOF scores.

#### 2.2.7 Hybrid-Work Empirical Constraint (v3)

The channel weight ranking in §2.2.2 was derived under the implicit assumption of a predominantly in-person or pre-pandemic work environment, where calendar (synchronous meeting) events reliably proxied for high-bandwidth tacit knowledge transfer. Yang et al. (2022), analysing the communication patterns of 61,182 Microsoft employees before and after the firm-wide shift to remote work, provide the largest available empirical benchmark for digital collaboration networks:

> Remote work caused collaboration networks to become more static and siloed, and communication to shift toward more asynchronous channels. Cross-group ties — the bridges captured by CDR and BC_norm in this model — declined in number and strength under fully remote conditions.

**Practical implication for ω calibration:** In organizations operating under hybrid or fully remote work models, two adjustments are warranted:

1. **Calendar weight re-evaluation.** A calendar `invited` event no longer guarantees a synchronous, co-located meeting. Video calls carry lower tacit-knowledge transfer than in-person sessions (reduced non-verbal cues, higher cognitive load, lower trust-building efficiency). The default `CHANNEL_WEIGHT_CALENDAR = 2.5` may overestimate the depth of knowledge transfer for remote-attended meetings. Until per-organization empirical calibration is available (§2.2.5), organizations with >50% remote workforce should consider reducing this parameter to 1.8–2.0.

2. **Silo risk baseline shift.** Yang et al. (2022) find that remote work structurally reduces cross-group tie formation. Organizations that transitioned to hybrid work will therefore show elevated silo_risk scores that partly reflect the channel shift rather than organizational pathology. This should be documented as a baseline shift in health score interpretation, not necessarily as an actionable alert.

**Reference:** Yang, L., Holtz, D., Jaffe, S., et al. (2022). The effects of remote work on collaboration among information workers. *Nature Human Behaviour, 6*(1), 43–54. https://doi.org/10.1038/s41562-021-01196-4

---

### 2.3 Inverse weighting for shortest paths

```
w_inv(u, v) = 1 / max(w(u, v), ε)
```

where ε = 1e-6 prevents division by zero. Unchanged from v1.

---

## 3. Centrality Metrics

[Sections 3.1–3.3 unchanged from original]

---

## 4. Entropy Trend

[Section 4.1–4.2 unchanged from original]

---

## 5. SPOF Score Formula

### 5.1 Full formula

```
SPOF(v) = α × R(BC_norm(v), V)
         + β × R(CDR(v), V)
         + γ × R((1 − CC(v)), V)
         − δ × R_signed(entropy_trend(v), V)
```

**CORRECTION from v1:** Each metric is now passed through a rank percentile transformation R(·, V) before linear combination. This ensures that each term contributes according to its nominal weight α, β, γ, δ — not according to its empirical variance in the population.

**Why rank normalization instead of raw values:**

In any real organization, BC_norm has a highly right-skewed distribution (most employees BC ≈ 0, a few employees BC >> 0). CDR has a more uniform distribution. entropy_trend is a bounded slope near zero for most employees. If raw values are combined directly, the term with highest empirical variance dominates — in practice, BC_norm — regardless of its assigned weight α. This creates an effective weight allocation that diverges from the nominal weights and cannot be controlled.

Rank percentile transformation maps each metric to [0, 1] uniformly, ensuring α = 0.4 means betweenness contributes exactly 40% of the score variance. The output remains in [0, 1] and the clamping condition is preserved.

**R_signed for entropy_trend:** The entropy trend is signed (negative = withdrawing, positive = engaging). Apply:

```
R_signed(entropy_trend(v), V) = PERCENT_RANK(entropy_trend(v)) within V
```

This maps the most negative trend (most withdrawing employee) to percentile 0, and the most positive (most engaging) to percentile 1. The subtraction `− δ × R_signed(...)` then correctly penalizes withdrawing employees (low percentile → subtracts less) and rewards engaging ones (high percentile → subtracts more from risk).

All terms are now guaranteed to be in [0, 1] by construction, and the sum is in [0, α+β+γ] = [0, 1.0], which is then clamped.

### 5.2 Cross-Department Ratio (CDR)

[Unchanged from original]

```
CDR(v) = |{(v,u) ∈ E : dept(u) ≠ dept(v)}| / max(|{(v,u) ∈ E}|, 1)
```

### 5.3 Weight Justification

| Term | Weight | Justification |
|---|---|---|
| α = 0.4 | Betweenness centrality | Primary structural measure |
| β = 0.3 | Cross-department ratio | Cross-dept connectors irreplaceable by within-dept alternatives |
| γ = 0.2 | (1 − clustering) | Amplifies score when neighbors are not self-sufficient |
| δ = 0.1 | Entropy trend | Temporal signal; noisiest metric, lowest weight |

**CORRECTION — Robustness requirement (new in v2):**

The specific weight values (0.4, 0.3, 0.2, 0.1) are theoretically motivated but not empirically derived. The justification for the *ordering* α > β > γ > δ is sound. The justification for the *magnitudes* is not.

Before using SPOF scores for personnel decisions, the implementation MUST compute and expose:

```
SPOF_lo(v) = SPOF(v) evaluated with α=0.35, β=0.25, γ=0.25, δ=0.15
SPOF_hi(v) = SPOF(v) evaluated with α=0.50, β=0.30, γ=0.15, δ=0.05
```

These bracket a ±20% perturbation in weight space while preserving the ordering constraint.

**Robustness condition:** An employee is flagged as `robust_critical` only if their SPOF score exceeds the critical threshold under ALL weight combinations in the perturbation set. An employee who crosses the threshold only under the central weights but not under perturbations is flagged as `weight_sensitive` — the classification depends on the specific weight choice and should not be acted upon without additional qualitative investigation.

This does not change the default scoring formula; it adds uncertainty quantification to the output.

#### 5.3.1 Centrality and Market Visibility — an undermodeled tension (v3)

The SPOF score identifies employees who are structurally critical from the organization's perspective. Younis, Ahsan & Chatteur (2023), in a systematic review of 30 ONA-based turnover studies, surface an important empirical finding that complicates the interpretation:

> Central network position is not always associated with lower voluntary turnover — in some contexts, employees with high betweenness centrality show *higher* turnover propensity, because their structural prominence makes them more visible to external recruiters and more confident in their labor-market alternatives.

This means a high SPOF score signals two distinct risks that the model currently fuses without distinguishing them:

| Risk type | Source | SPOF captures? | Additional signal needed |
|---|---|---|---|
| **Structural dependency risk** | Org loses a bridge if person departs | ✓ Yes — this is the SPOF's primary purpose | None |
| **Departure probability risk** | Person is likely to depart | ∼ Partially — entropy trend, churn GNN | CDR × market_demand proxy; external recruiter activity |

**Operational implication:** A `robust_critical` SPOF employee with a stable entropy trend is a structural risk but not necessarily an imminent departure. Succession planning is warranted regardless. A `robust_critical` employee with a declining entropy trend AND high CDR (maximum external visibility) represents both risks simultaneously and warrants immediate retention action.

**Reference:** Younis, S., Ahsan, A. & Chatteur, F.M. (2023). An employee retention model using organizational network analysis for voluntary turnover. *Social Network Analysis and Mining, 13*(1), 28. https://doi.org/10.1007/s13278-023-01031-w

### 5.4 Impact of Channel Weighting on SPOF

[Unchanged from original — the rank normalization in §5.1 does not alter the interpretation of channel weights. The sensitivity analysis in §5.3 implicitly captures channel weight uncertainty through SPOF score variability.]

### 5.5 Score Thresholds

| Range | Flag | HR interpretation |
|---|---|---|
| ≥ 0.7 (robust_critical) | `critical` | Immediate succession planning recommended |
| ≥ 0.7 (weight_sensitive) | `critical_uncertain` | Flag for qualitative review before action |
| 0.5–0.7 | `warning` | Cross-training investment warranted |
| 0.4–0.5 | `elevated` | Monitor entropy trend weekly |
| < 0.4 | `normal` | Standard; no structural dependency concern |

**CORRECTION from v1:** Added `critical_uncertain` tier for employees who cross the 0.7 threshold under central weights but are sensitive to weight perturbations. These employees require qualitative investigation before succession planning is initiated.

---

## 6. Knowledge Risk Score

[Sections 6.1–6.5 unchanged — no corrections required]

---

## 7. Churn Risk Model — Graph Attention Network

[Sections 7.1–7.3 unchanged — no corrections required]

### 7.4 Two Feature Gaps Identified by Recent Literature (v3)

#### 7.4.1 Turnover Contagion — a missing feature

The current feature matrix (§7.1) captures individual node properties and their 1-hop neighborhood aggregation, but does not include any measure of **peer turnover rate**. AlKetbi et al. (2025), analysing temporal networks of 121,883 financial professionals across 4,979 firms over 17 years (2007–2024), quantify a contagion effect:

> Professionals are 23% more likely to depart when more than 30% of their immediate peers depart within a six-month window. Embedding network contagion signals into ML models improves turnover prediction accuracy by 30% over individual-attribute baselines.

The mechanism is consistent with social comparison theory and job embeddedness research: when a significant fraction of an employee's relational ties dissolve through peer departures, the perceived cost of leaving decreases and the attractiveness of the employee's network as a retention anchor declines.

**Proposed feature addition — Feature 11:**

```
peer_churn_rate(v, t) = |{u ∈ N(v) : departed(u, t−180d, t)}| / max(|N(v)|, 1)
```

This measures the fraction of v's direct neighbors who departed in the preceding 180-day window. It is computable from the existing `churn_labels` table without new data collection, and provides a direct signal for the contagion mechanism.

**Recommended threshold alert:** `peer_churn_rate(v, t) > 0.30` should fire a supplemental `peer_contagion_risk` flag, independent of the GNN churn score. This is actionable without retraining: it can be implemented as a rule-based alert layer on top of the existing model.

**Reference:** AlKetbi, A., Yam, P., Marti, G., et al. (2025/2026). Network Contagion in Financial Labor Markets: Predicting Turnover in Hong Kong. In *Complex Networks & Their Applications XIV*, Studies in Computational Intelligence, vol. 1265. Springer. https://arxiv.org/abs/2509.08001

#### 7.4.2 Explainability — a missing output

The current GAT model (§7.3) produces a scalar churn risk score per employee. Ghanem et al. (2024), in a study applying explainable GNNs to employee attrition prediction, demonstrate that XAI techniques — specifically attention weight visualization and feature attribution — can identify the specific neighbors and features driving each individual employee's risk score, enabling targeted retention interventions rather than generic retention budgets.

> Although the GNN achieved lower overall accuracy than XGBoost, its explainability provides actionable insights for HR decision-making — identifying which factors contribute to each individual's predicted departure.

The current implementation does not surface the attention coefficients α(v,u) from the GAT layer (§7.3) in any user-facing output. These coefficients are already computed during the forward pass and indicate which neighboring employees are most predictive of v's churn risk. Exposing them requires no model retraining — only output layer modification.

**Recommended implementation:** Add a `churn_influence_neighbors` output field: the top-3 neighbors by attention weight α(v,u) for each flagged employee. This converts the GNN from a black box into an interpretable risk explanation: "Employee v is at churn risk, driven primarily by the recent departure of their top collaborators u₁, u₂, u₃."

**Reference:** Ghanem, R. et al. (2024). Explainable Machine Learning and Graph Neural Network Approaches for Predicting Employee Attrition. *Proceedings of IC3-2024 (16th International Conference on Contemporary Computing)*, Noida, India. https://doi.org/10.1145/3675888.3676058

---

## 8. Succession Compatibility Score

For a SPOF employee s and candidate c:

```
compat(s, c) = w_struct × jaccard_weighted_norm(N(s), N(c))
             + w_clust  × CC(c)
             + w_domain × domain_overlap(s, c)
```

Default weights: w_struct = 0.40, w_clust = 0.25, w_domain = 0.35. Unchanged.

### 8.1 Weighted Jaccard — CORRECTED

**CORRECTION from v1:** The original weighted Jaccard used raw accumulated edge weights w(s,u) and w(c,u). These accumulate over time and grow monotonically with employee tenure and event volume. A 5-year employee will have weights 5× larger than a 1-year employee for equivalent interaction intensity. This creates a systematic bias toward senior employees in succession scoring — not because they are structurally better successors, but because their weights are numerically larger.

**Corrected formulation — use normalized (proportional) weights:**

```
w_norm(v, u) = w(v, u) / Σ_{x ∈ N(v)} w(v, x)
```

This converts accumulated weights to proportional interaction shares: w_norm(v, u) ∈ (0, 1] and Σ_u w_norm(v, u) = 1 for each employee v.

**Normalized Weighted Jaccard (Tanimoto coefficient on proportional weights):**

```
jaccard_weighted_norm(N(s), N(c)) = 
    Σ_{u ∈ N(s) ∩ N(c)} min(w_norm(s,u), w_norm(c,u))
    / Σ_{u ∈ N(s) ∪ N(c)} max(w_norm(s,u), w_norm(c,u))
```

**Interpretation:** This measures whether s and c allocate similar *proportions* of their interaction budget to the same colleagues — regardless of how long they have been at the company. A candidate who spends 30% of their interactions with the same people as the SPOF employee is a structurally similar successor, whether they joined 6 months ago or 6 years ago.

**Implementation note:** w_norm must be recomputed at each snapshot, not cached from raw weights. The denominator uses the per-employee total within the current rolling window (GRAPH_WINDOW_DAYS), not the all-time total.

---

## 9. Onboarding Integration Score

For new hire v with `hire_date` within 180 days:

```
OIS(v, t) = 0.5 × DCP(v, t)
           + 0.3 × min(CDEC(v, t) / 5, 1.0)
           + 0.2 × CS_overlap(v, t)
```

**DCP and CDEC:** Unchanged from original.

### 9.1 Community Stability — CORRECTED

**CORRECTION from v1:** The original Community Stability metric was:

```
CS(v, t) = 1 if community_id(v, t) = community_id(v, t-7d), else 0   ← DEPRECATED
```

This has two structural problems:

**Problem A — Non-determinism:** The Louvain algorithm is non-deterministic. Two runs on identical graphs can produce different community ID assignments. A change in community_id between weeks may reflect algorithmic randomness, not structural movement.

**Problem B — ID non-comparability:** Community IDs are arbitrary integers assigned fresh at each run. Community 3 this week and Community 3 last week are not necessarily the same community.

**Replacement — Neighborhood Community Overlap:**

```
CS_overlap(v, t) = |Com(v, t) ∩ Com(v, t−7d)| / |Com(v, t) ∪ Com(v, t−7d)|
```

where `Com(v, t)` = the set of employees in the same community as v at time t (identified by membership, not by ID label).

This is the Jaccard similarity between community memberships across weeks. It measures whether the *people* in v's community are stable, not whether an arbitrary integer label matches.

**Range:** CS_overlap ∈ [0, 1]. A score of 1 means identical community composition; 0 means complete membership turnover.

**Stable threshold:** CS_overlap ≥ 0.6 indicates structural stability (at least 60% of community members overlap between weeks).

**Implementation note:** Requires storing community membership sets (arrays of employee_ids), not just community_id integers. Storage overhead is O(n) per snapshot and is manageable.

**Alert threshold:** Unchanged — `DCP < 0.25 AND tenure_days ≥ 60` fires `onboarding_risk`.

---

## 10. Team Composition Score

[Section 10 unchanged from original]

---

## 11. Structural Equity Analytics

For each demographic dimension d ∈ {gender_group, tenure_band, level_band} and metric m ∈ {betweenness, degree}:

**Group median and below-median flag:** Formulas unchanged.

```
M(g, m) = PERCENTILE_CONT(0.5) of m among employees in demographic group g
below_org_median(g, m) = 1 if M(g, m) < 0.8 × M(all, m)
```

### 11.1 EEOC Threshold — Reframed

**CORRECTION from v1:** The original document referenced the EEOC 4/5ths rule as justification for the 0.8 threshold. This requires clarification.

The EEOC 4/5ths rule (29 C.F.R. § 1607.4D) was designed to detect adverse impact in *employment selection rates* — specifically, whether the pass rate of a protected group is less than 80% of the pass rate of the highest-scoring group. It is a legal standard for hiring and promotion decisions.

Applying this threshold to betweenness centrality in a collaboration graph is an *analogy*, not a legal extension. Betweenness centrality is not a selection rate; the 0.8 threshold here is a **heuristic investigation trigger**, not a legal determination.

**Corrected framing:**

The 0.8 threshold is chosen because it is:
(1) A recognized standard for detecting meaningful disparity in related HR contexts (selection rates)
(2) Sufficiently sensitive to flag patterns worth investigating without generating excessive false positives
(3) Conservative — it flags groups for qualitative review, not for legal conclusions

**The flag `below_org_median(g, m) = 1` means:** "This group's median structural position is sufficiently below the organizational median to warrant qualitative investigation of whether structural barriers exist." It does not constitute evidence of discrimination, legal liability, or any specific cause. The appropriate response is human investigation, not algorithmic remediation.

---

## 12. Org Health Score

```
composite_risk = w_silo × silo_risk
              + w_spof × spof_risk
              + w_entropy × entropy_risk
              + w_frag × fragmentation_risk

health_score = (1 − composite_risk) × 100    ∈ [0, 100]
```

| Component | Weight | Formula |
|---|---|---|
| silo_risk | 0.20 | See §12.1 (corrected) |
| spof_risk | 0.35 | mean(SPOF(v)) over all v with SPOF > 0.3 |
| entropy_risk | 0.20 | fraction of employees with entropy_trend < −0.05 |
| fragmentation_risk | 0.25 | See §12.2 (corrected) |

### 12.1 Silo Risk — CORRECTED

**CORRECTION from v1:** The original formula:

```
silo_risk = min(active_silo_count / 10, 1.0)   ← DEPRECATED
```

used a fixed denominator of 10, implying that 10 silos always represents maximum silo risk. This is organization-size invariant in the wrong direction: 10 silos in a 50-person organization (20% of employees per silo on average) is catastrophic; 10 silos in a 5,000-person organization (0.2% of employees per silo on average) may be negligible.

**Corrected formula — denominator scales with organization structure:**

```
silo_threshold(n, d) = max(floor(d / 3), 2)

silo_risk = min(active_silo_count / silo_threshold(n, d), 1.0)
```

where:
- `n` = total active employees in the rolling window
- `d` = number of distinct departments
- `silo_threshold` = expected number of silos at maximum tolerable risk = 1/3 of the department count, minimum 2

**Rationale:** A healthy organization should have no more than one silo per 3 departments as a rough upper bound. If Engineering, Product, Legal, Finance, Sales, Marketing, and Operations (7 departments) each have a silo, that is threshold-level risk. The minimum of 2 prevents silo_threshold from collapsing to 0 or 1 in very small organizations.

**Environment variable:** `SILO_THRESHOLD_RATIO` (default: 3.0) replaces the fixed constant. `silo_threshold = max(floor(d / SILO_THRESHOLD_RATIO), 2)`.

### 12.2 Fragmentation Risk — CORRECTED

**CORRECTION from v1:** The original formula:

```
fragmentation_risk = (weakly_connected_components − 1) / (n − 1)   ← DEPRECATED
```

uses a linear normalization where WCC=2 contributes (2-1)/(n-1) ≈ 1/n to risk — a negligible value for any organization with more than 20 employees. This drastically underestimates the severity of the first fragmentation event (going from 1 to 2 components), which represents a complete severing of the organizational graph.

**Corrected formula — convex (exponential) penalty:**

```
fragmentation_risk = 1 − exp(−λ × (WCC − 1))
```

where λ = `FRAGMENTATION_LAMBDA` (default: 1.5, configurable).

**Behavior comparison (n = 200 employees):**

| WCC | Old formula | New formula (λ=1.5) |
|---|---|---|
| 1 (connected) | 0.000 | 0.000 |
| 2 | 0.005 | 0.777 |
| 3 | 0.010 | 0.950 |
| 5 | 0.020 | 0.999 |
| 10 | 0.045 | ≈ 1.000 |

**Rationale:** The first fragmentation event (WCC: 1 → 2) is qualitatively different from adding a 10th disconnected component. A graph with 2 components has a gap that absolutely cannot be bridged without a cross-component hire or structural intervention. This deserves near-maximum risk score immediately. The exponential form captures this by assigning ~0.78 risk to the first split, ~0.95 to two splits, and effectively saturating at maximum risk beyond 3 splits.

**λ calibration:** λ = 1.5 means WCC=2 → risk ≈ 0.78. Organizations that want more tolerance for early fragmentation (e.g., intentional geographic units) can lower λ to 0.5 (WCC=2 → risk ≈ 0.39). Organizations with zero tolerance for disconnected components should raise λ to 3.0 (WCC=2 → risk ≈ 0.95).

### 12.3 Post-Disruption Network Resilience — an interpretation caveat (v3)

The Org Health Score is designed to detect structural degradation and trigger alerts. An implicit assumption in the current model is that SPOF departures produce persistent health score declines that are proportional to the departed employee's SPOF score. Moore (2023), studying communication network restructuring following a corporate downsizing event, finds empirical evidence that complicates this assumption:

> Following a downsizing event, employees engage in active tie-seeking behavior during a disruption period, acquiring new instrumental ties to replace those lost. During the subsequent stabilization period, pre-disruption tie-making logics resume and betweenness centrality remains relatively stable — the network partially self-heals through informal reorganization.

**Implication for health score interpretation:** The Org Health Score will correctly show a decline immediately following a SPOF departure (the structural gap is real at t=0). However, the model should not be used to infer that the score at t=0 represents the organization's long-term equilibrium. In practice, if management does not intervene, informal network reorganization will partially compensate within 30–90 days. This means:

1. **Alert windows should be front-loaded.** The highest-urgency intervention window is the 30 days immediately following a SPOF departure. After 90 days without intervention, the visible health score may partially recover even without formal succession, masking residual structural risk.

2. **Health score recovery ≠ structural recovery.** Informal tie formation around the gap may restore BC_norm values for adjacent employees, but the *knowledge* carried by the departed SPOF is not recovered by network reorganization alone. The Knowledge Risk Score (§6) is the correct signal for the residual epistemic gap.

3. **spof_risk weight (0.35) may overstate long-run damage.** Given the network's self-healing capacity, `HEALTH_W_SPOF` could be reduced to 0.25 post-stabilization (>90 days post-departure), with the freed weight redistributed to `HEALTH_W_ENTROPY` (0.30) as a leading indicator of the next disruption cycle.

**Reference:** Moore, A. et al. (2023). Dynamic resource-acquisition strategies: Analysis of survivor betweenness centrality relationships after downsizing. *Journal of Occupational and Organizational Psychology*. https://doi.org/10.1111/joop.12418

---

## 13. Model Limitations (Updated v2)

**1. Metadata-only blind spots.** [Unchanged] The model captures digitally mediated collaboration. Informal mentoring over coffee, in-person whiteboard sessions, and phone calls leave no trace.

**2. Channel weight calibration is theoretical.** [Unchanged] The channel weights in section 2.2 are derived from organizational theory but not yet empirically validated against departure outcomes.

**3. The entropy trend is noisy for low-activity employees.** [Unchanged]

**4. Betweenness centrality is a snapshot metric.** [Unchanged]

**5. Succession compatibility is structurally conservative.** [Unchanged] The normalized Jaccard (§8.1 v2) reduces but does not eliminate this bias — it controls for tenure-driven weight accumulation but still favors structurally proximate candidates over potentially optimal candidates who are currently distant.

**6. DEI equity analysis reflects existing network structure.** [Unchanged] The equity module surfaces patterns in the existing graph. The EEOC 4/5 threshold is a heuristic investigative trigger (§11.1 v2), not a legal standard.

**7. The GNN churn model requires historical ground truth.** [Unchanged]

**8. [NEW] SPOF weight sensitivity.** The nominal SPOF weights (α=0.4, β=0.3, γ=0.2, δ=0.1) are theoretically ordered but not empirically calibrated. The rank normalization in §5.1 ensures weights control their nominal share of score variance. The sensitivity analysis in §5.3 ensures that personnel decisions are made only on robust classifications. Until departure-outcome validation is completed, all SPOF-derived decisions should be treated as structurally informed hypotheses, not definitive risk assessments.

**9. [NEW v2] Community Stability measurement depends on Louvain implementation.** The corrected CS_overlap metric (§9.1) requires storing community membership arrays, not just community IDs. The quality of the Louvain implementation (random seed handling, resolution parameter) affects community consistency across runs. Organizations using deterministic graph clustering (e.g., label propagation with fixed seed) may achieve higher CS_overlap baseline scores.

**10. [NEW v3] Channel weights assume stable work modality.** The channel semantic weights (§2.2.2) were derived under organizational communication theory developed primarily for in-person or early-digital work environments. Yang et al. (2022) demonstrate empirically that a shift to remote or hybrid work structurally reduces cross-group tie formation and shifts communication toward asynchronous channels. Organizations with high remote-work penetration should treat the default channel weights as upper-bound estimates of knowledge transfer depth and apply the calibration guidance in §2.2.7.

**11. [NEW v3] The churn model does not capture turnover contagion.** The GNN feature matrix (§7.1) includes individual and neighborhood structural signals but lacks any measure of peer departure rate. AlKetbi et al. (2025) demonstrate a 23% elevated departure probability when >30% of an employee's peers depart within six months, and show that contagion signals improve churn prediction accuracy by 30% over baselines without them. Until Feature 11 (`peer_churn_rate`) is implemented as described in §7.4.1, the model systematically underestimates churn risk during periods of elevated peer attrition — precisely the high-stakes scenario where accurate prediction matters most.

**12. [NEW v3] The temporal GNN uses discrete weekly snapshots; continuous-time models are strictly superior.** The current implementation (§7.3) stacks T=4 weekly graph snapshots through a GRU temporal convolution. Longa et al. (2023), in a systematic review accepted by TMLR, establish that continuous-time temporal graph models consistently outperform discrete-snapshot models on dynamic link prediction tasks, with performance degradation in snapshot models increasing as the ratio of intra-snapshot events to inter-snapshot events grows. For organizations with high collaboration event frequency (>1,000 events/day), the weekly granularity may miss intra-week structural dynamics that are predictive of churn. Migrating §7.3 to a Temporal Graph Network (TGN) architecture operating on event streams rather than weekly snapshots is a recommended future development.

---

## 14. Parameter Reference (Updated v2)

All parameters are configurable via environment variables. Weights sum to 1.0 within each formula.

| Variable | Default | Formula use | Changed in v2 |
|---|---|---|---|
| `SPOF_ALPHA` | 0.4 | Betweenness weight in SPOF | No |
| `SPOF_BETA` | 0.3 | Cross-dept ratio weight in SPOF | No |
| `SPOF_GAMMA` | 0.2 | (1 − clustering) weight in SPOF | No |
| `SPOF_DELTA` | 0.1 | Entropy trend weight in SPOF | No |
| `SPOF_PERTURB_RANGE` | 0.15 | Weight perturbation range for sensitivity bands | **NEW** |
| `KNOWLEDGE_LAMBDA` | 0.3 | Knowledge risk blend weight in Enhanced SPOF | No |
| `SUCCESSION_W_STRUCT` | 0.40 | Structural overlap weight in compatibility score | No |
| `SUCCESSION_W_CLUST` | 0.25 | Clustering weight in compatibility score | No |
| `SUCCESSION_W_DOMAIN` | 0.35 | Domain overlap weight in compatibility score | No |
| `HEALTH_W_SILO` | 0.20 | Silo component weight in Org Health | No |
| `HEALTH_W_SPOF` | 0.35 | SPOF component weight in Org Health | No |
| `HEALTH_W_ENTROPY` | 0.20 | Entropy component weight in Org Health | No |
| `HEALTH_W_FRAG` | 0.25 | Fragmentation component weight in Org Health | No |
| `SILO_THRESHOLD` | ~~4.0~~ REMOVED | Fixed threshold — deprecated | **REMOVED** |
| `SILO_THRESHOLD_RATIO` | 3.0 | Departments per silo threshold | **NEW** |
| `FRAGMENTATION_LAMBDA` | 1.5 | Exponential decay parameter for fragmentation risk | **NEW** |
| `BETWEENNESS_EXACT_THRESHOLD` | 500 | Node count below which exact betweenness is computed | No |
| `BETWEENNESS_K_PIVOTS` | 200 | k-pivot count for approximate betweenness | No |
| `ENTROPY_SLOPE_THRESHOLD` | −0.05 | Weekly slope below which employee is flagged `withdrawing` | No |
| `ENTROPY_WINDOW_DAYS` | 30 | Rolling window for entropy and slope computation | No |
| `GRAPH_WINDOW_DAYS` | 30 | Rolling window for edge accumulation | No |
| `GRAPH_MIN_EVENTS` | 100 | Minimum events in window before DAG proceeds | No |
| `ONBOARDING_ALERT_DAY` | 60 | Tenure day at which onboarding alert fires | No |
| `CS_OVERLAP_THRESHOLD` | 0.6 | Minimum Jaccard overlap for community stability | **NEW** |
| `TRANSFER_MIN_SPOF_SCORE` | 0.5 | SPOF threshold for transfer plan generation | No |
| `CACHE_TTL_SEC` | 3600 | Redis cache TTL for graph snapshot responses | No |
| `CHANNEL_WEIGHT_GITHUB` | 3.0 | Semantic weight for GitHub channel | No |
| `CHANNEL_WEIGHT_CALENDAR` | 2.5 | Semantic weight for Calendar channel | No |
| `CHANNEL_WEIGHT_JIRA` | 1.5 | Semantic weight for Jira channel | No |
| `CHANNEL_WEIGHT_EMAIL` | 1.0 | Semantic weight for Email (baseline) | No |
| `CHANNEL_WEIGHT_SLACK` | 1.0 | Semantic weight for Slack (baseline) | No |
| `CHANNEL_WEIGHT_TEAMS` | 1.0 | Semantic weight for Microsoft Teams | No |
| `DIRECTION_WEIGHT_REVIEWED` | 1.5 | Depth multiplier for code review | No |
| `DIRECTION_WEIGHT_ASSIGNED` | 1.2 | Depth multiplier for task assignment | No |
| `DIRECTION_WEIGHT_INVITED` | 1.0 | Depth multiplier for calendar invite | No |
| `DIRECTION_WEIGHT_MENTIONED` | 0.8 | Depth multiplier for @mention | No |
| `DIRECTION_WEIGHT_SENT` | 0.7 | Depth multiplier for message sent | No |

---

## 15. References

### Foundational (retained from v1)

- Burt, R. S. (1992). *Structural Holes: The Social Structure of Competition.* Harvard University Press.
- Daft, R. L., & Lengel, R. H. (1986). Organizational information requirements, media richness and structural design. *Management Science, 32*(5), 554–571.
- Granovetter, M. S. (1973). The strength of weak ties. *American Journal of Sociology, 78*(6), 1360–1380.
- Nonaka, I., & Takeuchi, H. (1995). *The Knowledge-Creating Company.* Oxford University Press.
- Brandes, U. (2001). A faster algorithm for betweenness centrality. *Journal of Mathematical Sociology, 25*(2), 163–177.
- Lee, T. W., & Mitchell, T. R. (1994). An alternative approach: The unfolding model of voluntary employee turnover. *Academy of Management Review, 19*(1), 51–89.
- Nemhauser, G. L., Wolsey, L. A., & Fisher, M. L. (1978). An analysis of approximations for maximizing submodular set functions. *Mathematical Programming, 14*(1), 265–294.
- EEOC (1978). Uniform Guidelines on Employee Selection Procedures. 29 C.F.R. § 1607.4D.

### Added in v3 — Empirical grounding (2022–2025)

- Yang, L., Holtz, D., Jaffe, S., Suri, S., Sinha, S., Weston, J., Joyce, C., Shah, N., Sherman, K., & Teevan, J. (2022). The effects of remote work on collaboration among information workers. *Nature Human Behaviour, 6*(1), 43–54. https://doi.org/10.1038/s41562-021-01196-4
  *Used in: §2.2.7 (channel weight hybrid-work caveat), §13 Limitation 10.*

- Younis, S., Ahsan, A., & Chatteur, F.M. (2023). An employee retention model using organizational network analysis for voluntary turnover. *Social Network Analysis and Mining, 13*(1), 28. https://doi.org/10.1007/s13278-023-01031-w
  *Used in: §5.3.1 (centrality / market-visibility tension).*

- Moore, A. et al. (2023). Dynamic resource-acquisition strategies: Analysis of survivor betweenness centrality relationships after downsizing. *Journal of Occupational and Organizational Psychology*. https://doi.org/10.1111/joop.12418
  *Used in: §12.3 (post-disruption network resilience).*

- Ghanem, R. et al. (2024). Explainable Machine Learning and Graph Neural Network Approaches for Predicting Employee Attrition. *Proceedings of the 2024 Sixteenth International Conference on Contemporary Computing (IC3-2024)*, Noida, India. https://doi.org/10.1145/3675888.3676058
  *Used in: §7.4.2 (XAI / explainability gap in churn model).*

- AlKetbi, A., Yam, P., Marti, G., AlNuaimi, K., Jaradat, R., & Henschel, A. (2025/2026). Network Contagion in Financial Labor Markets: Predicting Turnover in Hong Kong. In H. Cherifi et al. (eds.), *Complex Networks & Their Applications XIV*. Studies in Computational Intelligence, vol. 1265. Springer. https://arxiv.org/abs/2509.08001
  *Used in: §7.4.1 (turnover contagion feature), §13 Limitation 11.*

- Longa, A., Lachi, V., Gravina, R., Bontempelli, C., Lepri, B., Moschitti, A., Passerini, A., & Crestani, F. (2023). Graph Neural Networks for Temporal Graphs: State of the Art, Open Challenges, and Opportunities. *Transactions on Machine Learning Research*. https://openreview.net/forum?id=pHCdMat0gI
  *Used in: §13 Limitation 12 (discrete vs. continuous-time GNN).*

---