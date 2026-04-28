import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchSiloMembers } from "../lib/api.js";

function spofColor(score) {
  if (score > 0.75) return "#E03448";
  if (score > 0.5)  return "#F07020";
  if (score > 0.25) return "#336699";
  return "#27B97C";
}

function spofLabel(score) {
  if (score > 0.75) return "Critical";
  if (score > 0.5)  return "High";
  if (score > 0.25) return "Moderate";
  return "Low";
}

function ScoreDot({ score }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
      }}
    >
      <span
        style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: spofColor(score),
          flexShrink: 0,
        }}
      />
      <span style={{ fontFamily: "var(--fb)", fontSize: "12px", color: "var(--dark)" }}>
        {score.toFixed(3)}
      </span>
      <span style={{ fontFamily: "var(--fb)", fontSize: "10px", color: "var(--mid)" }}>
        ({spofLabel(score)})
      </span>
    </span>
  );
}

const SORT_KEYS = ["name", "role", "spof_score", "betweenness", "degree_out"];

export default function SiloDetail() {
  const { alertId } = useParams();
  const [sortKey, setSortKey] = useState("spof_score");
  const [sortAsc, setSortAsc] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["silo-members", alertId],
    queryFn: () => fetchSiloMembers(alertId),
    staleTime: 60_000,
  });

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortAsc((a) => !a);
    } else {
      setSortKey(key);
      setSortAsc(key === "name" || key === "role");
    }
  };

  const sorted = data
    ? [...data.members].sort((a, b) => {
        const av = a[sortKey];
        const bv = b[sortKey];
        if (typeof av === "string") {
          return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
        }
        return sortAsc ? av - bv : bv - av;
      })
    : [];

  const colHeader = (key, label) => (
    <th
      key={key}
      onClick={() => handleSort(key)}
      style={{
        padding: "10px 16px",
        fontFamily: "var(--fb)",
        fontSize: "10px",
        letterSpacing: "2px",
        textTransform: "uppercase",
        color: sortKey === key ? "var(--gold-light)" : "rgba(255,255,255,.7)",
        textAlign: key === "name" || key === "role" ? "left" : "right",
        cursor: "pointer",
        whiteSpace: "nowrap",
        userSelect: "none",
        borderBottom: "1px solid var(--primary-10)",
      }}
    >
      {label} {sortKey === key ? (sortAsc ? "↑" : "↓") : ""}
    </th>
  );

  return (
    <div
      style={{
        maxWidth: "960px",
        margin: "0 auto",
        padding: "32px 24px",
        fontFamily: "var(--fb)",
      }}
    >
      {/* Back link */}
      <Link
        to="/"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          fontFamily: "var(--fb)",
          fontSize: "12px",
          color: "var(--primary)",
          textDecoration: "none",
          marginBottom: "24px",
        }}
      >
        ← Back to Dashboard
      </Link>

      {/* Header card */}
      <div
        style={{
          background: "var(--white)",
          borderRadius: "12px",
          boxShadow: "0 1px 4px rgba(0,0,0,.08)",
          overflow: "hidden",
          marginBottom: "24px",
        }}
      >
        <div className="card-accent" />
        <div style={{ padding: "24px" }}>
          {isLoading ? (
            <div style={{ color: "var(--mid)", fontSize: "13px" }}>Loading…</div>
          ) : isError ? (
            <div style={{ color: "#E03448", fontSize: "13px" }}>Failed to load alert.</div>
          ) : (
            <>
              <div className="eyebrow">Silo alert</div>
              <h2
                style={{
                  fontFamily: "var(--fd)",
                  fontSize: "26px",
                  fontWeight: 300,
                  color: "var(--dark)",
                  margin: "4px 0 8px",
                }}
              >
                {data.department} Department
              </h2>
              <div
                style={{
                  display: "flex",
                  gap: "24px",
                  flexWrap: "wrap",
                  marginTop: "12px",
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
                      marginBottom: "2px",
                    }}
                  >
                    Isolation ratio
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--fd)",
                      fontSize: "28px",
                      fontWeight: 300,
                      color: "#E03448",
                    }}
                  >
                    {data.isolation_ratio.toFixed(2)}×
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
                      marginBottom: "2px",
                    }}
                  >
                    Members
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--fd)",
                      fontSize: "28px",
                      fontWeight: 300,
                      color: "var(--dark)",
                    }}
                  >
                    {data.member_count}
                  </div>
                </div>
              </div>
              <p
                style={{
                  fontFamily: "var(--fb)",
                  fontSize: "12px",
                  color: "var(--mid)",
                  margin: "12px 0 0",
                  maxWidth: "560px",
                  lineHeight: 1.5,
                }}
              >
                This department sends {data.isolation_ratio.toFixed(1)}× more messages internally
                than externally. Low cross-department communication increases knowledge silos
                and amplifies the impact when key connectors leave.
              </p>
            </>
          )}
        </div>
      </div>

      {/* Members table */}
      <div
        style={{
          background: "var(--white)",
          borderRadius: "12px",
          boxShadow: "0 1px 4px rgba(0,0,0,.08)",
          overflow: "hidden",
        }}
      >
        <div style={{ padding: "20px 24px 0" }}>
          <div className="eyebrow">Affected employees</div>
          <h3
            style={{
              fontFamily: "var(--fd)",
              fontSize: "18px",
              fontWeight: 400,
              color: "var(--dark)",
              margin: "4px 0 16px",
            }}
          >
            {sorted.length} employees in this silo
          </h3>
        </div>

        <div className="section-divider" />

        {isLoading ? (
          <div
            style={{
              padding: "32px 24px",
              textAlign: "center",
              color: "var(--mid)",
              fontSize: "13px",
            }}
          >
            Loading members…
          </div>
        ) : isError ? (
          <div
            style={{
              padding: "32px 24px",
              textAlign: "center",
              color: "#E03448",
              fontSize: "13px",
            }}
          >
            Could not load member list.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "var(--primary)" }}>
                  {colHeader("name", "Name")}
                  {colHeader("role", "Role")}
                  {colHeader("spof_score", "SPOF Score")}
                  {colHeader("betweenness", "Betweenness")}
                  {colHeader("degree_out", "Out-Degree")}
                  <th
                    style={{
                      padding: "10px 16px",
                      borderBottom: "1px solid var(--primary-10)",
                    }}
                  />
                </tr>
              </thead>
              <tbody>
                {sorted.map((m, i) => (
                  <tr
                    key={m.employee_id}
                    style={{
                      borderBottom: "1px solid var(--primary-10)",
                      background: i % 2 === 0 ? "var(--white)" : "var(--light)",
                    }}
                  >
                    <td
                      style={{
                        padding: "12px 16px",
                        fontFamily: "var(--fb)",
                        fontSize: "13px",
                        fontWeight: 600,
                        color: "var(--dark)",
                      }}
                    >
                      {m.name}
                    </td>
                    <td
                      style={{
                        padding: "12px 16px",
                        fontFamily: "var(--fb)",
                        fontSize: "12px",
                        color: "var(--mid)",
                      }}
                    >
                      {m.role}
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>
                      <ScoreDot score={m.spof_score} />
                    </td>
                    <td
                      style={{
                        padding: "12px 16px",
                        textAlign: "right",
                        fontFamily: "var(--fb)",
                        fontSize: "12px",
                        color: "var(--dark)",
                      }}
                    >
                      {m.betweenness.toFixed(4)}
                    </td>
                    <td
                      style={{
                        padding: "12px 16px",
                        textAlign: "right",
                        fontFamily: "var(--fb)",
                        fontSize: "12px",
                        color: "var(--dark)",
                      }}
                    >
                      {m.degree_out.toFixed(3)}
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>
                      <Link
                        to={`/employee/${m.employee_id}`}
                        style={{
                          fontFamily: "var(--fb)",
                          fontSize: "11px",
                          color: "var(--primary)",
                          textDecoration: "none",
                          padding: "4px 10px",
                          border: "1px solid var(--primary-10)",
                          borderRadius: "6px",
                          whiteSpace: "nowrap",
                        }}
                      >
                        View profile →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
