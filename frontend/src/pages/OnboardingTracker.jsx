import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, LineChart, Line,
} from "recharts";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function KPIStat({ value, label }) {
  return (
    <div style={{
      background: "#fff", borderRadius: "12px", borderTop: "3px solid var(--gold)",
      padding: "24px", textAlign: "center", flex: 1, boxShadow: "0 1px 4px rgba(0,0,0,.07)",
    }}>
      <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 300, color: "var(--dark)" }}>{value}</div>
      <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "3px", textTransform: "uppercase", color: "var(--mid)", marginTop: "6px" }}>{label}</div>
    </div>
  );
}

function HistoryPanel({ employeeId, name, onClose }) {
  const { data, isLoading } = useQuery({
    queryKey: ["onboardingHistory", employeeId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/onboarding/employee/${employeeId}/history`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    enabled: Boolean(employeeId),
  });

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.5)",
      zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: "24px",
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{
        background: "#fff", borderRadius: "12px", maxWidth: "640px", width: "100%",
        padding: "28px 32px", boxShadow: "0 8px 32px rgba(0,0,0,.2)",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <div style={{ fontFamily: "var(--fd)", fontSize: "20px", fontWeight: 400, color: "var(--dark)" }}>
              Integration <em style={{ fontStyle: "italic", color: "var(--gold)" }}>History</em>
            </div>
            <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginTop: "2px" }}>{name}</div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", fontSize: "20px", cursor: "pointer", color: "var(--mid)" }}>×</button>
        </div>
        {isLoading ? (
          <div style={{ fontFamily: "var(--fb)", color: "var(--mid)", textAlign: "center", padding: "32px" }}>Loading…</div>
        ) : data?.history?.length > 0 ? (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data.history}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E0EAF4" />
              <XAxis dataKey="scored_date" tick={{ fontSize: 11, fill: "#6B7280" }} tickFormatter={v => v.slice(5)} />
              <YAxis domain={[0, 1]} tick={{ fontSize: 11, fill: "#6B7280" }} tickFormatter={v => `${Math.round(v * 100)}%`} />
              <Tooltip formatter={v => [`${Math.round(v * 100)}%`, "Integration Score"]} />
              <ReferenceLine y={0.5} stroke="#F07020" strokeDasharray="4 4" label={{ value: "50% threshold", position: "insideTopRight", fontSize: 10, fill: "#F07020" }} />
              <Line type="monotone" dataKey="integration_score" stroke="#003366" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ fontFamily: "var(--fb)", color: "var(--mid)", textAlign: "center" }}>No history data.</div>
        )}
      </div>
    </div>
  );
}

const CustomDot = (props) => {
  const { cx, cy, payload, onClick } = props;
  const color = payload.below_cohort_threshold ? "#E03448" : "#003366";
  return (
    <circle
      cx={cx} cy={cy} r={6} fill={color} fillOpacity={0.85}
      style={{ cursor: "pointer" }}
      onClick={() => onClick(payload)}
    />
  );
};

export default function OnboardingTracker() {
  const [selected, setSelected] = useState(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["onboardingCohort"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/onboarding/cohort`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    staleTime: 300_000,
  });

  const scatterData = (data?.cohort || []).map(m => ({
    x: m.tenure_days,
    y: m.integration_score,
    ...m,
  }));

  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>
      {selected && (
        <HistoryPanel
          employeeId={selected.employee_id}
          name={selected.name}
          onClose={() => setSelected(null)}
        />
      )}

      {/* Hero */}
      <div style={{
        background: "var(--primary)",
        backgroundImage: "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
        backgroundSize: "48px 48px",
        padding: "48px",
      }}>
        <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 400, color: "#fff" }}>
          New Hire <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>Integration</em>
        </div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "rgba(255,255,255,.6)", marginTop: "8px" }}>
          Graph integration scores for employees in their first 180 days.
        </div>
      </div>

      <div style={{ padding: "40px 48px", maxWidth: "1100px", margin: "0 auto" }}>
        {isLoading && <div style={{ fontFamily: "var(--fb)", color: "var(--mid)", textAlign: "center", padding: "40px" }}>Loading cohort data…</div>}
        {error && <div style={{ background: "#FDEAEA", borderRadius: "8px", padding: "16px", fontFamily: "var(--fb)", color: "#7A1020" }}>{error.message}</div>}

        {data && (
          <>
            {/* KPI row */}
            <div style={{ display: "flex", gap: "16px", marginBottom: "32px" }}>
              <KPIStat value={data.cohort_size}   label="New Hires" />
              <KPIStat value={data.at_risk_count} label="Below Threshold" />
              <KPIStat value={`${Math.round((data.cohort_median_score || 0) * 100)}%`} label="Cohort Median" />
            </div>

            {/* Scatter chart */}
            <div style={{ background: "#fff", borderRadius: "12px", padding: "28px", boxShadow: "0 1px 4px rgba(0,0,0,.07)", marginBottom: "24px" }}>
              <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "3px", textTransform: "uppercase", color: "var(--gold)", display: "flex", alignItems: "center", gap: "8px", marginBottom: "16px" }}>
                <span style={{ width: "24px", height: "1px", background: "var(--gold)", display: "block" }} />
                Cohort Integration Map
              </div>
              <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", marginBottom: "16px" }}>
                Click any dot to view integration history. <span style={{ color: "#E03448" }}>●</span> = below threshold &nbsp; <span style={{ color: "#003366" }}>●</span> = on track
              </div>
              <ResponsiveContainer width="100%" height={300}>
                <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E0EAF4" />
                  <XAxis
                    dataKey="x" name="Tenure (days)"
                    label={{ value: "Days of Tenure", position: "insideBottom", offset: -4, fontSize: 12, fill: "#6B7280" }}
                    domain={[0, 180]} tick={{ fontSize: 11, fill: "#6B7280" }}
                  />
                  <YAxis
                    dataKey="y" name="Integration Score"
                    label={{ value: "Score", angle: -90, position: "insideLeft", fontSize: 12, fill: "#6B7280" }}
                    domain={[0, 1]} tick={{ fontSize: 11, fill: "#6B7280" }}
                    tickFormatter={v => `${Math.round(v * 100)}%`}
                  />
                  <Tooltip
                    cursor={{ strokeDasharray: "3 3" }}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const d = payload[0]?.payload;
                      if (!d) return null;
                      return (
                        <div style={{ background: "#fff", border: "1px solid #E0EAF4", borderRadius: "8px", padding: "12px 16px", fontFamily: "var(--fb)", fontSize: "13px" }}>
                          <div style={{ fontWeight: 600 }}>{d.name}</div>
                          <div style={{ color: "var(--mid)" }}>{d.department} · Day {d.tenure_days}</div>
                          <div>Score: {Math.round(d.integration_score * 100)}%</div>
                          {d.below_cohort_threshold && <div style={{ color: "#E03448" }}>⚠ Below threshold</div>}
                        </div>
                      );
                    }}
                  />
                  <ReferenceLine y={data.cohort_median_score} stroke="#C8982A" strokeDasharray="6 3"
                    label={{ value: "Cohort median", position: "right", fontSize: 10, fill: "#C8982A" }} />
                  <Scatter
                    data={scatterData}
                    shape={props => <CustomDot {...props} onClick={setSelected} />}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>

            {/* At-risk table */}
            {data.at_risk_count > 0 && (
              <div style={{ background: "#fff", borderRadius: "12px", padding: "28px", boxShadow: "0 1px 4px rgba(0,0,0,.07)" }}>
                <div style={{ fontFamily: "var(--fb)", fontSize: "10px", fontWeight: 500, letterSpacing: "3px", textTransform: "uppercase", color: "var(--gold)", display: "flex", alignItems: "center", gap: "8px", marginBottom: "16px" }}>
                  <span style={{ width: "24px", height: "1px", background: "var(--gold)", display: "block" }} />
                  At-Risk New Hires
                </div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--fb)", fontSize: "13px" }}>
                  <thead>
                    <tr style={{ background: "var(--primary)", color: "#fff" }}>
                      {["Name", "Department", "Day", "Score", "Cross-Dept Edges"].map(h => (
                        <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: "10px", letterSpacing: "2px", textTransform: "uppercase", fontWeight: 400 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.cohort.filter(m => m.below_cohort_threshold).map((m, i) => (
                      <tr
                        key={m.employee_id}
                        style={{ background: i % 2 === 0 ? "#fff" : "var(--primary-10)", cursor: "pointer" }}
                        onClick={() => setSelected(m)}
                      >
                        <td style={{ padding: "10px 14px" }}>{m.name}</td>
                        <td style={{ padding: "10px 14px" }}>{m.department}</td>
                        <td style={{ padding: "10px 14px" }}>{m.tenure_days}</td>
                        <td style={{ padding: "10px 14px" }}>
                          <span style={{ color: "#E03448", fontWeight: 600 }}>{Math.round(m.integration_score * 100)}%</span>
                        </td>
                        <td style={{ padding: "10px 14px" }}>{m.cross_dept_edge_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
