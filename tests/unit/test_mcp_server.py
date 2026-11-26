"""MCP server: tool surface, summarisation, error mapping and auth headers.

Runs in-process against a mocked API (httpx.MockTransport), so it needs no
server, database or network.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

pytest.importorskip("mcp.server.mcpserver")

from mcp import Client  # noqa: E402

from mcp_server.client import SynapseAPI  # noqa: E402
from mcp_server.server import build_server  # noqa: E402

A, B, C = (f"0000000{i}-0000-0000-0000-000000000000" for i in (1, 2, 3))

SNAPSHOT = {
    "snapshot_date": "2026-06-25",
    "node_count": 3,
    "edge_count": 2,
    "nodes": [
        {"employee_id": A, "name": "Carl Jackson", "department": "Sales", "betweenness": 0.25},
        {"employee_id": B, "name": "Olivia Hall", "department": "HR", "betweenness": 0.10},
        {"employee_id": C, "name": "Carla Moss", "department": "HR", "betweenness": 0.0},
    ],
    "edges": [{"source": A, "target": B, "weight": 5.0}, {"source": B, "target": C, "weight": 1.0}],
}

EGO = {
    "employee_id": A,
    "snapshot_date": "2026-06-25",
    "node": {**SNAPSHOT["nodes"][0], "spof_score": 0.9, "flag": "critical"},
    "neighbors": [{**SNAPSHOT["nodes"][1], "spof_score": 0.4}],
    "edges": [{"source": A, "target": B, "weight": 5.0}, {"source": B, "target": A, "weight": 2.0}],
}


def _arun(coro):
    """Run a coroutine on a private loop, leaving a fresh current loop behind.

    _arun() clears the current loop, which breaks later tests that still
    call the deprecated asyncio.get_event_loop() (tests/unit/test_websocket.py).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _handler(seen: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/graph/snapshot":
            return httpx.Response(200, json=SNAPSHOT)
        if path == f"/graph/employee/{A}":
            return httpx.Response(200, json=EGO)
        if path == "/risk/scores":
            top = int(request.url.params.get("top", 50))
            rows = [
                {
                    "employee_id": A,
                    "name": "Carl Jackson",
                    "department": "Sales",
                    "spof_score": 0.912345,
                    "flag": "critical",
                }
            ]
            return httpx.Response(200, json={"snapshot_date": "2026-06-25", "total": 1, "scores": rows[:top]})
        if path == "/risk/simulate":
            body = json.loads(request.content)
            return httpx.Response(200, json={"removed_employee_id": body["remove_employee_id"], "delta": {}})
        if path == "/alerts/silos":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "alerts": [
                        {
                            "id": "s1",
                            "type": "silo",
                            "severity": "critical",
                            "fired_at": "2026-06-25",
                            "details": "Engineering",
                            "affected_entities": {"member_ids": [f"m{i}" for i in range(60)], "member_count": 60},
                        }
                    ],
                },
            )
        if path == "/scenarios" and request.method == "POST":
            return httpx.Response(201, json={"scenario_id": "sc-1"})
        if path == "/scenarios/sc-1/compute":
            return httpx.Response(200, json={"scenario_id": "sc-1", "status": "computed"})
        if path == "/risk/forbidden":
            return httpx.Response(403, json={"detail": "Role 'analyst' is not authorised"})
        return httpx.Response(404, json={"detail": f"No data for {path}"})

    return handle


def _run(coro_fn, **api_kwargs):
    seen: list[httpx.Request] = []
    api = SynapseAPI(
        base_url="http://api.test",
        app_url="http://app.test",
        transport=httpx.MockTransport(_handler(seen)),
        **api_kwargs,
    )
    server = build_server(api)

    async def go():
        async with Client(server) as client:
            return await coro_fn(client)

    return _arun(go()), seen


async def _call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    text = r.content[0].text if r.content else ""
    return r.is_error, (text if r.is_error else json.loads(text))


def test_tool_surface_excludes_admin_and_irreversible_routes():
    async def names(client):
        return {t.name: t for t in (await client.list_tools()).tools}

    tools, _ = _run(names)
    assert {"search_employees", "get_employee_profile", "get_spof_risks", "simulate_departure"} <= set(tools)
    forbidden = (
        "admin",
        "purge",
        "seed",
        "reset",
        "billing",
        "consent",
        "export",
        "demographic",
        "webhook",
        "broadcast",
    )
    assert not [n for n in tools if any(f in n for f in forbidden)]
    # Only the scenario tool writes; everything else is declared read-only.
    writers = [n for n, t in tools.items() if not (t.annotations and t.annotations.read_only_hint)]
    assert writers == ["create_and_run_scenario"]


def test_search_is_case_insensitive_and_links_the_app():
    (err, out), _ = _run(lambda c: _call(c, "search_employees", {"name": "JACKSON"}))
    assert not err and out["total_matches"] == 1
    assert out["employees"][0]["app_url"] == f"http://app.test/employee/{A}"

    (err, out), _ = _run(lambda c: _call(c, "search_employees", {"name": "car", "department": "hr"}))
    assert [e["name"] for e in out["employees"]] == ["Carla Moss"]


def test_profile_summarises_ego_network_and_reports_missing_sections():
    (err, out), _ = _run(lambda c: _call(c, "get_employee_profile", {"employee_id": A}))
    assert not err
    assert out["employee"]["flag"] == "critical"
    assert out["strongest_collaborators"][0]["employee_id"] == B
    assert out["strongest_collaborators"][0]["tie_strength"] == 7.0  # both directions summed
    assert out["churn"] == "not computed yet"  # 404 becomes a readable status, not a failure


def test_large_lists_in_alerts_are_collapsed():
    (err, out), _ = _run(lambda c: _call(c, "get_alerts", {"kind": "silos"}))
    assert not err and out["alerts"][0]["affected"]["member_ids"] == "60 items"


def test_simulate_departure_posts_the_right_body():
    (err, out), seen = _run(lambda c: _call(c, "simulate_departure", {"employee_id": A, "window_days": 30}))
    assert not err and out["removed_employee_id"] == A
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content) == {"remove_employee_id": A, "window_days": 30}


def test_scenario_is_created_then_computed():
    ops = [{"op": "remove", "employee_ids": [A]}]
    (err, out), seen = _run(lambda c: _call(c, "create_and_run_scenario", {"name": "t", "operations": ops}))
    assert not err and out["scenario_id"] == "sc-1" and out["result"]["status"] == "computed"
    assert [r.url.path for r in seen if r.method == "POST"] == ["/scenarios", "/scenarios/sc-1/compute"]


def test_unknown_scenario_operation_rejected_before_calling_api():
    (err, text), seen = _run(
        lambda c: _call(c, "create_and_run_scenario", {"name": "t", "operations": [{"op": "fire"}]})
    )
    assert err and "Unknown operation" in text
    assert not [r for r in seen if r.method == "POST"]


def test_not_found_and_validation_errors_are_readable():
    (err, text), _ = _run(lambda c: _call(c, "get_departure_report", {"employee_id": A}))
    assert err and "Not found" in text
    (err, text), _ = _run(lambda c: _call(c, "get_spof_risks", {"limit": 0}))
    assert err and "greater than or equal to 1" in text


def test_tenant_headers_are_sent_when_configured():
    _, seen = _run(lambda c: _call(c, "get_spof_risks", {"limit": 1}), tenant_id="t-1", api_key="k-1")
    assert seen[0].headers["X-Tenant-ID"] == "t-1" and seen[0].headers["X-Api-Key"] == "k-1"
    _, seen = _run(lambda c: _call(c, "get_spof_risks", {"limit": 1}), tenant_id="", api_key="")
    assert "X-Api-Key" not in seen[0].headers


def test_unreachable_api_gives_actionable_message():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    api = SynapseAPI(base_url="http://down.test", transport=httpx.MockTransport(down))

    async def go():
        async with Client(build_server(api)) as client:
            return await _call(client, "check_api_health")

    err, text = _arun(go())
    assert err and "not reachable at http://down.test" in text


def test_docs_resource_and_prompts():
    async def go(client):
        doc = await client.read_resource("synapse://docs/model-card-churn")
        prompts = [p.name for p in (await client.list_prompts()).prompts]
        brief = await client.get_prompt("succession_brief", {"employee_name": "Carl Jackson"})
        return doc.contents[0].text, prompts, brief.messages[0].content.text

    (doc, prompts, brief), _ = _run(go)
    assert doc.startswith("# Model Card")
    assert set(prompts) == {"weekly_risk_review", "succession_brief", "reorg_impact"}
    assert "Carl Jackson" in brief


def test_app_links():
    (err, out), _ = _run(lambda c: _call(c, "get_app_link", {"page": "scenarios"}))
    assert out == {"url": "http://app.test/scenarios"}
    (err, text), _ = _run(lambda c: _call(c, "get_app_link", {"page": "employee"}))
    assert err and "needs id" in text
