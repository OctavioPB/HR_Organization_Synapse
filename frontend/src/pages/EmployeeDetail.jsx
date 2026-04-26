import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { format } from "date-fns";
import {
  fetchEgoNetwork,
  fetchEmployeeRiskHistory,
  simulateRemoval,
} from "../lib/api.js";
import OrgGraph from "../components/OrgGraph.jsx";

function StatPill({ label, value, unit = "" }) {
  return (
    <div
      style={{
        background: "var(--primary-10)",
        borderRadius: "8px",
        padding: "12px 16px",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
      }}
    >
      <span
        style={{
          fontFamily: "var(--fd)",
          fontSize: "22px",
          fontWeight: 300,
          color: "var(--dark)",
        }}
      >
        {value !== undefined && value !== null
          ? typeof value === "number"
            ? value.toFixed(3)
            : value
          : "—"}
        {unit && (
          <span style={{ fontSize: "12px", color: "var(--mid)", marginLeft: "2px" }}>
            {unit}
          </span>
        )}
      </span>
      <span
        style={{
          fontFamily: "var(--fb)",
          fontSize: "10px",
          letterSpacing: "2px",
          textTransform: "uppercase",
          color: "var(--mid)",
        }}
      >
        {label}
      </span>
    </div>
  );
}

function SpofBadge({ flag }) {
  const cls =
    flag === "critical"
      ? "badge badge-critical"
      : flag === "warning"
      ? "badge badge-warning"
      : "badge badge-normal";
  return <span className={cls}>{flag ?? "normal"}</span>;
}

function SimulatePanel({ employeeId }) {
  const mutation = useMutation({ mutationFn: () => simulateRemoval(employeeId) });

  const { data, isPending, isError } = mutation;

  return (
    <div
      style={{
        background: "var(--white)",
        borderRadius: "12px",
        boxShadow: "0 1px 4px rgba(0,0,0,.08)",
        overflow: "hidden",
      }}
    >
      <div className="card-accent" />
      <div style={{ padding: "24px" }}>
        <div className="eyebrow">What-if simulation</div>
        <h3
          style={{
            fontFamily: "var(--fd)",
            fontSize: "18px",
            fontWeight: 400,
            color: "var(--dark)",
            margin: "4px 0 8px",
          }}
        >
          Impact of removal
        </h3>
        <p
          style={{
            fontFamily: "var(--fb)",
            fontSize: "12px",
            color: "var(--mid)",
            margin: "0 0 16px",
          }}
        >
          Recalculates graph health after removing this employee from the network.
        </p>

        <button
          onClick={() => mutation.mutate()}
          disabled={isPending}
          style={{
            background: isPending ? "var(--mid)" : "var(--primary)",
            color: "var(--white)",
            border: "none",
            borderRadius: "8px",
            padding: "10px 20px",
            fontFamily: "var(--fb)",
            fontSize: "13px",
            fontWeight: 600,
            cursor: isPending ? "not-allowed" : "pointer",
            letterSpacing: "0.5px",
          }}
        >
          {isPending ? "Simulating…" : "Run simulation →"}
        </button>

        {isError && (
          <p
            style={{
              fontFamily: "var(--fb)",
              fontSize: "13px",
              color: "#E03448",
              marginTop: "12px",
            }}
          >
            Simulation failed — employee may not be in the active graph.
          </p>
        )}

        {data && (
          <div style={{ marginTop: "20px" }}>
            <hr className="section-divider" style={{ marginBottom: "16px" }} />

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "12px",
                marginBottom: "16px",
              }}
            >
              <div>
                <div
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "10px",
                    letterSpacing: "2px",
                    textTransform: "uppercase",
                    color: "var(--mid)",
                    marginBottom: "8px",
                  }}
                >
                  Before
                </div>
                <StatPill label="Nodes" value={data.before.node_count} />
                <div style={{ marginTop: "8px" }}>
                  <StatPill
                    label="Avg betweenness"
                    value={data.before.avg_betweenness}
                  />
                </div>
                <div style={{ marginTop: "8px" }}>
                  <StatPill
                    label="Components"
                    value={data.before.weakly_connected_components}
                  />
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "10px",
                    letterSpacing: "2px",
                    textTransform: "uppercase",
                    color: "var(--mid)",
                    marginBottom: "8px",
                  }}
                >
                  After
                </div>
                <StatPill label="Nodes" value={data.after.node_count} />
                <div style={{ marginTop: "8px" }}>
                  <StatPill
                    label="Avg betweenness"
                    value={data.after.avg_betweenness}
                  />
                </div>
                <div style={{ marginTop: "8px" }}>
                  <StatPill
                    label="Components"
                    value={data.after.weakly_connected_components}
                  />
                </div>
              </div>
            </div>

            {/* Impact summary */}
            <div
              style={{
                background: "var(--primary-10)",
                borderRadius: "8px",
                padding: "14px 16px",
                borderLeft: "3px solid var(--gold)",
              }}
            >
              <div
                style={{
                  fontFamily: "var(--fb)",
                  fontSize: "11px",
                  letterSpacing: "2px",
                  textTransform: "uppercase",
                  color: "var(--mid)",
                  marginBottom: "8px",
                }}
              >
                Impact
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <span
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "13px",
                    color: "var(--dark)",
                  }}
                >
                  Components delta:{" "}
                  <strong
                    style={{
                      color:
                        data.impact.components_delta > 0
                          ? "#E03448"
                          : data.impact.components_delta < 0
                          ? "#27B97C"
                          : "var(--mid)",
                    }}
                  >
                    {data.impact.components_delta > 0 ? "+" : ""}
                    {data.impact.components_delta}
                  </strong>
                </span>
                <span
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "13px",
                    color: "var(--dark)",
                  }}
                >
                  Betweenness shift:{" "}
                  <strong>
                    {data.impact.betweenness_avg_delta >= 0 ? "+" : ""}
                    {data.impact.betweenness_avg_delta.toFixed(6)}
                  </strong>
                </span>
                <span
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "13px",
                    color: "var(--dark)",
                  }}
                >
                  Edges removed:{" "}
                  <strong>{data.impact.node_removed_degree}</strong>
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function EmployeeDetail() {
  const { id } = useParams();
  const [historyDays, setHistoryDays] = useState(30);

  const { data: egoData, isLoading: loadingEgo } = useQuery({
    queryKey: ["ego-network", id],
    queryFn: () => fetchEgoNetwork(id),
    enabled: !!id,
  });

  const { data: historyData, isLoading: loadingHistory } = useQuery({
    queryKey: ["risk-history", id, historyDays],
    queryFn: () => fetchEmployeeRiskHistory(id, historyDays),
    enabled: !!id,
  });

  const employee = egoData?.node;
  const neighbors = egoData?.neighbors ?? [];
  const egoEdges = egoData?.edges ?? [];

  // Build mini ego-graph nodes/edges for Sigma
  const egoNodes = employee
    ? [employee, ...neighbors].map((n) => ({ ...n, spof_score: n.betweenness ?? 0 }))
    : [];

  const chartData = (historyData?.history ?? []).map((p) => ({
    date: format(new Date(p.scored_at), "MMM d"),
    score: +(p.spof_score * 100).toFixed(1),
    flag: p.flag,
  }));

  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>
      {/* Hero */}
      <div
        style={{
          background: "var(--primary)",
          backgroundImage:
            "linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px)," +
            "linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          padding: "48px 48px 40px",
        }}
      >
        <Link
          to="/"
          style={{
            fontFamily: "var(--fb)",
            fontSize: "12px",
            color: "rgba(255,255,255,.5)",
            textDecoration: "none",
            letterSpacing: "1px",
          }}
        >
          ← Dashboard
        </Link>

        <div style={{ marginTop: "16px" }}>
          {loadingEgo ? (
            <div
              style={{
                fontFamily: "var(--fd)",
                fontSize: "32px",
                fontWeight: 300,
                color: "rgba(255,255,255,.4)",
              }}
            >
              Loading…
            </div>
          ) : (
            <>
              <h1
                style={{
                  fontFamily: "var(--fd)",
                  fontSize: "36px",
                  fontWeight: 300,
                  color: "var(--white)",
                  margin: 0,
                  lineHeight: 1.2,
                }}
              >
                <em style={{ fontStyle: "italic", color: "var(--gold-light)" }}>
                  {employee?.name ?? "Employee"}
                </em>
              </h1>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  marginTop: "10px",
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "13px",
                    color: "rgba(255,255,255,.6)",
                  }}
                >
                  {employee?.department}
                </span>
                {employee && (
                  <SpofBadge flag={employee.spof_flag ?? employee.flag ?? "normal"} />
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div style={{ padding: "32px 48px 64px" }}>
        {/* Metrics row */}
        {employee && (
          <div style={{ display: "flex", gap: "16px", marginBottom: "32px" }}>
            <StatPill label="Betweenness" value={employee.betweenness} />
            <StatPill label="Degree in" value={employee.degree_in} />
            <StatPill label="Degree out" value={employee.degree_out} />
            <StatPill label="Clustering" value={employee.clustering} />
            <StatPill label="Neighbors" value={neighbors.length} unit="" />
          </div>
        )}

        {/* Main grid: ego graph + panels */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 340px",
            gap: "24px",
            alignItems: "start",
          }}
        >
          {/* Left column */}
          <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
            {/* Ego network */}
            <div
              style={{
                background: "var(--white)",
                borderRadius: "12px",
                boxShadow: "0 1px 4px rgba(0,0,0,.08)",
                overflow: "hidden",
                height: "400px",
              }}
            >
              <div className="card-accent" />
              <div style={{ padding: "16px 20px 8px" }}>
                <div className="eyebrow">Ego network</div>
                <p
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "12px",
                    color: "var(--mid)",
                    margin: "2px 0 0",
                  }}
                >
                  Direct collaboration network · {neighbors.length} neighbors
                </p>
              </div>
              <div style={{ height: "calc(100% - 72px)" }}>
                <OrgGraph nodes={egoNodes} edges={egoEdges} />
              </div>
            </div>

            {/* SPOF trend chart */}
            <div
              style={{
                background: "var(--white)",
                borderRadius: "12px",
                boxShadow: "0 1px 4px rgba(0,0,0,.08)",
                overflow: "hidden",
              }}
            >
              <div className="card-accent" />
              <div
                style={{
                  padding: "20px 24px 12px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div className="eyebrow">Risk trend</div>
                  <p
                    style={{
                      fontFamily: "var(--fb)",
                      fontSize: "12px",
                      color: "var(--mid)",
                      margin: "2px 0 0",
                    }}
                  >
                    SPOF score over time
                  </p>
                </div>
                <select
                  value={historyDays}
                  onChange={(e) => setHistoryDays(Number(e.target.value))}
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "12px",
                    border: "1px solid var(--primary-10)",
                    borderRadius: "6px",
                    padding: "4px 8px",
                    color: "var(--dark)",
                    background: "var(--white)",
                    cursor: "pointer",
                  }}
                >
                  <option value={7}>7 days</option>
                  <option value={30}>30 days</option>
                  <option value={90}>90 days</option>
                </select>
              </div>

              <div style={{ padding: "0 16px 20px" }}>
                {loadingHistory ? (
                  <div
                    style={{
                      height: "200px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "var(--mid)",
                      fontFamily: "var(--fb)",
                      fontSize: "13px",
                    }}
                  >
                    Loading…
                  </div>
                ) : chartData.length < 2 ? (
                  <div
                    style={{
                      height: "200px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "var(--mid)",
                      fontFamily: "var(--fb)",
                      fontSize: "13px",
                    }}
                  >
                    Insufficient history for this window.
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--primary-10)" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontFamily: "var(--fb)", fontSize: 11, fill: "var(--mid)" }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        domain={[0, 100]}
                        tick={{ fontFamily: "var(--fb)", fontSize: 11, fill: "var(--mid)" }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        contentStyle={{
                          fontFamily: "var(--fb)",
                          fontSize: "12px",
                          borderRadius: "8px",
                          border: "1px solid var(--primary-10)",
                        }}
                        formatter={(v) => [`${v}`, "SPOF Score"]}
                      />
                      <ReferenceLine y={75} stroke="#E03448" strokeDasharray="4 4" />
                      <ReferenceLine y={50} stroke="#F07020" strokeDasharray="4 4" />
                      <Line
                        type="monotone"
                        dataKey="score"
                        stroke="#003366"
                        strokeWidth={2}
                        dot={{ r: 3, fill: "#003366" }}
                        activeDot={{ r: 5 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          </div>

          {/* Right column */}
          <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
            {/* Neighbor list */}
            <div
              style={{
                background: "var(--white)",
                borderRadius: "12px",
                boxShadow: "0 1px 4px rgba(0,0,0,.08)",
                overflow: "hidden",
              }}
            >
              <div className="card-accent" />
              <div style={{ padding: "20px 20px 12px" }}>
                <div className="eyebrow">Direct connections</div>
              </div>
              <div style={{ maxHeight: "300px", overflowY: "auto" }}>
                {neighbors.length === 0 && (
                  <div
                    style={{
                      padding: "20px",
                      textAlign: "center",
                      color: "var(--mid)",
                      fontFamily: "var(--fb)",
                      fontSize: "13px",
                    }}
                  >
                    No direct collaborators found.
                  </div>
                )}
                {neighbors.map((n, idx) => (
                  <div
                    key={n.employee_id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "10px 20px",
                      background: idx % 2 === 0 ? "var(--white)" : "var(--primary-10)",
                      borderBottom: "1px solid var(--primary-10)",
                    }}
                  >
                    <div>
                      <Link
                        to={`/employee/${n.employee_id}`}
                        style={{
                          fontFamily: "var(--fb)",
                          fontSize: "13px",
                          fontWeight: 600,
                          color: "var(--primary)",
                          textDecoration: "none",
                        }}
                      >
                        {n.name}
                      </Link>
                      <div
                        style={{
                          fontFamily: "var(--fb)",
                          fontSize: "11px",
                          color: "var(--mid)",
                        }}
                      >
                        {n.department}
                      </div>
                    </div>
                    <span
                      style={{
                        fontFamily: "var(--fd)",
                        fontSize: "13px",
                        fontWeight: 300,
                        color: "var(--dark)",
                      }}
                    >
                      {(n.betweenness ?? 0).toFixed(3)}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* What-If simulation */}
            <SimulatePanel employeeId={id} />
          </div>
        </div>
      </div>
    </div>
  );
}
