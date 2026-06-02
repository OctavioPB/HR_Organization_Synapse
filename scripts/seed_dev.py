#!/usr/bin/env python3
"""One-shot dev seed: generate synthetic data + build graph + score risks + seed all new features.

Steps:
  1  Generate synthetic employees + raw_events
  2  Build graph + compute metrics + write snapshot
  3  Detect silos
  4  Score SPOF risks
  5  Enrich employees with HRIS-style fields (hire_date, manager_id, reporting_level, pto)
  6  Seed employee knowledge domains (for team optimizer + transfer plans)
  7  Compute onboarding integration scores (new hire tracker)
  8  Run succession planning (cross-training recommendations)
  9  Generate knowledge transfer plans
 10  Seed employee demographics (DEI equity analytics)
 11  Compute structural equity scores
 12  Seed org health score (weekly digest)
 13  Simulate one departure + generate departure impact report

Usage:
    python scripts/seed_dev.py
    python scripts/seed_dev.py --employees 200 --days 90
    python scripts/seed_dev.py --skip-generate   # rebuild graph from existing raw_events
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_dev")

_DEPT_FRACTIONS = {
    "Engineering": 0.50,
    "Sales":       0.30,
    "HR":          0.20,
}

_KNOWLEDGE_DOMAINS = {
    "Engineering": ["backend", "frontend", "infrastructure", "security", "data-pipelines", "ml-ops"],
    "Sales":       ["crm", "pipeline-management", "enterprise-deals", "pricing", "partnerships"],
    "HR":          ["recruiting", "compensation", "compliance", "onboarding", "performance-management"],
}

_TENURE_BAND_MAP = [
    (0,  12, "0-1y"),
    (12, 36, "1-3y"),
    (36, 60, "3-5y"),
    (60, 999, "5y+"),
]

_LEVEL_BAND_BY_ROLE = {
    "Software Engineer":         "ic",
    "Account Executive":         "ic",
    "HR Specialist":             "ic",
    "Recruiter":                 "ic",
    "Sales Representative":      "ic",
    "BDR":                       "ic",
    "People Ops Analyst":        "ic",
    "Senior Engineer":           "senior_ic",
    "Tech Lead":                 "senior_ic",
    "Sales Manager":             "manager",
    "Engineering Manager":       "manager",
    "HR Manager":                "manager",
}


def _db_params() -> dict:
    return {
        "host":     os.environ.get("POSTGRES_HOST", "localhost"),
        "port":     int(os.environ.get("POSTGRES_PORT", "5433")),
        "dbname":   os.environ.get("POSTGRES_DB", "org_synapse"),
        "user":     os.environ.get("POSTGRES_USER", "opb"),
        "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
    }


def _get_conn():
    import psycopg2
    import psycopg2.extras
    p = _db_params()
    conn = psycopg2.connect(
        host=p["host"], port=p["port"], dbname=p["dbname"],
        user=p["user"], password=p["password"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed dev database with synthetic data.")
    parser.add_argument("--employees",     type=int, default=120)
    parser.add_argument("--days",          type=int, default=60)
    parser.add_argument("--connectors",    type=int, default=3)
    parser.add_argument("--seed",          type=int, default=42)
    parser.add_argument("--skip-generate", action="store_true",
                        help="Skip data generation (use existing raw_events)")
    args = parser.parse_args()

    import numpy as np
    rng = np.random.default_rng(args.seed)

    snapshot_date = date.today()

    # ── Step 1: Generate synthetic employees + raw_events ─────────────────────
    if not args.skip_generate:
        log.info("Step 1/%d — generating %d employees, %d days of events …",
                 13, args.employees, args.days)
        from ingestion.synthetic import (
            generate_employees,
            generate_edges,
            select_connectors,
            select_withdrawing,
            write_to_postgres,
        )

        employees      = generate_employees(args.employees, _DEPT_FRACTIONS, rng)
        connector_ids  = select_connectors(employees, rng, n_connectors=args.connectors)
        withdrawing_id = select_withdrawing(employees, connector_ids, rng)
        start_dt       = datetime(
            snapshot_date.year, snapshot_date.month, snapshot_date.day,
            tzinfo=timezone.utc,
        ) - timedelta(days=args.days)

        silo_depts = {"HR", "Sales"}
        silo_ids: set[str] = {
            e.employee_id for e in employees
            if e.department in silo_depts and e.employee_id not in connector_ids
        }
        log.info("  → Silo groups: %d employees in %s", len(silo_ids), sorted(silo_depts))

        edges = generate_edges(
            employees=employees,
            n_days=args.days,
            rng=rng,
            connector_ids=connector_ids,
            withdrawing_id=withdrawing_id,
            start_date=start_dt,
            silo_ids=silo_ids,
        )

        p = _db_params()
        write_to_postgres(
            employees, edges,
            host=p["host"], port=p["port"], dbname=p["dbname"],
            user=p["user"], password=p["password"],
        )
        log.info("  → %d employees, %d edges written", len(employees), len(edges))
    else:
        log.info("Step 1/13 — skipped (--skip-generate)")
        connector_ids  = set()
        withdrawing_id = None

    # ── Step 2: Build graph + compute metrics ──────────────────────────────────
    log.info("Step 2/13 — computing graph metrics for %s …", snapshot_date)
    from graph.builder import build_graph, load_raw_edges
    from graph.metrics import (
        compute_betweenness,
        compute_clustering,
        compute_community,
        compute_degree_centrality,
        write_snapshot,
    )

    raw_edges = load_raw_edges(snapshot_date, args.days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        log.error("Graph is empty — check postgres is reachable and raw_events were written.")
        sys.exit(1)

    betweenness            = compute_betweenness(G)
    degree_in, degree_out  = compute_degree_centrality(G)
    clustering             = compute_clustering(G)
    communities            = compute_community(G)
    write_snapshot(snapshot_date, betweenness, degree_in, degree_out, clustering, communities)
    log.info("  → %d nodes, %d communities", G.number_of_nodes(), len(set(communities.values())))

    # ── Step 3: Detect silos ───────────────────────────────────────────────────
    log.info("Step 3/13 — detecting silos …")
    from graph.silo_detector import detect_silos, write_alerts
    silo_alerts = detect_silos(G, communities)
    write_alerts(silo_alerts, snapshot_date)
    log.info("  → %d silo alert(s)", len(silo_alerts))

    # ── Step 4: Score SPOF risks ───────────────────────────────────────────────
    log.info("Step 4/13 — scoring SPOF risks …")
    from graph.risk_scorer import score_all, write_scores
    scores = score_all(G, betweenness, clustering)
    write_scores(scores, {}, snapshot_date)

    # Backfill weekly historical scores
    backfill_dates = list(range(7, args.days, 7))
    for back_days in backfill_dates:
        past_date = snapshot_date - timedelta(days=back_days)
        varied = {
            emp_id: float(np.clip(s + rng.normal(0, 0.03), 0.0, 1.0))
            for emp_id, s in scores.items()
        }
        write_scores(varied, {}, past_date)
    log.info("  → %d employees scored, %d historical snapshots backfilled",
             len(scores), len(backfill_dates))

    critical = sum(1 for s in scores.values() if s >= 0.7)
    warning  = sum(1 for s in scores.values() if 0.5 <= s < 0.7)

    # ── Step 5: Enrich employees with HRIS-style fields ────────────────────────
    log.info("Step 5/13 — enriching employees with HRIS fields …")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, name, department, role FROM employees WHERE active=TRUE ORDER BY id")
            all_employees = [dict(r) for r in cur.fetchall()]

        if not all_employees:
            log.warning("  No employees found — skipping HRIS enrichment")
        else:
            today = date.today()

            # Assign managers: first employee per dept becomes manager
            dept_first: dict[str, str] = {}
            for emp in all_employees:
                dept = emp["department"]
                if dept not in dept_first:
                    dept_first[dept] = emp["id"]

            with conn.cursor() as cur:
                for i, emp in enumerate(all_employees):
                    emp_id = emp["id"]
                    dept   = emp["department"]
                    role   = emp["role"]

                    # hire_date: 10% are new hires (< 90 days), rest random 3-48 months ago
                    if i % 10 == 0:
                        days_ago = int(rng.integers(14, 89))   # new hire for onboarding demo
                    else:
                        days_ago = int(rng.integers(90, 48 * 30))
                    hire_date = today - timedelta(days=days_ago)
                    tenure_months = days_ago // 30

                    # manager_id: non-managers report to first of their dept
                    manager_id_for_emp = None
                    if emp_id != dept_first.get(dept):
                        manager_id_for_emp = dept_first.get(dept)

                    # reporting_level from role
                    level = _LEVEL_BAND_BY_ROLE.get(role, 2)
                    if isinstance(level, str):  # e.g. "manager" → 3
                        level = {"ic": 1, "senior_ic": 2, "manager": 3, "director_plus": 5}.get(level, 2)

                    pto_days_ytd = int(rng.integers(0, 22))
                    is_comp_max  = bool(rng.random() < 0.08)

                    cur.execute(
                        """
                        UPDATE employees SET
                            hire_date            = %s,
                            tenure_months        = %s,
                            manager_id           = %s::uuid,
                            reporting_level      = %s,
                            pto_days_ytd         = %s,
                            is_comp_band_max     = %s,
                            hris_source          = 'demo_seed',
                            hris_synced_at       = NOW()
                        WHERE id = %s::uuid
                        """,
                        (hire_date, tenure_months,
                         manager_id_for_emp,
                         level, pto_days_ytd, is_comp_max, emp_id),
                    )
            conn.commit()
            log.info("  → %d employees enriched with HRIS fields", len(all_employees))
    finally:
        conn.close()

    # ── Step 6: Seed employee knowledge domains ────────────────────────────────
    log.info("Step 6/13 — seeding knowledge domains …")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, department FROM employees WHERE active=TRUE ORDER BY id")
            emp_rows = [dict(r) for r in cur.fetchall()]

        # Top SPOF employees get sole-expert status in more domains
        top_spof_ids = {k for k, v in sorted(scores.items(), key=lambda x: -x[1])[:10]}

        import psycopg2.extras as _extras
        records = []
        for emp in emp_rows:
            emp_id   = emp["id"]
            dept     = emp["department"]
            domains  = _KNOWLEDGE_DOMAINS.get(dept, ["general"])
            # Pick 1-3 domains
            n_domains = 3 if emp_id in top_spof_ids else int(rng.integers(1, 3))
            chosen    = list(rng.choice(domains, size=min(n_domains, len(domains)), replace=False))
            for domain in chosen:
                doc_count     = int(rng.integers(3, 30)) if emp_id in top_spof_ids else int(rng.integers(1, 12))
                is_sole_expert = (emp_id in top_spof_ids and rng.random() < 0.5)
                expertise_score = float(np.clip(doc_count / 30.0 + rng.normal(0, 0.05), 0.1, 1.0))
                records.append((emp_id, domain, doc_count, is_sole_expert, expertise_score, snapshot_date))

        with conn.cursor() as cur:
            _extras.execute_batch(
                cur,
                """
                INSERT INTO employee_knowledge (employee_id, domain, doc_count, is_sole_expert, expertise_score, computed_at)
                VALUES (%s::uuid, %s, %s, %s, %s, %s)
                ON CONFLICT (employee_id, domain, computed_at) DO UPDATE SET
                    doc_count = EXCLUDED.doc_count,
                    is_sole_expert = EXCLUDED.is_sole_expert,
                    expertise_score = EXCLUDED.expertise_score
                """,
                records,
                page_size=500,
            )
        conn.commit()
        log.info("  → %d knowledge domain records written", len(records))
    finally:
        conn.close()

    # ── Step 7: Compute onboarding integration scores ─────────────────────────
    log.info("Step 7/13 — computing onboarding integration scores …")
    conn = _get_conn()
    try:
        from etl.tasks.compute_onboarding import task_compute_onboarding
        result = task_compute_onboarding(str(snapshot_date), conn)
        log.info("  → %d onboarding scores, %d at-risk alerts",
                 result.get("processed", 0), result.get("alerts_fired", 0))
    except Exception as exc:
        log.warning("  Onboarding scoring skipped: %s", exc)
    finally:
        conn.close()

    # ── Step 8: Run succession planning ───────────────────────────────────────
    log.info("Step 8/13 — running succession planning …")
    conn = _get_conn()
    try:
        from graph.succession import compute_and_persist
        n_rows = compute_and_persist(snapshot_date, conn)
        log.info("  → %d succession recommendation rows written", n_rows)
    except Exception as exc:
        log.warning("  Succession planning skipped: %s", exc)
    finally:
        conn.close()

    # ── Step 9: Generate knowledge transfer plans ─────────────────────────────
    log.info("Step 9/13 — generating knowledge transfer plans …")
    conn = _get_conn()
    try:
        from etl.tasks.generate_transfer_plans import task_generate_transfer_plans
        result = task_generate_transfer_plans(str(snapshot_date), conn)
        log.info("  → %d transfer plans generated", result.get("plans_generated", 0))
    except Exception as exc:
        log.warning("  Transfer plan generation skipped: %s", exc)
    finally:
        conn.close()

    # ── Step 10: Seed employee demographics for DEI equity ────────────────────
    log.info("Step 10/13 — seeding employee demographics …")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, department, role FROM employees WHERE active=TRUE AND consent=TRUE")
            demo_emps = [dict(r) for r in cur.fetchall()]

        # Gender groups (anonymised — A/B/C, not real attributes)
        gender_choices = ["group_a", "group_b", "group_c"]
        # Deliberately skew: group_c gets lower-tenure, lower-level roles (for demo insight)

        with conn.cursor() as cur:
            for i, emp in enumerate(demo_emps):
                emp_id = emp["id"]
                role   = emp["role"]

                # Assign gender group with slight imbalance for demo
                g_weights = [0.45, 0.35, 0.20]
                gender_group = rng.choice(gender_choices, p=g_weights)

                # Tenure band from index (deterministic for demo reproducibility)
                tenure_months_approx = int(rng.integers(3, 49 * 12 // 30))
                for lo, hi, band in _TENURE_BAND_MAP:
                    if lo <= tenure_months_approx < hi:
                        tenure_band = band
                        break
                else:
                    tenure_band = "5y+"

                level_band = _LEVEL_BAND_BY_ROLE.get(role, "ic")
                if not isinstance(level_band, str):
                    level_band = "ic"

                # group_c employees skew toward "0-1y" / "ic" for demo gap
                if gender_group == "group_c" and rng.random() < 0.6:
                    tenure_band = "0-1y"
                    level_band  = "ic"

                cur.execute(
                    """
                    INSERT INTO employee_demographics
                        (employee_id, gender_group, tenure_band, level_band, consent, source)
                    VALUES (%s::uuid, %s, %s, %s, TRUE, 'demo_seed')
                    ON CONFLICT (employee_id) DO UPDATE SET
                        gender_group = EXCLUDED.gender_group,
                        tenure_band  = EXCLUDED.tenure_band,
                        level_band   = EXCLUDED.level_band,
                        consent      = TRUE,
                        source       = 'demo_seed'
                    """,
                    (emp_id, gender_group, tenure_band, level_band),
                )
        conn.commit()
        log.info("  → %d demographic records written", len(demo_emps))
    except Exception as exc:
        log.warning("  Demographics seeding skipped: %s", exc)
    finally:
        conn.close()

    # ── Step 11: Compute structural equity scores ──────────────────────────────
    log.info("Step 11/13 — computing structural equity scores …")
    conn = _get_conn()
    try:
        from etl.tasks.compute_equity import task_compute_equity
        result = task_compute_equity(str(snapshot_date), conn)
        if result.get("skipped"):
            log.info("  Equity computation skipped: %s", result.get("reason"))
        else:
            log.info("  → %d equity score rows written", result.get("rows_written", 0))
    except Exception as exc:
        log.warning("  Equity computation skipped: %s", exc)
    finally:
        conn.close()

    # ── Step 12: Seed org health score ────────────────────────────────────────
    log.info("Step 12/13 — seeding org health score …")
    conn = _get_conn()
    try:
        from graph.org_health import compute_and_persist as compute_health
        result = compute_health(snapshot_date, conn)
        score = result.get("score", 0) if isinstance(result, dict) else 0
        tier  = result.get("tier", "caution") if isinstance(result, dict) else "caution"
        log.info("  → Org Health Score: %.1f (%s)", score, tier)

        # Backfill 6 weeks of org health for digest trend demo
        for back_weeks in range(1, 7):
            past = snapshot_date - timedelta(weeks=back_weeks)
            try:
                compute_health(past, conn)
            except Exception:
                pass
        log.info("  → 6 weeks of org health history seeded")
    except Exception as exc:
        log.warning("  Org health scoring skipped: %s", exc)
    finally:
        conn.close()

    # ── Step 13: Simulate departure + generate impact report ──────────────────
    log.info("Step 13/13 — simulating one departure …")
    conn = _get_conn()
    try:
        # Pick the highest-SPOF employee that is NOT a connector (so connectors
        # stay in the graph for live demo queries)
        spof_sorted = sorted(scores.items(), key=lambda x: -x[1])
        departure_id = None
        for emp_id, spof_score in spof_sorted:
            if emp_id not in connector_ids and spof_score >= 0.4:
                departure_id = emp_id
                break

        if departure_id:
            # Mark employee as inactive with a departure date 31 days ago
            # (so t+30 snapshots can be compared against existing snapshot history)
            departure_date = snapshot_date - timedelta(days=31)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE employees
                    SET active = FALSE, deactivated_at = %s
                    WHERE id = %s::uuid
                    """,
                    (departure_date, departure_id),
                )
            conn.commit()

            from etl.tasks.generate_departure_report import task_generate_departure_report
            result = task_generate_departure_report(
                employee_id=departure_id,
                departure_date_str=str(departure_date),
                conn=conn,
            )
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM employees WHERE id = %s::uuid", (departure_id,))
                emp_name = cur.fetchone()["name"]
            log.info("  → Departure report ready for %s (SPOF: %.2f)",
                     emp_name, scores.get(departure_id, 0))
        else:
            log.info("  No eligible employee for departure simulation — skipping")
    except Exception as exc:
        log.warning("  Departure simulation skipped: %s", exc)
    finally:
        conn.close()

    # Flush Redis cache
    try:
        from api.cache import flush_all
        n = flush_all()
        if n:
            log.info("  → Flushed %d Redis cache key(s)", n)
    except Exception:
        pass

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("  Seed complete — %s", snapshot_date)
    log.info("=" * 60)
    log.info("  Nodes        : %d", G.number_of_nodes())
    log.info("  Edges        : %d", G.number_of_edges())
    log.info("  Communities  : %d", len(set(communities.values())))
    log.info("  Silos        : %d", len(silo_alerts))
    log.info("  Critical SPOF: %d  |  Warning: %d", critical, warning)
    log.info("")
    log.info("  New feature data ready:")
    log.info("    /manager          — manager_id set on employees, risk signals ready")
    log.info("    /onboarding       — %d new-hire integration scores", G.number_of_nodes() // 10)
    log.info("    /scenarios        — build a scenario at /scenarios")
    log.info("    /teams            — knowledge domains seeded, optimizer ready")
    log.info("    /equity           — demographics + equity scores seeded")
    log.info("    /succession       — succession + transfer plans generated")
    log.info("    /alerts/departures— departure impact report ready")
    log.info("    Admin > Digest    — org health score history seeded for digest")
    log.info("")
    log.info("  Open http://localhost:5173 and explore the new pages.")


if __name__ == "__main__":
    main()
