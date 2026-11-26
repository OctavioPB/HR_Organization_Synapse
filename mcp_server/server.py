"""MCP server for Organizational Synapse: the API and the app, usable from an LLM.

Every tool calls the REST API (mcp_server/client.py); nothing reads the
database directly, so the API's auth, roles, consent filters and k-anonymity
rules apply unchanged.  Large responses are summarised (the raw graph
snapshot is ~200 KB) and every person in a result carries an `app_url` that
opens their page in the web app.

Deliberately NOT exposed: tenant/API-key administration, billing, dev
seed/reset, retention purges, consent changes, GDPR data exports,
demographic imports, connector webhooks and internal broadcast routes.  They
are administrative or irreversible, or hand over an individual's full data;
use the API or the dashboard for those, with a human in the loop.

Write tools (create/compute scenarios) only add records; nothing here
deletes or changes employee data.

Run:
    python -m mcp_server                               # stdio (Claude Desktop, Claude Code, IDEs)
    python -m mcp_server --transport streamable-http   # HTTP on :8765/mcp
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_server.client import SynapseAPI

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

EmployeeId = Annotated[str, Field(description="Employee UUID (get it from search_employees)")]
Limit = Annotated[int, Field(ge=1, le=200, description="Maximum number of rows to return")]

INSTRUCTIONS = """\
Organizational Synapse maps how work actually flows through an organisation (collaboration
metadata only, never message content) and scores structural risk.

Start with search_employees to turn a name into an employee_id. Key concepts:
- SPOF score (0–1): how much the network depends on a person as a bridge. `critical` needs a score
  ≥ 0.7 AND ≥ 90 % confidence; `critical_uncertain` means "review", not "critical".
- Churn probability: calibrated 90-day voluntary-departure probability; tiers are relative to the
  org's base rate. Missing until the churn model has enough history.
- Silos: departments far more inward-looking than random mixing (homophily index).
- Org health: 0–100 composite of brokerage concentration, silos, withdrawal and fragmentation.

Rules for using these results:
- Scores are triage signals for HR, never inputs to pay, promotion, performance or termination
  decisions, and must not be shared with the person scored.
- Equity flags are investigative heuristics (not legal findings) and only exist for groups of ≥ 5.
- Say when data is missing ("not computed yet") instead of guessing.
- Link people with their app_url so the user can open them in the dashboard.
"""

_DOCS = {
    "math-model": "docs/MATH_MODEL.md",
    "model-card-churn": "docs/MODEL_CARD_CHURN.md",
    "business-value": "docs/BUSINESS_VALUE.md",
    "architecture": "docs/ARCHITECTURE.md",
    "lessons-learned": "docs/LESSONS_LEARNED.md",
}
_ROOT = Path(__file__).resolve().parents[1]

_APP_PAGES = {
    "dashboard": "/",
    "people": "/people",
    "manager": "/manager",
    "onboarding": "/onboarding",
    "scenarios": "/scenarios",
    "equity": "/equity",
    "teams": "/teams",
    "info": "/info",
}


def _round(v: Any, n: int = 4) -> Any:
    return round(v, n) if isinstance(v, float) else v


def _person(api: SynapseAPI, row: dict, *keys: str) -> dict:
    """Compact person record: id, name, department, selected fields, app link."""
    out = {"employee_id": row.get("employee_id"), "name": row.get("name"), "department": row.get("department")}
    for k in keys:
        if k in row:
            out[k] = _round(row[k])
    if out["employee_id"]:
        out["app_url"] = api.employee_link(out["employee_id"])
    return out


def build_server(api: SynapseAPI | None = None) -> MCPServer:
    api = api or SynapseAPI()
    mcp = MCPServer(
        name="org-synapse",
        title="Organizational Synapse",
        description="Organizational network analysis: SPOF risk, silos, churn, succession and reorg scenarios.",
        instructions=INSTRUCTIONS,
        version="1.0.0",
    )

    # ── People and the network ───────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def search_employees(
        name: Annotated[str | None, Field(description="Part of the name, case-insensitive")] = None,
        department: Annotated[str | None, Field(description="Exact department, case-insensitive")] = None,
        limit: Limit = 20,
    ) -> dict:
        """Find employees by name and/or department. Call this first whenever the user names a person:
        every other per-person tool needs the employee_id it returns."""
        snap = await api.snapshot()
        q = (name or "").strip().lower()
        d = (department or "").strip().lower()
        hits = [
            n
            for n in snap.get("nodes", [])
            if (not q or q in (n.get("name") or "").lower()) and (not d or d == (n.get("department") or "").lower())
        ]
        hits.sort(key=lambda n: (n.get("name") or ""))
        return {
            "snapshot_date": snap.get("snapshot_date"),
            "total_matches": len(hits),
            "employees": [_person(api, n) for n in hits[:limit]],
        }

    @mcp.tool(annotations=READ)
    async def get_network_overview(top: Limit = 10) -> dict:
        """Size of the collaboration network, departments, communities and the top brokers by betweenness."""
        snap = await api.snapshot()
        nodes = snap.get("nodes", [])
        depts: dict[str, int] = {}
        for n in nodes:
            depts[n.get("department") or "unknown"] = depts.get(n.get("department") or "unknown", 0) + 1
        comms = await api.optional("/graph/communities")
        brokers = sorted(nodes, key=lambda n: n.get("betweenness") or 0, reverse=True)[:top]
        return {
            "snapshot_date": snap.get("snapshot_date"),
            "people": snap.get("node_count", len(nodes)),
            "collaboration_ties": snap.get("edge_count"),
            "departments": depts,
            "communities": [
                {"community_id": c.get("community_id"), "members": c.get("member_count")}
                for c in (comms or {}).get("communities", [])
            ],
            "top_brokers": [_person(api, n, "betweenness", "degree_in", "degree_out", "clustering") for n in brokers],
            "app_url": f"{api.app_url}/",
        }

    @mcp.tool(annotations=READ)
    async def get_employee_profile(employee_id: EmployeeId, top_neighbors: Limit = 8) -> dict:
        """Everything known about one person: network position, SPOF score and flag, strongest
        collaborators, churn risk, knowledge concentration and succession plan (sections that have
        not been computed yet are reported as missing)."""
        ego = await api.get(f"/graph/employee/{employee_id}")
        node = ego.get("node", {})
        weights: dict[str, float] = {}
        for e in ego.get("edges", []):
            if employee_id in (e.get("source"), e.get("target")):
                other = e["target"] if e.get("source") == employee_id else e["source"]
                weights[other] = weights.get(other, 0.0) + float(e.get("weight") or 0)
        by_id = {n["employee_id"]: n for n in ego.get("neighbors", [])}
        strongest = sorted(weights, key=weights.get, reverse=True)[:top_neighbors]

        churn = await api.optional(f"/risk/employee/{employee_id}/churn")
        knowledge = await api.optional(f"/knowledge/employee/{employee_id}")
        succession = await api.optional(f"/succession/employee/{employee_id}")
        history = await api.optional(f"/risk/employee/{employee_id}/history", days=90)
        return {
            "employee": _person(
                api, node, "spof_score", "flag", "betweenness", "degree_in", "degree_out", "clustering", "community_id"
            ),
            "snapshot_date": ego.get("snapshot_date"),
            "direct_collaborators": len(ego.get("neighbors", [])),
            "strongest_collaborators": [
                {**_person(api, by_id.get(o, {"employee_id": o}), "spof_score"), "tie_strength": round(weights[o], 2)}
                for o in strongest
            ],
            "spof_history": (history or {}).get("history", [])[-12:],
            "churn": churn if churn is not None else "not computed yet",
            "knowledge": knowledge if knowledge is not None else "no knowledge-contribution data",
            "succession": succession
            if succession is not None
            else "no succession plan (not high-SPOF, or not computed)",
        }

    @mcp.tool(annotations=READ)
    async def find_connection_path(from_employee_id: EmployeeId, to_employee_id: EmployeeId) -> dict:
        """Shortest collaboration path between two people (who could make an introduction)."""
        return await api.get("/graph/path", from_employee_id=from_employee_id, to_employee_id=to_employee_id)

    @mcp.tool(annotations=READ)
    async def get_reachability(
        employee_id: EmployeeId,
        hops: Annotated[int, Field(ge=1, le=4, description="Collaboration steps")] = 2,
        limit: Limit = 25,
    ) -> dict:
        """How much of the organisation a person reaches within N collaboration steps."""
        data = await api.get(f"/graph/reachability/{employee_id}", hops=hops)
        names = await api.names()
        reach = data.get("reachable", [])
        by_dept: dict[str, int] = {}
        for r in reach:
            by_dept[r.get("department") or "unknown"] = by_dept.get(r.get("department") or "unknown", 0) + 1
        return {
            "employee_id": employee_id,
            "hops": hops,
            "reachable_count": data.get("reachable_count", len(reach)),
            "by_department": by_dept,
            "sample": [
                _person(api, {**r, "name": r.get("name") or names.get(r["employee_id"], {}).get("name")}, "spof_score")
                for r in reach[:limit]
            ],
        }

    @mcp.tool(annotations=READ)
    async def get_knowledge_islands(max_size: Annotated[int, Field(ge=1, le=10)] = 2) -> dict:
        """Small groups cut off from the rest of the collaboration network."""
        return await api.get("/graph/knowledge-islands", max_size=max_size)

    # ── Risk ─────────────────────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def get_spof_risks(
        limit: Limit = 20,
        only_flagged: Annotated[bool, Field(description="Only critical / critical_uncertain / elevated")] = False,
    ) -> dict:
        """People the network depends on most (single points of failure), highest score first."""
        data = await api.get(
            "/risk/critical-nodes" if only_flagged else "/risk/scores", top=None if only_flagged else limit
        )
        rows = data.get("scores", [])[:limit]
        return {
            "snapshot_date": data.get("snapshot_date"),
            "total": data.get("total", len(rows)),
            "people": [_person(api, r, "spof_score", "flag", "entropy_trend") for r in rows],
        }

    @mcp.tool(annotations=READ)
    async def simulate_departure(
        employee_id: EmployeeId,
        window_days: Annotated[int, Field(ge=7, le=365, description="Collaboration window")] = 30,
    ) -> dict:
        """What-if: remove one person from the network and measure the damage (path length,
        fragmentation, who becomes more critical). Read-only: nothing is saved."""
        return await api.post("/risk/simulate", {"remove_employee_id": employee_id, "window_days": window_days})

    @mcp.tool(annotations=READ)
    async def get_churn_risks(
        limit: Limit = 20,
        min_probability: Annotated[float, Field(ge=0, le=1)] = 0.0,
    ) -> dict:
        """Calibrated 90-day voluntary-departure probabilities, highest first, with the main drivers.
        Triage signal for HR only."""
        data = await api.optional("/risk/churn-scores", top=limit, min_prob=min_probability)
        if data is None:
            return {
                "status": "not computed yet",
                "why": "The churn model trains once 90+ days of features and ≥ 20 leavers exist.",
            }
        rows = data.get("scores", [])[:limit]
        return {
            "scored_at": data.get("scored_at") or data.get("snapshot_date"),
            "people": [
                _person(api, r, "churn_prob", "risk_tier", "peer_churn_rate", "peer_contagion_risk") for r in rows
            ],
        }

    @mcp.tool(annotations=READ)
    async def get_temporal_anomalies(limit: Limit = 10) -> dict:
        """People whose interaction pattern changed unusually over recent weeks (temporal GNN)."""
        data = await api.optional("/graph/temporal/anomalies", top=limit)
        return data if data is not None else {"status": "not computed yet"}

    # ── Alerts and org health ────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def get_org_health(trend_weeks: Annotated[int, Field(ge=1, le=52)] = 8) -> dict:
        """Org health score (0–100), its tier and components, the trend, and recommended actions."""
        briefing = await api.optional("/org-health/briefing", trend_weeks=trend_weeks)
        score = await api.optional("/org-health/score")
        trend = await api.optional("/org-health/trend", weeks=trend_weeks)
        if briefing is None and score is None:
            return {"status": "not computed yet"}
        return {
            "score": score,
            "briefing": briefing,
            "trend": (trend or {}).get("points", []),
            "app_url": f"{api.app_url}/",
        }

    @mcp.tool(annotations=READ)
    async def get_alerts(
        kind: Annotated[
            Literal["silos", "withdrawal", "departures", "recent"],
            Field(description="silos | withdrawal (entropy) | departures (impact reports) | recent (all types)"),
        ] = "recent",
        days: Annotated[int, Field(ge=1, le=365, description="Look-back for kind=recent")] = 30,
        limit: Limit = 20,
    ) -> dict:
        """Active alerts. Silo alerts list member counts; use get_silo_members for the people."""
        if kind == "departures":
            data = await api.get("/alerts/departures", top=limit)
            return {"total": data.get("total"), "reports": data.get("reports", [])[:limit]}
        path = {"silos": "/alerts/silos", "withdrawal": "/alerts/entropy", "recent": "/alerts/history"}[kind]
        data = await api.get(path, days=days if kind == "recent" else None)
        alerts = []
        for a in data.get("alerts", [])[:limit]:
            ents = dict(a.get("affected_entities") or {})
            for k, v in list(ents.items()):
                if isinstance(v, list) and len(v) > 5:
                    ents[k] = f"{len(v)} items"
            alerts.append(
                {**{k: a.get(k) for k in ("id", "type", "severity", "fired_at", "details")}, "affected": ents}
            )
        return {"total": data.get("total", len(alerts)), "alerts": alerts}

    @mcp.tool(annotations=READ)
    async def get_silo_members(
        alert_id: Annotated[str, Field(description="Silo alert id from get_alerts")], limit: Limit = 30
    ) -> dict:
        """The people in one silo alert, with their network metrics."""
        data = await api.get(f"/alerts/silos/{alert_id}/members")
        members = data.get("members", [])
        return {
            **{k: v for k, v in data.items() if k != "members"},
            "members": [_person(api, m, "role", "betweenness", "degree_out") for m in members[:limit]],
            "app_url": f"{api.app_url}/silo/{alert_id}",
        }

    @mcp.tool(annotations=READ)
    async def get_departure_report(employee_id: EmployeeId) -> dict:
        """Impact report for someone who left: what changed in the network before and after."""
        return await api.get(f"/compliance/departure/{employee_id}")

    # ── Knowledge and succession ─────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def get_knowledge_risks(limit: Limit = 20) -> dict:
        """Knowledge concentration: people who are sole experts in documented domains, and domains
        with a single contributor."""
        scores = await api.optional("/knowledge/scores", top=limit)
        domains = await api.optional("/knowledge/domains")
        seen: dict[str, dict] = {}
        for d in (domains or {}).get("domains", []):
            entry = seen.setdefault(
                d["domain"],
                {
                    "domain": d["domain"],
                    "total_docs": d.get("total_docs"),
                    "contributors": d.get("contributor_count"),
                    "sole_experts": [],
                },
            )
            if d.get("sole_expert_name"):
                entry["sole_experts"].append(d["sole_expert_name"])
        return {
            "scores": (scores or {}).get("scores", "not computed yet"),
            "domains": list(seen.values()),
        }

    @mcp.tool(annotations=READ)
    async def get_knowledge_impact(employee_id: EmployeeId) -> dict:
        """What knowledge would be at risk if this person left."""
        return await api.get(f"/knowledge/impact/{employee_id}")

    @mcp.tool(annotations=READ)
    async def get_succession_plan(employee_id: EmployeeId) -> dict:
        """Succession candidates and the knowledge-transfer plan for a high-SPOF person."""
        rec = await api.optional(f"/succession/employee/{employee_id}")
        plan = await api.optional(f"/succession/{employee_id}/transfer-plan")
        if rec is None and plan is None:
            return {"status": "no succession plan: the person is not high-SPOF, or the succession DAG has not run"}
        return {"candidates": rec, "transfer_plan": plan, "app_url": api.employee_link(employee_id)}

    @mcp.tool(annotations=READ)
    async def get_succession_overview(top_spof: Limit = 10) -> dict:
        """Succession recommendations for the most critical people in the organisation."""
        data = await api.optional("/succession/recommendations", top_spof=top_spof)
        return data if data is not None else {"status": "not computed yet"}

    # ── Managers and onboarding ──────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def list_managers() -> dict:
        """Managers (people with direct reports) and their team sizes."""
        return await api.get("/manager/list")

    @mcp.tool(annotations=READ)
    async def get_team_view(manager_employee_id: EmployeeId) -> dict:
        """Traffic-light view of a manager's direct reports (green / amber / red), as managers see it."""
        data = await api.get("/manager/team", manager_employee_id=manager_employee_id)
        team = data.get("team", [])
        counts: dict[str, int] = {}
        for t in team:
            counts[t.get("status", "unknown")] = counts.get(t.get("status", "unknown"), 0) + 1
        return {
            "manager_employee_id": manager_employee_id,
            "total_reports": data.get("total_reports", len(team)),
            "status_counts": counts,
            "team": [_person(api, t, "role", "status", "contracting_network", "contracting_weeks") for t in team],
            "app_url": f"{api.app_url}/manager",
        }

    @mcp.tool(annotations=READ)
    async def get_one_on_one_suggestions(employee_id: EmployeeId, manager_employee_id: EmployeeId) -> dict:
        """Suggested 1:1 conversation topics for a manager about one direct report."""
        return await api.get(f"/manager/team/{employee_id}/suggestions", manager_employee_id=manager_employee_id)

    @mcp.tool(annotations=READ)
    async def get_onboarding_cohort() -> dict:
        """Recent hires and how well they are integrating into the network; at-risk new joiners."""
        return await api.get("/onboarding/cohort")

    # ── Scenarios and team building ──────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def list_scenarios() -> dict:
        """Saved reorg scenarios and their headline impact."""
        return await api.get("/scenarios")

    @mcp.tool(annotations=READ)
    async def get_scenario(scenario_id: str) -> dict:
        """Full detail and impact report of one reorg scenario."""
        return await api.get(f"/scenarios/{scenario_id}")

    @mcp.tool(annotations=WRITE)
    async def create_and_run_scenario(
        name: Annotated[str, Field(max_length=200)],
        operations: Annotated[
            list[dict],
            Field(
                description=(
                    'List of operations, e.g. [{"op": "remove", "employee_ids": ["<uuid>"]}], '
                    '[{"op": "merge_depts", "source_dept": "HR", "target_dept": "Sales"}] or '
                    '[{"op": "move_team", "employee_ids": ["<uuid>"], "target_dept": "Engineering"}]'
                ),
                min_length=1,
            ),
        ],
        description: str | None = None,
    ) -> dict:
        """Save a reorg scenario (remove people, merge departments, move a team) and compute its
        impact. Creates a record visible in the Scenario Planner; changes no employee data."""
        for op in operations:
            if op.get("op") not in ("remove", "merge_depts", "move_team"):
                raise ToolError(f"Unknown operation {op.get('op')!r}: use remove, merge_depts or move_team.")
        created = await api.post("/scenarios", {"name": name, "description": description, "operations": operations})
        sid = created.get("scenario_id") or created.get("id")
        result = await api.post(f"/scenarios/{sid}/compute") if sid else created
        return {"scenario_id": sid, "result": result, "app_url": f"{api.app_url}/scenarios"}

    @mcp.tool(annotations=READ)
    async def compare_scenarios(scenario_ids: Annotated[list[str], Field(min_length=2, max_length=4)]) -> dict:
        """Side-by-side comparison of 2–4 computed scenarios."""
        return await api.get("/scenarios/compare", ids=",".join(scenario_ids))

    @mcp.tool(annotations=READ)
    async def optimize_team(
        departments: Annotated[list[str], Field(description="Departments the team must bridge")] = [],  # noqa: B006
        domains: Annotated[list[str], Field(description="Knowledge domains the team must cover")] = [],  # noqa: B006
        min_size: Annotated[int, Field(ge=2, le=12)] = 3,
        max_size: Annotated[int, Field(ge=2, le=20)] = 6,
        exclude_spof_above: Annotated[float, Field(ge=0, le=1, description="Skip already-overloaded people")] = 0.7,
    ) -> dict:
        """Propose team compositions that bridge departments and cover knowledge domains without
        overloading critical people. Use list_departments_and_domains for valid values."""
        return await api.post(
            "/teams/optimize",
            {
                "departments": departments,
                "domains": domains,
                "min_size": min_size,
                "max_size": max_size,
                "exclude_spof_above": exclude_spof_above,
            },
        )

    @mcp.tool(annotations=READ)
    async def list_departments_and_domains() -> dict:
        """Valid department and knowledge-domain names."""
        deps = await api.get("/teams/departments")
        doms = await api.get("/teams/domains")
        return {"departments": deps.get("departments", []), "domains": doms.get("domains", [])}

    # ── Equity and compliance ────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def get_equity_distribution(
        dimension: Literal["gender_group", "tenure_band", "level_band"] = "tenure_band",
        metric: Literal["betweenness", "degree"] = "degree",
    ) -> dict:
        """Network centrality by group, with permutation-test p/q-values. Groups under 5 people are
        suppressed. A flag is an investigative heuristic, not evidence of discrimination."""
        return await api.get("/equity/centrality-distribution", dimension=dimension, metric=metric)

    @mcp.tool(annotations=READ)
    async def get_succession_equity_check(employee_id: EmployeeId) -> dict:
        """Group composition of the succession candidates for one critical person (counts only)."""
        return await api.get(f"/equity/succession-check/{employee_id}")

    @mcp.tool(annotations=READ)
    async def get_data_inventory() -> dict:
        """What personal data the platform stores, retention periods and legal basis (GDPR/CCPA)."""
        return await api.get("/compliance/data-audit")

    # ── Natural language and the app ─────────────────────────────────────────

    @mcp.tool(annotations=READ)
    async def ask_synapse(question: Annotated[str, Field(min_length=3, max_length=2000)]) -> dict:
        """Ask the platform's own natural-language agent (needs ANTHROPIC_API_KEY on the API side).
        Prefer the specific tools when they fit; use this for open questions."""
        return await api.post("/query/natural", {"question": question})

    @mcp.tool(annotations=READ)
    async def get_app_link(
        page: Literal[
            "dashboard", "people", "employee", "silo", "manager", "onboarding", "scenarios", "equity", "teams", "info"
        ],
        id: Annotated[str | None, Field(description="employee_id for page=employee, alert_id for page=silo")] = None,
    ) -> dict:
        """URL of a page in the web app, to hand the user a place to look or act."""
        if page in ("employee", "silo"):
            if not id:
                raise ToolError(f"page={page} needs id")
            path = f"/employee/{id}" if page == "employee" else f"/silo/{id}"
        else:
            path = _APP_PAGES[page]
        return {"url": f"{api.app_url}{path}"}

    @mcp.tool(annotations=READ)
    async def check_api_health() -> dict:
        """Whether the API is up, and which base URLs this MCP server uses."""
        health = await api.get("/health")
        return {"api_url": api.base_url, "app_url": api.app_url, "health": health}

    # ── Resources: model documentation ───────────────────────────────────────

    @mcp.resource(
        "synapse://docs/{name}",
        description="Model documentation: math-model, model-card-churn, business-value, architecture, lessons-learned",
        mime_type="text/markdown",
    )
    def docs(name: str) -> str:
        path = _DOCS.get(name)
        if path is None:
            raise ValueError(f"Unknown document {name!r}; available: {', '.join(_DOCS)}")
        return (_ROOT / path).read_text(encoding="utf-8")

    # ── Prompts ──────────────────────────────────────────────────────────────

    @mcp.prompt(title="Weekly organisational risk review")
    def weekly_risk_review() -> str:
        """Org health, critical people, silos and withdrawal signals, with actions."""
        return (
            "Run a weekly organisational risk review with the Organizational Synapse tools:\n"
            "1. get_org_health — score, tier, trend and recommended actions.\n"
            "2. get_spof_risks with only_flagged=true — who is critical; separate critical from critical_uncertain.\n"
            "3. get_alerts kind=silos and kind=withdrawal.\n"
            "4. For the top 3 critical people, get_succession_plan.\n"
            "Summarise in under 300 words for an HR business partner: what changed, the three most important "
            "risks, and one concrete action each. Link people with their app_url. Do not speculate beyond the data."
        )

    @mcp.prompt(title="Succession brief for one person")
    def succession_brief(employee_name: str) -> str:
        """Departure impact and succession options for a named person."""
        return (
            f"Prepare a succession brief for {employee_name}:\n"
            "1. search_employees to find their employee_id (ask if several match).\n"
            "2. get_employee_profile, then simulate_departure.\n"
            "3. get_succession_plan and get_knowledge_impact.\n"
            "Explain what would break if they left, who could cover, and the first two knowledge-transfer steps. "
            "This is planning material for HR, not an assessment of the person."
        )

    @mcp.prompt(title="Reorg impact check")
    def reorg_impact(change: str) -> str:
        """Model a proposed reorg as a scenario and explain its structural impact."""
        return (
            f"A reorg is proposed: {change}\n"
            "Translate it into scenario operations (use search_employees and list_departments_and_domains for "
            "ids and names), confirm the operations with the user, then create_and_run_scenario. Report path-length "
            "change, new silos, and who becomes more critical, and link the Scenario Planner."
        )

    return mcp
