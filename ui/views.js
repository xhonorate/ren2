import { useEffect, useRef, useState } from "./vendor/hooks.js";
import { html, nav, fmt } from "./h.js";
import { api } from "./api.js";

// ---------------------------------------------------------------- helpers
function useLoad(fn, deps) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { let ok = true; setErr(null); fn().then(d => ok && setData(d)).catch(e => ok && setErr(e.message)); return () => { ok = false; }; }, [...deps, tick]);
  return [data, err, () => setTick(t => t + 1)];
}
const Err = ({ e }) => e ? html`<p class="err">${e}</p>` : null;
const Spinner = () => html`<p class="muted">Loading…</p>`;
const Chip = ({ kind, children }) => html`<span class=${"chip " + (kind || "")}>${children}</span>`;
function Missing({ m }) {
  if (!m) return null;
  const parts = [["sprites", m.sprites], ["bg", m.backgrounds], ["cg", m.cgs], ["voice", m.lines]].filter(x => x[1]);
  return parts.length ? parts.map(([k, v]) => html`<${Chip} kind="warn">${v} ${k}</${Chip}>`) : html`<${Chip} kind="ok">assets ready</${Chip}>`;
}
async function run(kind, payload, label) {
  try { const j = await api.job(kind, payload, label); window.toast(`Queued: ${j.label}`); return j; }
  catch (e) { window.toast(e.message, "bad"); }
}
function Img({ slug, path, onClick, cls }) {
  return html`<img class=${"thumb " + (cls || "")} loading="lazy" src=${api.file(slug, path)} alt=${path} onClick=${onClick} />`;
}
function Audio({ slug, path }) {
  return html`<audio controls preload="none" src=${api.file(slug, path)}></audio>`;
}
function Modal({ title, onClose, children }) {
  return html`<div class="modal-bg" onClick=${onClose}><div class="modal" onClick=${e => e.stopPropagation()}>
    <div class="modal-head"><b>${title}</b><button class="ghost" onClick=${onClose}>✕</button></div>${children}</div></div>`;
}

// ---------------------------------------------------------------- projects
export function Projects() {
  const [ps, err] = useLoad(() => api.get("/api/projects"), []);
  if (err) return html`<${Err} e=${err} />`;
  if (!ps) return html`<${Spinner} />`;
  return html`<div class="cards">${ps.map(p => html`
    <a class="card" href=${"#/p/" + p.slug}>
      <h3>${p.title || p.slug}</h3>
      ${p.error ? html`<p class="err">${p.error}</p>` : html`<p class="muted">${p.written}/${p.scenes} scenes written · ${p.rating} · ${p.characters.length} characters</p><p class="muted small">${p.path}</p>`}
    </a>`)}</div>`;
}

// ---------------------------------------------------------------- board
export function Board({ slug }) {
  const [p, err, reload] = useLoad(() => api.get(`/api/projects/${slug}`), [slug]);
  const [route, setRoute] = useState("all");
  const [only, setOnly] = useState("all");
  const [queueOpen, setQueueOpen] = useState(false);
  const [val, setVal] = useState(null);
  if (err) return html`<${Err} e=${err} />`;
  if (!p) return html`<${Spinner} />`;
  const routes = ["all", "common", ...Object.keys(p.characters)];
  let rows = p.board.filter(r => route === "all" || r.route === route);
  if (only === "unwritten") rows = rows.filter(r => !r.written);
  if (only === "notes") rows = rows.filter(r => r.notes);
  if (only === "missing") rows = rows.filter(r => r.missing && Object.values(r.missing).some(x => x));
  const written = p.board.filter(r => r.written).length;
  async function validate() { try { setVal(await api.post(`/api/projects/${slug}/validate`)); } catch (e) { window.toast(e.message, "bad"); } }
  return html`
    <div class="toolbar">
      <span><b>${written}</b>/${p.board.length} written · ${p.rating} · ${p.branching}</span>
      <span class="grow"></span>
      <button onClick=${validate}>Validate</button>
      <button onClick=${() => run("build", { project: slug }, `build ${slug}`)}>Build</button>
      <button onClick=${() => run("launch", { project: slug }, `launch ${slug}`)}>Launch</button>
      <button class="primary" onClick=${() => setQueueOpen(true)}>Queue missing…</button>
    </div>
    ${val && html`<div class=${"panel " + (val.valid ? "ok" : "bad")}>${val.valid ? "Project is valid." : html`<b>${val.errors.length} problem(s)</b><ul>${val.errors.map(e => html`<li>${e}</li>`)}</ul>`}</div>`}
    <div class="toolbar wrap">
      <select value=${route} onChange=${e => setRoute(e.target.value)}>${routes.map(r => html`<option value=${r}>${r}</option>`)}</select>
      <select value=${only} onChange=${e => setOnly(e.target.value)}>
        <option value="all">everything</option><option value="unwritten">unwritten</option><option value="missing">missing assets</option><option value="notes">with notes</option>
      </select>
    </div>
    <div class="rows">${rows.map(r => html`
      <a class=${"row " + (r.written ? "" : "dim")} href=${`#/p/${slug}/scene/${r.id}`}>
        <div class="row-main"><span class="mono">${r.id}</span> <b>${r.title}</b>
          <div class="muted small">${r.route} · ${r.location}, ${r.time} · level ${r.intimacy} · ${r.rating}</div></div>
        <div class="row-side">
          ${r.written ? html`<${Chip} kind="ok">written</${Chip}>` : html`<${Chip}>outline</${Chip}>`}
          ${r.notes ? html`<${Chip} kind="note">${r.notes} note${r.notes > 1 ? "s" : ""}</${Chip}>` : null}
          <${Missing} m=${r.missing} />
          ${r.error && html`<${Chip} kind="bad">error</${Chip}>`}
        </div>
      </a>`)}</div>
    ${queueOpen && html`<${QueueMissing} slug=${slug} rows=${rows} onClose=${() => setQueueOpen(false)} />`}`;
}

function QueueMissing({ slug, rows, onClose }) {
  const [kinds, setKinds] = useState({ backgrounds: true, sprites: true, cgs: true, lines: true });
  const [scope, setScope] = useState("filtered");
  async function go() {
    const scenes = scope === "filtered" ? rows.filter(r => r.written).map(r => r.id) : null;
    try { const r = await api.post(`/api/projects/${slug}/queue-missing`, { kinds: Object.keys(kinds).filter(k => kinds[k]), scenes }); window.toast(`${r.jobs.length} job(s) queued`); onClose(); nav("jobs"); }
    catch (e) { window.toast(e.message, "bad"); }
  }
  return html`<${Modal} title="Queue missing assets" onClose=${onClose}>
    <p class="muted">One overnight batch: background plates once per location/time, a sprite sheet per character, four candidates per CG, best-of-N voice takes per line.</p>
    ${Object.keys(kinds).map(k => html`<label class="check"><input type="checkbox" checked=${kinds[k]} onChange=${e => setKinds({ ...kinds, [k]: e.target.checked })} /> ${k}</label>`)}
    <label class="check"><input type="radio" checked=${scope === "filtered"} onChange=${() => setScope("filtered")} /> the ${rows.filter(r => r.written).length} written scene(s) in the current filter</label>
    <label class="check"><input type="radio" checked=${scope === "all"} onChange=${() => setScope("all")} /> every written scene</label>
    <div class="actions"><button class="primary" onClick=${go}>Queue</button></div>
  </${Modal}>`;
}

// ---------------------------------------------------------------- scene reader
export function SceneView({ slug, id }) {
  const [s, err, reload] = useLoad(() => api.get(`/api/projects/${slug}/scenes/${id}`), [slug, id]);
  const [proj] = useLoad(() => api.get(`/api/projects/${slug}`), [slug]);
  const [noteAt, setNoteAt] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [mode, setMode] = useState("read");
  const [yamlText, setYamlText] = useState(null);
  const [showVoice, setShowVoice] = useState(false);
  if (err) return html`<${Err} e=${err} />`;
  if (!s) return html`<${Spinner} />`;
  const colors = proj ? Object.fromEntries(Object.entries(proj.characters).map(([k, v]) => [k, v.color || "#888"])) : {};
  const names = proj ? Object.fromEntries(Object.entries(proj.characters).map(([k, v]) => [k, v.name])) : {};
  const name = (cid) => cid === "player" ? "You" : (names[cid] || cid);

  async function addNote() {
    if (!noteText.trim()) return;
    try { await api.post(`/api/projects/${slug}/scenes/${id}/notes`, { step: noteAt.i, kind: noteAt.kind, quote: noteAt.text || noteAt.note || "", note: noteText }); setNoteText(""); setNoteAt(null); reload(); window.toast("Note saved"); }
    catch (e) { window.toast(e.message, "bad"); }
  }
  async function delNote(nid) { await api.del(`/api/projects/${slug}/scenes/${id}/notes/${nid}`); reload(); }
  async function loadYaml() { const r = await api.get(`/api/projects/${slug}/scenes/${id}/yaml`); setYamlText(r.yaml); setMode("yaml"); }
  async function saveYaml() {
    try { const r = await api.put(`/api/projects/${slug}/scenes/${id}/yaml`, { yaml: yamlText }); window.toast(r.errors.length ? `Saved with ${r.errors.length} validation error(s)` : "Saved and valid", r.errors.length ? "bad" : "ok"); reload(); }
    catch (e) { window.toast(e.message, "bad"); }
  }
  const lines = s.assets ? Object.fromEntries(s.assets.lines.map(l => [l.n, l])) : {};
  const cgs = s.assets ? Object.fromEntries(s.assets.cgs.map(c => [c.id, c])) : {};
  const pending = (s.notes || []).filter(n => !n.applied);

  const header = html`
    <div class="scene-head">
      <div class="scene-nav">
        ${s.prev ? html`<a href=${`#/p/${slug}/scene/${s.prev}`}>‹ ${s.prev}</a>` : html`<span></span>`}
        <a href=${`#/p/${slug}`} class="muted">board</a>
        ${s.next ? html`<a href=${`#/p/${slug}/scene/${s.next}`}>${s.next} ›</a>` : html`<span></span>`}
      </div>
      <h2><span class="mono">${id}</span> ${s.title || s.outline.title}</h2>
      <div class="muted small">${s.outline.route} · ${s.outline.location}, ${s.outline.time} · level ${s.outline.intimacy} · ${s.outline.rating}${s.present ? " · present: " + s.present.map(name).join(", ") : ""}</div>
      ${s.errors && s.errors.length ? html`<div class="panel bad"><b>${s.errors.length} validation error(s)</b><ul>${s.errors.map(e => html`<li>${e}</li>`)}</ul></div>` : null}
    </div>
    <div class="toolbar wrap">
      <button class=${mode === "read" ? "active" : ""} onClick=${() => setMode("read")}>Read</button>
      <button class=${mode === "outline" ? "active" : ""} onClick=${() => setMode("outline")}>Outline</button>
      ${s.written && html`<button class=${mode === "yaml" ? "active" : ""} onClick=${loadYaml}>YAML</button>`}
      <span class="grow"></span>
      ${s.written && html`<button onClick=${() => setShowVoice(v => !v)}>${showVoice ? "Hide voice" : "Voice"}</button>`}
      ${s.written && html`<button onClick=${() => run("tts_line", { project: slug, scene: id }, `voice ${id}`)}>Voice lines</button>`}
      ${s.written && s.assets && s.assets.cgs.length ? html`<button onClick=${() => s.assets.cgs.forEach(c => run("cg", { project: slug, cg: c.id }, `cg ${c.id}`))}>Render CGs</button>` : null}
      ${s.written && html`<button onClick=${() => run("build", { project: slug, start: id }, `preview ${id}`)}>Build at this scene</button>`}
      ${s.written && html`<button class="primary" disabled=${!pending.length} onClick=${() => run("revise", { project: slug, scene: id }, `revise ${id}`)}>Revise with Claude${pending.length ? ` (${pending.length})` : ""}</button>`}
    </div>`;

  if (mode === "outline" || !s.written) return html`${header}
    <div class="panel">
      ${!s.written && html`<p class="muted">Not written yet. This is the outline entry the writer will work from.</p>`}
      <p><b>Goal.</b> ${s.outline.goal}</p>
      <ol>${(s.outline.beats || []).map(b => html`<li>${b}</li>`)}</ol>
      ${s.outline.cg && s.outline.cg.length ? html`<p class="muted">CG: ${s.outline.cg.join("; ")}</p>` : null}
      ${s.outline.direction && html`<p><b>Direction.</b> ${s.outline.direction}</p>`}
    </div>`;

  if (mode === "yaml") return html`${header}
    <textarea class="yaml" value=${yamlText || ""} onInput=${e => setYamlText(e.target.value)} spellcheck="false"></textarea>
    <div class="actions"><button class="primary" onClick=${saveYaml}>Save</button> <span class="muted small">A backup goes to review/backup/ first.</span></div>`;

  const items = s.items || [];
  return html`${header}
    <div class="script">
      ${items.map(it => {
        const ind = { marginLeft: (it.depth * 14) + "px" };
        const flag = html`<button class="ghost flag" title="Add a note on this line" onClick=${() => { setNoteAt(it); setNoteText(""); }}>✎</button>`;
        const noteHere = (s.notes || []).filter(n => n.step === it.i && !n.applied);
        const badge = noteHere.length ? html`<span class="note-dot" title=${noteHere.map(n => n.note).join("\n")}>${noteHere.length}</span>` : null;
        let body;
        switch (it.kind) {
          case "narr": body = html`<div class="narr" style=${ind}>${it.text}${badge}${flag}</div>`; break;
          case "say": {
            const l = it.line && lines[it.line];
            body = html`<div class="say" style=${ind}>
              <span class="speaker" style=${{ color: colors[it.speaker] || "inherit" }}>${name(it.speaker)}</span>
              <span class="tags">${[it.expr, it.pose, it.delivery].filter(Boolean).join(" · ")}</span>
              <div class="text">${it.text}${badge}${flag}</div>
              ${showVoice && it.line ? html`<${LineVoice} slug=${slug} sid=${id} n=${it.line} l=${l} />` : null}
            </div>`; break;
          }
          case "choice": body = html`<div class="choice" style=${ind}>Choice — ${it.prompt}${flag}</div>`; break;
          case "option": body = html`<div class="option" style=${ind}>▸ <b>${it.text}</b> ${it.effects && it.effects.affection ? html`<span class="tags">${Object.entries(it.effects.affection).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(", ")}</span>` : null}${it.goto ? html`<span class="tags">→ ${it.goto}</span>` : null}${it.requires ? html`<span class="tags">[${it.requires}]</span>` : null}${flag}</div>`; break;
          case "cg": {
            const g = cgs[it.id];
            body = html`<div class="cgcard" style=${ind}>
              <div><b>CG ${it.id}</b> <span class="tags">${it.shot} · ${it.lighting} · ${(it.characters || []).map(c => `${c.id} ${c.pose}/${c.expr}${c.outfit ? "/" + c.outfit : ""}`).join("; ")}</span>${flag}</div>
              <div class="muted small">${it.note}</div>
              ${g && g.present ? html`<${Img} slug=${slug} path=${g.file} cls="wide" />` : g && g.candidates.length ? html`<div class="muted small">${g.candidates.length} candidate(s) — <a href=${`#/p/${slug}/cgs`}>review</a></div>` : html`<div class="muted small">no image yet — <a onClick=${() => run("cg", { project: slug, cg: it.id }, `cg ${it.id}`)} href="#">render candidates</a></div>`}
            </div>`; break;
          }
          case "cgend": body = html`<div class="meta" style=${ind}>[CG ends]</div>`; break;
          case "stage": body = html`<div class="meta" style=${ind}>[stage: ${[it.location, it.time, it.present && "present=" + it.present.join(","), it.outfits && Object.entries(it.outfits).map(([k, v]) => `${k}:${v}`).join(",")].filter(Boolean).join(" ")}]</div>`; break;
          case "enter": body = html`<div class="meta" style=${ind}>[enter ${name(it.char)}]</div>`; break;
          case "exit": body = html`<div class="meta" style=${ind}>[exit ${name(it.char)}]</div>`; break;
          case "if": body = html`<div class="meta" style=${ind}>[if ${it.cond}]</div>`; break;
          case "else": body = html`<div class="meta" style=${ind}>[else]</div>`; break;
          case "setflags": body = html`<div class="meta" style=${ind}>[set ${Object.entries(it.values).map(([k, v]) => `${k}=${v}`).join(", ")}]</div>`; break;
          case "affection": body = html`<div class="meta" style=${ind}>[affection ${Object.entries(it.deltas).map(([k, v]) => `${k} ${v}`).join(", ")}]</div>`; break;
          case "nsfw": body = html`<div class="nsfw" style=${ind}>[explicit section ${it.ref}: not filled] ${s.briefs.filter(b => b.startsWith(it.ref)).map(b => html`<${BriefLink} slug=${slug} sid=${id} name=${b} />`)}${flag}</div>`; break;
          case "jump": case "call": body = html`<div class="meta" style=${ind}>[${it.kind} ${it.target}]</div>`; break;
          default: body = html`<div class="meta" style=${ind}>[${it.kind}]</div>`;
        }
        return html`<div key=${it.i}>${body}${noteAt && noteAt.i === it.i ? html`
          <div class="note-box" style=${ind}>
            <textarea autofocus rows="3" placeholder="What should change here? (Claude applies these with Revise.)" value=${noteText} onInput=${e => setNoteText(e.target.value)}></textarea>
            <div class="actions"><button class="primary" onClick=${addNote}>Save note</button><button class="ghost" onClick=${() => setNoteAt(null)}>Cancel</button></div>
          </div>` : null}</div>`;
      })}
    </div>
    <section class="notes">
      <h3>Notes ${pending.length ? html`<span class="muted">(${pending.length} to apply)</span>` : ""}</h3>
      ${!(s.notes || []).length && html`<p class="muted">Tap ✎ on any line to leave a note. "Revise with Claude" applies them.</p>`}
      ${(s.notes || []).map(n => html`<div class=${"note " + (n.applied ? "applied" : "")}>
        <div class="muted small">step ${n.step} · ${n.created}${n.applied ? " · applied" : ""}</div>
        ${n.quote && html`<div class="quote">“${n.quote}”</div>`}
        <div>${n.note}</div>
        ${!n.applied && html`<button class="ghost small" onClick=${() => delNote(n.id)}>delete</button>`}
      </div>`)}
    </section>`;
}

function BriefLink({ slug, sid, name }) {
  const [open, setOpen] = useState(false);
  const [md, setMd] = useState("");
  async function toggle(e) { e.preventDefault(); if (!open) { const r = await api.get(`/api/projects/${slug}/scenes/${sid}/brief/${name}`); setMd(r.markdown); } setOpen(!open); }
  return html`<a href="#" onClick=${toggle}>${open ? "hide brief" : "brief"}</a>${open ? html`<pre class="brief">${md}</pre>` : null}`;
}

function LineVoice({ slug, sid, n, l }) {
  const [open, setOpen] = useState(false);
  const [t, setT] = useState(null);
  async function load() { setOpen(o => !o); if (!t) setT(await api.get(`/api/projects/${slug}/voice/${sid}/${n}`)); }
  async function pick(file) { await api.post(`/api/projects/${slug}/voice/${sid}/${n}/pick`, { file }); window.toast("Take picked"); }
  return html`<div class="linevoice">
    ${l && l.present ? html`<${Audio} slug=${slug} path=${l.file} />` : html`<span class="muted small">no voice yet</span>`}
    <button class="ghost small" onClick=${load}>${open ? "hide takes" : "takes"}</button>
    <button class="ghost small" onClick=${() => run("tts_line", { project: slug, scene: sid, lines: [n] }, `voice ${sid} line ${n}`)}>re-render</button>
    ${open && t && html`<div class="takes">${(t.takes || []).length ? t.takes.map(k => html`<div class="take">
        <${Audio} slug=${slug} path=${k.file} /><span class="tags">${k.score != null ? "score " + k.score : ""}${k.wer != null ? " · wer " + k.wer.toFixed(2) : ""}${k.transcript ? " · “" + k.transcript + "”" : ""}</span>
        <button class="small" onClick=${() => pick(k.file)}>use</button></div>`) : html`<span class="muted small">no takes rendered</span>`}</div>`}
  </div>`;
}

// ---------------------------------------------------------------- cast
export function Cast({ slug }) {
  const [p, err] = useLoad(() => api.get(`/api/projects/${slug}`), [slug]);
  if (err) return html`<${Err} e=${err} />`;
  if (!p) return html`<${Spinner} />`;
  return html`<div class="cards">${Object.entries(p.characters).map(([cid, c]) => html`
    <a class="card" href=${`#/p/${slug}/cast/${cid}`} style=${{ borderLeft: "4px solid " + (c.color || "#888") }}>
      <h3>${c.name}</h3><p class="muted">${c.outfits.join(", ")}</p>
      <p class="muted small">${c.lora ? "LoRA: " + c.lora.file : "no LoRA yet"}</p>
    </a>`)}</div>`;
}

export function Character({ slug, cid }) {
  const [c, err, reload] = useLoad(() => api.get(`/api/projects/${slug}/characters/${cid}`), [slug, cid]);
  const [proj] = useLoad(() => api.get(`/api/projects/${slug}`), [slug]);
  const [tab, setTab] = useState("art");
  const [lora, setLora] = useState(false);
  const [count, setCount] = useState(12);
  if (err) return html`<${Err} e=${err} />`;
  if (!c) return html`<${Spinner} />`;
  const a = c.appearance;
  async function cull(file, keep) { await api.post(`/api/projects/${slug}/characters/${cid}/cull`, { file, keep }); reload(); }
  async function verdict(item, v, regenerate) { await api.post(`/api/projects/${slug}/characters/${cid}/sprite-verdict`, { file: item.file, item, verdict: v, regenerate }); window.toast(v === "fail" ? (regenerate ? "Rejected; re-render queued" : "Rejected") : "Kept"); reload(); }
  const deliveries = proj ? proj.vocab.deliveries : [];
  const refs = (c.voice && c.voice.reference) || {};
  return html`
    <h2 style=${{ color: a.color || "inherit" }}>${a.display_name} <span class="muted small">${cid}</span></h2>
    <div class="toolbar wrap">
      ${["art", "sprites", "voice", "sheet"].map(t => html`<button class=${tab === t ? "active" : ""} onClick=${() => setTab(t)}>${t}</button>`)}
    </div>
    ${tab === "sheet" && html`<pre class="sheet">${c.sheet}</pre><h3>Appearance</h3><pre class="sheet">${JSON.stringify(a, null, 1)}</pre>`}
    ${tab === "art" && html`
      <div class="toolbar wrap">
        <label>count <input type="number" min="1" max="48" value=${count} onInput=${e => setCount(+e.target.value)} style=${{ width: "4em" }} /></label>
        <button onClick=${() => run("char_candidates", { project: slug, char: cid, count }, `candidates ${cid}`)}>Generate candidates</button>
        <button class="primary" disabled=${c.canon.length < 8} title=${c.canon.length < 8 ? "needs 8+ canon images" : ""} onClick=${() => setLora(true)}>Train LoRA…</button>
        ${a.lora && html`<${Chip} kind="ok">LoRA ${a.lora.file} (${a.lora.trigger})</${Chip}>`}
      </div>
      <h3>Candidates <span class="muted small">(${c.candidates.length}) tap ✓ to keep as canon, ✕ to reject</span></h3>
      <div class="grid">${c.candidates.map(f => html`<div class="cell"><${Img} slug=${slug} path=${f} />
        <div class="cell-actions"><button class="ok" onClick=${() => cull(f, true)}>✓</button><button class="bad" onClick=${() => cull(f, false)}>✕</button></div></div>`)}</div>
      <h3>Canon <span class="muted small">(${c.canon.length}; the LoRA trains on these)</span></h3>
      <div class="grid">${c.canon.map(f => html`<div class="cell"><${Img} slug=${slug} path=${f} /><div class="cell-actions"><button class="bad" onClick=${() => cull(f, false)}>✕</button></div></div>`)}</div>
      ${c.rejected.length ? html`<p class="muted small">${c.rejected.length} rejected (in art/rejected/)</p>` : null}`}
    ${tab === "sprites" && html`
      <div class="toolbar wrap">
        <span class="muted">${c.sprites.filter(s => s.present).length}/${c.sprites.length} sprites the written scenes need</span>
        <span class="grow"></span>
        <button class="primary" onClick=${() => run("sprite_sheet", { project: slug, char: cid }, `sprites ${cid}`)}>Render missing</button>
      </div>
      <div class="grid">${c.sprites.map(s => html`<div class=${"cell " + (s.present ? "" : "missing")}>
        ${s.present ? html`<${Img} slug=${slug} path=${s.file} />` : html`<div class="ph">missing</div>`}
        <div class="cell-label">${s.outfit} · ${s.pose} · ${s.expr}</div>
        ${s.present && html`<div class="cell-actions"><button class="ok" onClick=${() => verdict(s, "pass")}>✓</button><button class="bad" onClick=${() => verdict(s, "fail", true)}>✕ redo</button></div>`}
      </div>`)}</div>`}
    ${tab === "voice" && html`
      <div class="toolbar wrap">
        <span class="muted">${c.source_clips} source clip(s) · model: ${(c.voice && c.voice.model) || "none"}</span>
        <span class="grow"></span>
        <button onClick=${() => run("voice_finetune", { project: slug, char: cid }, `voice fine-tune ${cid}`)}>Fine-tune checklist</button>
      </div>
      <p class="muted small">Put 5–30 minutes of clean audio in <code>characters/${cid}/voice/source/</code>, fine-tune in GPT-SoVITS, then set a neutral reference clip. Each delivery tag gets its reference by mining: render twelve takes, pick the best.</p>
      <table class="tbl"><tr><th>delivery</th><th>reference</th><th>mined</th><th></th></tr>
      ${deliveries.map(d => html`<tr>
        <td><b>${d}</b></td>
        <td>${refs[d] ? html`<${Audio} slug=${slug} path=${"characters/" + cid + "/voice/" + refs[d]} />` : html`<span class="muted">none</span>`}</td>
        <td>${(c.mining[d] || []).length ? html`<details><summary>${c.mining[d].length} takes</summary>${c.mining[d].map(f => html`<div class="take"><${Audio} slug=${slug} path=${f} /><button class="small" onClick=${async () => { await api.post(`/api/projects/${slug}/characters/${cid}/pick-ref`, { file: f, delivery: d }); window.toast("Reference set"); reload(); }}>use</button></div>`)}</details>` : html`<span class="muted">—</span>`}</td>
        <td><button class="small" onClick=${() => run("emotion_mine", { project: slug, char: cid, delivery: d }, `mine ${cid} ${d}`)}>mine</button></td>
      </tr>`)}</table>`}
    ${lora && html`<${LoraWizard} slug=${slug} cid=${cid} canon=${c.canon.length} trigger=${a.lora ? a.lora.trigger : cid + "char"} onClose=${() => setLora(false)} />`}`;
}

function LoraWizard({ slug, cid, canon, trigger, onClose }) {
  const [s, setS] = useState({ trigger, name: cid + "_v1", dim: 32, alpha: 16, epochs: 10, lr: 0.0001, resolution: 1024, repeats: 10 });
  const set = (k) => (e) => setS({ ...s, [k]: e.target.type === "number" ? +e.target.value : e.target.value });
  async function go() { await run("lora_train", { project: slug, char: cid, trigger: s.trigger, name: s.name, settings: s }, `LoRA ${cid}`); onClose(); nav("jobs"); }
  return html`<${Modal} title=${"Train LoRA: " + cid} onClose=${onClose}>
    <p class="muted">${canon} canon images. Captions are the trigger plus the identity tags from appearance.yaml (outfit tags are left out so the LoRA learns the person). ~20–40 min on a 4090-class card; the job copies the result into ComfyUI's LoRA folder and writes it onto the character.</p>
    <div class="form">
      <label>trigger <input value=${s.trigger} onInput=${set("trigger")} /></label>
      <label>output name <input value=${s.name} onInput=${set("name")} /></label>
      <label>dim <input type="number" value=${s.dim} onInput=${set("dim")} /></label>
      <label>alpha <input type="number" value=${s.alpha} onInput=${set("alpha")} /></label>
      <label>epochs <input type="number" value=${s.epochs} onInput=${set("epochs")} /></label>
      <label>learning rate <input type="number" step="0.00001" value=${s.lr} onInput=${set("lr")} /></label>
      <label>resolution <input type="number" value=${s.resolution} onInput=${set("resolution")} /></label>
      <label>repeats <input type="number" value=${s.repeats} onInput=${set("repeats")} /></label>
    </div>
    <div class="actions"><button class="primary" onClick=${go}>Start training</button></div>
  </${Modal}>`;
}

// ---------------------------------------------------------------- CG review
export function CGs({ slug }) {
  const [cgs, err, reload] = useLoad(() => api.get(`/api/projects/${slug}/cgs`), [slug]);
  const [filter, setFilter] = useState("todo");
  if (err) return html`<${Err} e=${err} />`;
  if (!cgs) return html`<${Spinner} />`;
  const groups = { todo: cgs.filter(c => !c.present && c.candidates.length), pending: cgs.filter(c => !c.present && !c.candidates.length), done: cgs.filter(c => c.present) };
  const explicit = groups.todo.filter(c => c.rating === "explicit").length;
  async function pick(id, file) { await api.post(`/api/projects/${slug}/cgs/${id}/pick`, { file }); window.toast("CG set"); reload(); }
  return html`
    <div class="toolbar wrap">
      ${[["todo", `to review (${groups.todo.length})`], ["pending", `not rendered (${groups.pending.length})`], ["done", `done (${groups.done.length})`]].map(([k, l]) => html`<button class=${filter === k ? "active" : ""} onClick=${() => setFilter(k)}>${l}</button>`)}
      <span class="grow"></span>
      ${filter === "pending" && groups.pending.length ? html`<button class="primary" onClick=${() => groups.pending.forEach(c => run("cg", { project: slug, cg: c.id }, `cg ${c.id}`))}>Render all</button>` : null}
    </div>
    ${filter === "todo" && explicit ? html`<p class="muted small">${explicit} explicit CG(s) in this queue are for your eyes only; Claude's review skips them.</p>` : null}
    ${groups[filter].map(c => html`<div class="panel">
      <div><b>${c.id}</b> <span class="muted small">${c.scene} · ${c.shot} · ${c.lighting} · ${c.location}, ${c.time}</span> ${c.rating === "explicit" ? html`<${Chip} kind="warn">explicit</${Chip}>` : null}</div>
      <div class="muted small">${c.characters.map(x => `${x.id} ${x.pose}/${x.expr}${x.outfit ? "/" + x.outfit : ""}`).join("; ")} — ${c.note}</div>
      ${c.present && html`<${Img} slug=${slug} path=${c.file} cls="wide" />`}
      ${c.candidates.length ? html`<div class="grid">${c.candidates.map(f => {
        const rv = (c.review || []).find(r => f.endsWith(r.file));
        return html`<div class="cell"><${Img} slug=${slug} path=${f} />
          ${rv && html`<div class=${"cell-label " + (rv.verdict === "pass" ? "ok" : "bad")}>${rv.verdict}: ${rv.reason}</div>`}
          <div class="cell-actions"><button class="ok" onClick=${() => pick(c.id, f)}>use</button></div></div>`; })}</div>` : null}
      <div class="actions">
        <button onClick=${() => run("cg", { project: slug, cg: c.id }, `cg ${c.id}`)}>${c.candidates.length ? "Render more" : "Render"}</button>
        ${c.candidates.length && c.rating !== "explicit" ? html`<button onClick=${() => run("review_images", { project: slug, folder: "review/cg/" + c.id }, `review ${c.id}`)}>Claude review</button>` : null}
      </div>
    </div>`)}`;
}

// ---------------------------------------------------------------- voice overview
export function Voice({ slug }) {
  const [p, err] = useLoad(() => api.get(`/api/projects/${slug}`), [slug]);
  if (err) return html`<${Err} e=${err} />`;
  if (!p) return html`<${Spinner} />`;
  const rows = p.board.filter(r => r.written && r.missing);
  const total = rows.reduce((a, r) => a + (r.missing.lines || 0), 0);
  return html`
    <p class="muted">Per character: references and fine-tuning live on the <a href=${`#/p/${slug}/cast`}>cast</a> pages. Per scene: open the scene and toggle <b>Voice</b> to hear lines and pick takes.</p>
    <div class="toolbar"><span><b>${total}</b> line(s) without audio across ${rows.filter(r => r.missing.lines).length} scene(s)</span><span class="grow"></span>
      <button class="primary" onClick=${async () => { const r = await api.post(`/api/projects/${slug}/queue-missing`, { kinds: ["lines"] }); window.toast(`${r.jobs.length} job(s) queued`); nav("jobs"); }}>Queue all missing voice</button></div>
    <div class="rows">${rows.map(r => html`<a class="row" href=${`#/p/${slug}/scene/${r.id}`}><div class="row-main"><span class="mono">${r.id}</span> ${r.title}</div><div class="row-side">${r.missing.lines ? html`<${Chip} kind="warn">${r.missing.lines} lines</${Chip}>` : html`<${Chip} kind="ok">voiced</${Chip}>`}</div></a>`)}</div>`;
}

// ---------------------------------------------------------------- jobs
export function Jobs() {
  const [jobs, setJobs] = useState(null);
  const [open, setOpen] = useState(null);
  const [detail, setDetail] = useState(null);
  useEffect(() => { let ok = true; const tick = () => api.get("/api/jobs").then(j => ok && setJobs(j)).catch(() => {}); tick(); const t = setInterval(tick, 2000); return () => { ok = false; clearInterval(t); }; }, []);
  useEffect(() => { if (!open) { setDetail(null); return; } let ok = true; const tick = () => api.get(`/api/jobs/${open}`).then(j => ok && setDetail(j)).catch(() => {}); tick(); const t = setInterval(tick, 2000); return () => { ok = false; clearInterval(t); }; }, [open]);
  if (!jobs) return html`<${Spinner} />`;
  const act = async (id, what) => { await api.post(`/api/jobs/${id}/${what}`); };
  return html`
    <div class="toolbar"><span>${jobs.filter(j => j.status === "running").length} running · ${jobs.filter(j => j.status === "queued").length} queued</span><span class="grow"></span>
      <button onClick=${async () => { await api.post("/api/jobs/clear"); }}>Clear finished</button></div>
    ${!jobs.length && html`<p class="muted">No jobs. Queue work from a board, a scene, a character or the CG page.</p>`}
    <div class="rows">${jobs.map(j => html`<div class=${"row job " + j.status} onClick=${() => setOpen(open === j.id ? null : j.id)}>
      <div class="row-main"><b>${j.label}</b> <span class="muted small">${j.kind} · ${j.lane} · ${fmt.ago(j.created)} ago</span>
        <div class="bar"><div class="fill" style=${{ width: fmt.pct(j.progress) }}></div></div>
        <div class="muted small">${j.message || ""}${j.error ? html`<span class="err"> ${j.error}</span>` : ""}</div></div>
      <div class="row-side"><${Chip} kind=${j.status === "done" ? "ok" : j.status === "failed" ? "bad" : j.status === "running" ? "note" : ""}>${j.status}</${Chip}>
        ${(j.status === "queued" || j.status === "running") && html`<button class="small" onClick=${e => { e.stopPropagation(); act(j.id, "cancel"); }}>cancel</button>`}
        ${(j.status === "failed" || j.status === "cancelled") && html`<button class="small" onClick=${e => { e.stopPropagation(); act(j.id, "retry"); }}>retry</button>`}</div>
      ${open === j.id && detail && html`<pre class="log" onClick=${e => e.stopPropagation()}>${(detail.log || []).join("\n")}${detail.result ? "\n\nresult: " + JSON.stringify(detail.result, null, 1).slice(0, 2000) : ""}</pre>`}
    </div>`)}</div>`;
}

// ---------------------------------------------------------------- setup
export function Setup() {
  const [s, err, reload] = useLoad(() => api.get("/api/setup"), []);
  const [cfg, setCfg] = useState(null);
  useEffect(() => { if (s) setCfg(s.config); }, [s]);
  if (err) return html`<${Err} e=${err} />`;
  if (!s || !cfg) return html`<${Spinner} />`;
  const keys = ["comfyui_url", "comfyui_lora_dir", "checkpoint", "tts_url", "whisper_model", "kohya_path", "kohya_python", "train_checkpoint", "claude_cmd", "renpy_cmd", "renpy_template", "style_tags", "negative_tags", "steps", "cfg", "takes"];
  async function save() { try { const r = await api.put("/api/setup/config", Object.fromEntries(keys.map(k => [k, cfg[k]]))); window.toast("Saved " + r.saved); reload(); } catch (e) { window.toast(e.message, "bad"); } }
  return html`
    <h3>Checks</h3>
    <div class="rows">${s.checks.map(c => html`<div class="row"><div class="row-main"><b>${c.name}</b><div class="muted small">${c.detail}</div></div><div class="row-side"><${Chip} kind=${c.ok ? "ok" : "bad"}>${c.ok ? "ok" : "missing"}</${Chip}></div></div>`)}</div>
    <h3>Config <span class="muted small">${s.config_path}</span></h3>
    <div class="form">${keys.map(k => html`<label>${k} <input value=${cfg[k] == null ? "" : cfg[k]} onInput=${e => setCfg({ ...cfg, [k]: typeof cfg[k] === "number" ? +e.target.value : e.target.value })} /></label>`)}</div>
    <div class="actions"><button class="primary" onClick=${save}>Save</button> <button onClick=${reload}>Re-check</button></div>
    <h3>Models to install</h3>
    <p class="muted small">See <code>docs/models.md</code>: an Illustrious/NoobAI SDXL checkpoint in ComfyUI's checkpoints folder (name it in <code>checkpoint</code>), an SDXL base for training (<code>train_checkpoint</code>), kohya sd-scripts, GPT-SoVITS with its api_v2 server, optional faster-whisper and resemblyzer for take scoring, ffmpeg for ogg.</p>`;
}
