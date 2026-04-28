/**
 * AlertToast — real-time WebSocket alert notification panel.
 *
 * Shows a floating badge with the count of new (unread) alerts,
 * expands on click to list the most recent ones.
 * Integrates with useAlertSocket to receive push notifications.
 *
 * Usage (in Dashboard or App):
 *   import { AlertToast } from "./components/AlertToast";
 *   <AlertToast />
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { format } from "date-fns";
import { useAlertSocket } from "../hooks/useAlertSocket";

// ─── Severity helpers — aligned to BRAND.md badge palette ─────────────────────

const SEVERITY_STYLES = {
  critical: { border: "#E03448", bg: "#FDEAEA", badge: "#7A1020" },
  high:     { border: "#F07020", bg: "#FEF0E6", badge: "#7A3800" },
  medium:   { border: "#C8982A", bg: "#FDF5E0", badge: "#7A5800" },
  low:      { border: "#003366", bg: "#E0EAF4", badge: "#001F4D" },
};

function severityStyle(severity) {
  return SEVERITY_STYLES[severity?.toLowerCase()] ?? SEVERITY_STYLES.low;
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ConnectionDot({ isConnected }) {
  return (
    <span
      title={isConnected ? "Live — connected" : "Reconnecting…"}
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: isConnected ? "#27B97C" : "#99BBDD",
        marginRight: 6,
        flexShrink: 0,
      }}
    />
  );
}

function ToastItem({ alert, onDismiss }) {
  const sev = severityStyle(alert.severity);
  const time = alert.fired_at
    ? format(new Date(alert.fired_at), "HH:mm")
    : "—";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "8px 10px",
        marginBottom: 6,
        borderLeft: `3px solid ${sev.border}`,
        background: sev.bg,
        borderRadius: 4,
        fontSize: 13,
      }}
    >
      <span
        style={{
          flexShrink: 0,
          fontWeight: 600,
          color: sev.badge,
          minWidth: 56,
          fontSize: 11,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        {alert.severity ?? "info"}
      </span>
      <span style={{ flex: 1, color: "var(--dark)", lineHeight: 1.4 }}>
        <span style={{ fontWeight: 600 }}>{alert.type ?? "alert"}</span>
        {alert.details ? ` — ${alert.details}` : ""}
      </span>
      <span style={{ flexShrink: 0, color: "var(--mid)", fontSize: 11, marginTop: 1 }}>
        {time}
      </span>
      <button
        onClick={() => onDismiss(alert.id)}
        aria-label="Dismiss alert"
        style={{
          flexShrink: 0,
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "var(--mid)",
          fontSize: 16,
          lineHeight: 1,
          padding: 0,
        }}
      >
        ×
      </button>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function AlertToast() {
  const { alerts, isConnected, clearAlerts } = useAlertSocket();
  const [dismissed, setDismissed] = useState(new Set());
  const [isOpen, setIsOpen] = useState(false);
  const panelRef = useRef(null);

  // Close panel when clicking outside
  useEffect(() => {
    if (!isOpen) return;
    function handleOutside(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [isOpen]);

  const dismissAlert = useCallback((id) => {
    setDismissed((prev) => new Set([...prev, id]));
  }, []);

  const visible = alerts.filter((a) => !dismissed.has(a.id));
  const unread = visible.length;

  return (
    <div
      ref={panelRef}
      style={{ position: "relative", display: "inline-block", userSelect: "none" }}
    >
      {/* Trigger button */}
      <button
        onClick={() => setIsOpen((o) => !o)}
        aria-label={`${unread} alerts — ${isConnected ? "live" : "reconnecting"}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "6px 12px",
          background: unread > 0 ? "var(--primary)" : "var(--light)",
          color: unread > 0 ? "var(--white)" : "var(--mid)",
          border: "1px solid var(--primary-10)",
          borderRadius: 6,
          cursor: "pointer",
          fontSize: 13,
          fontWeight: 500,
          transition: "background 0.15s",
        }}
      >
        <ConnectionDot isConnected={isConnected} />
        {unread > 0 ? (
          <>
            <span style={{ fontWeight: 700 }}>{unread}</span>
            <span style={{ fontWeight: 400 }}> alert{unread !== 1 ? "s" : ""}</span>
          </>
        ) : (
          <span>No new alerts</span>
        )}
      </button>

      {/* Dropdown panel */}
      {isOpen && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            width: 360,
            maxHeight: 480,
            overflowY: "auto",
            background: "var(--white)",
            border: "1px solid var(--primary-10)",
            borderRadius: 8,
            boxShadow: "0 4px 20px rgba(0,0,0,0.12)",
            zIndex: 9999,
            padding: 10,
          }}
          role="dialog"
          aria-label="Alert notifications"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 8,
              paddingBottom: 8,
              borderBottom: "1px solid var(--primary-10)",
            }}
          >
            <span style={{ fontWeight: 600, fontSize: 13, color: "var(--dark)" }}>
              Recent Alerts
            </span>
            {visible.length > 0 && (
              <button
                onClick={() => {
                  clearAlerts();
                  setDismissed(new Set());
                  setIsOpen(false);
                }}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--mid)",
                  fontSize: 12,
                }}
              >
                Clear all
              </button>
            )}
          </div>

          {visible.length === 0 ? (
            <p style={{ color: "var(--mid)", fontSize: 13, textAlign: "center", padding: "16px 0" }}>
              {isConnected ? "No alerts — system healthy." : "Connecting to alert stream…"}
            </p>
          ) : (
            visible.map((alert) => (
              <ToastItem key={alert.id ?? alert.fired_at} alert={alert} onDismiss={dismissAlert} />
            ))
          )}

          <div
            style={{
              paddingTop: 8,
              borderTop: "1px solid var(--primary-10)",
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 11,
              color: "var(--mid)",
            }}
          >
            <ConnectionDot isConnected={isConnected} />
            {isConnected ? "Live stream connected" : "Reconnecting…"}
          </div>
        </div>
      )}
    </div>
  );
}
