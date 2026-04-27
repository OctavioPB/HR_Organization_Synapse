import { Link } from "react-router-dom";

function spofBadgeClass(flag) {
  if (flag === "critical") return "badge badge-critical";
  if (flag === "warning")  return "badge badge-warning";
  return "badge badge-normal";
}

function trendArrow(trend) {
  if (trend === null || trend === undefined) return null;
  if (trend > 0.02)  return { symbol: "↑", color: "#E03448" };
  if (trend < -0.02) return { symbol: "↓", color: "#27B97C" };
  return { symbol: "→", color: "var(--mid)" };
}

function ScoreBar({ score }) {
  const pct = Math.round(score * 100);
  const fill = score > 0.75 ? "#E03448" : score > 0.5 ? "#F07020" : score > 0.25 ? "#336699" : "#27B97C";
  return (
    <div
      style={{
        background: "var(--light)",
        borderRadius: "4px",
        height: "8px",
        width: "100%",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          background: fill,
          borderRadius: "4px",
          position: "relative",
          transition: "width 600ms ease",
        }}
      />
    </div>
  );
}

export default function CriticalNodePanel({ scores = [], loading = false }) {
  return (
    <div
      style={{
        background: "var(--white)",
        borderRadius: "12px",
        boxShadow: "0 1px 4px rgba(0,0,0,.08)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <div className="card-accent" />
      <div style={{ padding: "24px 24px 16px" }}>
        <div className="eyebrow">Critical nodes</div>
        <h3
          style={{
            fontFamily: "var(--fd)",
            fontSize: "18px",
            fontWeight: 400,
            color: "var(--dark)",
            margin: "4px 0 0",
          }}
        >
          Top SPOF employees
        </h3>
        <p
          style={{
            fontFamily: "var(--fb)",
            fontSize: "12px",
            color: "var(--mid)",
            margin: "4px 0 0",
          }}
        >
          People whose absence would most disrupt collaboration. High-scorers bridge multiple teams with few peers who could absorb their role.
        </p>
        <p
          style={{
            fontFamily: "var(--fb)",
            fontSize: "11px",
            color: "var(--mid)",
            margin: "6px 0 0",
            opacity: 0.75,
          }}
        >
          Score bar: <span style={{ color: "#27B97C" }}>■</span> low · <span style={{ color: "#336699" }}>■</span> moderate · <span style={{ color: "#F07020" }}>■</span> elevated · <span style={{ color: "#E03448" }}>■</span> critical &nbsp;·&nbsp; Trend: ↑ risk rising · ↓ improving · → stable
        </p>
      </div>

      <div className="section-divider" />

      {/* Table header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto auto",
          gap: "8px",
          padding: "8px 24px",
          background: "var(--primary)",
        }}
      >
        {["Employee", "Score", "Trend"].map((h) => (
          <span
            key={h}
            style={{
              fontFamily: "var(--fb)",
              fontSize: "10px",
              letterSpacing: "2px",
              textTransform: "uppercase",
              color: "rgba(255,255,255,.8)",
            }}
          >
            {h}
          </span>
        ))}
      </div>

      <div style={{ overflowY: "auto", flex: 1 }}>
        {loading && (
          <div
            style={{
              padding: "40px 24px",
              textAlign: "center",
              color: "var(--mid)",
              fontFamily: "var(--fb)",
              fontSize: "13px",
            }}
          >
            Loading…
          </div>
        )}

        {!loading && scores.length === 0 && (
          <div
            style={{
              padding: "40px 24px",
              textAlign: "center",
              color: "var(--mid)",
              fontFamily: "var(--fb)",
              fontSize: "13px",
            }}
          >
            No risk scores available.
          </div>
        )}

        {!loading &&
          scores.map((s, idx) => {
            const arrow = trendArrow(s.entropy_trend);
            return (
              <div
                key={s.employee_id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto auto",
                  gap: "8px",
                  alignItems: "center",
                  padding: "12px 24px",
                  background: idx % 2 === 0 ? "var(--white)" : "var(--primary-10)",
                  borderBottom: "1px solid var(--primary-10)",
                }}
              >
                {/* Employee */}
                <div>
                  <Link
                    to={`/employee/${s.employee_id}`}
                    style={{
                      fontFamily: "var(--fb)",
                      fontSize: "13px",
                      fontWeight: 600,
                      color: "var(--primary)",
                      textDecoration: "none",
                    }}
                  >
                    {s.name}
                  </Link>
                  <div
                    style={{
                      fontFamily: "var(--fb)",
                      fontSize: "11px",
                      color: "var(--mid)",
                      marginTop: "2px",
                    }}
                  >
                    {s.department}
                  </div>
                  <div style={{ marginTop: "6px" }}>
                    <ScoreBar score={s.spof_score} />
                  </div>
                </div>

                {/* Score + badge */}
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-end",
                    gap: "4px",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--fd)",
                      fontSize: "16px",
                      fontWeight: 300,
                      color: "var(--dark)",
                    }}
                  >
                    {(s.spof_score * 100).toFixed(0)}
                  </span>
                  <span className={spofBadgeClass(s.flag)}>{s.flag}</span>
                </div>

                {/* Trend arrow */}
                <div style={{ textAlign: "center", minWidth: "24px" }}>
                  {arrow && (
                    <span
                      style={{
                        fontSize: "16px",
                        color: arrow.color,
                        fontWeight: 600,
                      }}
                    >
                      {arrow.symbol}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
      </div>
    </div>
  );
}
