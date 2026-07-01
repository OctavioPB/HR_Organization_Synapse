import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

const API_BASE = "/api";

const PHASE_CONFIG = [
  { key: "weeks_1_4",  label: "Weeks 1–4",  subtitle: "Relationship Introductions",  color: "#003366" },
  { key: "weeks_5_8",  label: "Weeks 5–8",  subtitle: "Document Reviews",             color: "#7C4DBD" },
  { key: "weeks_9_12", label: "Weeks 9–12", subtitle: "Structural Shadowing",         color: "#27B97C" },
];

function ActionChecklist({ actions, phaseKey, planId }) {
  const [checked, setChecked] = useState(() => {
    const stored = localStorage.getItem(`tp_${planId}_${phaseKey}`);
    return stored ? new Set(JSON.parse(stored)) : new Set();
  });

  function toggle(i) {
    setChecked(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      localStorage.setItem(`tp_${planId}_${phaseKey}`, JSON.stringify([...next]));
      return next;
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {actions.map((a, i) => (
        <label key={i} style={{ display: "flex", alignItems: "flex-start", gap: "10px", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={checked.has(i)}
            onChange={() => toggle(i)}
            style={{ marginTop: "3px", accentColor: "var(--primary)" }}
          />
          <span style={{
            fontFamily: "var(--fb)", fontSize: "14px", lineHeight: 1.6,
            color: "var(--dark)",
            textDecoration: checked.has(i) ? "line-through" : "none",
            opacity: checked.has(i) ? 0.5 : 1,
          }}>
            {a.description}
          </span>
        </label>
      ))}
      {actions.length === 0 && (
        <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)" }}>No actions for this phase.</div>
      )}
    </div>
  );
}

export default function TransferPlanPanel({ employeeId }) {
  const [activePhase, setActivePhase] = useState(0);
  const [activePlan,  setActivePlan]  = useState(0);

  const { data, isLoading, error } = useQuery({
    queryKey: ["transferPlan", employeeId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/succession/${employeeId}/transfer-plan`);
      if (!res.ok) {
        if (res.status === 404) return null;
        throw new Error(`HTTP ${res.status}`);
      }
      return res.json();
    },
    enabled: Boolean(employeeId),
    staleTime: 600_000,
  });

  function downloadCSV() {
    window.open(`${API_BASE}/succession/${employeeId}/transfer-plan/export.csv`, "_blank");
  }

  if (isLoading) return <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", padding: "16px" }}>Loading transfer plan…</div>;
  if (error)     return <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "#E03448", padding: "16px" }}>Failed to load transfer plan.</div>;
  if (!data)     return <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", padding: "16px" }}>No transfer plan yet. Run the succession_dag to generate one.</div>;

  const plan = data.plans?.[activePlan];
  const planJson = plan?.plan_json || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>

      {/* Candidate selector */}
      {data.plans.length > 1 && (
        <div style={{ display: "flex", gap: "8px" }}>
          {data.plans.map((p, i) => (
            <button key={i} onClick={() => { setActivePlan(i); setActivePhase(0); }} style={{
              padding: "6px 16px", borderRadius: "20px", border: "1px solid var(--primary-30)",
              fontFamily: "var(--fb)", fontSize: "13px", cursor: "pointer",
              background: activePlan === i ? "var(--primary)" : "#fff",
              color: activePlan === i ? "#fff" : "var(--primary)",
            }}>
              {p.candidate_name}
            </button>
          ))}
        </div>
      )}

      {/* AI narrative */}
      {planJson.narrative && (
        <div style={{
          borderLeft: "3px solid var(--gold)", padding: "14px 18px",
          background: "#fff", borderRadius: "0 8px 8px 0",
          fontFamily: "var(--fb)", fontSize: "14px", color: "var(--dark)", lineHeight: 1.7,
        }}>
          {planJson.narrative}
        </div>
      )}

      {/* Phase tabs */}
      <div style={{ display: "flex", gap: "0", borderBottom: "1px solid var(--primary-10)" }}>
        {PHASE_CONFIG.map((phase, i) => (
          <button key={i} onClick={() => setActivePhase(i)} style={{
            padding: "10px 20px", fontFamily: "var(--fb)", fontSize: "13px",
            border: "none", background: "none", cursor: "pointer",
            borderBottom: activePhase === i ? `2px solid ${phase.color}` : "2px solid transparent",
            color: activePhase === i ? phase.color : "var(--mid)",
            fontWeight: activePhase === i ? 600 : 400,
          }}>
            {phase.label}
            <div style={{ fontSize: "11px", fontWeight: 400, marginTop: "2px" }}>{phase.subtitle}</div>
          </button>
        ))}
      </div>

      {/* Actions for active phase */}
      <ActionChecklist
        actions={planJson[PHASE_CONFIG[activePhase].key] || []}
        phaseKey={PHASE_CONFIG[activePhase].key}
        planId={plan?.plan_id || employeeId}
      />

      {/* Export */}
      <div style={{ marginTop: "8px" }}>
        <button
          onClick={downloadCSV}
          style={{
            background: "none", border: "1px solid var(--primary-30)", borderRadius: "8px",
            padding: "8px 18px", fontFamily: "var(--fb)", fontSize: "13px",
            color: "var(--primary)", cursor: "pointer",
          }}
        >
          Export CSV ↓
        </button>
      </div>
    </div>
  );
}
