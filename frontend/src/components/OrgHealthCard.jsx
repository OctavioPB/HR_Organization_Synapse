/**
 * OrgHealthCard — Executive-facing Org Health Score widget (F9).
 *
 * Follows BRAND.md:
 *   - Dark hero header with grid texture (var(--primary) + subtle grid)
 *   - Fraunces score callout (weight 300)
 *   - Gold top accent bar on the card
 *   - Gold eyebrow labels (uppercase, letter-spacing)
 *   - BRAND.md semantic status colours for tier
 *   - Component risk bars in BRAND.md data-viz palette
 *   - KPI stat layout (centered column, no icons)
 *
 * Usage:
 *   import { OrgHealthCard } from "./components/OrgHealthCard";
 *   <OrgHealthCard />
 */

import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// ─── Brand tokens ─────────────────────────────────────────────────────────────

const T = {
  primary:    "#003366",
  primary80:  "#1A4D80",
  primary10:  "#E0EAF4",
  gold:       "#C8982A",
  goldLight:  "#E8C46A",
  dark:       "#1C1C2E",
  mid:        "#6B7280",
  light:      "#F4F6F9",
  white:      "#FFFFFF",
  green:      "#27B97C",
  greenBg:    "#E0F7EF",
  greenText:  "#0D5C3A",
  orange:     "#F07020",
  orangeBg:   "#FEF0E6",
  orangeText: "#7A3800",
  red:        "#E03448",
  redBg:      "#FDEAEA",
  redText:    "#7A1020",
  purple:     "#7C4DBD",
  purpleBg:   "#F0EBF9",
  purpleText: "#3D1F70",
};

const TIER_COLORS = {
  healthy:  { dot: T.green,  bg: T.greenBg,  text: T.greenText,  label: "Healthy"  },
  caution:  { dot: T.orange, bg: T.orangeBg, text: T.orangeText, label: "Caution"  },
  at_risk:  { dot: T.red,    bg: T.redBg,    text: T.redText,    label: "At Risk"  },
  critical: { dot: T.purple, bg: T.purpleBg, text: T.purpleText, label: "Critical" },
};

// Component risk bars use BRAND.md data-viz series (in order of visual weight)
const COMPONENT_COLORS = {
  spof:    T.primary,
  silo:    "#27B97C",
  frag:    "#7C4DBD",
  entropy: "#F07020",
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function GoldEyebrow({ label }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        marginBottom: 6,
      }}
    >
      <span
        style={{
          display: "inline-block",
          width: 24,
          height: 1,
          background: T.gold,
          flexShrink: 0,
        }}
      />
      <span
        style={{
          fontSize: 9,
          letterSpacing: 4,
          textTransform: "uppercase",
          color: T.gold,
          fontFamily: "'Plus Jakarta Sans', sans-serif",
          fontWeight: 500,
        }}
      >
        {label}
      </span>
    </div>
  );
}

function TierBadge({ tier }) {
  const c = TIER_COLORS[tier] ?? TIER_COLORS.caution;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: c.bg,
        color: c.text,
        borderRadius: 20,
        padding: "4px 12px",
        fontSize: 10,
        fontWeight: 500,
        fontFamily: "'Plus Jakarta Sans', sans-serif",
        letterSpacing: "0.03em",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: c.dot,
          flexShrink: 0,
        }}
      />
      {c.label}
    </span>
  );
}

function ComponentBar({ label, value }) {
  const pct   = Math.round(value * 100);
  const color = COMPONENT_COLORS[label] ?? T.mid;

  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginBottom: 3,
          fontSize: 11,
          color: T.mid,
          fontFamily: "'Plus Jakarta Sans', sans-serif",
        }}
      >
        <span style={{ textTransform: "capitalize" }}>{label} risk</span>
        <span style={{ fontWeight: 600 }}>{pct}%</span>
      </div>
      <div
        style={{
          height: 6,
          background: T.light,
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: color,
            borderRadius: 4,
            transition: "width 0.6s ease",
          }}
        />
      </div>
    </div>
  );
}

function TrendSparkline({ points }) {
  if (!points || points.length < 2) return null;

  const scores  = points.map((p) => p.score);
  const min     = Math.min(...scores);
  const max     = Math.max(...scores);
  const range   = max - min || 1;
  const W = 200, H = 40;

  const pts = scores.map((s, i) => {
    const x = (i / (scores.length - 1)) * W;
    const y = H - ((s - min) / range) * (H - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  const lastScore = scores[scores.length - 1];
  const prevScore = scores[scores.length - 2];
  const lineColor = lastScore >= prevScore ? T.green : T.red;

  return (
    <svg width={W} height={H} style={{ overflow: "visible" }}>
      <polyline
        points={pts}
        fill="none"
        stroke={lineColor}
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {scores.map((s, i) => {
        const x = (i / (scores.length - 1)) * W;
        const y = H - ((s - min) / range) * (H - 6) - 3;
        return (
          <circle key={i} cx={x} cy={y} r={i === scores.length - 1 ? 4 : 2}
            fill={i === scores.length - 1 ? lineColor : T.primary30 ?? T.primary80} />
        );
      })}
    </svg>
  );
}

// ─── Data fetching ────────────────────────────────────────────────────────────

function useOrgHealth() {
  const [score, setScore]     = useState(null);
  const [trend, setTrend]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetch(`${API_BASE}/org-health/score`).then((r) => r.json()),
      fetch(`${API_BASE}/org-health/trend?weeks=8`).then((r) => r.json()),
    ])
      .then(([s, t]) => {
        if (cancelled) return;
        if (s.detail) { setError(s.detail); return; }
        setScore(s);
        setTrend(t);
      })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return { score, trend, loading, error };
}

// ─── Main component ───────────────────────────────────────────────────────────

export function OrgHealthCard() {
  const { score, trend, loading, error } = useOrgHealth();

  if (loading) {
    return (
      <div
        style={{
          borderRadius: 12,
          overflow: "hidden",
          boxShadow: "0 2px 12px rgba(0,0,0,0.08)",
          background: T.white,
          minHeight: 280,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: T.mid,
          fontSize: 13,
          fontFamily: "'Plus Jakarta Sans', sans-serif",
        }}
      >
        Loading health score…
      </div>
    );
  }

  if (error || !score) {
    return (
      <div
        style={{
          borderRadius: 12,
          overflow: "hidden",
          boxShadow: "0 2px 12px rgba(0,0,0,0.08)",
          background: T.white,
          padding: "24px 28px",
        }}
      >
        <GoldEyebrow label="Org Health" />
        <p style={{ color: T.mid, fontSize: 13, fontFamily: "'Plus Jakarta Sans', sans-serif" }}>
          {error ?? "No health data — run org_health_dag first."}
        </p>
      </div>
    );
  }

  const comp        = score.component_scores ?? {};
  const trendPoints = trend?.points ?? [];
  const prevScore   = trendPoints.length >= 2
    ? trendPoints[trendPoints.length - 2].score
    : score.score;
  const delta       = (score.score - prevScore).toFixed(1);
  const deltaPos    = parseFloat(delta) >= 0;

  return (
    <div
      style={{
        borderRadius: 12,
        overflow: "hidden",
        boxShadow: "0 2px 12px rgba(0,0,0,0.08)",
        fontFamily: "'Plus Jakarta Sans', sans-serif",
      }}
    >
      {/* Top gold accent bar */}
      <div style={{ height: 3, background: T.gold }} />

      {/* Dark hero header */}
      <div
        style={{
          background: T.primary,
          backgroundImage: [
            "linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px)",
            "linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)",
          ].join(","),
          backgroundSize: "48px 48px",
          padding: "24px 28px 20px",
          color: T.white,
        }}
      >
        <div
          style={{
            fontSize: 9,
            letterSpacing: 4,
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.45)",
            marginBottom: 10,
          }}
        >
          Organizational Intelligence
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
          {/* Score callout — Fraunces */}
          <div style={{ lineHeight: 1 }}>
            <span
              style={{
                fontFamily: "'Fraunces', Georgia, serif",
                fontSize: 52,
                fontWeight: 300,
                color: T.white,
              }}
            >
              {score.score}
            </span>
            <span
              style={{
                fontFamily: "'Fraunces', Georgia, serif",
                fontSize: 20,
                fontWeight: 300,
                color: "rgba(255,255,255,0.5)",
                marginLeft: 4,
              }}
            >
              /100
            </span>
          </div>

          <div style={{ paddingBottom: 6 }}>
            <TierBadge tier={score.tier} />
            <div
              style={{
                marginTop: 6,
                fontSize: 11,
                color: deltaPos ? T.goldLight : T.red,
                fontWeight: 500,
              }}
            >
              {deltaPos ? "▲" : "▼"} {Math.abs(parseFloat(delta))} pts vs last week
            </div>
          </div>
        </div>

        {/* Sparkline */}
        {trendPoints.length >= 2 && (
          <div style={{ marginTop: 14 }}>
            <TrendSparkline points={trendPoints} />
          </div>
        )}
      </div>

      {/* Body */}
      <div style={{ background: T.white, padding: "20px 28px 24px" }}>

        {/* KPI row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 16,
            marginBottom: 20,
            paddingBottom: 18,
            borderBottom: `1px solid ${T.primary10}`,
          }}
        >
          {[
            { label: "Active Silos",   value: score.silo_count },
            { label: "Avg SPOF Score", value: score.avg_spof_score.toFixed(2) },
            { label: "Graph Clusters", value: score.wcc_count },
          ].map(({ label, value }) => (
            <div key={label} style={{ textAlign: "center" }}>
              <div
                style={{
                  fontFamily: "'Fraunces', Georgia, serif",
                  fontSize: 28,
                  fontWeight: 300,
                  color: T.dark,
                  lineHeight: 1,
                }}
              >
                {value}
              </div>
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: 3,
                  color: T.mid,
                  marginTop: 4,
                }}
              >
                {label}
              </div>
            </div>
          ))}
        </div>

        {/* Component breakdown */}
        <GoldEyebrow label="Risk Breakdown" />
        <div style={{ marginTop: 10 }}>
          {Object.entries(comp)
            .sort(([, a], [, b]) => b - a)
            .map(([key, val]) => (
              <ComponentBar key={key} label={key} value={parseFloat(val)} />
            ))}
        </div>

        {/* Footer meta */}
        <div
          style={{
            marginTop: 14,
            paddingTop: 12,
            borderTop: `1px solid ${T.primary10}`,
            fontSize: 11,
            color: T.mid,
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>{score.node_count} employees tracked</span>
          <span>Computed {score.computed_at}</span>
        </div>
      </div>
    </div>
  );
}
