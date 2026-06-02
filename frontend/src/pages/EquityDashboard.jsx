import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ErrorBar, ResponsiveContainer, Cell,
} from "recharts";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const DIMENSIONS = [
  { key: "tenure_band",  label: "Tenure Band" },
  { key: "level_band",   label: "Level Band" },
  { key: "gender_group", label: "Group" },
];

const METRICS = [
  { key: "betweenness", label: "Betweenness Centrality" },
  { key: "degree",      label: "Degree Centrality" },
];

async function fetchDistribution(dimension, metric) {
  const res = await fetch(`${API_BASE}/equity/centrality-distribution?dimension=${dimension}&metric=${metric}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function EquityChart({ data, metric }) {
  if (!data?.groups?.length) return (
    <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)", textAlign: "center", padding: "40px" }}>
      No demographic data available. Import demographics via <code>POST /equity/import-demographics</code> first.
    </div>
  );

  const chartData = data.groups.map(g => ({
    name: g.group_value,
    median: g.median_score != null ? parseFloat((g.median_score * 1000).toFixed(4)) : 0,
    range:  (g.p75_score != null && g.p25_score != null)
      ? [g.median_score * 1000 - g.p25_score * 1000, g.p75_score * 1000 - g.median_score * 1000]
      : [0, 0],
    below:  g.below_org_median,
    count:  g.member_count,
  }));

  const orgMedian = chartData.reduce((s, d) => s + d.median, 0) / Math.max(chartData.length, 1);

  return (
    <div>
      <div style={{ fontFamily: "var(--fb)", fontSize: "12px", color: "var(--mid)", marginBottom: "8px" }}>
        {METRICS.find(m => m.key === metric)?.label} × 1000 — bars show median; whiskers show IQR (p25–p75)
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} margin={{ top: 16, right: 24, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E0EAF4" />
          <XAxis dataKey="name" tick={{ fontFamily: "var(--fb)", fontSize: 12, fill: "#6B7280" }} />
          <YAxis tick={{ fontFamily: "var(--fb)", fontSize: 11, fill: "#6B7280" }} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0]?.payload;
              return (
                <div style={{ background: "#fff", border: "1px solid #E0EAF4", borderRadius: "8px", padding: "10px 14px", fontFamily: "var(--fb)", fontSize: "13px" }}>
                  <div style={{ fontWeight: 600 }}>{d.name}</div>
                  <div>Median: {d.median.toFixed(3)}</div>
                  <div style={{ color: "var(--mid)" }}>n = {d.count}</div>
                  {d.below && <div style={{ color: "#E03448", marginTop: "4px" }}>⚠ Below org median (80% threshold)</div>}
                </div>
              );
            }}
          />
          <ReferenceLine y={orgMedian} stroke="#C8982A" strokeDasharray="5 3" label={{ value: "Org median", position: "right", fontSize: 10, fill: "#C8982A" }} />
          <Bar dataKey="median" radius={[4, 4, 0, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={entry.below ? "#E03448" : "#003366"} fillOpacity={0.85} />
            ))}
            <ErrorBar dataKey="range" width={4} strokeWidth={1.5} stroke="#6B7280" direction="y" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div style={{ display: "flex", gap: "16px", marginTop: "12px", fontFamily: "var(--fb)", fontSize: "12px", color: "var(--mid)" }}>
        <span><span style={{ display: "inline-block", width: "10px", height: "10px", background: "#003366", borderRadius: "2px", marginRight: "6px" }} />On track</span>
        <span><span style={{ display: "inline-block", width: "10px", height: "10px", background: "#E03448", borderRadius: "2px", marginRight: "6px" }} />Below 80% of org median</span>
      </div>

      {/* Below-median groups alert */}
      {data.groups.some(g => g.below_org_median) && (
        <div style={{ marginTop: "16px", background: "#FDEAEA", borderLeft: "3px solid #E03448", borderRadius: "0 8px 8px 0", padding: "12px 16px" }}>
          <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "#7A1020", fontWeight: 600 }}>Investigation trigger</div>
          <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "#7A1020", marginTop: "4px" }}>
            {data.groups.filter(g => g.below_org_median).map(g => g.group_value).join(", ")} — below 80% of the organisational median for {metric}. This is a heuristic flag for qualitative human review of whether structural barriers exist — not evidence of discrimination or a legal determination. A possible response is deliberate cross-functional assignments or bridging-role investments for this group.
          </div>
        </div>
      )}
    </div>
  );
}

export default function EquityDashboard() {
  const [dimension, setDimension] = useState("tenure_band");
  const [metric,    setMetric]    = useState("betweenness");

  const { data, isLoading, error } = useQuery({
    queryKey: ["equityDist", dimension, metric],
    queryFn: () => fetchDistribution(dimension, metric),
    staleTime: 600_000,
  });

  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>
      {/* Hero */}
      <div style={{
        background: "var(--primary)",
        backgroundImage: "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
        backgroundSize: "48px 48px", padding: "48px",
      }}>
        <div style={{ fontFamily: "var(--fd)", fontSize: "32px", fontWeight: 400, color: "#fff" }}>
          Structural <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>Equity</em>
        </div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "rgba(255,255,255,.6)", marginTop: "8px" }}>
          Network centrality distributions by demographic group — aggregate view only.
        </div>
      </div>

      <div style={{ padding: "40px 48px", maxWidth: "1100px", margin: "0 auto" }}>

        {/* Privacy notice */}
        <div style={{
          background: "#E0EAF4", borderRadius: "8px", padding: "14px 18px",
          fontFamily: "var(--fb)", fontSize: "13px", color: "var(--primary)",
          marginBottom: "24px",
        }}>
          <strong>Privacy:</strong> All data shown is aggregated by group (minimum 5 members per group). No individual demographic attributes are accessible through this interface.
        </div>

        {/* Controls */}
        <div style={{ display: "flex", gap: "12px", marginBottom: "24px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: "6px" }}>
            {DIMENSIONS.map(d => (
              <button key={d.key} onClick={() => setDimension(d.key)} style={{
                padding: "7px 16px", borderRadius: "20px", border: "1px solid var(--primary-30)",
                fontFamily: "var(--fb)", fontSize: "13px", cursor: "pointer",
                background: dimension === d.key ? "var(--primary)" : "#fff",
                color: dimension === d.key ? "#fff" : "var(--primary)",
              }}>
                {d.label}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: "6px" }}>
            {METRICS.map(m => (
              <button key={m.key} onClick={() => setMetric(m.key)} style={{
                padding: "7px 16px", borderRadius: "20px", border: "1px solid #7C4DBD",
                fontFamily: "var(--fb)", fontSize: "13px", cursor: "pointer",
                background: metric === m.key ? "#7C4DBD" : "#fff",
                color: metric === m.key ? "#fff" : "#7C4DBD",
              }}>
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {/* Chart */}
        <div style={{ background: "#fff", borderRadius: "12px", padding: "28px", boxShadow: "0 1px 4px rgba(0,0,0,.07)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "20px" }}>
            <span style={{ width: "24px", height: "1px", background: "var(--gold)", display: "block" }} />
            <span style={{ fontFamily: "var(--fb)", fontSize: "9px", fontWeight: 500, letterSpacing: "4px", textTransform: "uppercase", color: "var(--gold)" }}>
              Centrality Distribution
            </span>
          </div>

          {isLoading && <div style={{ fontFamily: "var(--fb)", color: "var(--mid)", textAlign: "center", padding: "40px" }}>Loading…</div>}
          {error   && <div style={{ fontFamily: "var(--fb)", color: "#E03448", padding: "16px", background: "#FDEAEA", borderRadius: "8px" }}>{error.message}</div>}
          {!isLoading && !error && <EquityChart data={data} metric={metric} />}
        </div>

        {/* Import instructions */}
        <div style={{ background: "#fff", borderRadius: "12px", padding: "24px", boxShadow: "0 1px 4px rgba(0,0,0,.07)", marginTop: "24px" }}>
          <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 500, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "12px" }}>Import Demographics</div>
          <div style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--dark)", lineHeight: 1.7 }}>
            Import anonymised group labels via <code style={{ fontFamily: "Courier New", background: "#F4F6F9", padding: "2px 6px", borderRadius: "4px" }}>POST /equity/import-demographics</code>.
            Valid <code>tenure_band</code> values: <code>0-1y</code>, <code>1-3y</code>, <code>3-5y</code>, <code>5y+</code>.
            Valid <code>level_band</code> values: <code>ic</code>, <code>senior_ic</code>, <code>manager</code>, <code>director_plus</code>.
            All employees must have <code>consent=TRUE</code> to be included.
          </div>
        </div>
      </div>
    </div>
  );
}
