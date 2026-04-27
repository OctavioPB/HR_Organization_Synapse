/**
 * AdminPanel — Tenant management dashboard (F6).
 *
 * BRAND.md compliant:
 *   - Dark hero header with grid texture
 *   - Fraunces titles, Plus Jakarta Sans body
 *   - Gold eyebrow labels + accent bars
 *   - BRAND.md semantic status badges (plan, active)
 *   - BRAND.md table styling (primary thead, alternating rows)
 *
 * Requires X-Admin-Key header — set VITE_ADMIN_KEY in .env.local (dev only).
 * In production this page is served on an internal-only subdomain.
 */

import { useCallback, useEffect, useState } from "react";

const API_BASE  = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ADMIN_KEY = import.meta.env.VITE_ADMIN_KEY ?? "";

// ─── Brand tokens ─────────────────────────────────────────────────────────────

const T = {
  primary:   "#003366",
  primary10: "#E0EAF4",
  primary80: "#1A4D80",
  gold:      "#C8982A",
  goldLight: "#E8C46A",
  dark:      "#1C1C2E",
  mid:       "#6B7280",
  light:     "#F4F6F9",
  white:     "#FFFFFF",
  green:     "#27B97C",
  greenBg:   "#E0F7EF",
  greenText: "#0D5C3A",
  orange:    "#F07020",
  orangeBg:  "#FEF0E6",
  orangeText:"#7A3800",
  red:       "#E03448",
  redBg:     "#FDEAEA",
  redText:   "#7A1020",
  purple:    "#7C4DBD",
  purpleBg:  "#F0EBF9",
  purpleText:"#3D1F70",
};

const PLAN_COLORS = {
  free:       { bg: T.primary10, text: T.primary },
  starter:    { bg: T.greenBg,   text: T.greenText },
  pro:        { bg: T.purpleBg,  text: T.purpleText },
  enterprise: { bg: T.orangeBg,  text: T.orangeText },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function adminFetch(path, opts = {}) {
  return fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json", ...(opts.headers ?? {}) },
  });
}

function GoldEyebrow({ label }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
      <span style={{ width: 24, height: 1, background: T.gold, flexShrink: 0 }} />
      <span style={{
        fontSize: 9, letterSpacing: 4, textTransform: "uppercase",
        color: T.gold, fontFamily: "'Plus Jakarta Sans', sans-serif", fontWeight: 500,
      }}>{label}</span>
    </div>
  );
}

function PlanBadge({ plan }) {
  const c = PLAN_COLORS[plan] ?? PLAN_COLORS.free;
  return (
    <span style={{
      background: c.bg, color: c.text, borderRadius: 20, padding: "3px 10px",
      fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
      textTransform: "uppercase", fontFamily: "'Plus Jakarta Sans', sans-serif",
    }}>{plan}</span>
  );
}

function StatusBadge({ active }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      background: active ? T.greenBg : T.redBg,
      color: active ? T.greenText : T.redText,
      borderRadius: 20, padding: "3px 10px",
      fontSize: 10, fontWeight: 500,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: active ? T.green : T.red }} />
      {active ? "Active" : "Inactive"}
    </span>
  );
}

// ─── Tenant table ─────────────────────────────────────────────────────────────

function TenantTable({ tenants, onDeactivate }) {
  const thStyle = {
    background: T.primary, color: T.white, padding: "12px 16px",
    fontSize: 10, textTransform: "uppercase", letterSpacing: 2,
    fontFamily: "'Plus Jakarta Sans', sans-serif", textAlign: "left",
  };
  const tdStyle = (i) => ({
    padding: "12px 16px", fontSize: 13,
    background: i % 2 === 0 ? T.white : T.primary10,
    borderBottom: `1px solid ${T.primary10}`,
    fontFamily: "'Plus Jakarta Sans', sans-serif", color: T.dark,
  });

  if (!tenants.length) {
    return <p style={{ color: T.mid, fontSize: 13, fontFamily: "'Plus Jakarta Sans', sans-serif" }}>No tenants yet.</p>;
  }

  return (
    <div style={{ overflowX: "auto", borderRadius: 8, overflow: "hidden", border: `1px solid ${T.primary10}` }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            {["Name", "Slug", "Plan", "Schema", "Status", "Created", "Actions"].map(h => (
              <th key={h} style={thStyle}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tenants.map((t, i) => (
            <tr key={t.id}>
              <td style={tdStyle(i)}><strong>{t.name}</strong></td>
              <td style={{ ...tdStyle(i), fontFamily: "Courier New, monospace", fontSize: 12 }}>{t.slug}</td>
              <td style={tdStyle(i)}><PlanBadge plan={t.plan} /></td>
              <td style={{ ...tdStyle(i), fontFamily: "Courier New, monospace", fontSize: 11, color: T.mid }}>{t.schema_name}</td>
              <td style={tdStyle(i)}><StatusBadge active={t.active} /></td>
              <td style={{ ...tdStyle(i), color: T.mid, fontSize: 11 }}>
                {new Date(t.created_at).toLocaleDateString()}
              </td>
              <td style={tdStyle(i)}>
                {t.active && (
                  <button
                    onClick={() => onDeactivate(t.id, t.name)}
                    style={{
                      background: "none", border: `1px solid ${T.red}`, color: T.red,
                      borderRadius: 4, padding: "3px 10px", fontSize: 11, cursor: "pointer",
                    }}
                  >Deactivate</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Create tenant form ───────────────────────────────────────────────────────

function CreateTenantForm({ onCreate }) {
  const [form, setForm]       = useState({ slug: "", name: "", plan: "free" });
  const [result, setResult]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await adminFetch("/admin/tenants", {
        method: "POST",
        body: JSON.stringify(form),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setResult(data);
      onCreate?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    border: `1px solid ${T.primary10}`, borderRadius: 6, padding: "8px 12px",
    fontSize: 13, width: "100%", fontFamily: "'Plus Jakarta Sans', sans-serif",
    outline: "none", boxSizing: "border-box",
  };

  return (
    <div style={{ background: T.white, borderRadius: 12, padding: "24px 28px", boxShadow: "0 2px 8px rgba(0,0,0,0.06)" }}>
      <div style={{ height: 3, background: T.gold, marginBottom: 20, borderRadius: 2 }} />
      <GoldEyebrow label="New Tenant" />
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 12 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: T.mid, fontFamily: "'Plus Jakarta Sans', sans-serif", textTransform: "uppercase", letterSpacing: 2 }}>Company Name</label>
          <input style={{ ...inputStyle, marginTop: 4 }} value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="Acme Corp" required />
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: T.mid, fontFamily: "'Plus Jakarta Sans', sans-serif", textTransform: "uppercase", letterSpacing: 2 }}>Slug</label>
          <input style={{ ...inputStyle, marginTop: 4, fontFamily: "Courier New, monospace" }} value={form.slug}
            onChange={e => setForm(f => ({ ...f, slug: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") }))}
            placeholder="acme-corp" required />
          <span style={{ fontSize: 11, color: T.mid }}>Used in Kafka topics and PostgreSQL schema name.</span>
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: T.mid, fontFamily: "'Plus Jakarta Sans', sans-serif", textTransform: "uppercase", letterSpacing: 2 }}>Plan</label>
          <select style={{ ...inputStyle, marginTop: 4 }} value={form.plan}
            onChange={e => setForm(f => ({ ...f, plan: e.target.value }))}>
            {["free", "starter", "pro", "enterprise"].map(p => (
              <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
            ))}
          </select>
        </div>
        <button type="submit" disabled={loading} style={{
          background: loading ? T.mid : T.primary, color: T.white, border: "none",
          borderRadius: 6, padding: "10px 20px", fontSize: 13, fontWeight: 600,
          cursor: loading ? "not-allowed" : "pointer", fontFamily: "'Plus Jakarta Sans', sans-serif",
        }}>
          {loading ? "Provisioning…" : "Create Tenant →"}
        </button>
        {error && <p style={{ color: T.red, fontSize: 13, margin: 0 }}>Error: {error}</p>}
      </form>

      {result && (
        <div style={{ marginTop: 16, padding: 16, background: T.greenBg, borderRadius: 8, borderLeft: `3px solid ${T.green}` }}>
          <p style={{ fontWeight: 600, color: T.greenText, fontSize: 13, margin: "0 0 8px" }}>Tenant created successfully.</p>
          <p style={{ fontSize: 12, color: T.dark, margin: "4px 0" }}>
            <strong>Tenant ID:</strong> <code>{result.tenant_id}</code>
          </p>
          <p style={{ fontSize: 12, color: T.dark, margin: "4px 0" }}>
            <strong>Schema:</strong> <code>{result.schema_name}</code>
          </p>
          <p style={{ fontSize: 12, color: T.red, margin: "8px 0 0", fontWeight: 600 }}>
            ⚠ Save this API key — it will not be shown again:
          </p>
          <code style={{ display: "block", background: T.white, padding: 10, borderRadius: 4, fontSize: 12, wordBreak: "break-all", marginTop: 4 }}>
            {result.raw_api_key}
          </code>
        </div>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function AdminPanel() {
  const [tenants, setTenants]   = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await adminFetch("/admin/tenants");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setTenants(data.tenants ?? []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadTenants(); }, [loadTenants]);

  const handleDeactivate = async (tenantId, name) => {
    if (!window.confirm(`Deactivate tenant "${name}"? This prevents further logins.`)) return;
    await adminFetch(`/admin/tenants/${tenantId}`, { method: "DELETE" });
    loadTenants();
  };

  return (
    <div style={{ minHeight: "100vh", background: T.light, fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* Hero header */}
      <div style={{
        background: T.primary,
        backgroundImage: [
          "linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px)",
          "linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)",
        ].join(","),
        backgroundSize: "48px 48px",
        padding: "48px",
        color: T.white,
      }}>
        <div style={{ fontSize: 9, letterSpacing: 4, textTransform: "uppercase", color: "rgba(255,255,255,0.4)", marginBottom: 12 }}>
          Org Synapse · Internal
        </div>
        <h1 style={{
          fontFamily: "'Fraunces', Georgia, serif", fontWeight: 300,
          fontSize: 40, margin: 0, color: T.white,
        }}>
          Tenant <em style={{ color: T.goldLight, fontStyle: "italic" }}>Administration</em>
        </h1>
        <p style={{ color: "rgba(255,255,255,0.5)", fontSize: 14, marginTop: 8, fontWeight: 300 }}>
          Provision and manage customer tenants for Org Synapse SaaS.
        </p>
      </div>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "40px 48px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 32, alignItems: "start" }}>

          {/* Tenant list */}
          <div>
            <GoldEyebrow label="Active Tenants" />
            <p style={{ fontSize: 13, color: T.mid, marginBottom: 20 }}>
              {loading ? "Loading…" : `${tenants.length} tenant${tenants.length !== 1 ? "s" : ""} provisioned.`}
            </p>
            {error && <p style={{ color: T.red, fontSize: 13 }}>Error: {error}</p>}
            <TenantTable tenants={tenants} onDeactivate={handleDeactivate} />
          </div>

          {/* Create form */}
          <div>
            <CreateTenantForm onCreate={loadTenants} />
          </div>

        </div>

        {/* Divider */}
        <div style={{ height: 1, background: T.primary10, margin: "40px 0" }} />

        {/* Footer */}
        <div style={{ fontSize: 11, color: T.mid, textTransform: "uppercase", letterSpacing: 2 }}>
          Org Synapse Admin · Internal use only · OPB AI Mastery Lab
        </div>
      </div>
    </div>
  );
}
