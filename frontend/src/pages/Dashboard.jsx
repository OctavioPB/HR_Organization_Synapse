import { useQuery } from "@tanstack/react-query";
import {
  fetchGraphSnapshot,
  fetchRiskScores,
  fetchSiloAlerts,
  fetchCommunities,
} from "../lib/api.js";
import OrgGraph from "../components/OrgGraph.jsx";
import CriticalNodePanel from "../components/CriticalNodePanel.jsx";
import SiloAlert from "../components/SiloAlert.jsx";

function KpiCard({ value, label, sub }) {
  return (
    <div
      style={{
        background: "var(--white)",
        borderRadius: "12px",
        boxShadow: "0 1px 4px rgba(0,0,0,.08)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        flex: 1,
        minWidth: 0,
      }}
    >
      <div className="card-accent" style={{ width: "100%", borderRadius: "12px 12px 0 0" }} />
      <div style={{ padding: "24px 20px" }}>
        <div
          style={{
            fontFamily: "var(--fd)",
            fontSize: "32px",
            fontWeight: 300,
            color: "var(--dark)",
            lineHeight: 1.1,
          }}
        >
          {value ?? "—"}
        </div>
        <div
          style={{
            fontFamily: "var(--fb)",
            fontSize: "10px",
            letterSpacing: "3px",
            textTransform: "uppercase",
            color: "var(--mid)",
            marginTop: "8px",
          }}
        >
          {label}
        </div>
        {sub && (
          <div
            style={{
              fontFamily: "var(--fb)",
              fontSize: "11px",
              color: "var(--mid)",
              marginTop: "4px",
            }}
          >
            {sub}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { data: snapshot, isLoading: loadingGraph } = useQuery({
    queryKey: ["graph-snapshot"],
    queryFn: () => fetchGraphSnapshot(),
  });

  const { data: riskData, isLoading: loadingRisk } = useQuery({
    queryKey: ["risk-scores"],
    queryFn: () => fetchRiskScores(10),
  });

  const { data: siloData, isLoading: loadingAlerts } = useQuery({
    queryKey: ["silo-alerts"],
    queryFn: fetchSiloAlerts,
  });

  const { data: communityData } = useQuery({
    queryKey: ["communities"],
    queryFn: () => fetchCommunities(),
  });

  const nodes = snapshot?.nodes ?? [];
  const edges = snapshot?.edges ?? [];
  const scores = riskData?.scores ?? [];
  const siloAlerts = siloData?.alerts ?? [];
  const criticalCount = scores.filter((s) => s.flag === "critical").length;
  const communityCount = communityData?.community_count ?? "—";
  const siloCount = communityData?.communities?.filter((c) => c.is_silo).length ?? "—";

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
          padding: "56px 48px 48px",
        }}
      >
        <h1
          style={{
            fontFamily: "var(--fd)",
            fontSize: "40px",
            fontWeight: 300,
            color: "var(--white)",
            margin: 0,
            lineHeight: 1.2,
          }}
        >
          Organizational{" "}
          <em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>
            Network Analysis
          </em>
        </h1>
        <p
          style={{
            fontFamily: "var(--fb)",
            fontSize: "14px",
            color: "rgba(255,255,255,.6)",
            margin: "12px 0 0",
            maxWidth: "560px",
          }}
        >
          Real-time collaboration graph · SPOF risk scoring · Silo detection
        </p>
        {snapshot?.snapshot_date && (
          <div
            style={{
              fontFamily: "var(--fb)",
              fontSize: "11px",
              letterSpacing: "2px",
              textTransform: "uppercase",
              color: "rgba(255,255,255,.35)",
              marginTop: "16px",
            }}
          >
            Snapshot · {snapshot.snapshot_date}
          </div>
        )}
      </div>

      <div style={{ padding: "0 48px 64px" }}>
        {/* KPI row */}
        <div style={{ display: "flex", gap: "16px", marginTop: "-24px", marginBottom: "32px" }}>
          <KpiCard
            value={snapshot?.node_count ?? "—"}
            label="Employees mapped"
            sub="active in graph"
          />
          <KpiCard
            value={snapshot?.edge_count ?? "—"}
            label="Collaboration edges"
            sub="weighted interactions"
          />
          <KpiCard
            value={communityCount}
            label="Communities"
            sub={`${siloCount} silo${siloCount !== 1 ? "s" : ""} detected`}
          />
          <KpiCard
            value={criticalCount}
            label="Critical nodes"
            sub="SPOF score > 0.75"
          />
        </div>

        {/* Silo alerts */}
        <div style={{ marginBottom: "24px" }}>
          <SiloAlert alerts={siloAlerts} loading={loadingAlerts} />
        </div>

        <hr className="section-divider" style={{ margin: "0 0 24px" }} />

        {/* Main content: graph + panel */}
        <div style={{ marginBottom: "16px" }}>
          <div className="eyebrow">Collaboration graph</div>
          <p
            style={{
              fontFamily: "var(--fb)",
              fontSize: "13px",
              color: "var(--mid)",
              margin: "4px 0 0",
            }}
          >
            Force-directed layout · Node size = betweenness centrality · Color = SPOF risk
          </p>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 360px",
            gap: "24px",
            height: "560px",
          }}
        >
          {/* Graph card */}
          <div
            style={{
              background: "var(--white)",
              borderRadius: "12px",
              boxShadow: "0 1px 4px rgba(0,0,0,.08)",
              overflow: "hidden",
            }}
          >
            {loadingGraph ? (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                  fontFamily: "var(--fb)",
                  fontSize: "13px",
                  color: "var(--mid)",
                }}
              >
                Building graph…
              </div>
            ) : (
              <OrgGraph nodes={nodes} edges={edges} />
            )}
          </div>

          {/* Critical node panel */}
          <CriticalNodePanel scores={scores} loading={loadingRisk} />
        </div>
      </div>

      {/* Footer */}
      <footer
        style={{
          background: "var(--primary)",
          padding: "20px 48px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--fd)",
            fontSize: "14px",
            fontWeight: 300,
            color: "var(--white)",
          }}
        >
          O<em style={{ color: "var(--gold-light)", fontStyle: "italic" }}>PB</em>
        </span>
        <span
          style={{
            fontFamily: "var(--fb)",
            fontSize: "9px",
            letterSpacing: "3px",
            textTransform: "uppercase",
            color: "rgba(255,255,255,.35)",
          }}
        >
          Org Synapse · ONA Platform · 2025
        </span>
      </footer>
    </div>
  );
}
