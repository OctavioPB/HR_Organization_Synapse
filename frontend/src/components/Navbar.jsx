import { NavLink } from "react-router-dom";

const NAV_LINKS = [
  { to: "/",      label: "Dashboard" },
  { to: "/info",  label: "Platform" },
  { to: "/admin", label: "Admin" },
];

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

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
      {/* Left: OPB monogram + nav links */}
      <div style={{ display: "flex", alignItems: "center", gap: "32px" }}>
        <span
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: "20px",
            fontWeight: 300,
            textDecoration: "none",
          }}
        >
          <span style={{ color: "#ffffff" }}>O</span>
          <em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>PB</em>
        </span>

        <div style={{ display: "flex", alignItems: "center", gap: "24px" }}>
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
        </div>
      </div>

      {/* Right: external links + wordmark */}
      <div style={{ display: "flex", alignItems: "center", gap: "20px" }}>
        <a
          href={`${API_BASE}/docs`}
          target="_blank"
          rel="noreferrer"
          style={{
            fontFamily: "var(--fb)",
            fontSize: "11px",
            letterSpacing: "1px",
            textDecoration: "none",
            color: "rgba(255,255,255,.45)",
            transition: "color 150ms",
          }}
          onMouseEnter={e => e.target.style.color = "rgba(255,255,255,.8)"}
          onMouseLeave={e => e.target.style.color = "rgba(255,255,255,.45)"}
        >
          API Docs ↗
        </a>
        <span
          style={{
            fontFamily: "var(--fb)",
            fontSize: "9px",
            letterSpacing: "3px",
            textTransform: "uppercase",
            color: "rgba(255,255,255,.25)",
          }}
        >
          Org Synapse · ONA Platform
        </span>
      </div>
    </nav>
  );
}
