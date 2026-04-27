"""Tool definitions and executors for the NL query agent.

Each tool maps to one or more existing DB query functions.
Results are trimmed to stay within Claude's context budget (~500 tokens each).

Available tools:
    search_employees        — find employees by name/department (prerequisite for simulate_removal)
    get_graph_snapshot      — top nodes by betweenness centrality + network stats
    get_risk_scores         — SPOF risk scores with flags
    get_silo_alerts         — active communication silo alerts
    simulate_removal        — What-If: remove an employee and see graph health impact
    get_knowledge_scores    — knowledge concentration / sole-expert risk
    get_churn_risk          — GNN churn probability scores
    get_succession_plan     — cross-training recommendations for a SPOF employee
    get_temporal_anomalies  — employees with anomalous interaction trajectory
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─── Tool definitions (Anthropic tool_use schema) ─────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "search_employees",
        "description": (
            "Find employees by name or department. Always call this first when a user "
            "mentions a person by name — you need the employee_id UUID for other tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Partial employee name to search (case-insensitive)",
                },
                "department": {
                    "type": "string",
                    "description": "Filter by department name (exact, case-insensitive)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_graph_snapshot",
        "description": (
            "Get the current organizational collaboration graph: top employees by "
            "betweenness centrality (critical network connectors), plus overall stats. "
            "Use this to answer questions about key connectors, bridges, or network structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of top employees by betweenness to return (default: 15)",
                },
                "department": {
                    "type": "string",
                    "description": "Filter results to one department (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_risk_scores",
        "description": (
            "Get SPOF (Single Point of Failure) risk scores. High scores mean the "
            "employee's departure would severely disrupt communication flows. "
            "Use for questions about organizational risk, key people, or who is critical."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of highest-risk employees to return (default: 10)",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum SPOF score to include (0–1, default: 0)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_silo_alerts",
        "description": (
            "Get active communication silo alerts — teams or communities that have "
            "become isolated from the rest of the organization. "
            "Use for questions about silos, isolated teams, or inter-department communication."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "simulate_removal",
        "description": (
            "Simulate removing an employee from the collaboration graph and measure "
            "the impact on organizational connectivity. "
            "Returns before/after comparison of key health metrics. "
            "Use for What-If questions like 'What happens if X leaves?' "
            "Requires employee_id — call search_employees first if you only have a name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "UUID of the employee to remove from the graph",
                },
                "window_days": {
                    "type": "integer",
                    "description": "Rolling window of collaboration data to use (default: 30)",
                },
            },
            "required": ["employee_id"],
        },
    },
    {
        "name": "get_knowledge_scores",
        "description": (
            "Get knowledge concentration scores — employees who are the sole expert "
            "in one or more domains. Their departure means domain knowledge is lost. "
            "Use for questions about knowledge risk, expertise, or domain coverage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of highest-risk employees to return (default: 10)",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum knowledge_score to include (0–1, default: 0)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_churn_risk",
        "description": (
            "Get churn risk scores — probability (0–1) that employees will leave within "
            "the next 90 days, based on a GNN trained on HR and graph features. "
            "Use for questions about attrition risk or flight risk employees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of highest-risk employees to return (default: 10)",
                },
                "min_prob": {
                    "type": "number",
                    "description": "Minimum churn probability (0–1, default: 0.5 for high-risk only)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_succession_plan",
        "description": (
            "Get cross-training recommendations for a specific high-SPOF employee. "
            "Returns the top candidates who could absorb their bridge relationships. "
            "Use for questions about succession planning, cross-training, or risk mitigation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "UUID of the SPOF employee to get a succession plan for",
                },
            },
            "required": ["employee_id"],
        },
    },
    {
        "name": "get_temporal_anomalies",
        "description": (
            "Get employees with anomalous collaboration trajectory — people whose "
            "interaction patterns have diverged significantly from their historical baseline. "
            "A rising anomaly score may indicate disengagement or role change. "
            "Use for questions about behavioral changes, withdrawal, or early warning signals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of most anomalous employees to return (default: 10)",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum anomaly score (0–1, default: 0.5)",
                },
            },
            "required": [],
        },
    },
]


# ─── Tool executors ───────────────────────────────────────────────────────────


def _safe_date(d) -> str:
    return str(d) if d else "unknown"


def execute_tool(tool_name: str, tool_input: dict[str, Any], conn) -> dict[str, Any]:
    """Dispatch a tool call to the appropriate DB function.

    Returns a JSON-serialisable dict. Never raises — returns an error dict instead.
    """
    try:
        return _dispatch(tool_name, tool_input, conn)
    except Exception as exc:
        logger.warning("Tool %s failed: %s", tool_name, exc)
        return {"error": str(exc), "tool": tool_name}


def _dispatch(tool_name: str, inp: dict, conn) -> dict:
    match tool_name:
        case "search_employees":
            return _search_employees(inp, conn)
        case "get_graph_snapshot":
            return _get_graph_snapshot(inp, conn)
        case "get_risk_scores":
            return _get_risk_scores(inp, conn)
        case "get_silo_alerts":
            return _get_silo_alerts(conn)
        case "simulate_removal":
            return _simulate_removal(inp, conn)
        case "get_knowledge_scores":
            return _get_knowledge_scores(inp, conn)
        case "get_churn_risk":
            return _get_churn_risk(inp, conn)
        case "get_succession_plan":
            return _get_succession_plan(inp, conn)
        case "get_temporal_anomalies":
            return _get_temporal_anomalies(inp, conn)
        case _:
            return {"error": f"Unknown tool: {tool_name}"}


def _search_employees(inp: dict, conn) -> dict:
    name = inp.get("name", "")
    department = inp.get("department", "")
    with conn.cursor() as cur:
        if name and department:
            cur.execute(
                """
                SELECT id::text, name, department, role, active
                FROM employees
                WHERE LOWER(name) LIKE %s AND LOWER(department) = LOWER(%s)
                  AND active = true
                LIMIT 10
                """,
                (f"%{name.lower()}%", department),
            )
        elif name:
            cur.execute(
                """
                SELECT id::text, name, department, role, active
                FROM employees
                WHERE LOWER(name) LIKE %s AND active = true
                LIMIT 10
                """,
                (f"%{name.lower()}%",),
            )
        else:
            cur.execute(
                """
                SELECT id::text, name, department, role, active
                FROM employees
                WHERE LOWER(department) = LOWER(%s) AND active = true
                LIMIT 20
                """,
                (department,),
            )
        rows = [dict(r) for r in cur.fetchall()]
    return {"matches": rows, "count": len(rows)}


def _get_graph_snapshot(inp: dict, conn) -> dict:
    from api import db as queries

    top_n = int(inp.get("top_n", 15))
    dept_filter = inp.get("department")
    snapshot_date = queries.fetch_latest_snapshot_date(conn)
    if snapshot_date is None:
        return {"error": "No graph snapshots available. Run graph_builder_dag first."}

    nodes = queries.fetch_graph_nodes(snapshot_date, conn)
    if dept_filter:
        nodes = [n for n in nodes if n["department"].lower() == dept_filter.lower()]

    top = nodes[:top_n]
    return {
        "snapshot_date": _safe_date(snapshot_date),
        "total_employees_in_snapshot": len(nodes),
        "department_filter": dept_filter,
        "top_nodes_by_betweenness": [
            {
                "employee_id": n["employee_id"],
                "name": n["name"],
                "department": n["department"],
                "betweenness": round(float(n["betweenness"] or 0), 4),
                "degree_in": int(n["degree_in"] or 0),
                "degree_out": int(n["degree_out"] or 0),
                "clustering": round(float(n["clustering"] or 0), 4),
                "community_id": n["community_id"],
            }
            for n in top
        ],
    }


def _get_risk_scores(inp: dict, conn) -> dict:
    from api import db as queries

    top_n = int(inp.get("top_n", 10))
    min_score = float(inp.get("min_score", 0.0))
    snapshot_date = queries.fetch_latest_snapshot_date(conn)
    if snapshot_date is None:
        return {"error": "No risk scores available. Run graph_builder_dag first."}

    rows = queries.fetch_risk_scores(snapshot_date, top_n, conn)
    filtered = [r for r in rows if (r["spof_score"] or 0) >= min_score]
    return {
        "snapshot_date": _safe_date(snapshot_date),
        "high_risk_employees": [
            {
                "employee_id": r["employee_id"],
                "name": r["name"],
                "department": r["department"],
                "spof_score": round(float(r["spof_score"] or 0), 4),
                "entropy_trend": round(float(r["entropy_trend"]), 4) if r["entropy_trend"] else None,
                "flag": r["flag"],
            }
            for r in filtered
        ],
        "count": len(filtered),
    }


def _get_silo_alerts(conn) -> dict:
    from api import db as queries

    silos = queries.fetch_silo_alerts(conn)
    snapshot_date = queries.fetch_latest_snapshot_date(conn)
    communities = []
    if snapshot_date:
        communities = queries.fetch_communities(snapshot_date, conn)

    silo_ids = {a.get("affected_entities", {}).get("community_id") for a in silos}
    active_silos = [c for c in communities if c["community_id"] in silo_ids or c["is_silo"]]

    return {
        "total_silo_alerts": len(silos),
        "active_silos": [
            {
                "community_id": c["community_id"],
                "departments": c["departments"],
                "member_count": c["member_count"],
                "is_silo": c["is_silo"],
            }
            for c in active_silos[:10]
        ],
    }


def _simulate_removal(inp: dict, conn) -> dict:
    from graph.builder import build_graph
    from graph.metrics import compute_betweenness
    from api import db as queries
    import networkx as nx

    employee_id = inp.get("employee_id", "")
    window_days = int(inp.get("window_days", 30))
    snapshot_date = queries.fetch_latest_snapshot_date(conn)
    if snapshot_date is None:
        return {"error": "No snapshot data available."}

    # Load raw edges using the provided conn
    end_ts = datetime.combine(snapshot_date, datetime.max.time()).replace(tzinfo=timezone.utc)
    start_ts = end_ts - timedelta(days=window_days)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT re.source_id::text, re.target_id::text, re.weight,
                   es.department, et.department
            FROM raw_events re
            JOIN employees es ON re.source_id = es.id
            JOIN employees et ON re.target_id = et.id
            WHERE re.ts BETWEEN %s AND %s
              AND es.consent = true AND et.consent = true
              AND es.active = true AND et.active = true
            """,
            (start_ts, end_ts),
        )
        raw_edges = list(cur.fetchall())

    if not raw_edges:
        return {"error": f"No collaboration data for the {window_days}-day window."}

    G = build_graph(raw_edges)
    if employee_id not in G:
        return {"error": f"Employee {employee_id} not found in the graph."}

    def _stats(graph: nx.DiGraph) -> dict:
        bw = compute_betweenness(graph) if graph.number_of_nodes() > 0 else {}
        vals = list(bw.values())
        return {
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "avg_betweenness": round(sum(vals) / len(vals), 6) if vals else 0.0,
            "weakly_connected_components": nx.number_weakly_connected_components(graph),
        }

    before = _stats(G)
    degree = G.degree(employee_id)
    G_after = G.copy()
    G_after.remove_node(employee_id)
    after = _stats(G_after)

    components_added = after["weakly_connected_components"] - before["weakly_connected_components"]
    bw_pct_change = (
        round((after["avg_betweenness"] - before["avg_betweenness"]) / max(before["avg_betweenness"], 1e-9) * 100, 1)
        if before["avg_betweenness"] > 0 else 0.0
    )

    return {
        "employee_id": employee_id,
        "snapshot_date": _safe_date(snapshot_date),
        "before": before,
        "after": after,
        "impact": {
            "employee_connections_lost": degree,
            "new_isolated_components": components_added,
            "avg_betweenness_change_pct": bw_pct_change,
            "summary": (
                f"Removing this employee disconnects {degree} collaboration links "
                f"and creates {components_added} new isolated cluster(s). "
                f"Average betweenness stress increases by {bw_pct_change}%."
            ),
        },
    }


def _get_knowledge_scores(inp: dict, conn) -> dict:
    from api import db as queries

    top_n = int(inp.get("top_n", 10))
    min_score = float(inp.get("min_score", 0.0))
    computed_at = queries.fetch_latest_knowledge_date(conn)
    if computed_at is None:
        return {"error": "No knowledge scores. Run the knowledge_score DAG first."}

    rows = queries.fetch_knowledge_scores(computed_at, top_n, min_score, conn)
    return {
        "computed_at": _safe_date(computed_at),
        "employees": [
            {
                "employee_id": r["employee_id"],
                "name": r["name"],
                "department": r["department"],
                "knowledge_score": round(float(r["knowledge_score"]), 4),
                "sole_expert_count": r["sole_expert_count"],
                "domain_count": r["domain_count"],
                "impacted_departments": r["impacted_departments"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _get_churn_risk(inp: dict, conn) -> dict:
    from api import db as queries

    top_n = int(inp.get("top_n", 10))
    min_prob = float(inp.get("min_prob", 0.5))
    scored_at = queries.fetch_latest_churn_date(conn)
    if scored_at is None:
        return {"error": "No churn scores. Run the churn_gnn_score DAG first."}

    rows = queries.fetch_churn_scores(scored_at, top_n, min_prob, conn)
    return {
        "scored_at": _safe_date(scored_at),
        "high_risk_employees": [
            {
                "employee_id": r["employee_id"],
                "name": r["name"],
                "department": r["department"],
                "churn_prob": round(float(r["churn_prob"]), 4),
                "risk_tier": r["risk_tier"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _get_succession_plan(inp: dict, conn) -> dict:
    from api import db as queries

    employee_id = inp.get("employee_id", "")
    data = queries.fetch_employee_succession(employee_id, conn)
    if not data:
        return {
            "error": (
                f"No succession plan for {employee_id}. "
                "Either not flagged as high-SPOF or succession_dag hasn't run."
            )
        }

    return {
        "source_employee_id": data["source_employee_id"],
        "source_name": data["source_name"],
        "source_department": data["source_department"],
        "spof_score": round(float(data["spof_score"] or 0), 4),
        "computed_at": _safe_date(data["computed_at"]),
        "top_candidates": [
            {
                "name": c["candidate_name"],
                "department": c["candidate_department"],
                "compatibility_score": round(float(c["compatibility_score"]), 4),
                "structural_overlap": round(float(c["structural_overlap"] or 0), 4),
                "domain_overlap": round(float(c["domain_overlap"] or 0), 4),
                "rank": c["rank"],
            }
            for c in data.get("candidates", [])[:5]
        ],
    }


def _get_temporal_anomalies(inp: dict, conn) -> dict:
    from api import db as queries

    top_n = int(inp.get("top_n", 10))
    min_score = float(inp.get("min_score", 0.5))
    scored_at = queries.fetch_latest_temporal_anomaly_date(conn)
    if scored_at is None:
        return {"error": "No temporal anomaly scores. Run the temporal_gnn_score DAG first."}

    rows = queries.fetch_temporal_anomaly_scores(scored_at, top_n, min_score, conn)
    return {
        "scored_at": _safe_date(scored_at),
        "anomalous_employees": [
            {
                "employee_id": r["employee_id"],
                "name": r["name"],
                "department": r["department"],
                "anomaly_score": round(float(r["anomaly_score"]), 4),
                "anomaly_tier": r["anomaly_tier"],
                "trend_slope": round(float(r["trend_slope"]), 4),
                "trend_direction": "worsening" if r["trend_slope"] > 0 else "recovering",
            }
            for r in rows
        ],
        "count": len(rows),
    }
