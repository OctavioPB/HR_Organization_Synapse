import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

const API_BASE = "/api";

// ─── Data fetching ────────────────────────────────────────────────────────────

async function fetchSnapshot() {
  const res = await fetch(`${API_BASE}/graph/snapshot`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchRisk() {
  const res = await fetch(`${API_BASE}/risk/scores?top=500`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchChurn() {
  const res = await fetch(`${API_BASE}/risk/churn-scores?top=500`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── Derived helpers ──────────────────────────────────────────────────────────

function leaveRisk(churnProb, entropyTrend, flag) {
  if (churnProb != null) {
    if (churnProb >= 0.6) return "High";
    if (churnProb >= 0.3) return "Medium";
    return "Low";
  }
  if (flag === "critical" || (entropyTrend != null && entropyTrend < -0.05)) return "High";
  if (flag === "warning") return "Medium";
  return "Low";
}

const RISK_COLOR = {
  High:   { bg: "#FEE2E2", text: "#B91C1C", border: "#FECACA" },
  Medium: { bg: "#FEF3C7", text: "#92400E", border: "#FDE68A" },
  Low:    { bg: "#D1FAE5", text: "#065F46", border: "#A7F3D0" },
};

const SPOF_COLOR = (s) => {
  if (s >= 0.7) return "#B91C1C";
  if (s >= 0.5) return "#D97706";
  if (s >= 0.4) return "#F59E0B";
  return "#059669";
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function Tag({ level }) {
  const c = RISK_COLOR[level];
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 10px",
      borderRadius: "999px",
      fontSize: "11px",
      fontWeight: 600,
      fontFamily: "var(--fb)",
      letterSpacing: "0.5px",
      background: c.bg,
      color: c.text,
      border: `1px solid ${c.border}`,
    }}>
      {level}
    </span>
  );
}

function SpofBar({ score }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <div style={{
        width: "56px", height: "6px", borderRadius: "3px",
        background: "#E5E7EB", overflow: "hidden",
      }}>
        <div style={{
          width: `${Math.round(score * 100)}%`,
          height: "100%",
          background: SPOF_COLOR(score),
          borderRadius: "3px",
          transition: "width 300ms",
        }} />
      </div>
      <span style={{
        fontFamily: "var(--fb)", fontSize: "12px",
        color: SPOF_COLOR(score), fontWeight: 600, minWidth: "32px",
      }}>
        {score.toFixed(2)}
      </span>
    </div>
  );
}

function UUIDCell({ id }) {
  const [copied, setCopied] = useState(false);
  const short = id.slice(0, 8) + "…";

  function copy() {
    navigator.clipboard.writeText(id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
      <span style={{
        fontFamily: "monospace", fontSize: "11px",
        color: "var(--mid)", letterSpacing: "0.3px",
      }}>
        {short}
      </span>
      <button
        onClick={copy}
        title="Copy UUID"
        style={{
          background: "none", border: "none", cursor: "pointer",
          padding: "2px 4px", borderRadius: "4px",
          fontSize: "11px", color: copied ? "#059669" : "var(--mid)",
          transition: "color 200ms",
        }}
      >
        {copied ? "✓" : "⎘"}
      </button>
    </div>
  );
}

function SlicerBtn({ label, active, onClick, color }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "5px 14px",
        borderRadius: "999px",
        border: `1.5px solid ${active ? (color || "var(--gold)") : "#E5E7EB"}`,
        background: active ? (color ? color + "18" : "#FFF8E1") : "#fff",
        color: active ? (color || "var(--dark)") : "var(--mid)",
        fontFamily: "var(--fb)",
        fontSize: "12px",
        fontWeight: active ? 600 : 400,
        cursor: "pointer",
        transition: "all 150ms",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

const SPOF_TIERS = [
  { label: "All",      min: 0,   max: 1   },
  { label: "Critical", min: 0.7, max: 1,   color: "#B91C1C" },
  { label: "Warning",  min: 0.5, max: 0.7, color: "#D97706" },
  { label: "Elevated", min: 0.4, max: 0.5, color: "#F59E0B" },
  { label: "Normal",   min: 0,   max: 0.4, color: "#059669" },
];

const SORT_KEYS = ["name", "department", "spof_score", "direct_links", "leave_risk"];

export default function EmployeeRoster() {
  const [search, setSearch]         = useState("");
  const [teamFilter, setTeamFilter] = useState("All");
  const [spofTier, setSpofTier]     = useState(0);      // index into SPOF_TIERS
  const [riskFilter, setRiskFilter] = useState("All");
  const [sortKey, setSortKey]       = useState("spof_score");
  const [sortDir, setSortDir]       = useState("desc");

  const snapshot = useQuery({ queryKey: ["roster-snapshot"], queryFn: fetchSnapshot, staleTime: 60_000 });
  const risk     = useQuery({ queryKey: ["roster-risk"],     queryFn: fetchRisk,     staleTime: 60_000 });
  const churn    = useQuery({ queryKey: ["roster-churn"],    queryFn: fetchChurn,    staleTime: 60_000, retry: false });

  const rows = useMemo(() => {
    if (!snapshot.data || !risk.data) return [];

    const degreeMap = {};
    for (const n of snapshot.data.nodes) {
      degreeMap[n.employee_id] = Math.round((n.degree_in + n.degree_out) * (snapshot.data.node_count - 1));
    }

    const churnMap = {};
    if (churn.data?.scores) {
      for (const c of churn.data.scores) churnMap[c.employee_id] = c.churn_prob;
    }

    return risk.data.scores.map(r => ({
      employee_id: r.employee_id,
      name:        r.name,
      department:  r.department,
      spof_score:  r.spof_score,
      flag:        r.flag,
      entropy_trend: r.entropy_trend,
      direct_links: degreeMap[r.employee_id] ?? 0,
      churn_prob:   churnMap[r.employee_id] ?? null,
      leave_risk:   leaveRisk(churnMap[r.employee_id] ?? null, r.entropy_trend, r.flag),
    }));
  }, [snapshot.data, risk.data, churn.data]);

  const teams = useMemo(() => {
    const s = new Set(rows.map(r => r.department));
    return ["All", ...Array.from(s).sort()];
  }, [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const tier = SPOF_TIERS[spofTier];

    let out = rows.filter(r => {
      if (q && !r.name.toLowerCase().includes(q) && !r.employee_id.toLowerCase().includes(q)) return false;
      if (teamFilter !== "All" && r.department !== teamFilter) return false;
      if (r.spof_score < tier.min || r.spof_score > tier.max) return false;
      if (riskFilter !== "All" && r.leave_risk !== riskFilter) return false;
      return true;
    });

    out = [...out].sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (typeof av === "string") av = av.toLowerCase();
      if (typeof bv === "string") bv = bv.toLowerCase();
      const RISK_ORDER = { High: 2, Medium: 1, Low: 0 };
      if (sortKey === "leave_risk") { av = RISK_ORDER[a.leave_risk]; bv = RISK_ORDER[b.leave_risk]; }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });

    return out;
  }, [rows, search, teamFilter, spofTier, riskFilter, sortKey, sortDir]);

  function toggleSort(key) {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  }

  const loading = snapshot.isLoading || risk.isLoading;
  const error   = snapshot.error || risk.error;

  const TH = ({ label, sk, style = {} }) => (
    <th
      onClick={() => toggleSort(sk)}
      style={{
        padding: "10px 16px", textAlign: "left", cursor: "pointer",
        fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 600,
        letterSpacing: "1.5px", textTransform: "uppercase",
        color: sortKey === sk ? "var(--gold)" : "var(--mid)",
        background: "#F8FAFC", borderBottom: "1px solid #E5E7EB",
        userSelect: "none", whiteSpace: "nowrap",
        ...style,
      }}
    >
      {label} {sortKey === sk ? (sortDir === "asc" ? "↑" : "↓") : ""}
    </th>
  );

  return (
    <div style={{ minHeight: "100vh", background: "#F0F4F8", padding: "32px 40px" }}>

      {/* Header */}
      <div style={{ marginBottom: "28px" }}>
        <h1 style={{
          fontFamily: "var(--fd)", fontSize: "28px", fontWeight: 300,
          color: "var(--dark)", margin: 0,
        }}>
          Employee <em style={{ fontStyle: "italic", color: "var(--gold)" }}>Roster</em>
        </h1>
        <p style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginTop: "4px" }}>
          {rows.length} employees · showing {filtered.length}
          {churn.data ? "" : " · Leave risk derived from entropy trend (no GNN scores)"}
        </p>
      </div>

      {/* Search + Slicers */}
      <div style={{
        background: "#fff", borderRadius: "12px", padding: "20px 24px",
        boxShadow: "0 1px 4px rgba(0,0,0,.07)", marginBottom: "20px",
        display: "flex", flexDirection: "column", gap: "16px",
      }}>

        {/* Search bar */}
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search by name or UUID…"
          style={{
            width: "100%", padding: "10px 16px",
            border: "1.5px solid #E5E7EB", borderRadius: "8px",
            fontFamily: "var(--fb)", fontSize: "14px", color: "var(--dark)",
            outline: "none", boxSizing: "border-box",
            transition: "border-color 150ms",
          }}
          onFocus={e => e.target.style.borderColor = "var(--gold)"}
          onBlur={e => e.target.style.borderColor = "#E5E7EB"}
        />

        {/* Slicer rows */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: "20px", alignItems: "flex-start" }}>

          {/* Team */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "8px", fontWeight: 600 }}>Team</div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {teams.map(t => (
                <SlicerBtn key={t} label={t} active={teamFilter === t} onClick={() => setTeamFilter(t)} />
              ))}
            </div>
          </div>

          {/* SPOF tier */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "8px", fontWeight: 600 }}>SPOF Tier</div>
            <div style={{ display: "flex", gap: "6px" }}>
              {SPOF_TIERS.map((t, i) => (
                <SlicerBtn key={t.label} label={t.label} active={spofTier === i} onClick={() => setSpofTier(i)} color={t.color} />
              ))}
            </div>
          </div>

          {/* Leave risk */}
          <div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "8px", fontWeight: 600 }}>Leave Risk</div>
            <div style={{ display: "flex", gap: "6px" }}>
              {["All", "High", "Medium", "Low"].map(r => (
                <SlicerBtn
                  key={r} label={r} active={riskFilter === r}
                  onClick={() => setRiskFilter(r)}
                  color={r === "High" ? "#B91C1C" : r === "Medium" ? "#D97706" : r === "Low" ? "#059669" : undefined}
                />
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Table */}
      <div style={{
        background: "#fff", borderRadius: "12px",
        boxShadow: "0 1px 4px rgba(0,0,0,.07)", overflow: "hidden",
      }}>
        {loading ? (
          <div style={{ padding: "64px", textAlign: "center", fontFamily: "var(--fb)", color: "var(--mid)" }}>
            Loading roster…
          </div>
        ) : error ? (
          <div style={{ padding: "64px", textAlign: "center", fontFamily: "var(--fb)", color: "#B91C1C" }}>
            {error.message}
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: "64px", textAlign: "center", fontFamily: "var(--fb)", color: "var(--mid)" }}>
            No employees match the current filters.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <TH label="Name"         sk="name" />
                  <TH label="UUID"         sk="employee_id" />
                  <TH label="Team"         sk="department" />
                  <TH label="SPOF"         sk="spof_score" />
                  <TH label="Direct Links" sk="direct_links" />
                  <TH label="Leave Risk"   sk="leave_risk" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => (
                  <tr
                    key={r.employee_id}
                    style={{
                      background: i % 2 === 0 ? "#fff" : "#FAFBFC",
                      borderBottom: "1px solid #F1F5F9",
                      transition: "background 150ms",
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = "#EFF6FF"}
                    onMouseLeave={e => e.currentTarget.style.background = i % 2 === 0 ? "#fff" : "#FAFBFC"}
                  >
                    <td style={{ padding: "12px 16px" }}>
                      <Link
                        to={`/employee/${r.employee_id}`}
                        style={{
                          fontFamily: "var(--fb)", fontSize: "14px", fontWeight: 500,
                          color: "var(--dark)", textDecoration: "none",
                        }}
                        onMouseEnter={e => e.target.style.color = "var(--gold)"}
                        onMouseLeave={e => e.target.style.color = "var(--dark)"}
                      >
                        {r.name}
                      </Link>
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <UUIDCell id={r.employee_id} />
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <span style={{
                        fontFamily: "var(--fb)", fontSize: "12px",
                        background: "#EFF6FF", color: "#1E40AF",
                        padding: "2px 10px", borderRadius: "999px",
                        border: "1px solid #BFDBFE",
                      }}>
                        {r.department}
                      </span>
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <SpofBar score={r.spof_score} />
                    </td>
                    <td style={{ padding: "12px 16px", fontFamily: "var(--fb)", fontSize: "14px", color: "var(--dark)", textAlign: "center" }}>
                      {r.direct_links}
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <Tag level={r.leave_risk} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer count */}
        {!loading && !error && filtered.length > 0 && (
          <div style={{
            padding: "12px 20px", borderTop: "1px solid #F1F5F9",
            fontFamily: "var(--fb)", fontSize: "12px", color: "var(--mid)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>{filtered.length} employees shown</span>
            <span>
              {filtered.filter(r => r.leave_risk === "High").length} high risk ·{" "}
              {filtered.filter(r => r.spof_score >= 0.7).length} critical SPOF
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
