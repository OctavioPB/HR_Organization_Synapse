# Math Model — Organizational Synapse

A single reference for every quantity the platform computes, **written from the code as it runs today**. Each formula links to its implementation.

[MODEL.md](MODEL.md) is the companion research document. It holds the theoretical justification, the literature, and the v2/v3 change history. Several of its sections still read "unchanged from original" and point at a v1 that is no longer in the repo; this document fills those gaps from the source. Where the code and MODEL.md disagree, §12 lists the difference so it can be fixed rather than hidden.

---

## Contents

1. [Notation](#1-notation)
2. [Graph construction](#2-graph-construction)
3. [Node metrics](#3-node-metrics)
4. [Interaction entropy and trend](#4-interaction-entropy-and-trend)
5. [SPOF score](#5-spof-score)
6. [Knowledge risk and enhanced SPOF](#6-knowledge-risk-and-enhanced-spof)
7. [Silo detection](#7-silo-detection)
8. [Anomaly detection](#8-anomaly-detection-isolation-forest)
9. [Churn models](#9-churn-models)
10. [Prescriptive models](#10-prescriptive-models)
11. [Org health score](#11-org-health-score)
12. [Spec vs. implementation](#12-spec-vs-implementation)
13. [How the models are validated](#13-how-the-models-are-validated)

---

## 1. Notation

| Symbol | Meaning |
|---|---|
| G = (V, E) | Directed weighted collaboration graph over a rolling window W (default 30 days) |
| w(u,v) | Sum of event weights from u to v inside W |
| N(v) | Undirected neighbourhood: predecessors ∪ successors of v |
| dept(v) | Department attribute on node v |
| R(x, V) | Percent rank of x in population V: `#{y ∈ V : y < x} / (|V| − 1)` |
| clip(x, a, b) | min(max(x, a), b) |

---

## 2. Graph construction

**Code:** [`graph/builder.py`](../graph/builder.py), [`graph/edge_weights.py`](../graph/edge_weights.py)

```
Events e = (s, t, channel, direction, ts, weight)   with ts ∈ [T − W, T]
          and consent(s) ∧ consent(t) ∧ active(s) ∧ active(t)

w(s, t) = Σ_e weight(e) · ω_c(channel(e)) · ω_d(direction(e))
```

| Channel | ω_c | | Direction | ω_d |
|---|---|---|---|---|
| github | 3.0 | | reviewed | 1.5 |
| calendar | 2.5 | | assigned | 1.2 |
| jira | 1.5 | | invited | 1.0 |
| email / slack / teams | 1.0 | | mentioned | 0.8 |
| | | | sent | 0.7 |

A GitHub review is therefore worth 4.5 Slack-message-equivalents per event, and a Slack message 0.7. The rationale is in MODEL.md §2.2 (media richness, tacit-knowledge depth). For remote-heavy orgs, consider lowering `CHANNEL_WEIGHT_CALENDAR` to 1.8–2.0 (§2.2.7).

- The graph is **directed**. A→B and B→A are separate edges, which keeps asymmetry (who initiates and who receives).
- Every loader that turns `raw_events` into edges (graph build, scenarios, succession, NL tools, the dashboard edge endpoints) uses the same `weighted_edge_sql()`, so all metrics see one graph. `CHANNEL_WEIGHTING=off` reverts to raw counts for A/B comparison.
- The consent and active filters run **in SQL**, so excluded employees never become nodes.
- **Exception:** the ChurnGAT and temporal GNN use `log1p(event count)` edge weights. Their trained checkpoints depend on that, so they are unchanged until the next retrain.

---

## 3. Node metrics

**Code:** [`graph/metrics.py`](../graph/metrics.py)

| Metric | Definition | Implementation detail |
|---|---|---|
| **Betweenness** BC(v) | Σ_{s≠v≠t} σ(s,t\|v) / σ(s,t), normalized | Computed on the **undirected** projection, with tie strength w(u,v) + w(v,u) and path cost `1 / strength`, so frequent collaborators are "closer". Exact for n ≤ 500; above that, Brandes k-pivot sampling with k = 200 and `seed = 42` (error ≈ O(1/√k)). See the note below |
| **Degree** d_in, d_out | in/out degree ÷ (n − 1) | NetworkX degree centrality |
| **Clustering** CC(v) | Triangles through v ÷ possible triangles | On the **undirected** projection |
| **Community** | Louvain, max modularity | `random_state = 42`; for n > 500, up to 8 parallel trials and the best modularity wins |
| **Cross-department ratio** CDR(v) | `|{(v,u) ∈ E_out : dept(u) ≠ dept(v)}| / |E_out(v)|` | Counts out-edges (not weights); 0 if v has no out-edges |

> **Why undirected, and why it took two attempts.** [Decisions.md §6](Decisions.md) argues for undirected betweenness: knowledge flows A→B→C through B whoever initiated each tie. The code was directed until v3.4.
>
> The first switch, made with the old weights, cut *synthetic* Precision@critical from 100 % to 50 %. The synthetic generator defines bridges purely by who *initiates* cross-department contact, so directed betweenness suited it.
>
> On the real SNAP email-Eu-core network, with all 986 people removed in turn, undirected BC tracks measured damage better than directed: raw BC 0.87 vs 0.86 Spearman, and **SPOF 0.85 vs 0.81** once the weights were refitted (§5).
>
> The refitted weights also pass every synthetic gate, so v3.4 adopts undirected BC.
---|---|
> | SPOF with directed BC (current) | 0.87 |
> | SPOF with undirected BC | 0.89 |
> | Raw directed BC | 0.90 |
> | Raw undirected BC | 0.91 |
>
> The 100 % → 50 % precision drop therefore looks like an artifact of the synthetic generator, which defines bridges by who *initiates* cross-department contact. The SPOF composite also tracks damage no better than raw betweenness: the CDR, clustering and entropy terms add little for this purpose.

---

## 4. Interaction entropy and trend

**Code:** [`ml/features/feature_extractor.py`](../ml/features/feature_extractor.py)

The 28 days ending on the snapshot date are split into 4 equal 7-day weeks, k = 0…3 (oldest first), counted back from the snapshot. Only events between consenting, active employees count. For each employee and week:

```
p_k(v, u) = count_k(v → u) / Σ_x count_k(v → x)

H_k(v) = − Σ_u p_k(v,u) · log₂ p_k(v,u)        (bits; 0 if no activity)
```

H measures how *spread* someone's collaboration is. A falling trend means the person is interacting with fewer distinct people, which is the **withdrawal** signal. It is the earliest-moving input in the system and also the noisiest one, which is why it carries the smallest weight in SPOF.

### 4.1 Robust, shrunk trend

A straight-line fit through 4 noisy points has two failure modes: one odd week (holiday, offsite) swings it, and people with only a few events a week dominate both tails. The trend is therefore estimated in three steps:

```
1. raw slope      s_v  = median over pairs i<j of (H_j − H_i)/(j − i)              Theil-Sen, 6 pairs
2. its noise      v_v  = Σ_k w_k² · (Var(H_k) + σ²_extra) / (Σ_k w_k²)²,   w_k = k − 1.5
                  Var(H_k) = max( (Σ p·log₂²p − H²)/n_k , 1/n_k )                  delta method, floored; empty week = 1
                  σ²_extra = max(0, median_v (RSS_v/2 − mean_k Var(H_vk)))         week-to-week variation beyond sampling
3. shrinkage      τ²   = DerSimonian–Laird between-employee variance of s given v
                  B_v  = τ² / (τ² + v_v)
                  entropy_trend(v) = m + B_v (s_v − m),   m = median_v s_v
                  sd_v = √(B_v · v_v)

withdrawing(v)  ⇔  P(true slope < −0.05) = Φ((−0.05 − entropy_trend) / sd) ≥ 0.90
```

- **Theil-Sen** fully ignores an outlier in a middle week. An outlier in the *latest* week moves it about halfway, because with 4 points a collapse in the newest week looks the same as the start of real withdrawal. The sudden-withdrawal test (§4.2) adjudicates that case.
- **Shrinkage** pulls an employee toward the population center in proportion to how noisy their estimate is. On the synthetic org, a low-activity employee keeps a median 23 % of their raw slope and a well-measured one keeps 64 %.
- **DerSimonian–Laird** is the standard random-effects estimator for units with heterogeneous known variances. The simpler method of moments, Var(s) − mean(v), collapsed τ² to ≈0 on real data and shrank everyone onto the median.
- **σ²_extra** captures how real collaboration sets shift week to week (projects, meetings, travel) beyond multinomial sampling noise. Without it the posterior is over-confident on real e-mail. It is estimated from each person's scatter around their own trend line, using a median because a mean is dominated by a few erratic people. It comes out **0** on the synthetic org and **0.37 bits²** on real e-mail.
- **The variance formula** over-states true sampling variance by about 1.5–3× in isolation. It was still the best of four variants inside the full pipeline.
- **The withdrawing flag is probabilistic.** `ENTROPY_SLOPE_THRESHOLD` and `ENTROPY_WITHDRAW_CONFIDENCE` are configurable.

| | Synthetic org (`entropy_trend_evaluator.py`) | Real e-mail (`real_network_evaluator.py`) |
|---|---|---|
| Slope RMSE vs OLS | 0.300 → **0.140** (−53 %); low-activity −55 % | no ground truth |
| False positives: stable / trendless people flagged | **2.5 %** (old rule: 50.8 %) | **2.0 %** on week-shuffled histories (old rule: 48.9 %; 12.5 % without σ²_extra) |
| Power | 37 % of planted withdrawers; 84 % at ≥ 20 events/week | **58 %** when the last 2 weeks are re-routed to the person's top-2 recipients |

Recall is limited by data, not by the method: a person with a few events a week cannot support a confident call. On the demo database the old rule flagged 53 of 118 people; the new flag flags 0–1 per week.

### 4.2 Sudden withdrawal: recent activity vs. personal baseline

**Code:** [`ml/features/changepoint.py`](../ml/features/changepoint.py), alert type `sudden_withdrawal` (migration 018)

The shrunk trend reacts slowly to an abrupt stop, so sudden drops get their own test. It compares each employee's last 3 weeks of *initiated* interactions with their own previous 9 weeks. Inbound messages are excluded because they keep arriving after someone withdraws.

```
baseline weeks b_1…b_9:  m = mean, v = max(var, m)          → one week ~ NB(r, p),  r = m²/(v − m),  p = r/(r + m)
recent weeks x_1…x_3:    total X ~ NB(3r, p) if nothing changed

sudden withdrawal  ⇔  P(NB(3r, p) ≤ X) < 0.001   and   mean(x) ≤ 0.5·m   and   m ≥ 7 / week
```

Weekly totals remove the weekday cycle, and the person's own baseline variance means bursty people need more evidence. Twelve weeks fit inside the 90-day `raw_events` retention.

**Why not a daily change-point.** The first version, a Poisson single change-point on 28 days of daily counts calibrated in simulation to 0.05 % false alarms, failed on real e-mail:
- Real activity has a strong weekly cycle: weekends run at ≈0.1× weekdays.
- Real activity is overdispersed: variance is a median 4.8× the mean.
- It false-alarmed on **32–43 %** of order-shuffled real histories.
- Seasonal and quasi-Poisson corrections cut that to ≈14 %, but power collapsed to 6–16 %.

| On real e-mail (`real_network_evaluator.py`) | Poisson daily change-point (v1) | NB personal baseline (current) |
|---|---|---|
| False alarms on shuffled real histories | 32–43 % | **1.5 %** |
| Power, sustained 70 % drop | — | 22 % |
| Power, sustained 90 % drop | — | **53 %** (higher for more active people) |

The 9 + 3 weeks, α = 0.001 operating point was chosen from a grid (10 + 2 and 9 + 3 weeks; α 0.001–0.01). Looser settings raise power to 72 % at 4.6 % false alarms.

**Alert-volume control (v3.4).** The test runs on every employee every day, so a fixed per-person α produces false alerts in proportion to headcount. On real e-mail the raw NB p-values are also ≈15× too small, because a 9-week baseline under-states true variability. So:
1. Calibrate each p-value against a pooled null: every employee's 12 weeks shuffled ≈20,000 times in total (`SUDDEN_WITHDRAWAL_NULL_DRAWS`), giving p_cal = (1 + #{null ≤ p}) / (1 + #null).
2. Apply **Benjamini–Hochberg** false-discovery-rate control across employees at q = 0.10 (`SUDDEN_WITHDRAWAL_FDR`).

The only assumption is the one the null already makes: with no real drop, a person's weeks are exchangeable.

| Per run (~117 eligible people, real e-mail) | Raw α = 0.001 | Calibrated + BH (current) |
|---|---|---|
| False alerts on the shuffled null | 1.4–1.7 | **0.00** |
| Injected 90 % drops caught | ≈50 % | 36 % (3 seeds pooled) |

- **Alerting.** `graph_builder_dag` → `detect_sudden_withdrawal` fires once per employee per recent window, at severity `high` for drops of 80 % or more and `medium` otherwise. Unshuffled real data still produces alerts for genuine drops, and vacations look exactly like one. The alert text asks HR to check for leave before acting, and HRIS leave dates could suppress these (open item).
- **Honest limit.** One person's e-mail volume is noisy, so this is a coarse safety net for near-total stops, not an early-warning system for subtle disengagement.
> **Fixed November 2026: the trend had never been computed on real data.** The bucketing SQL used `EXTRACT(EPOCH FROM (date − date))`. In Postgres `date − date` is an integer, so the query failed, and every stored `entropy_trend` was 0. That left the SPOF δ term, the anomaly detector's entropy features, and health `entropy_risk` without their signal.
>
> The old buckets were also unequal: a 30-day window gave the last "week" about 10 days, which inflates its entropy and biased slopes upward (median +0.08 on the demo data; −0.02 after the fix).
>
> A raw 4-point slope is noisy: on the demo data its 10th–90th percentile range is −0.46 to +0.38 bits/week, wide enough that a fixed −0.05 cut sits inside the noise. §4.1 is the fix.

---

## 5. SPOF score

**Code:** [`graph/risk_scorer.py`](../graph/risk_scorer.py) · theory: [MODEL.md §5](MODEL.md#5-spof-score-formula)

### 5.1 Formula

Every component is converted to a percent rank over the population **before** weighting, so each weight controls its nominal share instead of letting the most skewed metric (betweenness) dominate:

```
SPOF(v) = clip(  α · R(BC(v))                 BC undirected (§3)
               + β · R(CDR(v))
               + γ · R(1 − CC(v))
               − δ · R(entropy_trend(v)) ,  0, 1)

α, β, γ, δ = 0.50, 0.325, 0.05, 0.125        (SPOF_ALPHA … SPOF_DELTA; v2 was 0.4, 0.3, 0.2, 0.1)
```

The entropy term subtracts: the most-withdrawing employee has percentile 0 and loses nothing, while the most-engaging employee loses up to δ.

**Where the weights come from (v3.4).** The v2 weights were theoretically ordered but never fitted. "Damage if this person leaves" can be *measured*: remove each person and record the increase in average path length plus lost reachability. That was done for all 986 people on the real network and for all 100 in the synthetic org.

1. A least-squares fit of damage on the component ranks gave **BC ≈ 0.81, CDR ≈ 0.13, (1 − CC) ≈ −0.06**. Clustering, at 0.2 in v2, carried no damage signal.
2. Pure betweenness fails the synthetic archetype gates, though: sole experts and silo members score too high. So the final weights come from a **constrained search** over 575 weight sets in 0.025 steps. Only sets passing every gate were kept: all bridges critical, precision@critical ≥ 80 %, sole experts < 0.7, silo members < 0.5. The survivors were ranked by damage agreement on both datasets.
3. The chosen set sits in the best region (α ≈ 0.5, β ≈ 0.3, small γ, δ ≈ 0.125) with the widest gate margins. A small γ remains necessary, because tightly clustered silo members must not look like bridges.

| Spearman(SPOF, measured removal damage) | v2 (0.4/0.3/0.2/0.1, directed) | v3.4 (0.50/0.325/0.05/0.125, undirected) |
|---|---|---|
| Real network, all 986 removals | 0.81 | **0.85** |
| Synthetic org, all 100 removals | 0.35 | **0.57** |

Raw undirected betweenness alone reaches 0.87–0.91 on the real network. The extra terms cost a little damage agreement in exchange for the archetype separation HR needs, such as not flagging sole experts and silo members as structural SPOFs. δ is a departure-likelihood signal, not a damage signal, so it can't be fitted on static data.

**Honest limits.** The weights are fitted to two networks: one real research institution and one synthetic org. A different organization could prefer different weights, which is why the departure backtesting harness (MATH_MODEL §13, open) matters. Rank tiers also flag a roughly fixed share of the org. On the real network 12.6 % score ≥ 0.7, whatever its structure. A structure-adaptive rule (log-BC outliers beyond median + z·MAD) was tested and flagged almost no one on either dataset, so it was not adopted.

### 5.2 Uncertainty: P(critical)

A person's rank over a 30-day window is noisy. On the real e-mail data, resampling a window's events kept only **≈77 %** of the top-20 brokers, and a baseline #3 landed anywhere between #2 and #20. Data noise, not weight choice, is the main uncertainty, so:

```
for b in 1…30 (SPOF_UNCERTAINTY_SAMPLES):
    resample the window's events with replacement → rebuild graph → recompute BC, CDR (and CC if γ > 0)
    draw weights ~ Dirichlet(200 · w_central)        (zero weights stay zero)
    score_b(v) = SPOF formula on the replicate

P(critical)(v) = share of replicates with score_b(v) ≥ 0.7
score_lo, score_hi = 10th / 90th percentile of score_b      (an 80 % interval)
```

| Flag | Rule | Action |
|---|---|---|
| `critical` | score ≥ 0.7 **and** P(critical) ≥ 0.9 | Automatic `spof_critical` alert |
| `critical_uncertain` | score ≥ 0.7, P(critical) < 0.9 | Human review, **no** automatic alert |
| `warning` | 0.5 – 0.7 | Cross-training |
| `elevated` | 0.4 – 0.5 | Watch entropy weekly |
| `normal` | < 0.4 | — |

This replaces the v2 two-corner weight bracket: its strict α > β > γ > δ ordering doesn't hold for fitted weights (γ < δ), and it ignored data noise entirely. Cost: one betweenness computation per replicate, ≈0.4 s at 700 people, so ≈12 s per daily run. For larger orgs, k-pivot betweenness keeps each replicate bounded. On the synthetic org, which has no event data and so only weight jitter, the second planted bridge (0.75) has P(critical) = 0.83 and is held for review.
![Employee detail — SPOF components, ego network, What-If](screenshots/employee-detail.png)

---

## 6. Knowledge risk and enhanced SPOF

**Code:** [`graph/knowledge_risk.py`](../graph/knowledge_risk.py)

The inputs are document contribution counts c(v, d) per employee v and domain d, taken from Confluence and Notion.

```
sole(v, d)  ⇔  c(v,d) > 0  and  v is the only contributor to d

K(v) = min(1,  0.5 · |sole domains of v| / |domains of v|
             + 0.3 · docs(v) / max_u docs(u)
             + 0.2 · |domains of v| / |all domains| )

SPOF⁺(v) = (1 − δ_k) · SPOF(v) + δ_k · K(v),        δ_k = KNOWLEDGE_DELTA = 0.3  (KNOWLEDGE_LAMBDA accepted as alias)
```

Per-domain expertise is `c(v,d) / max_u c(u,d)`, multiplied by 1.2 for a sole expert and capped at 1.

**Why it's separate:** a sole expert with no cross-department bridges is a knowledge risk, not a structural one. The validation suite (see §13) confirms that graph-only SPOF keeps sole experts below the critical line (max 0.63). K(v) is the signal that catches them.

---

## 7. Silo detection

**Code:** [`graph/silo_detector.py`](../graph/silo_detector.py)

Silos are evaluated per **department**, not per Louvain community. Department is the unit HR can act on, and Louvain can merge a siloed department into a hub's community.

The measure is **Coleman's homophily index**, which compares a department with what random mixing would give a department of its size:

```
p_D = Σ w(u,v) over u ∈ D, v ∈ D  /  Σ w(u,v) over u ∈ D        observed internal share of D's outgoing volume
q_D = (n_D − 1) / (N − 1)                                       share expected if each interaction picked a random colleague
H_D = (p_D − q_D) / (1 − q_D)                                   0 = random, 1 = fully closed, < 0 = outward-looking

silo      if H_D ≥ 0.50     (SILO_HOMOPHILY_THRESHOLD)
critical  if H_D ≥ 0.75     (SILO_HOMOPHILY_CRITICAL)           departments with < 3 people never flagged
```

- **Why not the old ratio.** It was `internal edges / outgoing edges`, with a threshold of 2.5. That ratio grows with department size even under pure random mixing, where its expected value is (n−1)/(N−n). In the 120-person demo org that is ≈1.0 for Engineering (60 people) and ≈0.24 for HR (24 people). One threshold therefore over-flagged large departments and **inverted the ranking**: Engineering scored most siloed (ratio 5.95) while HR, the most inward relative to its size (12.4× its random baseline), scored least (3.0).
- **Why volume, not unique edges.** The random baseline q assumes each *interaction* picks a target. Unique edges saturate inside small departments (19 colleagues are at most 19 edges, 40 outsiders 40), which understates p exactly where it matters.
- Only D's *outgoing* activity counts, so a department isn't excused because others reach into it. The legacy `isolation_ratio` is still stored on alerts for continuity but no longer drives detection. The scenario planner now counts silos with this same detector; it previously used its own threshold of 4.0.

---

## 8. Anomaly detection (Isolation Forest)

**Code:** [`ml/anomaly/isolation_forest.py`](../ml/anomaly/isolation_forest.py)

The feature vector (8 dimensions, z-scored with `StandardScaler`) is:

```
[ BC, d_in, d_out, CC, ΔBC_7d, Δd_out_7d, H_current, entropy_trend ]
```

`IsolationForest(n_estimators=100, contamination=0.05)`. The sklearn `decision_function` is min-max inverted onto [0, 1], so 1 = most anomalous:

```
a(v) = 1 − (s(v) − min s) / (max s − min s)
severity:  a > 0.9 critical · a > 0.75 high · else medium
```

The 7-day deltas matter most for **bridge collapse**, where current values look ordinary but betweenness has just dropped sharply.

---

## 9. Churn models

### 9.1 ChurnGAT (graph)

**Code:** [`ml/gnn/feature_builder.py`](../ml/gnn/feature_builder.py), [`ml/gnn/model.py`](../ml/gnn/model.py) · card: [MODEL_CARD_CHURN.md](MODEL_CARD_CHURN.md)

There are 11 node features:

| # | Feature | Scaling |
|---|---|---|
| 0 | tenure | days / 3650, cap 1 |
| 1 | role level | level / 7 |
| 2 | PTO used | days / 90, cap 1 |
| 3–6 | BC, d_in, d_out, CC | as §3 |
| 7–8 | ΔBC_7d, Δd_out_7d | as §8 |
| 9 | H_current | cap 1 |
| 10 | entropy_trend | clip [−1, 1] |

Edge weight is `log1p(count)`. The architecture is:

```
GATConv(11 → 64 × 4 heads) → ELU → dropout 0.3
GATConv(256 → 64, 1 head)  → ELU
Linear(64 → 32) → ReLU → dropout → Linear(32 → 1) → σ        loss: BCEWithLogits
```

Two attention layers cover the 1-hop signal. Deeper stacks over-smooth a graph with diameter around 4.

### 9.2 Peer contagion (rule layer)

**Code:** [`etl/tasks/compute_peer_contagion.py`](../etl/tasks/compute_peer_contagion.py)

```
peer_churn_rate(v) = |N(v) ∩ departed(T − 180d, T)| / max(|N(v)|, 1)

peer_contagion_risk  if  peer_churn_rate(v) > 0.30
```

This sits on top of the GAT rather than inside it, so existing checkpoints stay valid ([MODEL.md §7.4.1](MODEL.md#741-turnover-contagion--a-missing-feature)).

### 9.3 Temporal GNN

**Code:** [`graph/temporal/model.py`](../graph/temporal/model.py)

A `GConvGRU` (Chebyshev K = 1, hidden 32) reads T − 1 weekly snapshots and predicts the features of week T. The anomaly signal is the masked MSE reconstruction error:

```
H_t = GConvGRU(X_t, A_t, H_{t−1}),   X̂_T = Linear(H_{T−1}),   err(v) = ‖X̂_T(v) − X_T(v)‖²
```

---

## 10. Prescriptive models

### 10.1 Succession compatibility

**Code:** [`graph/succession.py`](../graph/succession.py) · [MODEL.md §8](MODEL.md#8-succession-compatibility-score)

The candidates are **border employees**: people outside the source's Louvain community who have at least one edge into it.

```
w̃(v,u) = w(v,u) / Σ_x w(v,x)                     (share of v's interaction budget)

J̃(s,c) = Σ_{u ∈ N(s) ∩ N(c)} min(w̃(s,u), w̃(c,u)) / Σ_{u ∈ N(s) ∪ N(c)} max(w̃(s,u), w̃(c,u))

compat(s,c) = 0.40 · J̃(s,c) + 0.25 · CC(c) + 0.35 · |dom(s) ∩ dom(c)| / max(|dom(s)|, 1)
```

Using proportional weights removes the tenure bias: raw weights grow with time at the company, not with structural fit.

### 10.2 Onboarding integration

**Code:** [`etl/tasks/compute_onboarding.py`](../etl/tasks/compute_onboarding.py)

This applies to hires within the last 180 days, compared against a cohort in the same 30-day tenure band:

```
OIS(v) = 0.5 · min(degree(v) / median_cohort_degree, 1)
       + 0.3 · min(cross_dept_edges_30d(v) / 5, 1)
       + 0.2 · CS_overlap(v)

CS_overlap(v) = |Com_t(v) ∩ Com_{t−7}(v)| / |Com_t(v) ∪ Com_{t−7}(v)|     (stable if ≥ 0.6)

alert  if  degree(v) ≤ p25_cohort  and  tenure(v) ≥ 60 days
```

CS_overlap compares *membership sets*, not Louvain IDs, because IDs are arbitrary from one run to the next.

### 10.3 Team composition

**Code:** [`graph/team_optimizer.py`](../graph/team_optimizer.py)

```
score(T) = 0.40 · bridge_coverage(T) + 0.35 · domain_coverage(T) + 0.25 · (1 − load(T))

bridge_coverage = fraction of requested dept pairs (d1,d2) with a member in d1 adjacent to d2 (or vice versa)
load(T)         = Σ_{v ∈ T} SPOF(v) / max possible load
```

Search is **greedy set-cover**. The coverage objective is submodular, so greedy is within (1 − 1/e) of optimal (Nemhauser et al., 1978). Members above the SPOF exclusion threshold (UI default 0.70) are skipped so already-overloaded people aren't piled on.

![Team optimizer — greedy set-cover results](screenshots/team-optimizer.png)

### 10.4 Reorg scenario impact

**Code:** [`graph/scenario_simulator.py`](../graph/scenario_simulator.py)

Operations (`remove`, `merge_depts`, `move_team`) are applied to an in-memory copy of G. The simulator reports:

```
Δpath%       = (L_after − L_before) / L_before × 100      L = avg shortest path, largest WCC
new_isolated = WCC_after − WCC_before
ΔSPOF(v)     = SPOF_after(v) − SPOF_before(v)   for the most affected employees
```

The validation suite combines these into `damage = Δpath% + 20 · new_isolated`.

![Scenario planner — before/after SPOF](screenshots/scenario-planner.png)

### 10.5 Structural equity

**Code:** [`etl/tasks/compute_equity.py`](../etl/tasks/compute_equity.py) · [MODEL.md §11](MODEL.md#11-structural-equity-analytics)

For dimension g ∈ {gender_group, tenure_band, level_band} and metric m ∈ {betweenness, degree, cross_dept_ratio}:

```
M(g, m) = median of m over consenting members of g        (only groups with ≥ k members; k = EQUITY_MIN_GROUP_SIZE = 5)

flag  if  M(g, m) < 0.8 · M(all, m)                        (EQUITY_DISPARITY_RATIO)
```

The 0.8 ratio borrows from the EEOC four-fifths rule **as an investigation trigger only**. Centrality is not a selection rate.

Groups smaller than k are suppressed twice: they are never persisted (`HAVING COUNT(*) >= k`) and never returned by the API. The API reports only how many groups were hidden, because a median over one to four people can reveal an individual's position.

![Structural equity — centrality by tenure band](screenshots/equity-dashboard.png)

---

## 11. Org health score

**Code:** [`graph/org_health.py`](../graph/org_health.py) · [MODEL.md §12](MODEL.md#12-org-health-score)

Every component is an **absolute** property of the network, so it is comparable across weeks and across organizations:

```
spof_risk    = (S_k − k/N) / (1 − k/N),   S_k = Σ top-k BC / Σ BC,   k = max(1, ⌈0.05 · N⌉)
silo_risk    = Σ_D n_D · max(H_D, 0)  /  Σ_D n_D                        (H_D from §7)
entropy_risk = |{v : withdrawing(v)}| / N                               (confident flag, §4.1)
frag_risk    = 1 − exp(−1.5 · (WCC − 1))

composite = 0.20 · silo + 0.35 · spof + 0.20 · entropy + 0.25 · frag
health    = 100 · (1 − composite)
```

The tiers are: healthy ≥ 80 · caution 60–79 · at_risk 40–59 · critical < 40.

- **spof_risk is brokerage concentration.** It answers "what share of all brokerage sits with the top 5 % of people", rescaled so an even spread scores 0 and total concentration scores 1 (`HEALTH_SPOF_TOP_FRACTION`).
  - It replaced mean SPOF. SPOF is built from rank percentiles, and the mean percentile of any population is ≈0.5, so mean SPOF ≈ 0.5·(α+β+γ−δ) ≈ 0.4 *for every organization*. On the demo data it moved 0.427–0.433 across eight weekly runs while carrying 35 % of the score.
  - After the change, the same eight weeks span 0.32–0.42 and track real changes in how concentrated brokerage is.
- **silo_risk is continuous.** It used to be `active silos / max(⌊departments/3⌋, 2)`, which pinned at 1.0 as soon as two departments were flagged. Outward-looking departments (H < 0) count as 0, so they can't cancel out a closed one elsewhere.
- **entropy_risk is the share of people withdrawing.** Averaging slopes let one strong withdrawer be cancelled by many mild engagers.
- **frag_risk is convex on purpose.** The first split (WCC 1 → 2) costs ≈ 0.78 of its weight right away, because a disconnected component cannot be bridged without intervention.

On the demo data the redesigned score spans 63.8–75.7 over eight weeks. The old score sat at 64.9 ± 0.1, because three of its four components were constant or saturated.

---

## 12. Spec vs. implementation

This is the drift ledger between [MODEL.md](MODEL.md) and the running code. Each open row is a backlog item, not a hidden assumption.

**Open**

| Area | Spec says | Code does | Why it stays open |
|---|---|---|---|
| Onboarding alert | DCP < 0.25 and tenure ≥ 60 | degree ≤ cohort p25 and tenure ≥ 60 | Code is the better rule (cohort-relative). MODEL.md §9 should be updated to match |
| GNN edge weights | Graph uses ω-weighted edges | ChurnGAT / temporal GNN use `log1p(count)` | Changing inputs invalidates trained checkpoints; adopt ω at next retrain |

**Resolved in November 2026**

| Area | Was | Now |
|---|---|---|
| Channel semantics | ω documented, `CHANNEL_WEIGHT_*` read nowhere; every event counted 1.0 | `graph/edge_weights.py` applied in every edge loader (§2) |
| SPOF perturbation | ±15 % proportional, narrower than documented | Spec corners as offsets; `lo` adjusted to keep strict ordering (§5.2) |
| Knowledge blend env var | Documented `KNOWLEDGE_LAMBDA` ignored | Accepted as alias of `KNOWLEDGE_DELTA` |
| Org health `spof_risk` | Mean of rank-based SPOF ≈ 0.4 for every org | Brokerage concentration (§11) |
| Org health `silo_risk` | Alert count / department-count floor; saturated at 1.0 | Size-weighted positive homophily (§11) |
| Org health `entropy_risk` | −mean(slope)/0.05 | Share of employees below the withdrawal threshold, as specified |
| Silo detection | Size-biased isolation ratio; thresholds 2.5 (detector), 4.0 (scenarios, Airflow env) | Coleman homophily on interaction volume, one detector everywhere (§7) |
| Entropy trend | SQL failed on Postgres, so every trend was 0; unequal buckets; no consent filter | Integer-day equal weeks, consent-filtered (§4) |
| Entropy estimator | 4-point OLS slope; "withdrawing" = any negative slope (≈51 % of stable people) | Theil-Sen + DerSimonian–Laird shrinkage; probabilistic flag, 2.5 % false positives (§4.1) |
| Sudden withdrawal | Not detected | Recent 3 weeks vs. personal 9-week NB baseline, `sudden_withdrawal` alerts, 1.5 % false alarms on real e-mail (§4.2) |
| Betweenness graph | Directed, despite the undirected intent | Undirected (tie strength summed), adopted with refitted weights (§3, §5.1) |
| SPOF weights | Theoretical, never fitted (0.4/0.3/0.2/0.1) | Fitted to measured removal damage under all archetype gates; Spearman with damage 0.81 → 0.85 real, 0.35 → 0.57 synthetic (§5.1) |
| SPOF uncertainty | Two fixed weight corners; data noise ignored | Event bootstrap + Dirichlet weights → P(critical) and an 80 % interval (§5.2) |
| Alert volume | Per-person α; false alerts grow with headcount | Calibrated p-values + Benjamini–Hochberg FDR (§4.2) |
| Silo index bias | Verified on synthetic data only | Unbiased on a real network: Spearman with size +0.00 under shuffled departments (legacy ratio +0.88) |

---

## 13. How the models are validated

`tests/validation/` builds synthetic organizations with **planted ground truth** (bridges, withdrawing employees, sole experts, silos) and checks that each model recovers them. There are 38 tests, they need no DB or Kafka, and they run in about 15 seconds. All 38 pass after the November 2026 corrections, including the 8 entropy-trend and change-point tests in `entropy_trend_evaluator.py` (§4.1–4.2). [`tests/unit/test_math_model_corrections.py`](../tests/unit/test_math_model_corrections.py) pins each resolved ledger row, including size-bias and ranking-inversion regressions for the silo index.

| Model | Key result | Gate |
|---|---|---|
| SPOF identification | Precision@critical 100 %, bridge recall 100 %, Spearman ρ 0.80 on archetype medians | ≥ 80 %, 100 %, ≥ 0.60 |
| SPOF vs. removal damage | ρ 0.90 (archetype medians), Precision@5 60 % | ≥ 0.80, ≥ 40 % |
| Churn | LogReg (3 feats) AUROC 0.722 vs GraphMLP 0.616 at N = 300 | both > 0.5; **baseline wins** |
| Isolation Forest | Severe recall 100 % (oracle), 75 % at production contamination | 100 %, ≥ 50 % |
| Entropy trend | Slope RMSE −53 %; withdrawal false positives 2.5 % (was 50.8 %) | ≥ 35 %, ≤ 6 % |
| Sudden withdrawal | False alarms 0.38 % on an overdispersed null; 100 % power for a 90 % drop | ≤ 0.5 %, ≥ 80 % |

### 13.1 Real-network test bed

Synthetic organizations share the models' assumptions, so a model can pass its own simulation and fail on real data, and twice it did (LESSONS_LEARNED §1.16–1.17). [`tests/validation/real_network_evaluator.py`](../tests/validation/real_network_evaluator.py) runs every model on the public SNAP **email-Eu-core** networks from a European research institution:
- **Static network:** 1,005 people and 25,571 edges, with 42 department labels.
- **Temporal network:** 332,334 timestamped e-mails over 803 days.

Real data has no "who is truly withdrawing" labels. So each test uses a **null built from the real data that is known to contain no signal**, or a **signal injected into real histories**:

| Model | Real-data null / injection | Result | Synthetic said |
|---|---|---|---|
| Silo index | Department labels shuffled across people | Size correlation **+0.00**, no silos flagged (legacy ratio +0.88) | unbiased |
| SPOF | All 986 people removed in turn, measured damage | Spearman **0.85** (v2: 0.81) | 0.57 individual, 0.90 archetype medians |
| Entropy flag | Each person's 4 weeks shuffled | **2.0 %** false positives (12.5 % before σ²_extra) | 2.5 % |
| Entropy flag | Last 2 weeks re-routed to top-2 recipients | **58 %** power | 37 % |
| Sudden withdrawal | Each person's 12 weeks shuffled | **1.5 %** false alarms (Poisson v1: 32–43 %) | 0.05 % (v1) |
| Sudden withdrawal | 90 % drop injected for 3 weeks | **53 %** power per person | 94–100 % (v1) |
| Sudden withdrawal, population | Everyone's weeks shuffled, one run ≈ 117 people | **0.00** false alerts per run (raw α: 1.4–1.7) | — |

Fetch the data with `python scripts/fetch_external_data.py`; it is not committed, because SNAP states no redistribution license. The tests skip when it is absent, and CI fetches it on a best-effort basis. Please cite Yin et al. (KDD 2017), Leskovec et al. (TKDD 2007) and Paranjape et al. (WSDM 2017) when publishing results.

The full tables are in the [README](../README.md#validation--results). What these results mean for decisions is in [LESSONS_LEARNED.md](LESSONS_LEARNED.md).

---

*OPB · Octavio Pérez Bravo · Data & AI Strategy Architect*
