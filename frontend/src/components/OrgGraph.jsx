import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Graph from "graphology";
import { SigmaContainer, useLoadGraph, useRegisterEvents } from "@react-sigma/core";
import { useWorkerLayoutForceAtlas2 } from "@react-sigma/layout-forceatlas2";
import "@react-sigma/core/lib/react-sigma.min.css";

// SPOF score → node color per BRAND.md spec
function spofColor(score) {
  if (score > 0.75) return "#E03448";
  if (score > 0.5)  return "#F07020";
  if (score > 0.25) return "#336699";
  return "#27B97C";
}

function spofSize(betweenness) {
  return 4 + betweenness * 20;
}

function GraphLoader({ nodes, edges }) {
  const loadGraph = useLoadGraph();
  const registerEvents = useRegisterEvents();
  const navigate = useNavigate();

  useEffect(() => {
    const graph = new Graph({ type: "directed", multi: false });

    nodes.forEach((n, idx) => {
      const angle = (idx / Math.max(nodes.length, 1)) * 2 * Math.PI;
      graph.addNode(n.employee_id, {
        label: n.name,
        size: spofSize(n.betweenness ?? 0),
        color: spofColor(n.spof_score ?? 0),
        x: Math.cos(angle) * 50,
        y: Math.sin(angle) * 50,
        employeeId: n.employee_id,
      });
    });

    edges.forEach((e) => {
      try {
        if (!graph.hasEdge(e.source, e.target)) {
          graph.addDirectedEdge(e.source, e.target, {
            size: Math.min(1 + (e.weight ?? 1) / 20, 4),
            color: "rgba(0,51,102,.15)",
          });
        }
      } catch {
        // skip duplicate or missing node edges
      }
    });

    loadGraph(graph);
  }, [nodes, edges, loadGraph]);

  useEffect(() => {
    registerEvents({
      clickNode({ node }) {
        navigate(`/employee/${node}`);
      },
    });
  }, [registerEvents, navigate]);

  return null;
}

// FA2Controller has no callbacks — its lifecycle is self-contained.
// The overlay in OrgGraph is driven by its own timer (6500ms), which is
// 500ms longer than the FA2 run (6000ms) so positions are fully frozen
// before the graph is revealed.
function FA2Controller() {
  const { start, stop } = useWorkerLayoutForceAtlas2({
    settings: {
      gravity: 8,
      scalingRatio: 1,
      linLogMode: true,
      slowDown: 3,
      barnesHutOptimize: true,
    },
  });

  useEffect(() => {
    start();
    const timer = setTimeout(() => stop(), 6000);
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [start, stop]);

  return null;
}

export default function OrgGraph({ nodes = [], edges = [], style }) {
  const [layoutDone, setLayoutDone] = useState(false);

  // graphId changes only when new data is loaded (different seed / navigation).
  // Using the first node's UUID is stable across re-renders and resets the
  // overlay exactly when the graph data actually changes.
  const graphId = nodes.length > 0 ? nodes[0].employee_id : null;

  useEffect(() => {
    if (!graphId) return;
    setLayoutDone(false);
    // 6500ms: 500ms after FA2Controller calls stop(), ensuring positions
    // are fully frozen before we remove the overlay and fade in.
    const timer = setTimeout(() => setLayoutDone(true), 6500);
    return () => clearTimeout(timer);
  }, [graphId]);

  if (!nodes.length) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--mid)",
          fontFamily: "var(--fb)",
          fontSize: "14px",
          ...style,
        }}
      >
        No graph data available.
      </div>
    );
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", ...style }}>
      {/* Sigma renders behind the overlay while FA2 runs, then fades in */}
      <SigmaContainer
        style={{
          width: "100%",
          height: "100%",
          background: "var(--white)",
          opacity: layoutDone ? 1 : 0,
          transition: layoutDone ? "opacity 0.5s ease" : "none",
        }}
        settings={{
          nodeProgramClasses: {},
          defaultEdgeType: "arrow",
          renderEdgeLabels: false,
          labelFont: "Plus Jakarta Sans",
          labelSize: 11,
          labelWeight: "500",
          labelColor: { color: "#1C1C2E" },
          minCameraRatio: 0.1,
          maxCameraRatio: 10,
        }}
      >
        <GraphLoader nodes={nodes} edges={edges} />
        <FA2Controller />
      </SigmaContainer>

      {/* Loading overlay — removed once layout settles */}
      {!layoutDone && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: "var(--white)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "12px",
            zIndex: 1,
          }}
        >
          <style>{`@keyframes fa2spin { to { transform: rotate(360deg); } }`}</style>
          <div
            style={{
              width: "28px",
              height: "28px",
              border: "2px solid var(--primary-10)",
              borderTop: "2px solid var(--primary)",
              borderRadius: "50%",
              animation: "fa2spin 0.8s linear infinite",
            }}
          />
          <span
            style={{
              fontFamily: "var(--fb)",
              fontSize: "12px",
              color: "var(--mid)",
              letterSpacing: "1px",
            }}
          >
            Calculating layout…
          </span>
        </div>
      )}

      {/* Legend */}
      <div
        style={{
          position: "absolute",
          bottom: "16px",
          right: "16px",
          background: "rgba(255,255,255,.92)",
          backdropFilter: "blur(8px)",
          borderRadius: "8px",
          padding: "12px 16px",
          border: "1px solid var(--primary-10)",
          display: "flex",
          flexDirection: "column",
          gap: "6px",
          zIndex: 2,
        }}
      >
        <span
          style={{
            fontFamily: "var(--fb)",
            fontSize: "9px",
            letterSpacing: "3px",
            textTransform: "uppercase",
            color: "var(--gold)",
            marginBottom: "4px",
          }}
        >
          SPOF Risk
        </span>
        {[
          { color: "#27B97C", label: "Low  (< 0.25)" },
          { color: "#336699", label: "Moderate (0.25–0.5)" },
          { color: "#F07020", label: "High  (0.5–0.75)" },
          { color: "#E03448", label: "Critical  (> 0.75)" },
        ].map(({ color, label }) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span
              style={{
                width: "10px",
                height: "10px",
                borderRadius: "50%",
                background: color,
                flexShrink: 0,
              }}
            />
            <span style={{ fontFamily: "var(--fb)", fontSize: "11px", color: "var(--dark)" }}>
              {label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
