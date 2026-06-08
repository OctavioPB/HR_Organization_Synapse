"""Generate 90-day knowledge transfer plans for high-SPOF employees.

For each SPOF employee with score > threshold, finds top-2 succession candidates
and produces a three-phase plan:
  - Weeks 1-4:  Relationship bridge introductions
  - Weeks 5-8:  Document gap reviews (Confluence/Notion domains)
  - Weeks 9-12: Structural shadowing opportunities (recurring meeting patterns)

Then generates a plain-language narrative via Claude and persists to
knowledge_transfer_plans table.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_MIN_SPOF_SCORE = float(os.environ.get("TRANSFER_MIN_SPOF_SCORE", "0.5"))
_MAX_CANDIDATES = int(os.environ.get("TRANSFER_MAX_CANDIDATES", "2"))
_MAX_INTRO_ACTIONS = int(os.environ.get("TRANSFER_MAX_INTROS", "5"))
_MAX_DOC_ACTIONS = int(os.environ.get("TRANSFER_MAX_DOCS", "5"))
_MAX_SHADOW_ACTIONS = int(os.environ.get("TRANSFER_MAX_SHADOWS", "3"))


def task_generate_transfer_plans(snapshot_date_str: str, conn) -> dict:
    snapshot_date = date.fromisoformat(snapshot_date_str)
    logger.info("Generating transfer plans for %s …", snapshot_date)

    # 1. Fetch high-SPOF employees with their top succession candidates
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (sr.source_employee_id)
                sr.source_employee_id::text,
                es.name AS spof_name,
                es.department AS spof_dept,
                rs.spof_score,
                sr.candidate_employee_id::text,
                ec.name AS candidate_name,
                ec.department AS candidate_dept,
                sr.compatibility_score
            FROM succession_recommendations sr
            JOIN employees es ON es.id = sr.source_employee_id
            JOIN employees ec ON ec.id = sr.candidate_employee_id
            JOIN risk_scores rs ON rs.employee_id = sr.source_employee_id
            WHERE rs.spof_score >= %s
              AND sr.computed_at = (SELECT MAX(computed_at) FROM succession_recommendations)
              AND rs.scored_at >= %s
              AND es.active = TRUE AND ec.active = TRUE
            ORDER BY sr.source_employee_id, sr.rank ASC
            LIMIT 50
            """,
            (_MIN_SPOF_SCORE, snapshot_date - timedelta(days=7)),
        )
        pairs = [dict(r) for r in cur.fetchall()]

    if not pairs:
        logger.info("No SPOF/candidate pairs to process.")
        return {"plans_generated": 0}

    # Load graph for neighbor computation
    try:
        from graph.scenario_simulator import load_current_graph

        G = load_current_graph(conn)
    except Exception as exc:
        logger.warning("Could not load graph for transfer plans: %s — using empty graph.", exc)
        import networkx as nx

        G = nx.DiGraph()

    plans_written = 0
    for pair in pairs:
        spof_id = pair["source_employee_id"]
        candidate_id = pair["candidate_employee_id"]
        try:
            plan_json = _build_plan(spof_id, candidate_id, pair, G, conn)
            _upsert_plan(spof_id, candidate_id, plan_json, conn)
            plans_written += 1
        except Exception as exc:
            logger.error("Plan failed for %s → %s: %s", spof_id, candidate_id, exc)

    conn.commit()
    logger.info("Transfer plans: %d written.", plans_written)
    return {"plans_generated": plans_written, "snapshot_date": snapshot_date_str}


def _build_plan(spof_id: str, candidate_id: str, pair: dict, G, conn) -> dict:
    spof_name = pair["spof_name"]
    candidate_name = pair["candidate_name"]
    spof_dept = pair["spof_dept"]

    # Phase 1: Relationship bridges
    spof_neighbors = set(G.neighbors(spof_id)) if spof_id in G else set()
    cand_neighbors = set(G.neighbors(candidate_id)) if candidate_id in G else set()
    unshared = spof_neighbors - cand_neighbors - {candidate_id}

    intro_actions = []
    for n_id in list(unshared)[:_MAX_INTRO_ACTIONS]:
        with conn.cursor() as cur:
            cur.execute("SELECT name, department FROM employees WHERE id = %s::uuid", (n_id,))
            emp = cur.fetchone()
        if emp:
            intro_actions.append(
                {
                    "action_type": "introduction",
                    "description": f"Introduce {candidate_name} to {emp['name']} ({emp['department']}) — currently connected only to {spof_name}.",
                    "parties": [n_id],
                }
            )

    # Phase 2: Document gap
    doc_actions = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT domain, doc_count FROM employee_knowledge WHERE employee_id = %s::uuid ORDER BY doc_count DESC",
            (spof_id,),
        )
        spof_domains = {r["domain"]: r["doc_count"] for r in cur.fetchall()}

        cur.execute(
            "SELECT domain FROM employee_knowledge WHERE employee_id = %s::uuid",
            (candidate_id,),
        )
        cand_domains = {r["domain"] for r in cur.fetchall()}

    gap_domains = [(d, c) for d, c in spof_domains.items() if d not in cand_domains]
    gap_domains.sort(key=lambda x: -x[1])
    for domain, doc_count in gap_domains[:_MAX_DOC_ACTIONS]:
        doc_actions.append(
            {
                "action_type": "document_review",
                "description": f"Review {doc_count} document(s) in domain '{domain}' authored by {spof_name}.",
                "document_domain": domain,
            }
        )

    # Phase 3: Shadow opportunities (recurring calendar events)
    shadow_actions = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT target_id::text, COUNT(*) AS cnt
            FROM raw_events
            WHERE source_id = %s::uuid
              AND channel = 'calendar'
              AND ts >= NOW() - INTERVAL '30 days'
            GROUP BY target_id
            HAVING COUNT(*) >= 3
            ORDER BY cnt DESC
            LIMIT %s
            """,
            (spof_id, _MAX_SHADOW_ACTIONS),
        )
        for r in cur.fetchall():
            with conn.cursor() as cur2:
                cur2.execute("SELECT name, department FROM employees WHERE id = %s::uuid", (r["target_id"],))
                emp = cur2.fetchone()
            if emp:
                shadow_actions.append(
                    {
                        "action_type": "shadow",
                        "description": f"Shadow {spof_name}'s recurring meetings with {emp['name']} ({emp['department']}) — {r['cnt']}× in last 30 days.",
                        "meeting_type": "recurring",
                    }
                )

    # Generate narrative
    narrative = _generate_narrative(spof_name, candidate_name, spof_dept, intro_actions, doc_actions, shadow_actions)

    return {
        "spof_name": spof_name,
        "candidate_name": candidate_name,
        "spof_score": round(float(pair.get("spof_score", 0)), 4),
        "weeks_1_4": intro_actions,
        "weeks_5_8": doc_actions,
        "weeks_9_12": shadow_actions,
        "narrative": narrative,
        "generated_at": str(date.today()),
    }


def _generate_narrative(spof_name: str, candidate_name: str, dept: str, intros: list, docs: list, shadows: list) -> str:
    try:
        from graph.claude_client import call_claude

        prompt = (
            f"You are an HR advisor writing a knowledge transfer briefing. "
            f"{candidate_name} is being prepared to absorb {spof_name}'s structural role in {dept}. "
            f"The plan has {len(intros)} introduction action(s), {len(docs)} document review(s), and {len(shadows)} shadowing opportunity(ies). "
            f"Write 2 concise paragraphs (max 100 words total): "
            f"(1) why this transfer is strategically important, "
            f"(2) the key milestone to watch by week 12. "
            f"Professional, direct tone."
        )
        return call_claude(prompt, max_tokens=200)
    except Exception as exc:
        logger.warning("Narrative generation failed: %s", exc)
        return (
            f"{candidate_name} is the primary structural successor for {spof_name} in {dept}. "
            f"The 90-day plan covers {len(intros)} relationship introduction(s), "
            f"{len(docs)} knowledge domain gap(s), and {len(shadows)} shadowing opportunity(ies). "
            f"By week 12, {candidate_name} should have absorbed at least 60% of {spof_name}'s bridging connections."
        )


def _upsert_plan(spof_id: str, candidate_id: str, plan_json: dict, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowledge_transfer_plans
              (spof_employee_id, candidate_id, plan_json, status)
            VALUES (%s::uuid, %s::uuid, %s::jsonb, 'active')
            ON CONFLICT (spof_employee_id, candidate_id) DO UPDATE SET
              plan_json    = EXCLUDED.plan_json,
              generated_at = NOW(),
              status       = 'active'
            """,
            (spof_id, candidate_id, json.dumps(plan_json)),
        )
