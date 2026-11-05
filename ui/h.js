import { h } from "./vendor/preact.js";
import htm from "./vendor/htm.js";
export const html = htm.bind(h);
export function nav(path) { location.hash = "#/" + path; }
export const fmt = {
  pct: (x) => Math.round((x || 0) * 100) + "%",
  ago: (t) => { if (!t) return ""; const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`; },
};
