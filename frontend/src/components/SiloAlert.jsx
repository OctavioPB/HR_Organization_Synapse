import { useState } from "react";
import { Link } from "react-router-dom";
import { format } from "date-fns";

function SeverityBadge({ severity }) {
  const cls =
    severity === "high" || severity === "critical"
      ? "badge badge-critical"
      : severity === "medium"
      ? "badge badge-warning"
      : "badge badge-info";
  return <span className={cls}>{severity}</span>;
}

function AlertRow({ alert, isOpen, onToggle }) {
  const firedAt = alert.fired_at ? format(new Date(alert.fired_at), "MMM d, yyyy HH:mm") : "—";
  const entities = alert.affected_entities ?? {};

  return (
    <div
      style={{
        borderBottom: "1px solid var(--primary-10)",
        overflow: "hidden",
      }}
    >
      {/* Header row */}
      <button
        onClick={onToggle}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "16px",
          padding: "14px 24px",
          background: isOpen ? "var(--primary-10)" : "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          transition: "background 150ms",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1 }}>
          <SeverityBadge severity={alert.severity} />
          <span
            style={{
              fontFamily: "var(--fb)",
              fontSize: "13px",
              fontWeight: 600,
              color: "var(--dark)",
            }}
          >
            {alert.type === "silo" ? "Silo Detected" : alert.type.replace(/_/g, " ")}
          </span>
          {(entities.departments?.length > 0 || entities.member_count !== undefined) && (
            <span
              style={{
                fontFamily: "var(--fb)",
                fontSize: "11px",
                color: "var(--mid)",
              }}
            >
              {entities.departments?.length > 0
                ? `${entities.departments[0]} Dept.`
                : `Community ${entities.community_id}`}
              {entities.member_count !== undefined && ` · ${entities.member_count} members`}
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <span
            style={{
              fontFamily: "var(--fb)",
              fontSize: "11px",
              color: "var(--mid)",
              whiteSpace: "nowrap",
            }}
          >
            {firedAt}
          </span>
          <span
            style={{
              color: "var(--primary-60)",
              fontSize: "12px",
              transform: isOpen ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 200ms",
              display: "inline-block",
            }}
          >
            ▾
          </span>
        </div>
      </button>

      {/* Expanded details */}
      {isOpen && (
        <div
          style={{
            padding: "0 24px 16px 24px",
            background: "var(--primary-10)",
          }}
        >
          {alert.details && (
            <p
              style={{
                fontFamily: "var(--fb)",
                fontSize: "13px",
                color: "var(--dark)",
                margin: "0 0 8px",
              }}
            >
              {alert.details}
            </p>
          )}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "12px" }}>
            <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center" }}>
              <span
                style={{
                  fontFamily: "var(--fb)",
                  fontSize: "11px",
                  color: "var(--mid)",
                }}
              >
                Status:{" "}
                <strong style={{ color: alert.resolved ? "#27B97C" : "#E03448" }}>
                  {alert.resolved ? "Resolved" : "Active"}
                </strong>
              </span>
              {alert.resolved_at && (
                <span
                  style={{
                    fontFamily: "var(--fb)",
                    fontSize: "11px",
                    color: "var(--mid)",
                  }}
                >
                  Resolved: {format(new Date(alert.resolved_at), "MMM d, yyyy HH:mm")}
                </span>
              )}
            </div>
            {!alert.resolved && (
              <Link
                to={`/silo/${alert.id}`}
                style={{
                  fontFamily: "var(--fb)",
                  fontSize: "12px",
                  fontWeight: 600,
                  color: "var(--white)",
                  background: "var(--primary)",
                  border: "none",
                  borderRadius: "7px",
                  padding: "7px 14px",
                  textDecoration: "none",
                  whiteSpace: "nowrap",
                }}
              >
                View affected employees →
              </Link>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function SiloAlert({ alerts = [], loading = false }) {
  const [openId, setOpenId] = useState(null);

  const toggle = (id) => setOpenId((prev) => (prev === id ? null : id));

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

      <div style={{ padding: "24px 24px 16px" }}>
        <div className="eyebrow">Alert registry</div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginTop: "4px",
          }}
        >
          <h3
            style={{
              fontFamily: "var(--fd)",
              fontSize: "18px",
              fontWeight: 400,
              color: "var(--dark)",
              margin: 0,
            }}
          >
            Active silo alerts
          </h3>
          {!loading && (
            <span
              style={{
                fontFamily: "var(--fb)",
                fontSize: "11px",
                color: "var(--mid)",
              }}
            >
              {alerts.length} alert{alerts.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
        <p
          style={{
            fontFamily: "var(--fb)",
            fontSize: "12px",
            color: "var(--mid)",
            margin: "4px 0 0",
          }}
        >
          Teams where cross-department communication has dropped below a healthy baseline. Siloed groups are slower to respond to change and more exposed when key connectors leave. Click an alert to see affected employees.
        </p>
      </div>

      <div className="section-divider" />

      {loading && (
        <div
          style={{
            padding: "32px 24px",
            textAlign: "center",
            color: "var(--mid)",
            fontFamily: "var(--fb)",
            fontSize: "13px",
          }}
        >
          Loading alerts…
        </div>
      )}

      {!loading && alerts.length === 0 && (
        <div
          style={{
            padding: "32px 24px",
            display: "flex",
            alignItems: "center",
            gap: "12px",
          }}
        >
          <span style={{ fontSize: "20px" }}>✓</span>
          <span
            style={{
              fontFamily: "var(--fb)",
              fontSize: "13px",
              color: "#0D5C3A",
            }}
          >
            No active silo alerts. Collaboration patterns are healthy.
          </span>
        </div>
      )}

      {!loading &&
        alerts.map((alert) => (
          <AlertRow
            key={alert.id}
            alert={alert}
            isOpen={openId === alert.id}
            onToggle={() => toggle(alert.id)}
          />
        ))}
    </div>
  );
}
