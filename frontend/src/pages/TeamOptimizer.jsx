import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

const API_BASE = "/api";

async function fetchDepartments() {
  const res = await fetch(`${API_BASE}/teams/departments`);
  if (!res.ok) throw new Error("Failed to load departments");
  return res.json();
}

async function optimizeTeam(body) {
  const res = await fetch(`${API_BASE}/teams/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
  return res.json();
}

function ScoreBar({ label, value, color }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
      <div style={{ fontFamily: "var(--fb)", fontSize: "12px", color: "var(--mid)", width: "120px", flexShrink: 0 }}>{label}</div>
      <div style={{ flex: 1, background: "var(--light)", borderRadius: "4px", height: "12px", overflow: "hidden" }}>
        <div style={{ width: `${Math.round(value * 100)}%`, height: "100%", background: color, borderRadius: "4px" }} />
      </div>
      <div style={{ fontFamily: "var(--fb)", fontSize: "12px", color: "var(--dark)", width: "36px", textAlign: "right" }}>
        {Math.round(value * 100)}%
      </div>
    </div>
  );
}

function CompositionCard({ composition, index, constraints }) {
  function exportCSV() {
    const params = new URLSearchParams({
      composition_index: index,
      departments: constraints.departments.join(","),
      domains: constraints.domains.join(","),
      min_size: constraints.min_size,
      max_size: constraints.max_size,
      exclude_spof_above: constraints.exclude_spof_above,
    });
    window.open(`${API_BASE}/teams/optimize/export?${params}`, "_blank");
  }

  return (
    <div style={{
      background: "#fff", borderRadius: "12px",
      borderTop: "3px solid var(--gold)",
      padding: "24px", boxShadow: "0 1px 4px rgba(0,0,0,.07)",
      display: "flex", flexDirection: "column", gap: "16px",
    }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontFamily: "var(--fd)", fontSize: "18px", fontWeight: 400, color: "var(--dark)" }}>
          Option <em style={{ fontStyle: "italic", color: "var(--gold)" }}>{composition.rank}</em>
        </div>
        <div style={{
          background: "var(--primary-10)", color: "var(--primary)", fontFamily: "var(--fb)",
          fontSize: "13px", fontWeight: 600, padding: "4px 14px", borderRadius: "20px",
        }}>
          {Math.round(composition.composite_score * 100)}% composite
        </div>
      </div>

      {/* Score bars */}
      <div>
        <ScoreBar label="Bridge Coverage"     value={composition.bridge_coverage}     color="#003366" />
        <ScoreBar label="Domain Coverage"     value={composition.domain_coverage}     color="#7C4DBD" />
        <ScoreBar label="Relationship Density" value={composition.relationship_density} color="#27B97C" />
        <ScoreBar label="Low SPOF Load"       value={Math.max(0, 1 - composition.structural_load)} color="#F07020" />
      </div>

      {/* Members */}
      <div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "11px", letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "10px" }}>
          Team Members
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          {composition.members.map(m => (
            <div key={m.employee_id} style={{
              background: "var(--primary-10)", borderRadius: "8px",
              padding: "8px 12px", fontFamily: "var(--fb)", fontSize: "13px",
            }}>
              <div style={{ fontWeight: 600, color: "var(--dark)" }}>{m.name}</div>
              <div style={{ color: "var(--mid)", fontSize: "11px" }}>{m.department}</div>
              {m.spof_score > 0.4 && (
                <div style={{ color: "#F07020", fontSize: "10px", marginTop: "2px" }}>SPOF {Math.round(m.spof_score * 100)}%</div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Export */}
      <button onClick={exportCSV} style={{
        background: "none", border: "1px solid var(--primary-30)", borderRadius: "8px",
        padding: "8px 18px", fontFamily: "var(--fb)", fontSize: "13px",
        color: "var(--primary)", cursor: "pointer", alignSelf: "flex-start",
      }}>
        Export CSV ↓
      </button>
    </div>
  );
}

export default function TeamOptimizer() {
  const [deptSelections, setDeptSel] = useState([]);
  const [domainInput, setDomainInput] = useState("");
  const [domains, setDomains]         = useState([]);
  const [minSize, setMinSize]         = useState(3);
  const [maxSize, setMaxSize]         = useState(6);
  const [spofLimit, setSpofLimit]     = useState(0.7);
  const [results, setResults]         = useState(null);
  const [error, setError]             = useState(null);
  const [lastConstraints, setLastC]   = useState(null);

  const { data: deptsData } = useQuery({ queryKey: ["teamDepts"], queryFn: fetchDepartments, staleTime: 600_000 });

  const mutation = useMutation({
    mutationFn: optimizeTeam,
    onSuccess: (data) => { setResults(data); setError(null); },
    onError: (e) => setError(e.message),
  });

  function addDomain(e) {
    e.preventDefault();
    const v = domainInput.trim();
    if (v && !domains.includes(v)) setDomains(prev => [...prev, v]);
    setDomainInput("");
  }

  function toggleDept(dept) {
    setDeptSel(prev => prev.includes(dept) ? prev.filter(d => d !== dept) : [...prev, dept]);
  }

  function handleOptimize() {
    const constraints = { departments: deptSelections, domains, min_size: minSize, max_size: maxSize, exclude_spof_above: spofLimit };
    setLastC(constraints);
    mutation.mutate(constraints);
  }

  const sliderStyle = { width: "100%", accentColor: "var(--primary)" };

  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>
      {/* Hero */}
      <div style={{
        background: "var(--primary)",
        backgroundImage: "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
        backgroundSize: "48px 48px", padding: "48px",
      }}>
        <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 400, color: "#fff" }}>
          Team <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>Composition</em>
        </div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "rgba(255,255,255,.6)", marginTop: "8px" }}>
          Find the optimal team to bridge departments and cover knowledge domains.
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: "24px", padding: "32px 48px", maxWidth: "1200px", margin: "0 auto" }}>

        {/* Left: constraints form */}
        <div style={{ background: "#fff", borderRadius: "12px", padding: "24px", boxShadow: "0 1px 4px rgba(0,0,0,.07)", display: "flex", flexDirection: "column", gap: "20px", alignSelf: "start" }}>
          <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 500, letterSpacing: "2px", textTransform: "uppercase", color: "var(--gold)" }}>
            Constraints
          </div>

          {/* Department selector */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginBottom: "8px" }}>Departments to bridge</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
              {(deptsData?.departments || []).map(d => (
                <button key={d} onClick={() => toggleDept(d)} style={{
                  padding: "5px 12px", borderRadius: "20px", border: "1px solid var(--primary-30)",
                  fontFamily: "var(--fb)", fontSize: "12px", cursor: "pointer",
                  background: deptSelections.includes(d) ? "var(--primary)" : "#fff",
                  color: deptSelections.includes(d) ? "#fff" : "var(--primary)",
                }}>
                  {d}
                </button>
              ))}
            </div>
          </div>

          {/* Domains */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginBottom: "8px" }}>Knowledge domains required</div>
            <form onSubmit={addDomain} style={{ display: "flex", gap: "6px", marginBottom: "8px" }}>
              <input value={domainInput} onChange={e => setDomainInput(e.target.value)} placeholder="e.g. payments, compliance" style={{
                flex: 1, border: "1px solid var(--primary-30)", borderRadius: "8px",
                padding: "8px 12px", fontFamily: "var(--fb)", fontSize: "13px", outline: "none",
              }} />
              <button type="submit" style={{ background: "var(--primary)", color: "#fff", border: "none", borderRadius: "8px", padding: "8px 14px", cursor: "pointer", fontSize: "13px" }}>+</button>
            </form>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
              {domains.map(d => (
                <span key={d} style={{
                  background: "var(--primary-10)", color: "var(--primary)", borderRadius: "20px",
                  padding: "4px 10px", fontFamily: "var(--fb)", fontSize: "12px",
                  display: "flex", alignItems: "center", gap: "6px",
                }}>
                  {d}
                  <button onClick={() => setDomains(prev => prev.filter(x => x !== d))}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "var(--mid)", fontSize: "14px", lineHeight: 1 }}>×</button>
                </span>
              ))}
            </div>
          </div>

          {/* Size range */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginBottom: "8px" }}>
              Team size: {minSize}–{maxSize} people
            </div>
            <div style={{ display: "flex", gap: "12px" }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: "11px", color: "var(--mid)", marginBottom: "4px" }}>Min</div>
                <input type="range" min={2} max={10} value={minSize} onChange={e => setMinSize(+e.target.value)} style={sliderStyle} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: "11px", color: "var(--mid)", marginBottom: "4px" }}>Max</div>
                <input type="range" min={3} max={20} value={maxSize} onChange={e => setMaxSize(+e.target.value)} style={sliderStyle} />
              </div>
            </div>
          </div>

          {/* SPOF limit */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginBottom: "8px" }}>
              Exclude SPOF above: {Math.round(spofLimit * 100)}%
            </div>
            <input type="range" min={0.3} max={1.0} step={0.05} value={spofLimit} onChange={e => setSpofLimit(+e.target.value)} style={sliderStyle} />
          </div>

          {error && <div style={{ background: "#FDEAEA", borderRadius: "8px", padding: "10px", fontFamily: "var(--fb)", fontSize: "13px", color: "#7A1020" }}>{error}</div>}

          <button onClick={handleOptimize} disabled={mutation.isPending} style={{
            background: "var(--primary)", color: "#fff", border: "none", borderRadius: "8px",
            padding: "12px", fontFamily: "var(--fb)", fontSize: "14px", fontWeight: 600,
            cursor: mutation.isPending ? "not-allowed" : "pointer",
            opacity: mutation.isPending ? 0.7 : 1,
          }}>
            {mutation.isPending ? "Optimizing…" : "Find Optimal Teams →"}
          </button>
        </div>

        {/* Right: results */}
        <div>
          {!results && !mutation.isPending && (
            <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", paddingTop: "80px" }}>
              Set constraints and click "Find Optimal Teams" to get recommendations.
            </div>
          )}
          {mutation.isPending && (
            <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", paddingTop: "80px" }}>Optimizing…</div>
          )}
          {results && (
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)" }}>
                {results.total_found} composition(s) found
              </div>
              {results.compositions.map((comp, i) => (
                <CompositionCard key={i} composition={comp} index={i} constraints={lastConstraints} />
              ))}
              {results.total_found === 0 && (
                <div style={{ fontFamily: "var(--fb)", color: "var(--mid)", textAlign: "center", padding: "40px" }}>
                  No compositions found for these constraints. Try relaxing the SPOF limit or reducing required domains.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
