import { NavLink } from "react-router-dom";

const NAV_LINKS = [
  { to: "/",           label: "Dashboard" },
  { to: "/manager",    label: "My Team" },
  { to: "/onboarding", label: "Onboarding" },
  { to: "/scenarios",  label: "Scenarios" },
  { to: "/equity",     label: "Equity" },
  { to: "/teams",      label: "Team Builder" },
  { to: "/people",     label: "People" },
  { to: "/info",       label: "Platform" },
  { to: "/admin",      label: "Admin" },
];

const API_BASE = "/api";

export default function Navbar() {
  return (
    <nav
      style={{
        background: "rgba(0,51,102,.97)",
        backdropFilter: "blur(12px)",
        height: "52px",
        position: "sticky",
        top: 0,
        zIndex: 100,
        borderBottom: "1px solid rgba(255,255,255,.08)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 32px",
      }}
    >
      {/* Left: OPB monogram + project name */}
      <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
        <span
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: "20px",
            fontWeight: 300,
          }}
        >
          <span style={{ color: "#ffffff" }}>O</span>
          <em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>PB</em>
        </span>

        <span style={{ width: "1px", height: "18px", background: "rgba(255,255,255,.15)" }} />

        <span
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: "15px",
            fontWeight: 300,
            letterSpacing: "0.2px",
          }}
        >
          <span style={{ color: "rgba(255,255,255,.85)" }}>Org </span>
          <em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>Synapse</em>
        </span>
      </div>

      {/* Right: nav links + API Docs */}
      <div style={{ display: "flex", alignItems: "center", gap: "28px" }}>
        {NAV_LINKS.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end
            style={({ isActive }) => ({
              fontFamily: "var(--fb)",
              fontSize: "13px",
              fontWeight: 500,
              letterSpacing: "0.5px",
              textDecoration: "none",
              paddingBottom: "4px",
              color: isActive ? "var(--gold)" : "rgba(255,255,255,.5)",
              borderBottom: isActive
                ? "2px solid var(--gold)"
                : "2px solid transparent",
              transition: "color 150ms, border-color 150ms",
            })}
          >
            {label}
          </NavLink>
        ))}

        <a
          href={`${API_BASE}/docs`}
          target="_blank"
          rel="noreferrer"
          style={{
            fontFamily: "var(--fb)",
            fontSize: "11px",
            letterSpacing: "1px",
            textDecoration: "none",
            color: "rgba(255,255,255,.35)",
            transition: "color 150ms",
          }}
          onMouseEnter={e => e.target.style.color = "rgba(255,255,255,.75)"}
          onMouseLeave={e => e.target.style.color = "rgba(255,255,255,.35)"}
        >
          API Docs ↗
        </a>
      </div>
    </nav>
  );
}
