// Thin fetch wrapper. A 401 means the LAN PIN is needed; app.js shows the login.
export const onUnauthorized = { handler: null };

async function req(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) {
    onUnauthorized.handler && onUnauthorized.handler();
    throw new Error("login required");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || j.error || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  get: (u) => req("GET", u),
  post: (u, b) => req("POST", u, b || {}),
  put: (u, b) => req("PUT", u, b),
  del: (u) => req("DELETE", u),
  file: (slug, path) => `/files/${slug}/${path}`,
  job: (kind, payload, label) => req("POST", "/api/jobs", { kind, payload, label }),
};
