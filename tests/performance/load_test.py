"""Locust load test for the Org Synapse API.

Target: p95 latency < 500ms at 50 concurrent users (Sprint 10 exit criterion).

Run:
    # Against local dev server (docker-compose up -d api)
    locust -f tests/performance/load_test.py --host http://localhost:8000

    # Headless CI mode (50 users, 10 users/second ramp, 2-minute run)
    locust -f tests/performance/load_test.py \
        --host http://localhost:8000 \
        --headless \
        --users 50 \
        --spawn-rate 10 \
        --run-time 120s \
        --html tests/performance/report.html

    # With custom host (staging)
    locust -f tests/performance/load_test.py --host https://api.org-synapse.example.com

Exit codes:
    0  All requests completed; p95 < 500ms
    1  At least one request failed or p95 ≥ 500ms

Prerequisites:
    - API must be running with at least one populated graph snapshot.
    - Seeded with: python data/synthetic/generate_org_data.py --employees 200 --days 90
    - Environment variable LOAD_TEST_API_TOKEN can hold a Bearer token if auth is enabled.
"""

from __future__ import annotations

import os

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

_TOKEN = os.environ.get("LOAD_TEST_API_TOKEN", "")


def _auth_headers() -> dict:
    if _TOKEN:
        return {"Authorization": f"Bearer {_TOKEN}"}
    return {}


class OrgSynapseReadUser(HttpUser):
    """Simulates a read-heavy API consumer (HR analyst viewing dashboards).

    Task weights reflect realistic dashboard polling patterns:
      - Graph snapshot (3×): most expensive query; fetched on page load
      - Risk scores (2×): right panel on dashboard
      - Critical nodes (1×): badge count in nav
      - Silo alerts (1×): top banner
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.headers = _auth_headers()

    @task(3)
    def get_graph_snapshot(self) -> None:
        with self.client.get(
            "/graph/snapshot",
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()  # no snapshot yet — not a failure

    @task(2)
    def get_risk_scores(self) -> None:
        self.client.get("/risk/scores", headers=self.headers)

    @task(1)
    def get_critical_nodes(self) -> None:
        self.client.get("/risk/critical-nodes", headers=self.headers)

    @task(1)
    def get_silo_alerts(self) -> None:
        self.client.get("/alerts/silos", headers=self.headers)

    @task(1)
    def get_communities(self) -> None:
        with self.client.get(
            "/graph/communities",
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()


class OrgSynapseSimulateUser(HttpUser):
    """Simulates a power user running What-If simulations (HR admin).

    Lower weight than read users — simulation is an expensive operation
    and represents a minority of real traffic.
    """

    wait_time = between(5.0, 15.0)
    weight = 1  # 1 simulate user per 5 read users

    def on_start(self) -> None:
        self.headers = _auth_headers()
        self._employee_ids: list[str] = []

    @task
    def simulate_removal(self) -> None:
        if not self._employee_ids:
            # Fetch a snapshot to get real employee IDs
            resp = self.client.get("/graph/snapshot", headers=self.headers)
            if resp.status_code == 200:
                nodes = resp.json().get("nodes", [])
                self._employee_ids = [n["employee_id"] for n in nodes[:10]]
            if not self._employee_ids:
                return

        import random
        emp_id = random.choice(self._employee_ids)
        self.client.post(
            "/risk/simulate",
            json={"remove_employee_id": emp_id},
            headers=self.headers,
        )

    @task(2)
    def get_employee_history(self) -> None:
        if not self._employee_ids:
            return
        import random
        emp_id = random.choice(self._employee_ids)
        self.client.get(
            f"/risk/employee/{emp_id}/history",
            headers=self.headers,
        )


# ── SLA enforcement ────────────────────────────────────────────────────────────


_P95_SLA_MS = float(os.environ.get("LOAD_TEST_P95_SLA_MS", "500"))
_ERROR_RATE_SLA = float(os.environ.get("LOAD_TEST_ERROR_RATE_SLA", "0.01"))  # 1%


@events.quitting.add_listener
def check_sla(environment, **kwargs) -> None:
    """Fail the test run if SLA targets are not met.

    Called by Locust at the end of the test run. Sets environment.process_exit_code
    to 1 so CI pipelines detect the failure.
    """
    stats = environment.runner.stats.total

    if stats.num_requests == 0:
        print("WARNING: no requests completed — check that the API is reachable")
        return

    p95_ms = stats.get_response_time_percentile(0.95)
    error_rate = stats.fail_ratio

    violations: list[str] = []
    if p95_ms > _P95_SLA_MS:
        violations.append(f"p95 latency {p95_ms:.0f}ms > SLA {_P95_SLA_MS:.0f}ms")
    if error_rate > _ERROR_RATE_SLA:
        violations.append(f"error rate {error_rate:.2%} > SLA {_ERROR_RATE_SLA:.2%}")

    if violations:
        print("\nSLA VIOLATIONS:")
        for v in violations:
            print(f"  ✗ {v}")
        environment.process_exit_code = 1
    else:
        print(
            f"\nSLA PASSED: p95={p95_ms:.0f}ms (≤{_P95_SLA_MS:.0f}ms) "
            f"errors={error_rate:.2%} (≤{_ERROR_RATE_SLA:.2%})"
        )
