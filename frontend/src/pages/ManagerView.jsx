import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import TeamMemberCard from "../components/TeamMemberCard.jsx";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function KPIStat({ value, label }) {
  return (
    <div style={{
      background: "#fff",
      borderRadius: "12px",
      boxShadow: "0 1px 4px rgba(0,0,0,.07)",
      padding: "28px",
      borderTop: "3px solid var(--gold)",
      textAlign: "center",
      flex: 1,
    }}>
      <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 300, color: "var(--dark)" }}>
        {value}
      </div>
      <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "3px", textTransform: "uppercase", color: "var(--mid)", marginTop: "6px" }}>
        {label}
      </div>
    </div>
  );
}

export default function ManagerView() {
  const [managerEmployeeId, setManagerEmployeeId] = useState(
    () => localStorage.getItem("manager_employee_id") || ""
  );
  const [inputValue, setInputValue] = useState(managerEmployeeId);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["managerTeam", managerEmployeeId],
    queryFn: async () => {
      if (!managerEmployeeId) return null;
      const res = await fetch(
        `${API_BASE}/manager/team?manager_employee_id=${managerEmployeeId}`,
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      return res.json();
    },
    enabled: Boolean(managerEmployeeId),
    staleTime: 300_000,
  });

  function handleSubmit(e) {
    e.preventDefault();
    localStorage.setItem("manager_employee_id", inputValue.trim());
    setManagerEmployeeId(inputValue.trim());
  }

  const redCount   = data?.team?.filter(m => m.status === "red").length   ?? 0;
  const amberCount = data?.team?.filter(m => m.status === "amber").length ?? 0;
  const greenCount = data?.team?.filter(m => m.status === "green").length ?? 0;

  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>
      {/* Hero */}
      <div style={{
        background: "var(--primary)",
        backgroundImage: "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
        backgroundSize: "48px 48px",
        padding: "48px",
        color: "#fff",
      }}>
        <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 400 }}>
          My Team <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>Health</em>
        </div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "rgba(255,255,255,.6)", marginTop: "8px" }}>
          Engagement signals for your direct reports — no surveillance, just context.
        </div>
      </div>

      <div style={{ padding: "40px 48px", maxWidth: "1100px", margin: "0 auto" }}>

        {/* Manager ID form */}
        <form onSubmit={handleSubmit} style={{ display: "flex", gap: "12px", marginBottom: "32px" }}>
          <input
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            placeholder="Your employee UUID (from HR Admin)"
            style={{
              flex: 1,
              border: "1px solid var(--primary-30)",
              borderRadius: "8px",
              padding: "10px 14px",
              fontFamily: "var(--fb)",
              fontSize: "14px",
              outline: "none",
            }}
          />
          <button
            type="submit"
            style={{
              background: "var(--primary)",
              color: "#fff",
              border: "none",
              borderRadius: "8px",
              padding: "10px 24px",
              fontFamily: "var(--fb)",
              fontSize: "14px",
              cursor: "pointer",
            }}
          >
            Load Team
          </button>
        </form>

        {/* KPI row */}
        {data && (
          <div style={{ display: "flex", gap: "16px", marginBottom: "32px" }}>
            <KPIStat value={data.total_reports} label="Direct Reports" />
            <KPIStat value={greenCount}         label="Engaged" />
            <KPIStat value={amberCount}         label="Watch" />
            <KPIStat value={redCount}           label="At Risk" />
          </div>
        )}

        {/* States */}
        {!managerEmployeeId && (
          <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", padding: "40px" }}>
            Enter your employee ID above to load your team view.
          </div>
        )}

        {isLoading && (
          <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", padding: "40px" }}>
            Loading team data…
          </div>
        )}

        {error && (
          <div style={{
            background: "#FDEAEA", border: "1px solid #E03448", borderRadius: "8px",
            padding: "16px 20px", fontFamily: "var(--fb)", fontSize: "14px", color: "#7A1020",
          }}>
            {error.message}
          </div>
        )}

        {/* Team grid */}
        {data?.team?.length === 0 && (
          <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--mid)", textAlign: "center", padding: "40px" }}>
            No direct reports found for this employee ID.
          </div>
        )}

        {data?.team?.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: "16px" }}>
            {data.team.map(member => (
              <TeamMemberCard
                key={member.employee_id}
                member={member}
                managerEmployeeId={managerEmployeeId}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
