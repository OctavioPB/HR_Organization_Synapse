import { useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import Graph from "graphology";
import { SigmaContainer, useLoadGraph, useRegisterEvents } from "@react-sigma/core";
import { useWorkerLayoutForceAtlas2 } from "@react-sigma/layout-forceatlas2";
import "@react-sigma/core/lib/style.css";

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

function GraphLoader({ nodes, edges, onNodeClick }) {
  const loadGraph = useLoadGraph();
  const registerEvents = useRegisterEvents();
  const navigate = useNavigate();

  useEffect(() => {
    const graph = new Graph({ type: "directed", multi: false });

    nodes.forEach((n) => {
      graph.addNode(n.employee_id, {
        label: n.name,
        size: spofSize(n.betweenness ?? 0),
        color: spofColor(n.spof_score ?? 0),
        x: Math.random() * 200 - 100,
        y: Math.random() * 200 - 100,
        // store for click handler
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

function FA2Controller() {
  const { start, stop, isRunning } = useWorkerLayoutForceAtlas2({
    settings: { gravity: 1, scalingRatio: 2, slowDown: 5 },
  });

  useEffect(() => {
    start();
    const timer = setTimeout(() => stop(), 3000);
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [start, stop]);

  return null;
}

export default function OrgGraph({ nodes = [], edges = [], style }) {
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
      <SigmaContainer
        style={{ width: "100%", height: "100%", background: "var(--white)" }}
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
          <div
            key={label}
            style={{ display: "flex", alignItems: "center", gap: "8px" }}
          >
            <span
              style={{
                width: "10px",
                height: "10px",
                borderRadius: "50%",
                background: color,
                flexShrink: 0,
              }}
            />
            <span
              style={{
                fontFamily: "var(--fb)",
                fontSize: "11px",
                color: "var(--dark)",
              }}
            >
              {label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
