import { useState } from "react";

const STATUS_CONFIG = {
  green:  { color: "#27B97C", bg: "#E0F7EF", label: "Engaged" },
  amber:  { color: "#F07020", bg: "#FEF0E6", label: "Watch" },
  red:    { color: "#E03448", bg: "#FDEAEA", label: "At Risk" },
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export default function TeamMemberCard({ member, managerEmployeeId }) {
  const [open, setOpen]             = useState(false);
  const [suggestions, setSuggestions] = useState(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState(null);

  const cfg = STATUS_CONFIG[member.status] ?? STATUS_CONFIG.green;

  async function fetchSuggestions() {
    if (suggestions) { setOpen(o => !o); return; }
    setOpen(true);
    setLoading(true);
    setError(null);
    try {
      const params = managerEmployeeId
        ? `?manager_employee_id=${managerEmployeeId}`
        : "";
      const res = await fetch(
        `${API_BASE}/manager/team/${member.employee_id}/suggestions${params}`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSuggestions(data.suggestions);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        background: "#fff",
        borderRadius: "12px",
        boxShadow: "0 1px 4px rgba(0,0,0,.07)",
        padding: "20px 24px",
        borderTop: `3px solid ${cfg.color}`,
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}
    >
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontFamily: "var(--fb)", fontWeight: 600, fontSize: "15px", color: "var(--dark)" }}>
            {member.name}
          </div>
          <div style={{ fontFamily: "var(--fb)", fontSize: "12px", color: "var(--mid)", marginTop: "2px" }}>
            {member.department} · {member.role}
          </div>
        </div>

        {/* Status badge */}
        <span style={{
          background: cfg.bg,
          color: cfg.color,
          fontFamily: "var(--fb)",
          fontSize: "10px",
          fontWeight: 500,
          letterSpacing: "2px",
          textTransform: "uppercase",
          padding: "4px 12px",
          borderRadius: "20px",
          display: "flex",
          alignItems: "center",
          gap: "6px",
        }}>
          <span style={{
            width: "6px", height: "6px", borderRadius: "50%",
            background: cfg.color, display: "inline-block",
          }} />
          {cfg.label}
        </span>
      </div>

      {/* Contracting network warning */}
      {member.contracting_network && (
        <div style={{
          background: "#FEF0E6",
          borderLeft: "3px solid #F07020",
          borderRadius: "4px",
          padding: "8px 12px",
          fontFamily: "var(--fb)",
          fontSize: "13px",
          color: "#7A3800",
        }}>
          Network contracting for {member.contracting_weeks}+ weeks
        </div>
      )}

      {/* Suggestions toggle */}
      <button
        onClick={fetchSuggestions}
        style={{
          background: "none",
          border: "1px solid var(--primary-30)",
          borderRadius: "8px",
          padding: "8px 14px",
          fontFamily: "var(--fb)",
          fontSize: "13px",
          color: "var(--primary)",
          cursor: "pointer",
          textAlign: "left",
          transition: "background 150ms",
        }}
        onMouseEnter={e => e.currentTarget.style.background = "var(--primary-10)"}
        onMouseLeave={e => e.currentTarget.style.background = "none"}
      >
        {open ? "▲ Hide suggestions" : "▼ Get 1:1 suggestions"}
      </button>

      {open && (
        <div>
          {loading && (
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", padding: "8px 0" }}>
              Generating suggestions…
            </div>
          )}
          {error && (
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "#E03448", padding: "8px 0" }}>
              Error: {error}
            </div>
          )}
          {suggestions && (
            <ul style={{ margin: "8px 0 0 0", padding: "0 0 0 18px", display: "flex", flexDirection: "column", gap: "8px" }}>
              {suggestions.map((s, i) => (
                <li key={i} style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--dark)", lineHeight: 1.6 }}>
                  {s}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
