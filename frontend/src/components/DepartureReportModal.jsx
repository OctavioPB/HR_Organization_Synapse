import { useState, useEffect } from "react";

const API_BASE = "/api";

function KPICard({ value, label, color }) {
  return (
    <div style={{
      background: "#fff",
      borderRadius: "12px",
      padding: "20px",
      borderTop: `3px solid ${color || "var(--gold)"}`,
      textAlign: "center",
      flex: 1,
      minWidth: "120px",
    }}>
      <div style={{ fontFamily: "var(--fd)", fontSize: "24px", fontWeight: 300, color: "var(--dark)" }}>
        {value}
      </div>
      <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginTop: "6px" }}>
        {label}
      </div>
    </div>
  );
}

export default function DepartureReportModal({ employeeId, employeeName, onClose }) {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);

  useEffect(() => {
    if (!employeeId) return;
    setLoading(true);
    fetch(`${API_BASE}/compliance/departure/${employeeId}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [employeeId]);

  const overlay = {
    position: "fixed", inset: 0, background: "rgba(0,0,0,.5)",
    zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center",
    padding: "24px",
  };
  const modal = {
    background: "var(--light)",
    borderRadius: "12px",
    maxWidth: "720px",
    width: "100%",
    maxHeight: "90vh",
    overflowY: "auto",
    boxShadow: "0 8px 32px rgba(0,0,0,.2)",
  };

  return (
    <div style={overlay} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modal}>
        {/* Header */}
        <div style={{
          background: "var(--primary)",
          backgroundImage: "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
          backgroundSize: "48px 48px",
          padding: "28px 32px",
          borderRadius: "12px 12px 0 0",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}>
          <div>
            <div style={{ fontFamily: "var(--fd)", fontSize: "22px", fontWeight: 400, color: "#fff" }}>
              Departure <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>Impact Report</em>
            </div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "rgba(255,255,255,.6)", marginTop: "4px" }}>
              {employeeName || employeeId}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "rgba(255,255,255,.1)", border: "none", borderRadius: "8px",
              color: "#fff", cursor: "pointer", padding: "6px 14px", fontSize: "18px", lineHeight: 1,
            }}
          >×</button>
        </div>

        <div style={{ padding: "28px 32px" }}>
          {loading && <div style={{ fontFamily: "var(--fb)", color: "var(--mid)", textAlign: "center", padding: "32px" }}>Generating report…</div>}
          {error   && <div style={{ fontFamily: "var(--fb)", color: "#E03448", padding: "16px", background: "#FDEAEA", borderRadius: "8px" }}>Error: {error}</div>}

          {data && (() => {
            const impact = data.impact_json || {};
            const spof   = impact.predicted_spof_score != null ? `${Math.round(impact.predicted_spof_score * 100)}%` : "N/A";
            const delta  = impact.graph_diameter_delta_pct != null ? `${impact.graph_diameter_delta_pct > 0 ? "+" : ""}${impact.graph_diameter_delta_pct}%` : "N/A";
            const silos  = impact.new_silo_alerts ?? 0;
            const recov  = impact.recovery_trajectory || "unknown";
            const recovColor = recov === "recovering" ? "#27B97C" : recov === "deteriorating" ? "#E03448" : "var(--mid)";

            return (
              <>
                {/* KPI row */}
                <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", marginBottom: "24px" }}>
                  <KPICard value={spof}  label="Predicted SPOF" color={impact.was_flagged_critical ? "#E03448" : "var(--gold)"} />
                  <KPICard value={delta} label="Network Δ" color={delta.startsWith("+") ? "#E03448" : "#27B97C"} />
                  <KPICard value={silos} label="New Silos" color={silos > 0 ? "#F07020" : "#27B97C"} />
                  <KPICard value={<span style={{ color: recovColor, fontSize: "14px", textTransform: "capitalize" }}>{recov}</span>} label="Recovery" color={recovColor} />
                </div>

                {/* Narrative */}
                {data.narrative_text && (
                  <div style={{
                    borderLeft: "3px solid var(--gold)",
                    padding: "16px 20px",
                    background: "#fff",
                    borderRadius: "0 8px 8px 0",
                    fontFamily: "var(--fb)",
                    fontSize: "14px",
                    color: "var(--dark)",
                    lineHeight: 1.7,
                    marginBottom: "24px",
                  }}>
                    {data.narrative_text}
                  </div>
                )}

                {/* Snapshot table */}
                {impact.snapshots && (
                  <>
                    <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "3px", textTransform: "uppercase", color: "var(--gold)", display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
                      <span style={{ width: "24px", height: "1px", background: "var(--gold)", display: "block" }} />
                      Graph Timeline
                    </div>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--fb)", fontSize: "13px" }}>
                      <thead>
                        <tr style={{ background: "var(--primary)", color: "#fff" }}>
                          {["Checkpoint", "Nodes", "Avg Betweenness"].map(h => (
                            <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: "10px", letterSpacing: "2px", textTransform: "uppercase", fontWeight: 400 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {[
                          ["90 days before", impact.snapshots.t_minus_90],
                          ["Departure date", impact.snapshots.t_minus_0],
                          ["30 days after",  impact.snapshots.t_plus_30],
                          ["60 days after",  impact.snapshots.t_plus_60],
                        ].map(([label, snap], i) => (
                          <tr key={label} style={{ background: i % 2 === 0 ? "#fff" : "var(--primary-10)" }}>
                            <td style={{ padding: "10px 14px" }}>{label}</td>
                            <td style={{ padding: "10px 14px" }}>{snap?.node_count ?? "—"}</td>
                            <td style={{ padding: "10px 14px" }}>{snap?.avg_betweenness != null ? snap.avg_betweenness.toFixed(4) : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}

                {/* Succession candidate */}
                {impact.succession_candidate && (
                  <div style={{ marginTop: "20px", padding: "16px", background: "#E0F7EF", borderRadius: "8px" }}>
                    <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 600, color: "#0D5C3A", marginBottom: "4px" }}>Recommended successor</div>
                    <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--dark)" }}>
                      {impact.succession_candidate.name} · {impact.succession_candidate.department}
                      <span style={{ color: "var(--mid)", marginLeft: "12px" }}>
                        {Math.round((impact.succession_candidate.compatibility_score || 0) * 100)}% structural compatibility
                      </span>
                    </div>
                  </div>
                )}
              </>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
