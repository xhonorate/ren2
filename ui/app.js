import { render } from "./vendor/preact.js";
import { useEffect, useState } from "./vendor/hooks.js";
import { html } from "./h.js";
import { api, onUnauthorized } from "./api.js";
import { Projects, Board, SceneView, Cast, Character, CGs, Voice, Jobs, Setup } from "./views.js";

// ---- routing: #/p/<slug>/scene/<id> etc.
function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return parts;
}

function App() {
  const [route, setRoute] = useState(parseHash());
  const [needPin, setNeedPin] = useState(false);
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [jobsBadge, setJobsBadge] = useState(0);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    const f = () => setRoute(parseHash());
    addEventListener("hashchange", f);
    onUnauthorized.handler = () => setNeedPin(true);
    api.get("/api/session").then(s => { if (s.pin_required && !s.authed) setNeedPin(true); }).catch(() => {});
    return () => removeEventListener("hashchange", f);
  }, []);
  useEffect(() => {
    let alive = true;
    const tick = () => api.get("/api/jobs").then(js => { if (alive) setJobsBadge(js.filter(j => j.status === "running" || j.status === "queued").length); }).catch(() => {});
    tick(); const t = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  useEffect(() => {
    window.toast = (msg, kind = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };
  }, []);

  async function login(e) {
    e.preventDefault();
    try { await api.post("/api/login", { pin }); setNeedPin(false); setErr(""); location.reload(); }
    catch (ex) { setErr(ex.message); }
  }

  if (needPin) return html`
    <div class="login"><form onSubmit=${login}>
      <h1>vn console</h1>
      <p>Enter the PIN shown in the runner's terminal.</p>
      <input type="password" inputmode="numeric" autofocus value=${pin} onInput=${e => setPin(e.target.value)} placeholder="PIN" />
      <button class="primary" type="submit">Open</button>
      ${err && html`<p class="err">${err}</p>`}
    </form></div>`;

  const [a, slug, b, c] = route;
  let page, title = "vn console";
  const inProject = a === "p" && slug;
  if (!a) page = html`<${Projects} />`;
  else if (a === "jobs") { page = html`<${Jobs} />`; title = "Jobs"; }
  else if (a === "setup") { page = html`<${Setup} />`; title = "Setup"; }
  else if (inProject && !b) { page = html`<${Board} slug=${slug} />`; title = slug; }
  else if (inProject && b === "scene" && c) { page = html`<${SceneView} slug=${slug} id=${c} key=${c} />`; title = c; }
  else if (inProject && b === "cast" && c) { page = html`<${Character} slug=${slug} cid=${c} key=${c} />`; title = c; }
  else if (inProject && b === "cast") { page = html`<${Cast} slug=${slug} />`; title = "Cast"; }
  else if (inProject && b === "cgs") { page = html`<${CGs} slug=${slug} />`; title = "CGs"; }
  else if (inProject && b === "voice") { page = html`<${Voice} slug=${slug} />`; title = "Voice"; }
  else page = html`<p>Not found.</p>`;

  const tab = (path, label, active) => html`<a class=${"tab" + (active ? " active" : "")} href=${"#/" + path}>${label}</a>`;
  const nav = inProject ? [
    tab(`p/${slug}`, "Board", !b),
    tab(`p/${slug}/cast`, "Cast", b === "cast"),
    tab(`p/${slug}/cgs`, "CGs", b === "cgs"),
    tab(`p/${slug}/voice`, "Voice", b === "voice"),
    tab("jobs", jobsBadge ? `Jobs (${jobsBadge})` : "Jobs", false),
    tab("setup", "Setup", false),
  ] : [tab("", "Projects", !a), tab("jobs", jobsBadge ? `Jobs (${jobsBadge})` : "Jobs", a === "jobs"), tab("setup", "Setup", a === "setup")];

  return html`
    <div class="shell">
      <header class="top">
        <a class="brand" href="#/">vn</a>
        <span class="title">${title}</span>
        ${inProject && html`<a class="tab small" href="#/">all projects</a>`}
      </header>
      <nav class="nav">${nav}</nav>
      <main class="main">${page}</main>
      ${toast && html`<div class=${"toast " + toast.kind}>${toast.msg}</div>`}
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
