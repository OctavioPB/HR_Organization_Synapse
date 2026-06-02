import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// ─── API helpers ──────────────────────────────────────────────────────────────

async function fetchScenarios() {
  const res = await fetch(`${API_BASE}/scenarios`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function createAndCompute(body) {
  const create = await fetch(`${API_BASE}/scenarios`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!create.ok) { const e = await create.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${create.status}`); }
  const { scenario_id } = await create.json();

  const compute = await fetch(`${API_BASE}/scenarios/${scenario_id}/compute`, { method: "POST" });
  if (!compute.ok) { const e = await compute.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${compute.status}`); }
  return compute.json();
}

async function fetchScenario(id) {
  const res = await fetch(`${API_BASE}/scenarios/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ImpactKPI({ label, value, delta, unit = "" }) {
  const isPositive = delta > 0;
  const color = isPositive ? "#E03448" : "#27B97C";
  return (
    <div style={{ background: "#fff", borderRadius: "12px", borderTop: "3px solid var(--gold)", padding: "20px", textAlign: "center", flex: 1, boxShadow: "0 1px 4px rgba(0,0,0,.07)" }}>
      <div style={{ fontFamily: "var(--fd)", fontSize: "28px", fontWeight: 300, color: "var(--dark)" }}>
        {value}{unit}
      </div>
      <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginTop: "6px" }}>{label}</div>
      {delta !== undefined && (
        <div style={{ fontFamily: "var(--fb)", fontSize: "12px", color, marginTop: "4px" }}>
          {delta > 0 ? `+${delta}` : delta}{unit}
        </div>
      )}
    </div>
  );
}

function ImpactPanel({ report, name }) {
  if (!report) return null;
  const { before, after, silos_before, silos_after, nodes_removed, new_isolated_components, avg_path_length_delta_pct, spof_top10_delta } = report;

  const chartData = (spof_top10_delta || []).slice(0, 6).map(d => ({
    name: d.name ? d.name.split(" ")[0] : d.employee_id.slice(0, 8),
    before: parseFloat((d.spof_before * 100).toFixed(1)),
    after:  parseFloat((d.spof_after  * 100).toFixed(1)),
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      <div style={{ fontFamily: "var(--fd)", fontSize: "18px", fontWeight: 400, color: "var(--dark)" }}>
        Impact: <em style={{ fontStyle: "italic", color: "var(--gold)" }}>{name}</em>
      </div>

      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
        <ImpactKPI label="Nodes Removed"          value={nodes_removed}              delta={nodes_removed} />
        <ImpactKPI label="New Isolated Clusters"   value={new_isolated_components}    delta={new_isolated_components} />
        <ImpactKPI label="Path Length Δ"           value={avg_path_length_delta_pct}  delta={avg_path_length_delta_pct} unit="%" />
        <ImpactKPI label="Silos Before → After"    value={`${silos_before} → ${silos_after}`} delta={silos_after - silos_before} />
      </div>

      {chartData.length > 0 && (
        <div style={{ background: "#fff", borderRadius: "12px", padding: "24px", boxShadow: "0 1px 4px rgba(0,0,0,.07)" }}>
          <div style={{ fontFamily: "var(--fb)", fontSize: "12px", color: "var(--mid)", marginBottom: "12px", letterSpacing: "2px", textTransform: "uppercase" }}>
            SPOF Risk — Before vs After (top affected)
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E0EAF4" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#6B7280" }} />
              <YAxis unit="%" tick={{ fontSize: 11, fill: "#6B7280" }} />
              <Tooltip formatter={(v) => [`${v}%`]} />
              <Legend />
              <Bar dataKey="before" name="Before" fill="#003366" radius={[4, 4, 0, 0]} />
              <Bar dataKey="after"  name="After"  fill="#E03448" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function OperationTag({ op, onRemove }) {
  const labels = { remove: "Remove employees", merge_depts: "Merge departments", move_team: "Move team" };
  return (
    <div style={{
      background: "var(--primary-10)", borderRadius: "8px", padding: "8px 12px",
      fontFamily: "var(--fb)", fontSize: "13px", color: "var(--primary)",
      display: "flex", alignItems: "center", gap: "10px",
    }}>
      <span style={{ fontWeight: 600 }}>{labels[op.op] || op.op}</span>
      {op.employee_ids?.length > 0 && <span style={{ color: "var(--mid)" }}>{op.employee_ids.length} employee(s)</span>}
      {op.source_dept && <span style={{ color: "var(--mid)" }}>{op.source_dept} → {op.target_dept}</span>}
      {!op.source_dept && op.target_dept && <span style={{ color: "var(--mid)" }}>→ {op.target_dept}</span>}
      <button onClick={onRemove} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--mid)", fontSize: "16px", lineHeight: 1 }}>×</button>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function ScenarioPlanner() {
  const queryClient = useQueryClient();
  const [selected, setSelected]     = useState(null);
  const [scenarioName, setName]     = useState("");
  const [activeOp, setActiveOp]     = useState("remove");
  const [operations, setOps]        = useState([]);
  const [empInput, setEmpInput]     = useState("");
  const [sourceDept, setSourceDept] = useState("");
  const [targetDept, setTargetDept] = useState("");
  const [error, setError]           = useState(null);

  const { data: listData, isLoading } = useQuery({ queryKey: ["scenarios"], queryFn: fetchScenarios });

  const { data: selectedDetail } = useQuery({
    queryKey: ["scenario", selected],
    queryFn: () => fetchScenario(selected),
    enabled: Boolean(selected),
  });

  const mutation = useMutation({
    mutationFn: createAndCompute,
    onSuccess: () => {
      queryClient.invalidateQueries(["scenarios"]);
      setOps([]); setName(""); setError(null);
    },
    onError: (e) => setError(e.message),
  });

  function addOperation() {
    if (activeOp === "remove") {
      const ids = empInput.split(/[\s,]+/).filter(Boolean);
      if (!ids.length) return;
      setOps(prev => [...prev, { op: "remove", employee_ids: ids }]);
      setEmpInput("");
    } else if (activeOp === "merge_depts") {
      if (!sourceDept || !targetDept) return;
      setOps(prev => [...prev, { op: "merge_depts", source_dept: sourceDept, target_dept: targetDept }]);
      setSourceDept(""); setTargetDept("");
    } else if (activeOp === "move_team") {
      const ids = empInput.split(/[\s,]+/).filter(Boolean);
      if (!ids.length || !targetDept) return;
      setOps(prev => [...prev, { op: "move_team", employee_ids: ids, target_dept: targetDept }]);
      setEmpInput(""); setTargetDept("");
    }
  }

  function removeOp(i) { setOps(prev => prev.filter((_, idx) => idx !== i)); }

  function handleSimulate() {
    if (!scenarioName.trim() || !operations.length) return;
    mutation.mutate({ name: scenarioName, operations });
  }

  const inputStyle = {
    border: "1px solid var(--primary-30)", borderRadius: "8px",
    padding: "9px 14px", fontFamily: "var(--fb)", fontSize: "14px",
    outline: "none", width: "100%", boxSizing: "border-box",
  };

  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>
      {/* Hero */}
      <div style={{
        background: "var(--primary)",
        backgroundImage: "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
        backgroundSize: "48px 48px", padding: "48px",
      }}>
        <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 400, color: "#fff" }}>
          Reorg <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>Scenarios</em>
        </div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "rgba(255,255,255,.6)", marginTop: "8px" }}>
          Model structural impact of headcount reductions, department merges, and team moves.
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "340px 1fr", gap: "24px", padding: "32px 48px", maxWidth: "1200px", margin: "0 auto" }}>

        {/* Left: scenario list + builder */}
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>

          {/* Saved scenarios */}
          <div style={{ background: "#fff", borderRadius: "12px", padding: "20px", boxShadow: "0 1px 4px rgba(0,0,0,.07)" }}>
            <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 500, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "12px" }}>
              Saved Scenarios
            </div>
            {isLoading && <div style={{ color: "var(--mid)", fontFamily: "var(--fb)", fontSize: "13px" }}>Loading…</div>}
            {listData?.scenarios?.length === 0 && <div style={{ color: "var(--mid)", fontFamily: "var(--fb)", fontSize: "13px" }}>No scenarios yet.</div>}
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {(listData?.scenarios || []).map(s => (
                <button
                  key={s.scenario_id}
                  onClick={() => setSelected(s.scenario_id)}
                  style={{
                    background: selected === s.scenario_id ? "var(--primary-10)" : "#fff",
                    border: "1px solid var(--primary-30)",
                    borderRadius: "8px", padding: "10px 14px",
                    fontFamily: "var(--fb)", fontSize: "13px",
                    cursor: "pointer", textAlign: "left",
                    borderLeft: selected === s.scenario_id ? "3px solid var(--primary)" : "1px solid var(--primary-30)",
                  }}
                >
                  <div style={{ fontWeight: 600, color: "var(--dark)" }}>{s.name}</div>
                  <div style={{ color: "var(--mid)", fontSize: "11px", marginTop: "2px" }}>
                    {s.status} {s.path_length_delta_pct != null ? `· Path +${s.path_length_delta_pct}%` : ""}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Builder form */}
          <div style={{ background: "#fff", borderRadius: "12px", padding: "20px", boxShadow: "0 1px 4px rgba(0,0,0,.07)" }}>
            <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 500, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "12px" }}>
              New Scenario
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <input value={scenarioName} onChange={e => setName(e.target.value)} placeholder="Scenario name" style={inputStyle} />

              {/* Op type tabs */}
              <div style={{ display: "flex", gap: "6px" }}>
                {["remove", "merge_depts", "move_team"].map(op => (
                  <button key={op} onClick={() => setActiveOp(op)} style={{
                    flex: 1, padding: "6px 8px", fontFamily: "var(--fb)", fontSize: "11px",
                    border: "1px solid var(--primary-30)", borderRadius: "6px", cursor: "pointer",
                    background: activeOp === op ? "var(--primary)" : "#fff",
                    color: activeOp === op ? "#fff" : "var(--primary)",
                    letterSpacing: "0.5px",
                  }}>
                    {op === "remove" ? "Remove" : op === "merge_depts" ? "Merge" : "Move"}
                  </button>
                ))}
              </div>

              {activeOp === "remove" && (
                <input value={empInput} onChange={e => setEmpInput(e.target.value)} placeholder="Employee UUIDs (comma-separated)" style={inputStyle} />
              )}
              {activeOp === "merge_depts" && <>
                <input value={sourceDept} onChange={e => setSourceDept(e.target.value)} placeholder="Source department" style={inputStyle} />
                <input value={targetDept} onChange={e => setTargetDept(e.target.value)} placeholder="Target department" style={inputStyle} />
              </>}
              {activeOp === "move_team" && <>
                <input value={empInput}   onChange={e => setEmpInput(e.target.value)}   placeholder="Employee UUIDs (comma-separated)" style={inputStyle} />
                <input value={targetDept} onChange={e => setTargetDept(e.target.value)} placeholder="Target department" style={inputStyle} />
              </>}

              <button onClick={addOperation} style={{
                background: "var(--primary-10)", border: "none", borderRadius: "8px",
                padding: "9px", fontFamily: "var(--fb)", fontSize: "13px",
                color: "var(--primary)", cursor: "pointer",
              }}>+ Add operation</button>

              {/* Operations list */}
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {operations.map((op, i) => (
                  <OperationTag key={i} op={op} onRemove={() => removeOp(i)} />
                ))}
              </div>

              {error && <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "#E03448" }}>{error}</div>}

              <button
                onClick={handleSimulate}
                disabled={mutation.isPending || !scenarioName.trim() || operations.length === 0}
                style={{
                  background: "var(--primary)", color: "#fff", border: "none", borderRadius: "8px",
                  padding: "12px", fontFamily: "var(--fb)", fontSize: "14px", fontWeight: 600,
                  cursor: mutation.isPending ? "not-allowed" : "pointer",
                  opacity: (!scenarioName.trim() || !operations.length) ? 0.5 : 1,
                }}
              >
                {mutation.isPending ? "Simulating…" : "Save & Simulate"}
              </button>
            </div>
          </div>
        </div>

        {/* Right: impact panel */}
        <div style={{ background: "#fff", borderRadius: "12px", padding: "28px", boxShadow: "0 1px 4px rgba(0,0,0,.07)", minHeight: "400px" }}>
          {!selected && (
            <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", paddingTop: "80px" }}>
              Select a scenario on the left to view its impact report,<br />or create a new one.
            </div>
          )}
          {selected && selectedDetail && (
            <ImpactPanel
              report={selectedDetail.impact_report}
              name={selectedDetail.name}
            />
          )}
          {selected && !selectedDetail && (
            <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", paddingTop: "80px" }}>Loading…</div>
          )}
        </div>
      </div>
    </div>
  );
}
