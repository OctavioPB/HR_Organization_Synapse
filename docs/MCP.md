# MCP Server — Organizational Synapse

The MCP server lets an LLM client (Claude Code, Claude Desktop, an IDE assistant, or your own agent) ask questions of the platform and act in it. Example questions it can answer:
- Who are our single points of failure?
- What breaks if Carl leaves?
- Draft a succession brief.
- Model merging HR into Sales.

It is a thin layer over the REST API, so the LLM sees exactly what a dashboard user with the same API key would see. Code: [`mcp_server/`](../mcp_server).

```
LLM client ──MCP (stdio or HTTP)──► mcp_server ──HTTP──► FastAPI (api/) ──► Postgres
                                         │
                                         └── app_url deep links ──► web app (frontend/)
```

---

## 1. Quick start

1. Start the stack: `.\demo.ps1`, or the API alone with `uvicorn api.main:app --port 8001`.
2. Install the SDK: `pip install "mcp>=2.3,<3"`. It is in `requirements.txt`.
3. Connect a client:

**Claude Code.** The repo ships [`.mcp.json`](../.mcp.json). Open the project and approve the `org-synapse` server when prompted, or run:

```bash
claude mcp add org-synapse -e SYNAPSE_API_URL=http://localhost:8001 -- python -m mcp_server
```

**Claude Desktop.** Add this to `claude_desktop_config.json`, using absolute paths:

```json
{
  "mcpServers": {
    "org-synapse": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "C:/path/to/HR_Organization_Synapse",
      "env": { "SYNAPSE_API_URL": "http://localhost:8001", "SYNAPSE_APP_URL": "http://localhost:5173" }
    }
  }
}
```

**HTTP clients and remote agents:**

```bash
python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 8765   # → http://127.0.0.1:8765/mcp
```

The HTTP transport has no authentication of its own. Keep it on localhost, or put it behind your gateway: anyone who can reach it can read what its API key can read.

## 2. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SYNAPSE_API_URL` | `http://localhost:8001` | API base URL |
| `SYNAPSE_APP_URL` | `http://localhost:5173` | Web app base URL used in `app_url` deep links |
| `SYNAPSE_TENANT_ID` / `SYNAPSE_API_KEY` | (none) | Sent as `X-Tenant-ID` / `X-Api-Key` in multi-tenant deployments |
| `SYNAPSE_TIMEOUT` | `60` | Request timeout, in seconds |
| `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT` | `stdio`, `127.0.0.1`, `8765` | Defaults for the command-line flags |

**Use the least-privileged key that does the job.** Individual churn probabilities are `hr_admin` data. A `manager` key limits the LLM to what a manager may see, because the API enforces roles, not the MCP server.

## 3. What it exposes

**Tools.** 34 in all; every tool except `create_and_run_scenario` is declared read-only.

| Area | Tools |
|---|---|
| People and network | `search_employees`, `get_network_overview`, `get_employee_profile`, `find_connection_path`, `get_reachability`, `get_knowledge_islands` |
| Risk | `get_spof_risks`, `simulate_departure` (what-if, saves nothing), `get_churn_risks`, `get_temporal_anomalies` |
| Alerts and health | `get_org_health`, `get_alerts` (silos, withdrawal, departures, recent), `get_silo_members`, `get_departure_report` |
| Knowledge and succession | `get_knowledge_risks`, `get_knowledge_impact`, `get_succession_plan`, `get_succession_overview` |
| Managers and onboarding | `list_managers`, `get_team_view`, `get_one_on_one_suggestions`, `get_onboarding_cohort` |
| Scenarios and teams | `list_scenarios`, `get_scenario`, **`create_and_run_scenario`** (write), `compare_scenarios`, `optimize_team`, `list_departments_and_domains` |
| Equity and compliance | `get_equity_distribution`, `get_succession_equity_check`, `get_data_inventory` |
| App and platform | `get_app_link`, `ask_synapse` (the API's own NL agent), `check_api_health` |

**Resource.** `synapse://docs/{name}` serves `math-model`, `model-card-churn`, `business-value`, `architecture` and `lessons-learned`. The LLM can read how a score is computed before explaining it.

**Prompts:**
- `weekly_risk_review`
- `succession_brief(employee_name)`
- `reorg_impact(change)`

**Design choices:**
- **Summarised, not raw.** The graph snapshot is about 200 KB, and one person's network about 170 KB. Tools return what an answer needs: top brokers, strongest collaborators, counts per department. Lists longer than 5 in alert payloads are collapsed to a count.
- **Missing data is a status, not a failure.** Churn, knowledge and succession scores need their pipelines to run first. When they haven't, the tool says "not computed yet" instead of erroring or inventing data.
- **Every person carries an `app_url`** to their page in the dashboard, so the LLM hands the user a place to verify and act.
- **Name first.** The instructions tell the model to resolve names with `search_employees` and ask when several people match.

## 4. What it deliberately does not expose

| Not exposed | Why |
|---|---|
| Tenant and API-key admin, billing, usage | Administrative, and needs the admin secret |
| Dev seed and reset, retention purge | Destructive or irreversible |
| Consent changes, demographic import | Legal acts about a person; they need a human |
| GDPR data export of one employee | Hands over an individual's full record |
| Connector webhooks, internal broadcast | Machine-to-machine endpoints |

The only write is saving and computing a reorg scenario. It adds a record to the Scenario Planner and changes no employee data.

## 5. Responsible use

These rules are part of the server's instructions to the model:
- **Not for decisions about the person.** Scores are triage signals for HR, never inputs to pay, promotion, performance or termination decisions. They are not shared with the person scored.
- **`critical_uncertain` means review, not critical.**
- **Equity flags are investigative heuristics**, reported only for groups of at least 5 people. They are not findings of discrimination.
- **No guessing.** When data is missing, the model says so.

The platform's DPIA ([DPIA_THREAT_MODEL.md](DPIA_THREAT_MODEL.md)) applies. An LLM client is one more consumer of the API, and its conversation logs may now contain personal data.

## 6. Tests

[`tests/unit/test_mcp_server.py`](../tests/unit/test_mcp_server.py) runs the server in-process against a mocked API, so it needs no server, database or network. It checks:
- **Tool surface:** no admin or irreversible tools, and only one writer.
- **Summarisation and app links.**
- **Error mapping:** not found, validation, unreachable API.
- **Auth headers.**
- **Resources and prompts.**
