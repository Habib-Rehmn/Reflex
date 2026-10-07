"use strict";
const element = id => document.getElementById(id);
const number = value => value === null || value === undefined ? "Unknown" : Number(value).toLocaleString();
const latency = value => value === null || value === undefined ? "Unknown" : `${Number(value).toFixed(2)} ms`;
async function request(url, options = {}) {
  const response = await fetch(url, {...options, signal: AbortSignal.timeout(125000)});
  const body = await response.json();
  if (!response.ok) throw new Error(`${body.error || "Request failed"} (HTTP ${response.status})`);
  return body;
}
async function refresh() {
  element("refresh").disabled = true;
  element("status").textContent = "Loading activity…";
  try {
    const [metrics, history] = await Promise.all([request("/metrics"), request("/requests?limit=20")]);
    element("total").textContent = number(metrics.total_requests);
    element("local").textContent = number(metrics.routes.fast_path || 0);
    element("provider").textContent = number(metrics.provider_attempts);
    element("failed").textContent = number(metrics.failed_requests);
    element("latency").textContent = `${latency(metrics.average_latency_ms)} / ${latency(metrics.p95_latency_ms)}`;
    element("cost").textContent = `$${metrics.known_estimated_cost_usd}`;
    element("unknown-cost").textContent = `${number(metrics.unknown_cost_requests)} requests with unknown cost`;
    element("routes").textContent = `Blocked: ${number(metrics.routes.block || 0)} · Held for review: ${number(metrics.routes.review || 0)} · Retention: ${metrics.retention_days} days`;
    const rows = element("history-rows");
    rows.replaceChildren();
    for (const item of history.requests) {
      const row = document.createElement("tr");
      for (const value of [new Date(item.created_at).toLocaleString(), item.route || "Unrouted", item.status_code, latency(item.latency_ms), item.model || "—"]) {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.append(cell);
      }
      rows.append(row);
    }
    element("status").textContent = metrics.total_requests ? `Updated ${new Date().toLocaleTimeString()}` : "No requests yet. Try a local command below.";
    element("status").className = "";
  } catch (error) {
    element("status").textContent = `Could not refresh activity. Displayed values may be stale. ${error.message}`;
    element("status").className = "error";
  } finally { element("refresh").disabled = false; }
}
element("refresh").addEventListener("click", refresh);
element("query-form").addEventListener("submit", async event => {
  event.preventDefault();
  const prompt = element("prompt").value.trim();
  if (!prompt) { element("query-status").textContent = "Enter a prompt first."; return; }
  element("submit").disabled = true;
  element("answer").textContent = "";
  element("query-status").textContent = "Processing…";
  try {
    const body = await request("/query", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({prompt})});
    element("answer").textContent = body.answer;
    element("query-status").textContent = `${body.route} · ${latency(body.latency_ms)}${body.metadata_persisted ? "" : " · Metadata was not saved"}`;
  } catch (error) { element("query-status").textContent = error.message; }
  finally { element("submit").disabled = false; await refresh(); }
});
refresh();
