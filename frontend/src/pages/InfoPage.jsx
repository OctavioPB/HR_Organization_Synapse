import { Link } from "react-router-dom";

/* ─── Shared typography helpers ─────────────────────────────────────────────── */
const FB = "var(--fb)";
const FD = "var(--fd)";

function Eyebrow({ children }) {
  return <div className="eyebrow" style={{ marginBottom: 6 }}>{children}</div>;
}

function SectionTitle({ children, light = false }) {
  return (
    <h2 style={{
      fontFamily: FD, fontSize: 28, fontWeight: 300,
      color: light ? "var(--white)" : "var(--dark)", margin: "0 0 12px",
    }}>
      {children}
    </h2>
  );
}

function Body({ children, light = false, style = {} }) {
  return (
    <p style={{
      fontFamily: FB, fontSize: 14, lineHeight: 1.7,
      color: light ? "rgba(255,255,255,.65)" : "var(--mid)",
      margin: 0, ...style,
    }}>
      {children}
    </p>
  );
}

/* ─── Architecture diagram ───────────────────────────────────────────────────── */
function ArchDiagram() {
  const NAVY  = "#003366";
  const NAVY2 = "#0d3d66";
  const GOLD  = "#C9A84C";
  const WHITE = "#ffffff";
  const GREEN = "#27B97C";
  const BLUE2 = "#336699";
  const FONT  = "Inter, system-ui, sans-serif";
  const CX = 390;  // pipeline center x
  const PX = 190;  // pipeline left x
  const PW = 400;  // pipeline width

  const sources = [
    { x: 108, label: "Slack / Teams",  sub: "Chat · Video" },
    { x: 224, label: "Jira",           sub: "Tickets" },
    { x: 340, label: "GitHub",         sub: "Code reviews" },
    { x: 456, label: "Calendar",       sub: "Meetings" },
    { x: 572, label: "Confluence",     sub: "Documents" },
  ];

  return (
    <svg viewBox="0 0 780 320" style={{ width: "100%", display: "block" }}>
      <defs>
        {[["ad-g", GOLD], ["ad-n", NAVY], ["ad-gr", GREEN]].map(([id, c]) => (
          <marker key={id} id={id} viewBox="0 0 10 7" refX="9" refY="3.5" markerWidth="7" markerHeight="5" orient="auto">
            <polygon points="0 0,10 3.5,0 7" fill={c} />
          </marker>
        ))}
        <linearGradient id="arch-hero-grad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#f0f4f8" />
          <stop offset="100%" stopColor="#dce6f0" />
        </linearGradient>
      </defs>

      {/* Source boxes */}
      {sources.map(s => (
        <g key={s.label}>
          <rect x={s.x} y={10} width={100} height={38} rx={6} fill={BLUE2} />
          <text x={s.x + 50} y={25} textAnchor="middle" fill={WHITE}
            fontSize={11} fontFamily={FONT} fontWeight={600}>{s.label}</text>
          <text x={s.x + 50} y={40} textAnchor="middle" fill="rgba(255,255,255,.5)"
            fontSize={9} fontFamily={FONT}>{s.sub}</text>
          {/* Drop to bus */}
          <line x1={s.x + 50} y1={48} x2={s.x + 50} y2={64}
            stroke={GOLD} strokeWidth={1.5} strokeDasharray="3 3" />
        </g>
      ))}

      {/* Horizontal bus + drop to Kafka */}
      <line x1={158} y1={64} x2={622} y2={64} stroke={GOLD} strokeWidth={1.5} />
      <line x1={CX} y1={64} x2={CX} y2={80} stroke={GOLD} strokeWidth={2} markerEnd="url(#ad-g)" />

      {/* Kafka */}
      <rect x={PX} y={81} width={PW} height={42} rx={6} fill={GOLD} />
      <text x={CX} y={97} textAnchor="middle" fill={NAVY} fontSize={13}
        fontFamily={FONT} fontWeight={700}>Apache Kafka</text>
      <text x={CX} y={113} textAnchor="middle" fill={NAVY} fontSize={10}
        fontFamily={FONT}>Real-time metadata stream · 6 source connectors</text>
      <line x1={CX} y1={123} x2={CX} y2={139} stroke={NAVY} strokeWidth={2} markerEnd="url(#ad-n)" />

      {/* Airflow */}
      <rect x={PX} y={140} width={PW} height={42} rx={6} fill={NAVY} />
      <text x={CX} y={156} textAnchor="middle" fill={WHITE} fontSize={13}
        fontFamily={FONT} fontWeight={700}>Apache Airflow</text>
      <text x={CX} y={172} textAnchor="middle" fill="rgba(255,255,255,.6)" fontSize={10}
        fontFamily={FONT}>ETL orchestration · 10 scheduled DAGs · daily graph snapshots</text>
      <line x1={CX} y1={182} x2={CX} y2={198} stroke={NAVY2} strokeWidth={2} markerEnd="url(#ad-n)" />

      {/* Graph Analytics Core */}
      <rect x={PX} y={199} width={PW} height={42} rx={6} fill={NAVY2} />
      <text x={CX} y={215} textAnchor="middle" fill={WHITE} fontSize={13}
        fontFamily={FONT} fontWeight={700}>Graph Analytics Core</text>
      <text x={CX} y={231} textAnchor="middle" fill="rgba(255,255,255,.6)" fontSize={10}
        fontFamily={FONT}>NetworkX · Neo4j · SPOF scoring · community detection · ML risk</text>

      {/* Three output branches */}
      <path d={`M${CX} 241 L${CX} 255 L100 255 L100 269`}
        fill="none" stroke={GREEN} strokeWidth={1.5} markerEnd="url(#ad-gr)" />
      <line x1={CX} y1={241} x2={CX} y2={269} stroke={GREEN} strokeWidth={1.5} markerEnd="url(#ad-gr)" />
      <path d={`M${CX} 241 L${CX} 255 L680 255 L680 269`}
        fill="none" stroke={GREEN} strokeWidth={1.5} markerEnd="url(#ad-gr)" />

      {/* Output boxes */}
      {[
        { cx: 100, label: "Dashboard",    sub: "Graph · risk cards · silos · what-if" },
        { cx: 390, label: "REST API",     sub: "FastAPI · NL queries · compliance" },
        { cx: 680, label: "Alert Engine", sub: "SPOF · silo warnings · briefings" },
      ].map(o => (
        <g key={o.label}>
          <rect x={o.cx - 100} y={270} width={200} height={40} rx={6}
            fill="none" stroke={GREEN} strokeWidth={1.5} />
          <text x={o.cx} y={286} textAnchor="middle" fill={GREEN} fontSize={12}
            fontFamily={FONT} fontWeight={700}>{o.label}</text>
          <text x={o.cx} y={301} textAnchor="middle" fill={GREEN} fontSize={9}
            fontFamily={FONT} opacity={0.75}>{o.sub}</text>
        </g>
      ))}
    </svg>
  );
}

/* ─── SPOF formula visual ───────────────────────────────────────────────────── */
const SPOF_COMPONENTS = [
  { id: "bc", weight: 0.40, color: "#E03448", label: "Bridge Score",       greek: "α = 0.40",
    desc: "Betweenness centrality — how often this person is the shortest path between two colleagues who don't otherwise connect." },
  { id: "cd", weight: 0.30, color: "#F07020", label: "Cross-Dept Reach",   greek: "β = 0.30",
    desc: "Fraction of collaboration links that cross department boundaries. Employees who bridge silos score higher." },
  { id: "cl", weight: 0.20, color: "#336699", label: "Clustering Inverse", greek: "γ = 0.20",
    desc: "Inverse of the clustering coefficient — solo connectors (whose contacts don't know each other) score higher than members of tight-knit teams." },
  { id: "et", weight: 0.10, color: "#C9A84C", label: "Entropy Trend",      greek: "δ = 0.10",
    desc: "Rate of decline in interaction diversity. A shrinking network signals early-stage disengagement — the system detects it weeks before HR would." },
];

function SpofFormula() {
  return (
    <div>
      {/* Formula bar */}
      <div style={{ display: "flex", height: 36, borderRadius: 8, overflow: "hidden", marginBottom: 20 }}>
        {SPOF_COMPONENTS.map(c => (
          <div key={c.id} style={{
            width: `${c.weight * 100}%`, background: c.color,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <span style={{ fontFamily: FB, fontSize: 11, fontWeight: 700, color: "#fff" }}>
              {Math.round(c.weight * 100)}%
            </span>
          </div>
        ))}
      </div>

      {/* Legend rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {SPOF_COMPONENTS.map(c => (
          <div key={c.id} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
            <div style={{
              width: 14, height: 14, borderRadius: 3, background: c.color,
              flexShrink: 0, marginTop: 2,
            }} />
            <div>
              <span style={{ fontFamily: FB, fontSize: 13, fontWeight: 700, color: "var(--dark)" }}>
                {c.label}
              </span>
              <span style={{
                fontFamily: "monospace", fontSize: 11, color: "var(--mid)",
                marginLeft: 8, background: "var(--primary-10)", padding: "1px 5px", borderRadius: 3,
              }}>
                {c.greek}
              </span>
              <p style={{ fontFamily: FB, fontSize: 12, color: "var(--mid)", margin: "3px 0 0", lineHeight: 1.6 }}>
                {c.desc}
              </p>
            </div>
          </div>
        ))}
      </div>

      <div style={{
        marginTop: 20, padding: "12px 16px", background: "var(--primary-10)",
        borderRadius: 8, borderLeft: "3px solid var(--gold)",
      }}>
        <code style={{ fontFamily: "monospace", fontSize: 12, color: "var(--dark)" }}>
          SPOF = 0.40 × betweenness + 0.30 × cross_dept_edges + 0.20 × (1 − clustering) + 0.10 × entropy_trend
        </code>
        <p style={{ fontFamily: FB, fontSize: 11, color: "var(--mid)", margin: "6px 0 0" }}>
          Weights are configurable via environment variables. Scores normalize to [0, 1].
          Critical threshold: 0.75. Warning threshold: 0.50.
        </p>
      </div>
    </div>
  );
}

/* ─── Silo detection diagram ────────────────────────────────────────────────── */
function SiloDiagram() {
  const NAVY  = "#003366";
  const BLUE2 = "#336699";
  const RED   = "#E03448";
  const GREEN = "#27B97C";
  const MID   = "#8899aa";
  const FONT  = "Inter, system-ui, sans-serif";

  // Cluster A nodes
  const clA = [
    { id: "a1", cx: 100, cy: 80  },
    { id: "a2", cx: 60,  cy: 130 },
    { id: "a3", cx: 140, cy: 130 },
    { id: "a4", cx: 100, cy: 175 },
  ];
  // Cluster B nodes
  const clB = [
    { id: "b1", cx: 320, cy: 80  },
    { id: "b2", cx: 280, cy: 130 },
    { id: "b3", cx: 360, cy: 130 },
    { id: "b4", cx: 320, cy: 175 },
  ];
  // Internal edges A
  const intA = [["a1","a2"],["a1","a3"],["a2","a3"],["a2","a4"],["a3","a4"]];
  // Internal edges B
  const intB = [["b1","b2"],["b1","b3"],["b2","b3"],["b2","b4"],["b3","b4"]];

  const getNode = (id, nodes) => nodes.find(n => n.id === id) || clB.find(n => n.id === id);

  const allNodes = [...clA, ...clB];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
      {/* Healthy state */}
      <div>
        <div style={{
          fontFamily: FB, fontSize: 11, letterSpacing: "2px",
          textTransform: "uppercase", color: GREEN, marginBottom: 8,
        }}>
          ✓ Healthy — cross-team collaboration present
        </div>
        <svg viewBox="0 0 420 220" style={{ width: "100%", border: "1px solid var(--primary-10)", borderRadius: 8, background: "#fafbfc" }}>
          {/* Team labels */}
          <text x={100} y={22} textAnchor="middle" fill={NAVY} fontSize={11} fontFamily={FONT} fontWeight={700}>Engineering</text>
          <text x={320} y={22} textAnchor="middle" fill={NAVY} fontSize={11} fontFamily={FONT} fontWeight={700}>Sales</text>

          {/* Cross edges (bridge) */}
          {[["a3","b2"],["a1","b1"]].map(([a,b]) => {
            const na = allNodes.find(n=>n.id===a), nb = allNodes.find(n=>n.id===b);
            return <line key={a+b} x1={na.cx} y1={na.cy} x2={nb.cx} y2={nb.cy}
              stroke={GREEN} strokeWidth={2} strokeDasharray="5 3" opacity={0.7} />;
          })}
          {/* Internal edges */}
          {[...intA, ...intB].map(([a,b]) => {
            const na = allNodes.find(n=>n.id===a), nb = allNodes.find(n=>n.id===b);
            return <line key={a+b} x1={na.cx} y1={na.cy} x2={nb.cx} y2={nb.cy}
              stroke={BLUE2} strokeWidth={1.5} opacity={0.5} />;
          })}
          {/* Nodes */}
          {allNodes.map(n => (
            <circle key={n.id} cx={n.cx} cy={n.cy} r={12} fill={BLUE2} opacity={0.85} />
          ))}
          {/* Cross-edge annotation */}
          <text x={210} y={100} textAnchor="middle" fill={GREEN} fontSize={10}
            fontFamily={FONT} fontWeight={600}>cross-team links</text>
          <line x1={210} y1={105} x2={210} y2={118} stroke={GREEN} strokeWidth={1} />
        </svg>
        <p style={{ fontFamily: FB, fontSize: 11, color: "var(--mid)", marginTop: 6 }}>
          Isolation ratio = internal ÷ external edges. Below threshold → healthy.
        </p>
      </div>

      {/* Silo state */}
      <div>
        <div style={{
          fontFamily: FB, fontSize: 11, letterSpacing: "2px",
          textTransform: "uppercase", color: RED, marginBottom: 8,
        }}>
          ⚠ Silo — communication collapsed inward
        </div>
        <svg viewBox="0 0 420 220" style={{ width: "100%", border: "1px solid var(--primary-10)", borderRadius: 8, background: "#fafbfc" }}>
          <text x={100} y={22} textAnchor="middle" fill={NAVY} fontSize={11} fontFamily={FONT} fontWeight={700}>Engineering</text>
          <text x={320} y={22} textAnchor="middle" fill={NAVY} fontSize={11} fontFamily={FONT} fontWeight={700}>Sales</text>

          {/* No cross edges */}
          {/* Internal edges */}
          {[...intA, ...intB].map(([a,b]) => {
            const na = allNodes.find(n=>n.id===a), nb = allNodes.find(n=>n.id===b);
            return <line key={a+b} x1={na.cx} y1={na.cy} x2={nb.cx} y2={nb.cy}
              stroke={RED} strokeWidth={1.5} opacity={0.45} />;
          })}
          {/* Nodes */}
          {allNodes.map(n => (
            <circle key={n.id} cx={n.cx} cy={n.cy} r={12}
              fill={n.id.startsWith("a") ? RED : "#F07020"} opacity={0.85} />
          ))}
          {/* Barrier line */}
          <line x1={210} y1={40} x2={210} y2={195} stroke={RED} strokeWidth={2} strokeDasharray="6 4" opacity={0.5} />
          <text x={210} y={210} textAnchor="middle" fill={RED} fontSize={10}
            fontFamily={FONT} fontWeight={600}>isolation barrier</text>
        </svg>
        <p style={{ fontFamily: FB, fontSize: 11, color: "var(--mid)", marginTop: 6 }}>
          Isolation ratio exceeds threshold → silo alert fires. Affected departments and community ID are reported.
        </p>
      </div>
    </div>
  );
}

/* ─── Feature cards ──────────────────────────────────────────────────────────── */
const FEATURES = [
  {
    tag: "Data Pipeline",    abbr: "RT", color: "#003366",
    title: "Real-Time Ingestion",
    body: "Metadata from Slack, Teams, Jira, GitHub, Calendar, and Confluence streams continuously via Apache Kafka. Only collaboration metadata is captured — who interacted with whom, on which channel, and when. Message content is never read.",
  },
  {
    tag: "Core Analytics",   abbr: "GR", color: "#003366",
    title: "Organizational Graph",
    body: "Every interaction becomes a weighted edge in a directed graph updated daily by Airflow. The graph reveals the true shape of how work gets done — typically very different from the org chart. NetworkX and Neo4j power all graph computation.",
  },
  {
    tag: "Risk Scoring",     abbr: "SP", color: "#003366",
    title: "SPOF Risk Scoring",
    body: "A composite score (0–1) quantifies how much organizational disruption would result from each employee's departure. Four weighted factors — bridge score, cross-department reach, clustering inverse, and entropy trend — are combined into a single actionable number.",
  },
  {
    tag: "Fragmentation",    abbr: "SI", color: "#003366",
    title: "Silo Detection",
    body: "Department-based silo detection measures the ratio of internal to outbound communication for each team. When a department's isolation ratio exceeds the configurable threshold (default 2.5×), a silo alert fires with affected department, member list, and severity tier.",
  },
  {
    tag: "Knowledge Risk",   abbr: "KR", color: "#003366",
    title: "Knowledge Concentration",
    body: "Employees who are sole authors of critical documentation or the only contributors to key repositories represent a knowledge risk independent of their graph position. Org Synapse surfaces these concentrations by integrating Confluence and Notion edit histories.",
  },
  {
    tag: "Continuity",       abbr: "SU", color: "#003366",
    title: "Succession Planning",
    body: "For every critical node, the platform automatically ranks the top 5 internal candidates best positioned to absorb that role — scored on structural overlap, shared collaborators, and domain knowledge. Plans are regenerated every time the graph snapshot updates.",
  },
  {
    tag: "Alerting",         abbr: "AL", color: "#003366",
    title: "Real-Time Alert Engine",
    body: "Threshold-based alerts fire the moment a risk signal crosses into warning or critical territory. HR teams receive notifications via the dashboard, email, or Slack. All alerts carry timestamp, severity, affected employees, and recommended action.",
  },
  {
    tag: "NL Interface",     abbr: "NL", color: "#003366",
    title: "Natural Language Queries",
    body: 'Ask the live graph plain-English questions: "Who in Engineering would create the most disruption if they left?" or "Which teams are isolated from Product?" Claude (claude-sonnet-4-6) translates intent into graph queries and returns structured answers.',
  },
  {
    tag: "Executive View",   abbr: "OH", color: "#003366",
    title: "Org Health Score",
    body: "A single 0–100 composite score summarizes organizational health across silo density (20%), SPOF concentration (35%), engagement entropy (20%), and network fragmentation (25%). A formatted executive briefing is auto-generated weekly and delivered via email or Slack.",
  },
  {
    tag: "Compliance",       abbr: "GD", color: "#003366",
    title: "GDPR & Data Compliance",
    body: "Every personal data table is catalogued with sensitivity level and legal basis. Retention policies are enforced automatically (raw events: 90 days, graph snapshots: 365 days). Employees can request a full Article 20 data export. Every consent change is logged to an immutable audit trail.",
  },
  {
    tag: "Multi-Tenant",     abbr: "MT", color: "#003366",
    title: "Multi-Tenant SaaS",
    body: "The platform supports multiple client organizations on a single deployment. Each tenant's data is isolated at the PostgreSQL schema level. Tenant-aware Kafka topics ensure zero cross-contamination. Admin API endpoints manage provisioning, billing (Stripe), and configuration.",
  },
  {
    tag: "Simulation",       abbr: "WI", color: "#003366",
    title: "What-If Simulation",
    body: "Before restructuring or a departure, HR can model the precise impact: what percentage of cross-department connectivity disappears, by how many hops the average path between colleagues increases, and how many direct collaboration links are severed. Results are available in seconds.",
  },
];

function FeatureCard({ tag, abbr, color, title, body }) {
  return (
    <div style={{
      background: "var(--white)", borderRadius: 12,
      boxShadow: "0 1px 4px rgba(0,0,0,.08)", overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{ height: 4, background: color, borderRadius: "12px 12px 0 0" }} />
      <div style={{ padding: "20px 20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 6, background: color,
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0,
          }}>
            <span style={{ fontFamily: "monospace", fontSize: 11, fontWeight: 700, color: "#fff", letterSpacing: "0.5px" }}>
              {abbr}
            </span>
          </div>
          <div style={{
            fontFamily: FB, fontSize: 9, letterSpacing: "2px",
            textTransform: "uppercase", color: "var(--mid)",
          }}>
            {tag}
          </div>
        </div>
        <h3 style={{
          fontFamily: FD, fontSize: 17, fontWeight: 400,
          color: "var(--dark)", margin: "0 0 8px",
        }}>
          {title}
        </h3>
        <p style={{ fontFamily: FB, fontSize: 13, color: "var(--mid)", margin: 0, lineHeight: 1.7 }}>
          {body}
        </p>
      </div>
    </div>
  );
}

/* ─── What-If metric explainer ──────────────────────────────────────────────── */

const WHATIF_METRICS = [
  {
    id: "cross",
    abbr: "CD",
    label: "Cross-dept connectivity lost",
    example: "−23%",
    exampleSub: "70 of 304 cross-department edges removed",
    thresholds: [
      { label: "> 30%", color: "#E03448", text: "Critical" },
      { label: "15–30%", color: "#F07020", text: "Warning" },
      { label: "< 15%", color: "#336699", text: "Low" },
    ],
    desc: "The share of all cross-department collaboration edges that ran through this employee. When they leave, these are the bridges between Engineering and Sales or between Product and HR that disappear entirely. A high percentage means this person is the primary channel between departments — not just an active communicator, but a structural necessity.",
  },
  {
    id: "path",
    abbr: "PL",
    label: "Avg hops between colleagues",
    example: "+0.032 hops",
    exampleSub: "1.809 → 1.841 hops on average",
    thresholds: [
      { label: "> 1.0", color: "#E03448", text: "Critical" },
      { label: "> 0.3", color: "#F07020", text: "Warning" },
      { label: "< 0.3", color: "#336699", text: "Low" },
    ],
    desc: "The change in average shortest path length across all employee pairs in the graph. Every added hop is an intermediary who must relay information, coordinate decisions, or broker introductions. The global average obscures the real impact: even a small number like +0.03 represents specific department pairs whose direct route disappears and must now route through four or five people instead of two.",
  },
  {
    id: "degree",
    abbr: "DS",
    label: "Direct connections severed",
    example: "100",
    exampleSub: "collaboration links this employee held",
    thresholds: [
      { label: "High vs org avg", color: "#E03448", text: "Critical" },
      { label: "Moderate", color: "#F07020", text: "Warning" },
      { label: "Near avg", color: "#336699", text: "Low" },
    ],
    desc: "The number of unique direct collaboration links this employee maintained. Raw degree is only meaningful in context: a connector with 100 cross-department links is far more critical than a manager with 100 links entirely within their own team. Combine with the cross-dept loss percentage to understand whether this person's edges are bridges or just volume.",
  },
];

function WhatIfExplainer() {
  return (
    <div>
      {/* Metric rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {WHATIF_METRICS.map((m) => (
          <div
            key={m.id}
            style={{
              display: "grid",
              gridTemplateColumns: "200px 1fr",
              gap: 24,
              alignItems: "start",
            }}
          >
            {/* Example value card */}
            <div style={{
              background: "var(--primary)",
              borderRadius: 10,
              padding: "18px 20px",
              display: "flex",
              flexDirection: "column",
              gap: 6,
            }}>
              <div style={{
                fontFamily: "var(--fb)", fontSize: 9, letterSpacing: "2px",
                textTransform: "uppercase", color: "rgba(255,255,255,.45)",
                marginBottom: 2,
              }}>
                {m.abbr} · example
              </div>
              <div style={{
                fontFamily: "var(--fd)", fontSize: 28, fontWeight: 300,
                color: "var(--gold-light)", lineHeight: 1,
              }}>
                {m.example}
              </div>
              <div style={{
                fontFamily: "var(--fb)", fontSize: 10,
                color: "rgba(255,255,255,.5)", lineHeight: 1.5,
              }}>
                {m.exampleSub}
              </div>
            </div>

            {/* Label + thresholds + description */}
            <div>
              <div style={{
                fontFamily: FB, fontSize: 14, fontWeight: 700,
                color: "var(--dark)", marginBottom: 8,
              }}>
                {m.label}
              </div>

              {/* Threshold pills */}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                {m.thresholds.map((t) => (
                  <span key={t.label} style={{
                    fontFamily: FB, fontSize: 10, fontWeight: 600,
                    padding: "3px 8px", borderRadius: 4,
                    border: `1px solid ${t.color}`,
                    color: t.color,
                    whiteSpace: "nowrap",
                  }}>
                    {t.label} — {t.text}
                  </span>
                ))}
              </div>

              <p style={{
                fontFamily: FB, fontSize: 13, color: "var(--mid)",
                margin: 0, lineHeight: 1.7,
              }}>
                {m.desc}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Network fragments note */}
      <div style={{
        marginTop: 28, padding: "14px 16px",
        background: "var(--primary-10)", borderRadius: 8,
        borderLeft: "3px solid var(--gold)",
      }}>
        <div style={{ fontFamily: FB, fontSize: 11, fontWeight: 700, color: "var(--dark)", marginBottom: 4 }}>
          Network fragments created
        </div>
        <p style={{ fontFamily: FB, fontSize: 12, color: "var(--mid)", margin: 0, lineHeight: 1.6 }}>
          A fourth metric fires only when removal causes complete disconnection — teams that lose every path to another team, not just a slower route. This is rare in well-connected organisations but becomes likely when a single employee is the sole bridge between two departments. When it appears, it is the most severe signal the simulation can produce.
        </p>
      </div>
    </div>
  );
}

/* ─── Privacy principles ─────────────────────────────────────────────────────── */
const PRIVACY_POINTS = [
  {
    n: "01",
    title: "Metadata only",
    body: "No message content, email bodies, file contents, or document text is ever captured. The platform only records who collaborated with whom, on which channel, and when.",
  },
  {
    n: "02",
    title: "UUID anonymisation",
    body: "All graph computation uses employee UUIDs. Names and contact details are stored in a separate lookup table. The dashboard resolves names only for authorised HR roles.",
  },
  {
    n: "03",
    title: "Aggregate alerts, not surveillance",
    body: "Risk alerts target organisational patterns — 'Engineering ↔ Sales bridge is at risk' — not individual monitoring. The system is designed to protect the org, not rank employees.",
  },
  {
    n: "04",
    title: "Automatic retention enforcement",
    body: "Raw events are purged after 90 days. Graph snapshots after 365 days. Purges are logged to an immutable audit trail and reported in the quarterly compliance report.",
  },
  {
    n: "05",
    title: "GDPR Article 20 export",
    body: "Any employee can request a full machine-readable export of all personal data held across all six tables. The export is generated on demand via the compliance API.",
  },
  {
    n: "06",
    title: "Role-based access control",
    body: "HR admins see individual scores. Executives see department-level aggregates only. Analysts see anonymised graph topology. Access tiers are enforced at the API layer.",
  },
];

/* ─── Main page ──────────────────────────────────────────────────────────────── */
export default function InfoPage() {
  return (
    <div style={{ minHeight: "100vh", background: "var(--light)" }}>

      {/* ── Hero ──────────────────────────────────────────────────────────────── */}
      <div style={{
        background: "var(--primary)",
        backgroundImage:
          "linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px)," +
          "linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)",
        backgroundSize: "48px 48px",
        padding: "64px 48px 56px",
      }}>
        <div style={{ maxWidth: 720 }}>
          <div className="eyebrow" style={{ color: "var(--gold)", marginBottom: 12 }}>
            Platform Overview
          </div>
          <h1 style={{
            fontFamily: FD, fontSize: 44, fontWeight: 300,
            color: "var(--white)", margin: "0 0 20px", lineHeight: 1.15,
          }}>
            Built for HR leaders who{" "}
            <em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>
              can't afford to be surprised
            </em>
          </h1>
          <Body light style={{ fontSize: 16, maxWidth: 640 }}>
            Every week, employees leave, teams fragment, and knowledge siloes form — and HR
            usually finds out after the fact. Org Synapse turns collaboration metadata into
            advance warning: surfacing departure risk, team fragmentation, and knowledge
            concentration before any subjective signal exists.
          </Body>
        </div>
      </div>

      <div style={{ padding: "0 48px 80px" }}>

        {/* ── The Problem ─────────────────────────────────────────────────────── */}
        <div style={{ paddingTop: 56, marginBottom: 64 }}>
          <Eyebrow>Why it matters</Eyebrow>
          <SectionTitle>Traditional HR operates blind</SectionTitle>
          <Body style={{ maxWidth: 600, marginBottom: 36 }}>
            By the time an exit interview reveals that a key person was disengaging,
            the organisational damage is already done. The signals were always there —
            hidden in collaboration metadata that no tool was reading.
          </Body>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 }}>
            {[
              {
                stat: "60–80%",
                label: "of critical knowledge is undocumented",
                body: "Most organisations have no systematic way to identify who actually holds institutional knowledge — until that person resigns.",
              },
              {
                stat: "4–6 wks",
                label: "average lag between disengagement and visibility",
                body: "An employee can reduce their collaboration volume by 40% before their manager notices. The graph detects entropy decline in real time.",
              },
              {
                stat: "3–5×",
                label: "cost multiplier when a network bridge leaves",
                body: "When a connector who bridges two departments leaves, the communication cost for everyone in both groups rises dramatically — yet traditional metrics miss this entirely.",
              },
            ].map(c => (
              <div key={c.stat} style={{
                background: "var(--white)", borderRadius: 12,
                boxShadow: "0 1px 4px rgba(0,0,0,.08)", overflow: "hidden",
              }}>
                <div className="card-accent" />
                <div style={{ padding: "24px 24px 28px" }}>
                  <div style={{
                    fontFamily: FD, fontSize: 36, fontWeight: 300,
                    color: "var(--primary)", lineHeight: 1, marginBottom: 6,
                  }}>
                    {c.stat}
                  </div>
                  <div style={{
                    fontFamily: FB, fontSize: 11, fontWeight: 700,
                    color: "var(--dark)", textTransform: "uppercase",
                    letterSpacing: "0.5px", marginBottom: 10,
                  }}>
                    {c.label}
                  </div>
                  <Body>{c.body}</Body>
                </div>
              </div>
            ))}
          </div>
        </div>

        <hr className="section-divider" style={{ marginBottom: 56 }} />

        {/* ── Architecture ─────────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 64 }}>
          <Eyebrow>System Design</Eyebrow>
          <SectionTitle>How it works</SectionTitle>
          <Body style={{ maxWidth: 640, marginBottom: 32 }}>
            Five layers transform raw collaboration signals into HR decisions. Every layer
            is independently scalable; the graph analytics core can process 500k+ employees.
            The full pipeline runs on open-source infrastructure that any engineering team
            can operate.
          </Body>
          <div style={{
            background: "var(--white)", borderRadius: 12,
            boxShadow: "0 1px 4px rgba(0,0,0,.08)", padding: "32px",
          }}>
            <ArchDiagram />
            <div style={{
              display: "grid", gridTemplateColumns: "repeat(5, 1fr)",
              gap: 16, marginTop: 28,
              borderTop: "1px solid var(--primary-10)", paddingTop: 24,
            }}>
              {[
                { label: "Layer 1 — Sources",     desc: "6 connectors. Metadata only. No message content." },
                { label: "Layer 2 — Stream",       desc: "Apache Kafka KRaft. Real-time, 6-partition topic." },
                { label: "Layer 3 — Orchestration",desc: "Airflow. 10 DAGs, daily snapshots, quarterly compliance." },
                { label: "Layer 4 — Analytics",    desc: "NetworkX + Neo4j. Betweenness, Louvain, SPOF." },
                { label: "Layer 5 — Delivery",     desc: "FastAPI, React dashboard, alert engine, briefings." },
              ].map(l => (
                <div key={l.label}>
                  <div style={{ fontFamily: FB, fontSize: 11, fontWeight: 700, color: "var(--primary)", marginBottom: 4 }}>
                    {l.label}
                  </div>
                  <div style={{ fontFamily: FB, fontSize: 12, color: "var(--mid)", lineHeight: 1.5 }}>
                    {l.desc}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <hr className="section-divider" style={{ marginBottom: 56 }} />

        {/* ── Feature Grid ─────────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 64 }}>
          <Eyebrow>Capabilities</Eyebrow>
          <SectionTitle>12 features, one platform</SectionTitle>
          <Body style={{ maxWidth: 560, marginBottom: 36 }}>
            From ingestion to compliance, every capability is built on the same graph
            data model — so insights compound rather than fragment.
          </Body>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 }}>
            {FEATURES.map(f => <FeatureCard key={f.title} {...f} />)}
          </div>
        </div>

        <hr className="section-divider" style={{ marginBottom: 56 }} />

        {/* ── SPOF deep-dive ───────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 64 }}>
          <Eyebrow>Risk Methodology</Eyebrow>
          <SectionTitle>How the SPOF score is calculated</SectionTitle>
          <Body style={{ maxWidth: 640, marginBottom: 32 }}>
            The Single-Point-of-Failure score is not a single metric — it is a weighted
            composite that accounts for both structural position and behavioural change.
            All four weights are configurable and can be tuned to your organisation's
            risk model.
          </Body>
          <div style={{
            background: "var(--white)", borderRadius: 12,
            boxShadow: "0 1px 4px rgba(0,0,0,.08)", padding: "32px",
          }}>
            <SpofFormula />
          </div>
        </div>

        <hr className="section-divider" style={{ marginBottom: 56 }} />

        {/* ── Silo detection ───────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 64 }}>
          <Eyebrow>Fragmentation Detection</Eyebrow>
          <SectionTitle>How silos are identified</SectionTitle>
          <Body style={{ maxWidth: 640, marginBottom: 32 }}>
            Department-based silo detection measures each team's outbound communication
            ratio. A silo alert fires when a department sends more than a configurable
            multiple of messages internally versus externally — default threshold 2.5×.
          </Body>
          <div style={{
            background: "var(--white)", borderRadius: 12,
            boxShadow: "0 1px 4px rgba(0,0,0,.08)", padding: "32px",
          }}>
            <SiloDiagram />
            <div style={{
              marginTop: 24, padding: "14px 16px",
              background: "var(--primary-10)", borderRadius: 8,
              borderLeft: "3px solid var(--gold)",
            }}>
              <code style={{ fontFamily: "monospace", fontSize: 12, color: "var(--dark)" }}>
                isolation_ratio = internal_edges ÷ max(external_edges, 1)
              </code>
              <p style={{ fontFamily: FB, fontSize: 11, color: "var(--mid)", margin: "6px 0 0" }}>
                Default silo threshold: 2.5×. Configurable via <code>SILO_THRESHOLD</code> environment variable.
                Alerts include department name, member count, isolation ratio, and severity tier.
              </p>
            </div>
          </div>
        </div>

        <hr className="section-divider" style={{ marginBottom: 56 }} />

        {/* ── What-If deep-dive ────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 64 }}>
          <Eyebrow>Departure Simulation</Eyebrow>
          <SectionTitle>Reading the What-If results</SectionTitle>
          <Body style={{ maxWidth: 640, marginBottom: 32 }}>
            The simulation removes one employee from the live collaboration graph and
            recomputes three structural metrics. Each measures a different dimension
            of network damage — and they tell different stories depending on the
            person's role in the organisation.
          </Body>
          <div style={{
            background: "var(--white)", borderRadius: 12,
            boxShadow: "0 1px 4px rgba(0,0,0,.08)", padding: "32px",
          }}>
            <WhatIfExplainer />
          </div>
        </div>

        <hr className="section-divider" style={{ marginBottom: 56 }} />

        {/* ── Privacy by design ────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 64 }}>
          <Eyebrow>Ethics & Compliance</Eyebrow>
          <SectionTitle>Privacy by design — not by policy</SectionTitle>
          <Body style={{ maxWidth: 640, marginBottom: 36 }}>
            Org Synapse was designed from the ground up to operate within GDPR, CCPA,
            and enterprise data governance requirements. These are architectural
            constraints, not checkboxes.
          </Body>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
            {PRIVACY_POINTS.map(p => (
              <div key={p.n} style={{
                background: "var(--white)", borderRadius: 12,
                boxShadow: "0 1px 4px rgba(0,0,0,.08)", padding: "20px 20px 24px",
              }}>
                <div style={{
                  fontFamily: "monospace", fontSize: 11, color: "var(--gold)",
                  fontWeight: 700, marginBottom: 8,
                }}>
                  {p.n}
                </div>
                <div style={{
                  fontFamily: FD, fontSize: 15, fontWeight: 400,
                  color: "var(--dark)", marginBottom: 8,
                }}>
                  {p.title}
                </div>
                <Body>{p.body}</Body>
              </div>
            ))}
          </div>
        </div>

        {/* ── CTA ──────────────────────────────────────────────────────────────── */}
        <div style={{
          background: "var(--primary)",
          borderRadius: 16, padding: "48px 40px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: 32,
          backgroundImage:
            "linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px)," +
            "linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}>
          <div>
            <h3 style={{
              fontFamily: FD, fontSize: 28, fontWeight: 300,
              color: "var(--white)", margin: "0 0 10px",
            }}>
              Ready to explore your{" "}
              <em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>
                organisation's graph?
              </em>
            </h3>
            <Body light>
              Seed the database with synthetic data and the full dashboard is live in under a minute.
            </Body>
          </div>
          <div style={{ display: "flex", gap: 12, flexShrink: 0 }}>
            <Link
              to="/"
              style={{
                fontFamily: FB, fontSize: 13, fontWeight: 700,
                background: "var(--gold)", color: "#1a1a1a",
                padding: "12px 24px", borderRadius: 8,
                textDecoration: "none", whiteSpace: "nowrap",
                letterSpacing: "0.3px",
              }}
            >
              Open Dashboard →
            </Link>
            <Link
              to="/admin"
              style={{
                fontFamily: FB, fontSize: 13, fontWeight: 600,
                background: "transparent",
                border: "1px solid rgba(255,255,255,.3)",
                color: "rgba(255,255,255,.8)",
                padding: "12px 24px", borderRadius: 8,
                textDecoration: "none", whiteSpace: "nowrap",
              }}
            >
              Admin Panel
            </Link>
          </div>
        </div>

      </div>

      {/* Footer */}
      <footer style={{
        background: "var(--primary)", padding: "20px 48px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <span style={{ fontFamily: FD, fontSize: 14, fontWeight: 300, color: "var(--white)" }}>
          O<em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>PB</em>
        </span>
        <span style={{
          fontFamily: FB, fontSize: 9, letterSpacing: "3px",
          textTransform: "uppercase", color: "rgba(255,255,255,.35)",
        }}>
          Org Synapse · ONA Platform · 2025
        </span>
      </footer>
    </div>
  );
}
