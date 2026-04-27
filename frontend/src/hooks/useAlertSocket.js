/**
 * useAlertSocket — WebSocket hook for real-time alert streaming.
 *
 * Connects to WS /alerts/live, handles reconnection with exponential backoff,
 * and exposes the latest alerts plus connection state to React components.
 *
 * Usage:
 *   const { alerts, isConnected, connectionCount } = useAlertSocket();
 *
 * Message types received from server:
 *   { type: "initial", alerts: [...], connection_count: N }  — on connect
 *   { type: "alert",   alerts: [...], source: "...", timestamp: "..." }  — new alert
 *   { type: "ping" }   — server keep-alive (hook responds "ping" text)
 *   { type: "pong" }   — server response to our ping (ignored)
 */

import { useCallback, useEffect, useRef, useState } from "react";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000")
  .replace(/^http/, "ws");            // http://... → ws://...

const WS_URL = `${BASE_URL}/alerts/live`;

const INITIAL_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;
const MAX_STORED_ALERTS = 100;

/**
 * @returns {{
 *   alerts: object[],
 *   isConnected: boolean,
 *   connectionCount: number,
 *   clearAlerts: () => void,
 * }}
 */
export function useAlertSocket() {
  const [alerts, setAlerts] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [connectionCount, setConnectionCount] = useState(0);

  const wsRef = useRef(null);
  const retryDelayRef = useRef(INITIAL_RETRY_MS);
  const retryTimerRef = useRef(null);
  const unmountedRef = useRef(false);

  const addAlerts = useCallback((incoming) => {
    if (!Array.isArray(incoming) || incoming.length === 0) return;
    setAlerts((prev) => {
      const combined = [...incoming, ...prev];
      return combined.slice(0, MAX_STORED_ALERTS);
    });
  }, []);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) { ws.close(); return; }
      setIsConnected(true);
      retryDelayRef.current = INITIAL_RETRY_MS; // reset backoff on success
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      switch (msg.type) {
        case "initial":
          addAlerts(msg.alerts ?? []);
          setConnectionCount(msg.connection_count ?? 0);
          break;
        case "alert":
          addAlerts(msg.alerts ?? []);
          break;
        case "ping":
          // Server keep-alive — respond so server knows we're alive
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
          break;
        default:
          break;
      }
    };

    ws.onerror = () => {
      // onclose fires after onerror — reconnect logic lives there
    };

    ws.onclose = () => {
      if (unmountedRef.current) return;
      setIsConnected(false);
      setConnectionCount(0);

      // Exponential backoff reconnect
      retryTimerRef.current = setTimeout(() => {
        retryDelayRef.current = Math.min(retryDelayRef.current * 2, MAX_RETRY_MS);
        connect();
      }, retryDelayRef.current);
    };
  }, [addAlerts]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      clearTimeout(retryTimerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // suppress reconnect on intentional close
        wsRef.current.close();
      }
    };
  }, [connect]);

  const clearAlerts = useCallback(() => setAlerts([]), []);

  return { alerts, isConnected, connectionCount, clearAlerts };
}
