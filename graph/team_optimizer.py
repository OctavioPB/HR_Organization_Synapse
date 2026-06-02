"""Greedy set-cover team composition optimizer.

Given a set of departments to bridge and knowledge domains to cover,
returns up to 3 optimal team compositions ranked by a composite score.

Score = bridge_coverage * 0.40
      + domain_coverage * 0.35
      + (1 - structural_load_norm) * 0.25

where structural_load_norm = sum(spof_scores) / max_possible_load.
"""

from __future__ import annotations

import logging
from itertools import combinations
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


def _jaccard(set_a: set, set_b: set) -> float:
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _relationship_density(members: list[str], G: nx.DiGraph) -> float:
    """Average Jaccard similarity of neighbour sets across all pairs."""
    if len(members) < 2:
        return 0.0
    scores = []
    for a, b in combinations(members, 2):
        na = set(G.neighbors(a)) if a in G else set()
        nb = set(G.neighbors(b)) if b in G else set()
        scores.append(_jaccard(na, nb))
    return sum(scores) / len(scores) if scores else 0.0


def _bridge_coverage(members: list[str], dept_pairs: list[tuple], G: nx.DiGraph, dept_map: dict) -> float:
    """Fraction of department pairs bridged by at least one team member."""
    if not dept_pairs:
        return 1.0
    bridged = 0
    for (d1, d2) in dept_pairs:
        for m in members:
            m_dept = dept_map.get(m, "")
            neighbors = list(G.neighbors(m)) if m in G else []
            neighbor_depts = {dept_map.get(n, "") for n in neighbors}
            if (m_dept == d1 and d2 in neighbor_depts) or (m_dept == d2 and d1 in neighbor_depts):
                bridged += 1
                break
    return bridged / len(dept_pairs)


def optimize_team(
    G: nx.DiGraph,
    constraints: dict,
    conn,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Return up to top_n team compositions satisfying the constraints.

    constraints keys:
        departments: list[str]   — departments to bridge
        domains:     list[str]   — knowledge domains required
        min_size:    int         — minimum team size (default 3)
        max_size:    int         — maximum team size (default 6)
        exclude_spof_above: float — exclude employees with SPOF > threshold (default 0.7)
    """
    departments   = constraints.get("departments", [])
    domains       = constraints.get("domains", [])
    min_size      = int(constraints.get("min_size", 3))
    max_size      = int(constraints.get("max_size", 6))
    spof_limit    = float(constraints.get("exclude_spof_above", 0.7))

    dept_pairs = list(combinations(departments, 2)) if len(departments) >= 2 else []

    # Fetch candidate pool
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                e.id::text,
                e.name,
                e.department,
                e.role,
                COALESCE(rs.spof_score, 0) AS spof_score
            FROM employees e
            LEFT JOIN LATERAL (
                SELECT spof_score FROM risk_scores
                WHERE employee_id = e.id
                ORDER BY scored_at DESC LIMIT 1
            ) rs ON true
            WHERE e.active = TRUE AND e.consent = TRUE
              AND (rs.spof_score IS NULL OR rs.spof_score <= %s)
            ORDER BY rs.spof_score ASC NULLS FIRST
            """,
            (spof_limit,),
        )
        candidates = [dict(r) for r in cur.fetchall()]

    if not candidates:
        return []

    # Department map for graph nodes
    dept_map: dict[str, str] = {c["id"]: c["department"] for c in candidates}
    for n, attrs in G.nodes(data=True):
        if n not in dept_map:
            dept_map[n] = attrs.get("department", "")

    # Knowledge domains per candidate
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT employee_id::text, array_agg(domain) AS domains
            FROM employee_knowledge
            WHERE employee_id = ANY(%s::uuid[])
            GROUP BY employee_id
            """,
            ([c["id"] for c in candidates],),
        )
        domain_map: dict[str, set] = {r["employee_id"]: set(r["domains"] or []) for r in cur.fetchall()}

    required_domains = set(domains)

    # Filter by department relevance if departments specified
    if departments:
        relevant = [c for c in candidates if c["department"] in departments]
        # Add bridges (employees in adjacent depts with connections)
        for c in candidates:
            if c not in relevant:
                nbrs = list(G.neighbors(c["id"])) if c["id"] in G else []
                nbr_depts = {dept_map.get(n, "") for n in nbrs}
                if nbr_depts & set(departments):
                    relevant.append(c)
        candidates = relevant[:100]  # cap for performance

    logger.info("Team optimizer: %d candidates for size %d–%d", len(candidates), min_size, max_size)

    # Greedy set-cover: for each target size, greedily add best candidate
    def greedy_team(target_size: int) -> list[str]:
        selected = []
        remaining = list(candidates)
        covered_depts: set[str] = set()
        covered_domains: set[str] = set()

        for _ in range(target_size):
            if not remaining:
                break
            best, best_score = None, -1.0
            for c in remaining:
                cid = c["id"]
                # Marginal bridge coverage
                new_depts = ({c["department"]} | {dept_map.get(nb, "") for nb in (G.neighbors(cid) if cid in G else [])}) & set(departments)
                dept_gain = len(new_depts - covered_depts) / max(len(departments), 1)
                # Marginal domain coverage
                new_domains = domain_map.get(cid, set()) & required_domains
                domain_gain = len(new_domains - covered_domains) / max(len(required_domains), 1) if required_domains else 0.0
                score = 0.55 * dept_gain + 0.45 * domain_gain
                if score > best_score:
                    best_score = score
                    best = c
            if best:
                selected.append(best["id"])
                covered_depts |= {best["department"]}
                covered_domains |= domain_map.get(best["id"], set())
                remaining.remove(best)

        return selected

    results = []
    seen: set[frozenset] = set()
    id_to_cand = {c["id"]: c for c in candidates}

    for size in range(min_size, max_size + 1):
        team_ids = greedy_team(size)
        key = frozenset(team_ids)
        if key in seen or len(team_ids) < min_size:
            continue
        seen.add(key)

        bc = _bridge_coverage(team_ids, dept_pairs, G, dept_map)
        team_domains = set().union(*[domain_map.get(m, set()) for m in team_ids])
        dc = len(team_domains & required_domains) / max(len(required_domains), 1) if required_domains else 1.0
        total_spof = sum(id_to_cand.get(m, {}).get("spof_score", 0) for m in team_ids)
        max_load = size * spof_limit
        structural_load_norm = total_spof / max(max_load, 1e-9)
        rd = _relationship_density(team_ids, G)
        composite = 0.40 * bc + 0.35 * dc + 0.25 * (1 - structural_load_norm)

        members = []
        for mid in team_ids:
            c = id_to_cand.get(mid, {})
            members.append({
                "employee_id": mid,
                "name":        c.get("name", ""),
                "department":  c.get("department", ""),
                "role":        c.get("role", ""),
                "spof_score":  round(float(c.get("spof_score", 0)), 4),
            })

        results.append({
            "members":              members,
            "bridge_coverage":      round(bc, 4),
            "domain_coverage":      round(dc, 4),
            "structural_load":      round(total_spof, 4),
            "relationship_density": round(rd, 4),
            "composite_score":      round(composite, 4),
        })

    results.sort(key=lambda x: -x["composite_score"])
    for i, r in enumerate(results[:top_n]):
        r["rank"] = i + 1

    return results[:top_n]
