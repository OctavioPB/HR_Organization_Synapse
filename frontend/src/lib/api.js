import axios from "axios";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const client = axios.create({ baseURL: BASE });

// ─── Graph ────────────────────────────────────────────────────────────────────

export async function fetchGraphSnapshot(date = null) {
  const params = date ? { date } : {};
  const { data } = await client.get("/graph/snapshot", { params });
  return data;
}

export async function fetchEgoNetwork(employeeId, date = null) {
  const params = date ? { date } : {};
  const { data } = await client.get(`/graph/employee/${employeeId}`, { params });
  return data;
}

export async function fetchCommunities(date = null) {
  const params = date ? { date } : {};
  const { data } = await client.get("/graph/communities", { params });
  return data;
}

// ─── Risk ─────────────────────────────────────────────────────────────────────

export async function fetchRiskScores(top = 50, date = null) {
  const params = { top, ...(date ? { date } : {}) };
  const { data } = await client.get("/risk/scores", { params });
  return data;
}

export async function fetchCriticalNodes(threshold = 0.7) {
  const { data } = await client.get("/risk/critical-nodes", { params: { threshold } });
  return data;
}

export async function fetchEmployeeRiskHistory(employeeId, days = 30) {
  const { data } = await client.get(`/risk/employee/${employeeId}/history`, { params: { days } });
  return data;
}

export async function simulateRemoval(removeEmployeeId, windowDays = 30) {
  const { data } = await client.post("/risk/simulate", {
    remove_employee_id: removeEmployeeId,
    window_days: windowDays,
  });
  return data;
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export async function fetchSiloAlerts() {
  const { data } = await client.get("/alerts/silos");
  return data;
}

export async function fetchEntropyAlerts() {
  const { data } = await client.get("/alerts/entropy");
  return data;
}

export async function fetchAlertHistory(days = 30) {
  const { data } = await client.get("/alerts/history", { params: { days } });
  return data;
}
