"""Succession planning — cross-training candidate recommendations for SPOF nodes.

For each high-SPOF employee (source S), the algorithm:
1. Identifies S's Louvain community from graph_snapshots.
2. Finds 'border employees': active employees outside S's community who have
   at least 1 direct interaction with any member of S's community.
3. Scores each border employee as a succession candidate:

   compatibility = w_struct * structural_overlap
                 + w_clust  * clustering_score
                 + w_domain * domain_overlap

   structural_overlap — Jaccard(neighbors(S), neighbors(candidate))
   clustering_score   — candidate's clustering coefficient [0, 1]
   domain_overlap     — |domains(S) ∩ domains(candidate)| / max(|domains(S)|, 1)

4. Returns top-N by compatibility_score.

Default weights (configurable via env):
    SUCCESSION_W_STRUCT  = 0.40
    SUCCESSION_W_CLUST   = 0.25
    SUCCESSION_W_DOMAIN  = 0.35

Env vars controlling scope:
    SUCCESSION_TOP_N_SPOF       = 20    (how many top SPOF employees to plan for)
    SUCCESSION_N_CANDIDATES     = 5     (candidates per SPOF employee)
    SUCCESSION_MIN_SPOF_SCORE   = 0.3   (SPOF threshold to qualify)
    GRAPH_WINDOW_DAYS           = 30    (rolling window for graph edges)

Pure functions (testable without DB):
    compute_structural_overlap(source_id, candidate_id, G) → float
    compute_domain_overlap(source_domains, candidate_domains) → float
    find_border_employees(source_id, community_map, G) → set[str]
    score_candidates(source_id, G, node_metrics, knowledge_domains,
                     candidate_ids, n, w_struct, w_clust, w_domain) → list[dict]

DB-backed:
    load_node_metrics(snapshot_date, conn) → dict[str, dict]
    load_knowledge_domains(conn) → dict[str, set[str]]
    compute_and_persist(snapshot_date, conn, top_n_spof, n_candidates) → int
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger(__name__)

# ─── Configurable weights & scope ────────────────────────────────────────────

_W_STRUCT         = float(os.environ.get("SUCCESSION_W_STRUCT",       "0.40"))
_W_CLUST          = float(os.environ.get("SUCCESSION_W_CLUST",        "0.25"))
_W_DOMAIN         = float(os.environ.get("SUCCESSION_W_DOMAIN",       "0.35"))
_TOP_N_SPOF       = int(os.environ.get("SUCCESSION_TOP_N_SPOF",       "20"))
_N_CANDIDATES     = int(os.environ.get("SUCCESSION_N_CANDIDATES",     "5"))
_MIN_SPOF_SCORE   = float(os.environ.get("SUCCESSION_MIN_SPOF_SCORE", "0.3"))
_WINDOW_DAYS      = int(os.environ.get("GRAPH_WINDOW_DAYS",           "30"))


# ─── Pure computation ─────────────────────────────────────────────────────────


def compute_structural_overlap(
    source_id: str,
    candidate_id: str,
    G: nx.DiGraph,
) -> float:
    """Jaccard similarity of undirected neighbor sets for source and candidate.

    High overlap → candidate already knows most of source's network,
    making them easier to position as a bridge replacement.

    Args:
        source_id: SPOF employee UUID string.
        candidate_id: Succession candidate UUID string.
        G: Directed collaboration graph.

    Returns:
        Float in [0, 1]. Returns 0.0 if neither node has any neighbors.
    """
    src_neighbors = set(G.predecessors(source_id)) | set(G.successors(source_id))
    cand_neighbors = set(G.predecessors(candidate_id)) | set(G.successors(candidate_id))
    src_neighbors.discard(candidate_id)
    cand_neighbors.discard(source_id)
    union = src_neighbors | cand_neighbors
    if not union:
        return 0.0
    return len(src_neighbors & cand_neighbors) / len(union)


def compute_domain_overlap(
    source_domains: set[str],
    candidate_domains: set[str],
) -> float:
    """Fraction of source's knowledge domains the candidate already covers.

    A candidate with high overlap needs less total cross-training in knowledge.

    Args:
        source_domains: Knowledge domain tags of the SPOF employee.
        candidate_domains: Knowledge domain tags of the succession candidate.

    Returns:
        Float in [0, 1]. Returns 0.0 if source has no domains.
    """
    if not source_domains:
        return 0.0
    return len(source_domains & candidate_domains) / len(source_domains)


def find_border_employees(
    source_id: str,
    community_map: dict[str, int | None],
    G: nx.DiGraph,
) -> set[str]:
    """Return IDs of employees that border source's Louvain community.

    Border employees are those NOT in source's community who have
    at least 1 direct interaction with any community member. They are
    the best positioned to absorb the source's bridge relationships.

    Args:
        source_id: SPOF employee UUID string.
        community_map: Map employee_id → community_id (None if unassigned).
        G: Directed collaboration graph.

    Returns:
        Set of employee UUID strings (never includes source_id).
    """
    source_community = community_map.get(source_id)

    if source_community is not None:
        source_community_members: set[str] = {
            emp for emp, cid in community_map.items()
            if cid == source_community
        }
    else:
        source_community_members = {source_id}

    border: set[str] = set()
    for member in source_community_members:
        if member not in G:
            continue
        for neighbor in G.successors(member):
            if neighbor not in source_community_members:
                border.add(neighbor)
        for neighbor in G.predecessors(member):
            if neighbor not in source_community_members:
                border.add(neighbor)

    border.discard(source_id)
    return border


def score_candidates(
    source_id: str,
    G: nx.DiGraph,
    node_metrics: dict[str, dict],
    knowledge_domains: dict[str, set[str]],
    candidate_ids: list[str],
    n: int = 5,
    w_struct: float = _W_STRUCT,
    w_clust: float = _W_CLUST,
    w_domain: float = _W_DOMAIN,
) -> list[dict]:
    """Score and rank succession candidates for a given SPOF source node.

    Args:
        source_id: SPOF employee UUID string.
        G: Directed collaboration graph (source_id must be a node in G).
        node_metrics: Map employee_id → {clustering: float, community_id: int|None}.
        knowledge_domains: Map employee_id → set of knowledge domain strings.
        candidate_ids: Pre-filtered list of candidate employee IDs.
        n: Maximum number of candidates to return.
        w_struct, w_clust, w_domain: Component weights.

    Returns:
        List of dicts sorted by compatibility_score DESC, length ≤ n.
        Each dict: {candidate_employee_id, structural_overlap, clustering_score,
                    domain_overlap, compatibility_score}.
    """
    if source_id not in G:
        return []

    source_domains = knowledge_domains.get(source_id, set())
    results: list[dict] = []

    for cid in candidate_ids:
        if cid == source_id or cid not in G:
            continue

        structural = compute_structural_overlap(source_id, cid, G)
        clustering = float(node_metrics.get(cid, {}).get("clustering", 0.0))
        domain = compute_domain_overlap(source_domains, knowledge_domains.get(cid, set()))

        compatibility = round(
            w_struct * structural + w_clust * clustering + w_domain * domain,
            4,
        )
        results.append({
            "candidate_employee_id": cid,
            "structural_overlap":    round(structural, 4),
            "clustering_score":      round(clustering, 4),
            "domain_overlap":        round(domain, 4),
            "compatibility_score":   compatibility,
        })

    results.sort(key=lambda r: r["compatibility_score"], reverse=True)
    return results[:n]


# ─── DB-backed functions ──────────────────────────────────────────────────────


def load_node_metrics(snapshot_date: date, conn) -> dict[str, dict]:
    """Load clustering coefficient and community_id from graph_snapshots.

    Returns:
        Map employee_id → {betweenness: float, clustering: float, community_id: int|None}.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT employee_id::text, betweenness, clustering, community_id
            FROM graph_snapshots
            WHERE snapshot_date = %s
            """,
            (snapshot_date,),
        )
        return {
            str(r[0]): {
                "betweenness": float(r[1] or 0),
                "clustering":  float(r[2] or 0),
                "community_id": r[3],
            }
            for r in cur.fetchall()
        }


def load_knowledge_domains(conn) -> dict[str, set[str]]:
    """Load each employee's knowledge domains from the most recent run.

    Returns:
        Map employee_id → set of domain strings. Empty set if no data for that employee.
        Returns {} silently if employee_knowledge table doesn't exist yet (F3 not deployed).
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH latest AS (
                    SELECT employee_id, MAX(computed_at) AS max_at
                    FROM employee_knowledge
                    GROUP BY employee_id
                )
                SELECT ek.employee_id::text, ek.domain
                FROM employee_knowledge ek
                JOIN latest ON ek.employee_id = latest.employee_id
                           AND ek.computed_at = latest.max_at
                WHERE ek.doc_count > 0
                """
            )
            result: dict[str, set[str]] = defaultdict(set)
            for emp_id, domain in cur.fetchall():
                result[str(emp_id)].add(domain)
        return dict(result)
    except Exception as exc:
        logger.warning("load_knowledge_domains: %s — domain_overlap will be 0 for all.", exc)
        conn.rollback()
        return {}


def _load_raw_edges_from_conn(
    snapshot_date: date,
    window_days: int,
    conn,
) -> list[tuple[str, str, float, str, str]]:
    """Load raw_events for the rolling window ending on snapshot_date using a given conn."""
    end_ts = datetime.combine(snapshot_date, datetime.max.time()).replace(tzinfo=timezone.utc)
    start_ts = end_ts - timedelta(days=window_days)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                re.source_id::text,
                re.target_id::text,
                re.weight,
                es.department,
                et.department
            FROM raw_events re
            JOIN employees es ON re.source_id = es.id
            JOIN employees et ON re.target_id = et.id
            WHERE re.ts BETWEEN %s AND %s
              AND es.consent = true
              AND et.consent = true
              AND es.active  = true
              AND et.active  = true
            """,
            (start_ts, end_ts),
        )
        return list(cur.fetchall())


def _load_top_spof(
    snapshot_date: date,
    top_n: int,
    min_score: float,
    conn,
) -> list[tuple[str, float]]:
    """Return (employee_id, spof_score) for the top SPOF employees on snapshot_date."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rs.employee_id::text, rs.spof_score
            FROM risk_scores rs
            WHERE rs.scored_at::date = %s
              AND rs.spof_score >= %s
            ORDER BY rs.spof_score DESC
            LIMIT %s
            """,
            (snapshot_date, min_score, top_n),
        )
        return [(str(r[0]), float(r[1])) for r in cur.fetchall()]


def _load_active_employee_ids(conn) -> set[str]:
    """Return UUIDs of all active employees."""
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM employees WHERE active = true")
        return {str(r[0]) for r in cur.fetchall()}


def compute_and_persist(
    snapshot_date: date,
    conn,
    top_n_spof: int = _TOP_N_SPOF,
    n_candidates: int = _N_CANDIDATES,
    min_spof_score: float = _MIN_SPOF_SCORE,
    window_days: int = _WINDOW_DAYS,
) -> int:
    """Full succession planning pipeline for snapshot_date.

    Steps:
        1. Load collaboration graph from raw_events.
        2. Load node metrics (clustering, community_id) from graph_snapshots.
        3. Load knowledge domains (optional — 0-overlap if F3 not deployed).
        4. Load top SPOF employees above threshold.
        5. For each SPOF employee, find border employees and score candidates.
        6. Upsert into succession_recommendations.

    Args:
        snapshot_date: Date to label computed_at and to resolve SPOF scores.
        conn: Open psycopg2 connection.
        top_n_spof: Maximum number of SPOF employees to plan for.
        n_candidates: Candidates per SPOF employee.
        min_spof_score: Minimum SPOF score to qualify as a critical node.
        window_days: Rolling window in days for graph edges.

    Returns:
        Total number of recommendation rows written (source × candidates).
    """
    import json

    from graph.builder import build_graph

    raw_edges = _load_raw_edges_from_conn(snapshot_date, window_days, conn)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("compute_and_persist: empty graph for %s — skipping.", snapshot_date)
        return 0

    node_metrics = load_node_metrics(snapshot_date, conn)
    community_map: dict[str, int | None] = {
        emp_id: m["community_id"] for emp_id, m in node_metrics.items()
    }
    knowledge_domains = load_knowledge_domains(conn)
    spof_list = _load_top_spof(snapshot_date, top_n_spof, min_spof_score, conn)

    if not spof_list:
        logger.info(
            "compute_and_persist: no employees meet SPOF threshold %.2f on %s.",
            min_spof_score, snapshot_date,
        )
        return 0

    active_ids = _load_active_employee_ids(conn)

    rows: list[tuple] = []
    for source_id, _spof_score in spof_list:
        border = find_border_employees(source_id, community_map, G)
        candidates = list(border & active_ids - {source_id})

        scored = score_candidates(
            source_id, G, node_metrics, knowledge_domains, candidates, n_candidates,
        )

        for rank, entry in enumerate(scored, start=1):
            rows.append((
                snapshot_date,
                source_id,
                entry["candidate_employee_id"],
                entry["compatibility_score"],
                rank,
                entry["structural_overlap"],
                entry["clustering_score"],
                entry["domain_overlap"],
                json.dumps({
                    "structural_overlap": entry["structural_overlap"],
                    "clustering_score":   entry["clustering_score"],
                    "domain_overlap":     entry["domain_overlap"],
                }),
            ))

    if not rows:
        logger.info("compute_and_persist: no succession candidates found for %s.", snapshot_date)
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO succession_recommendations
                (computed_at, source_employee_id, candidate_employee_id,
                 compatibility_score, rank, structural_overlap,
                 clustering_score, domain_overlap, rationale)
            VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_employee_id, candidate_employee_id, computed_at) DO UPDATE
                SET compatibility_score = EXCLUDED.compatibility_score,
                    rank                = EXCLUDED.rank,
                    structural_overlap  = EXCLUDED.structural_overlap,
                    clustering_score    = EXCLUDED.clustering_score,
                    domain_overlap      = EXCLUDED.domain_overlap,
                    rationale           = EXCLUDED.rationale,
                    created_at          = NOW()
            """,
            rows,
        )

    conn.commit()
    logger.info(
        "compute_and_persist: %d succession rows written for %s.",
        len(rows), snapshot_date,
    )
    return len(rows)
