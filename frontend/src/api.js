// Thin API client. 409 responses carry the stale-revision conflict message.
async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error(data?.detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  listTickets: (status) =>
    request(`/api/tickets${status ? `?status=${status}` : ""}`),
  getTicket: (id) => request(`/api/tickets/${id}`),
  createTicket: (payload) =>
    request("/api/tickets", { method: "POST", body: JSON.stringify(payload) }),
  setStatus: (id, payload) =>
    request(`/api/tickets/${id}/status`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listPolicies: () => request("/api/policies"),
  createPolicy: (payload) =>
    request("/api/policies", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  previewMigration: (id, payload) =>
    request(`/api/tickets/${id}/migrate/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  applyMigration: (id, payload) =>
    request(`/api/tickets/${id}/migrate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  migrations: (id) => request(`/api/tickets/${id}/migrations`),
  scan: (payload) =>
    request("/api/admin/scan", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
