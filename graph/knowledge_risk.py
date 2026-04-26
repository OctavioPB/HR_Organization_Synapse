"""Knowledge risk quantification — domain expertise concentration scoring.

This module adds the epistemic risk dimension to the existing graph-based SPOF
score.  Where the SPOF score answers "who is critical to information flow,"
knowledge risk answers "who holds unique domain knowledge that cannot be
redistributed."

Scoring formula
───────────────
For each employee i:

  knowledge_score_i =
      α_sole * sole_expert_fraction_i     # fraction of their domains where
                                          # they are the sole contributor
    + α_vol  * normalised_doc_count_i     # relative documentation output
    + α_brd  * normalised_domain_count_i  # breadth of domains covered

  enhanced_spof_i = (1 − δ_k) * graph_spof_i + δ_k * knowledge_score_i

Default weights (configurable via env vars):
    KNOWLEDGE_ALPHA_SOLE  = 0.5   (sole expertise dominates)
    KNOWLEDGE_ALPHA_VOL   = 0.3
    KNOWLEDGE_ALPHA_BRD   = 0.2
    KNOWLEDGE_DELTA       = 0.3   (knowledge = 30% of enhanced SPOF)

Pure functions (testable without DB):
    compute_knowledge_scores_from_contributions(contributions, active_employees)
    compute_sole_experts(contributions) → set[(employee_id, domain)]

DB-backed functions:
    load_contributions(conn) → dict[(employee_id, domain), count]
    compute_and_persist(snapshot_date, conn)
    get_impact_statement(employee_id, conn) → dict
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger(__name__)

# ─── Configurable weights ─────────────────────────────────────────────────────

_ALPHA_SOLE  = float(os.environ.get("KNOWLEDGE_ALPHA_SOLE", "0.5"))
_ALPHA_VOL   = float(os.environ.get("KNOWLEDGE_ALPHA_VOL",  "0.3"))
_ALPHA_BRD   = float(os.environ.get("KNOWLEDGE_ALPHA_BRD",  "0.2"))
_DELTA_K     = float(os.environ.get("KNOWLEDGE_DELTA",      "0.3"))


# ─── Pure computation ─────────────────────────────────────────────────────────


def compute_sole_experts(
    contributions: dict[tuple[str, str], int],
) -> set[tuple[str, str]]:
    """Return the set of (employee_id, domain) pairs where the employee is the
    sole contributor (no other active employee has docs in that domain).

    Args:
        contributions: Map (employee_id, domain) → doc_count.

    Returns:
        Set of (employee_id, domain) tuples that are sole experts.
    """
    # domain → set of contributing employees
    domain_contributors: dict[str, set[str]] = defaultdict(set)
    for (emp_id, domain), count in contributions.items():
        if count > 0:
            domain_contributors[domain].add(emp_id)

    sole: set[tuple[str, str]] = set()
    for (emp_id, domain), count in contributions.items():
        if count > 0 and len(domain_contributors[domain]) == 1:
            sole.add((emp_id, domain))
    return sole


def compute_knowledge_scores_from_contributions(
    contributions: dict[tuple[str, str], int],
) -> dict[str, dict]:
    """Compute per-employee knowledge risk metrics from contribution counts.

    Args:
        contributions: Map (employee_id, domain) → doc_count.
                       Only entries with doc_count > 0 are meaningful.

    Returns:
        Dict employee_id → {
            knowledge_score: float [0, 1],
            sole_expert_count: int,
            domain_count: int,
            doc_count: int,
            sole_expert_domains: list[str],
            expertise_per_domain: dict[domain, {doc_count, is_sole_expert, expertise_score}],
        }
    """
    if not contributions:
        return {}

    sole_pairs = compute_sole_experts(contributions)

    # Per-employee aggregates
    emp_doc_count:    dict[str, int]       = defaultdict(int)
    emp_domains:      dict[str, set[str]]  = defaultdict(set)
    emp_sole_domains: dict[str, list[str]] = defaultdict(list)
    domain_detail:    dict[str, dict]      = defaultdict(dict)  # emp → {domain: {...}}

    for (emp_id, domain), count in contributions.items():
        if count <= 0:
            continue
        emp_doc_count[emp_id] += count
        emp_domains[emp_id].add(domain)
        is_sole = (emp_id, domain) in sole_pairs
        if is_sole:
            emp_sole_domains[emp_id].append(domain)
        domain_detail[emp_id][domain] = {
            "doc_count":   count,
            "is_sole_expert": is_sole,
        }

    if not emp_doc_count:
        return {}

    org_max_docs    = max(emp_doc_count.values(), default=1)
    org_domain_count = len({d for _, d in contributions})
    all_employees   = list(emp_doc_count.keys())

    results: dict[str, dict] = {}
    for emp_id in all_employees:
        doc_count    = emp_doc_count[emp_id]
        domain_count = len(emp_domains[emp_id])
        sole_count   = len(emp_sole_domains[emp_id])

        sole_fraction    = sole_count / max(domain_count, 1)
        norm_doc_count   = doc_count / org_max_docs
        norm_domain_count = domain_count / max(org_domain_count, 1)

        knowledge_score = min(
            1.0,
            _ALPHA_SOLE * sole_fraction
            + _ALPHA_VOL  * norm_doc_count
            + _ALPHA_BRD  * norm_domain_count,
        )

        # Per-domain expertise_score (for employee_knowledge table)
        for domain, detail in domain_detail[emp_id].items():
            # Expertise = normalised doc_count in this domain + sole bonus
            domain_max = max(
                contributions.get((e, domain), 0) for e in all_employees
            )
            expertise = (detail["doc_count"] / max(domain_max, 1)) * (
                1.2 if detail["is_sole_expert"] else 1.0
            )
            domain_detail[emp_id][domain]["expertise_score"] = min(1.0, expertise)

        results[emp_id] = {
            "knowledge_score":     round(knowledge_score, 4),
            "sole_expert_count":   sole_count,
            "domain_count":        domain_count,
            "doc_count":           doc_count,
            "sole_expert_domains": emp_sole_domains[emp_id],
            "expertise_per_domain": domain_detail[emp_id],
        }

    return results


def merge_with_graph_spof(
    knowledge_scores: dict[str, dict],
    graph_spof: dict[str, float],
    delta_k: float = _DELTA_K,
) -> dict[str, float]:
    """Compute enhanced SPOF = (1-δ) * graph_spof + δ * knowledge_score.

    Args:
        knowledge_scores: From compute_knowledge_scores_from_contributions.
        graph_spof: Map employee_id → graph-based SPOF score [0, 1].
        delta_k: Knowledge weight (0 = pure graph, 1 = pure knowledge).

    Returns:
        Map employee_id → enhanced_spof_score.
    """
    all_ids = set(knowledge_scores) | set(graph_spof)
    return {
        emp_id: round(
            (1 - delta_k) * graph_spof.get(emp_id, 0.0)
            + delta_k * knowledge_scores.get(emp_id, {}).get("knowledge_score", 0.0),
            4,
        )
        for emp_id in all_ids
    }


# ─── DB-backed functions ──────────────────────────────────────────────────────


def load_contributions(conn) -> dict[tuple[str, str], int]:
    """Aggregate document contributions from document_knowledge.

    Returns:
        Dict (employee_id, domain) → total_doc_count.
        Includes both author_id and all contributor_ids.
    """
    with conn.cursor() as cur:
        # Author contributions
        cur.execute(
            """
            SELECT author_id::text, unnest(domain_tags) AS domain, COUNT(*) AS cnt
            FROM document_knowledge
            WHERE author_id IS NOT NULL
            GROUP BY 1, 2
            """
        )
        contributions: dict[tuple[str, str], int] = defaultdict(int)
        for emp_id, domain, cnt in cur.fetchall():
            contributions[(str(emp_id), domain)] += int(cnt)

        # Contributor contributions (from contributor_ids array)
        cur.execute(
            """
            SELECT unnest(contributor_ids)::text AS emp_id,
                   unnest(domain_tags) AS domain,
                   COUNT(*) AS cnt
            FROM document_knowledge
            GROUP BY 1, 2
            """
        )
        for emp_id, domain, cnt in cur.fetchall():
            if emp_id:
                contributions[(str(emp_id), domain)] += int(cnt)

    return dict(contributions)


def _load_graph_spof(snapshot_date: date, conn) -> dict[str, float]:
    """Load the most recent SPOF scores from risk_scores table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT employee_id, MAX(scored_at) AS max_at
                FROM risk_scores
                GROUP BY employee_id
            )
            SELECT rs.employee_id::text, rs.spof_score
            FROM risk_scores rs
            JOIN latest l ON rs.employee_id = l.employee_id
                         AND rs.scored_at = l.max_at
            """
        )
        return {str(r[0]): float(r[1]) for r in cur.fetchall()}


def compute_and_persist(snapshot_date: date, conn) -> int:
    """Full knowledge risk computation pipeline for snapshot_date.

    Steps:
        1. Load document contributions.
        2. Compute knowledge scores.
        3. Load latest graph SPOF scores.
        4. Compute enhanced SPOF.
        5. Upsert employee_knowledge rows.
        6. Upsert knowledge_risk_scores rows (with impacted_departments).

    Args:
        snapshot_date: Date to stamp computed_at.
        conn: Open psycopg2 connection.

    Returns:
        Number of employees scored.
    """
    import json

    contributions = load_contributions(conn)
    if not contributions:
        logger.warning(
            "compute_and_persist: no document_knowledge rows found — "
            "run Confluence/Notion connectors first."
        )
        return 0

    scores = compute_knowledge_scores_from_contributions(contributions)
    graph_spof = _load_graph_spof(snapshot_date, conn)
    enhanced = merge_with_graph_spof(scores, graph_spof)

    with conn.cursor() as cur:
        # ── employee_knowledge ────────────────────────────────────────────
        ek_rows = []
        for emp_id, score_data in scores.items():
            for domain, detail in score_data["expertise_per_domain"].items():
                ek_rows.append((
                    emp_id,
                    domain,
                    detail["doc_count"],
                    detail["is_sole_expert"],
                    detail["expertise_score"],
                    snapshot_date,
                ))
        cur.executemany(
            """
            INSERT INTO employee_knowledge
                (employee_id, domain, doc_count, is_sole_expert,
                 expertise_score, computed_at)
            VALUES (%s::uuid, %s, %s, %s, %s, %s)
            ON CONFLICT (employee_id, domain, computed_at) DO UPDATE
                SET doc_count       = EXCLUDED.doc_count,
                    is_sole_expert  = EXCLUDED.is_sole_expert,
                    expertise_score = EXCLUDED.expertise_score,
                    created_at      = NOW()
            """,
            ek_rows,
        )

        # ── knowledge_risk_scores ─────────────────────────────────────────
        kr_rows = []
        for emp_id, score_data in scores.items():
            impacted = _compute_impacted_departments(
                emp_id, score_data["sole_expert_domains"], scores, conn
            )
            kr_rows.append((
                emp_id,
                score_data["knowledge_score"],
                score_data["sole_expert_count"],
                score_data["domain_count"],
                score_data["doc_count"],
                enhanced.get(emp_id),
                json.dumps(impacted),
                snapshot_date,
            ))

        cur.executemany(
            """
            INSERT INTO knowledge_risk_scores
                (employee_id, knowledge_score, sole_expert_count, domain_count,
                 doc_count, enhanced_spof_score, impacted_departments, computed_at)
            VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (employee_id, computed_at) DO UPDATE
                SET knowledge_score      = EXCLUDED.knowledge_score,
                    sole_expert_count    = EXCLUDED.sole_expert_count,
                    domain_count         = EXCLUDED.domain_count,
                    doc_count            = EXCLUDED.doc_count,
                    enhanced_spof_score  = EXCLUDED.enhanced_spof_score,
                    impacted_departments = EXCLUDED.impacted_departments,
                    created_at           = NOW()
            """,
            kr_rows,
        )

    conn.commit()
    logger.info(
        "compute_and_persist: %d employees scored for %s",
        len(scores), snapshot_date,
    )
    return len(scores)


def _compute_impacted_departments(
    employee_id: str,
    sole_expert_domains: list[str],
    all_scores: dict[str, dict],
    conn,
) -> list[str]:
    """Departments with no other contributor to this employee's sole-expert domains."""
    if not sole_expert_domains:
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT e.department
            FROM employees e
            WHERE e.active = true
              AND e.id != %s::uuid
              AND NOT EXISTS (
                  SELECT 1
                  FROM employee_knowledge ek
                  WHERE ek.employee_id = e.id
                    AND ek.domain = ANY(%s)
                    AND ek.doc_count > 0
              )
            ORDER BY e.department
            """,
            (employee_id, sole_expert_domains),
        )
        return [r[0] for r in cur.fetchall()]


def get_impact_statement(employee_id: str, conn) -> dict:
    """Build the human-readable impact statement for one employee.

    Returns a dict with:
        employee_id, name, department, sole_expert_domains,
        impacted_departments, statement (str).
    Returns None if no knowledge risk record exists.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                e.name,
                e.department,
                kr.sole_expert_count,
                kr.domain_count,
                kr.knowledge_score,
                kr.enhanced_spof_score,
                kr.impacted_departments,
                kr.computed_at
            FROM knowledge_risk_scores kr
            JOIN employees e ON kr.employee_id = e.id
            WHERE kr.employee_id = %s::uuid
            ORDER BY kr.computed_at DESC
            LIMIT 1
            """,
            (employee_id,),
        )
        row = cur.fetchone()

    if not row:
        return {}

    (name, dept, sole_count, domain_count, k_score,
     enhanced_spof, impacted_depts_json, computed_at) = row

    import json
    impacted = json.loads(impacted_depts_json) if isinstance(impacted_depts_json, str) else impacted_depts_json or []

    # Sole expert domain names
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT domain
            FROM employee_knowledge
            WHERE employee_id = %s::uuid
              AND is_sole_expert = true
              AND computed_at = %s
            ORDER BY domain
            """,
            (employee_id, computed_at),
        )
        sole_domains = [r[0] for r in cur.fetchall()]

    if sole_domains and impacted:
        domain_list = ", ".join(sole_domains)
        dept_count  = len(impacted)
        statement   = (
            f"If {name} leaves, {dept_count} department"
            f"{'s' if dept_count != 1 else ''} lose their only expert in: "
            f"{domain_list}."
        )
    elif sole_domains:
        statement = (
            f"{name} is the sole expert in {len(sole_domains)} domain"
            f"{'s' if len(sole_domains) != 1 else ''}: {', '.join(sole_domains)}."
        )
    else:
        statement = f"{name} has no sole-expert domains at this time."

    return {
        "employee_id":         employee_id,
        "name":                name,
        "department":          dept,
        "sole_expert_count":   sole_count,
        "domain_count":        domain_count,
        "knowledge_score":     k_score,
        "enhanced_spof_score": enhanced_spof,
        "sole_expert_domains": sole_domains,
        "impacted_departments": impacted,
        "statement":           statement,
        "computed_at":         str(computed_at),
    }
