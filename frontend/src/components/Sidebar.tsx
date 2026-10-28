import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { NodeType } from "../lib/types";
import { WorldBiblePanel } from "../views/SettingView";

const PALETTE: { type: NodeType; label: string; hint: string; data?: any }[] = [
  { type: "scene", label: "Scene", data: { title: "New scene" },
    hint: "Container for one scene: background, time of day, the NSFW flag and the route it belongs to. Every other node lives inside one." },
  { type: "say", label: "Say",
    hint: "One spoken line, attributed to a character. Carries the performance bracket, the voice take and its own take history." },
  { type: "narration", label: "Narration",
    hint: "Unattributed prose — description, interiority, anything nobody says out loud." },
  { type: "stage", label: "Stage",
    hint: "Puts someone on screen or takes them off, and changes the active outfit, expression or position. Staging that changes nothing is dropped at compile." },
  { type: "choice", label: "Choice",
    hint: "A player menu. Each option gets its own output port to wire, can be gated on a declared stat, and can change stats when taken.",
    data: {
    prompt: "What do you say?",
    options: [
      { id: "o1", label: "Option A", condition: "", branch_hint: "" },
      { id: "o2", label: "Option B", condition: "", branch_hint: "" },
    ],
  } },
  { type: "hub", label: "Hub",
    hint: "A menu you come back to: topics that drop once taken, gate on declared stats, and can point at the next scene in a route. Use it for a free-roam beat rather than a one-shot fork.",
    data: {
    prompt: "What do you want to do?",
    mode: "call", exhaust: true, loop_back: true, menuset: "hub_topics",
    options: [
      { id: "o1", label: "Topic A", requires: [], locked: "hide",
        locked_label: "", repeatable: false, branch_hint: "", effects: [],
        target_route: "" },
      { id: "o2", label: "Leave", requires: [], locked: "hide",
        locked_label: "", repeatable: true, branch_hint: "", effects: [],
        target_route: "" },
    ],
  } },
  { type: "condition", label: "Condition",
    hint: "if / elif / else on flags and stats. The player never sees it — for a fork they choose, use Choice.",
    data: {
    branches: [{ id: "b1", expr: "" }], has_else: true,
  } },
  { type: "set", label: "Set",
    hint: "Assigns a flag or stat (=, +=, -=) and has no visible effect of its own. This is how a scene records what happened for a later branch to read.",
    data: { assignments: [{ var: "", op: "=", value: "true" }] } },
  { type: "audio", label: "Audio",
    hint: "A music or sound cue. Nothing generates these — you write the description and supply the file; until then it compiles to a TODO comment rather than breaking the build." },
  { type: "transition", label: "Transition",
    hint: "A dissolve, fade or other Ren'Py transition between what is on screen now and what comes next." },
  { type: "jump", label: "Jump",
    hint: "Goes to another label and does not come back. Use it to rejoin the main line or send a branch elsewhere." },
  { type: "call", label: "Call",
    hint: "Goes to another label and returns here when it ends — the same mechanism a hub topic uses. Use Jump if you do not want to come back." },
  { type: "raw", label: "Raw",
    hint: "Literal Ren'Py, passed through untouched. The escape hatch for a screen call or an ATL block the node types do not cover." },
  { type: "end", label: "End",
    hint: "An ending. Stops the script here instead of falling through to whatever comes next in the file." },
];

export default function Sidebar() {
  const store = useStore();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const [stale, setStale] = useState<{ images: any[]; audio: any[] } | null>(null);
  const [worldOpen, setWorldOpen] = useState(false);

  useEffect(() => {
    if (!store.project || query.trim().length < 2) {
      setHits([]);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        setHits(await api.search(store.project!.id, query));
      } catch {
        setHits([]);
      }
    }, 220);
    return () => clearTimeout(timer);
  }, [query, store.project?.id]);

  useEffect(() => {
    if (!store.project) return;
    api.staleness(store.project.id).then(setStale).catch(() => setStale(null));
  }, [store.project?.id, store.nodes]);

  if (!store.project) return <aside className="sidebar" />;

  const add = (type: NodeType, data: any) => {
    const selected = store.nodes.find((n) => n.id === store.selected);
    store.addNode(type, data ?? {}, {
      x: selected ? selected.x : 80,
      y: selected ? selected.y + 160 : 80,
      parent_id: selected?.type === "scene" ? selected.id : selected?.parent_id ?? null,
      connect_from: selected && !["choice", "hub", "condition"].includes(selected.type)
        ? selected.id : null,
    });
  };

  const staleCount = (stale?.images.length ?? 0) + (stale?.audio.length ?? 0);

  return (
    <aside className="sidebar">
      <div className="section-title">Add node</div>
      <div className="palette">
        {PALETTE.map((entry) => (
          <button key={entry.type} title={entry.hint}
            onClick={() => add(entry.type, entry.data)}>
            {entry.label}
          </button>
        ))}
      </div>
      <div className="hint" style={{ marginTop: 6 }}>
        New nodes wire onto the selection automatically. Choice, Hub and
        Condition ports are wired by dragging.
      </div>

      <div className="section-title">World bible</div>
      <button style={{ width: "100%" }} onClick={() => setWorldOpen(true)}>
        Edit world bible…
      </button>
      <div className="hint" style={{ marginTop: 6 }}>
        Setting, era, POV, tense and style notes — the context every
        generation is written against. Also on the Setting tab, alongside
        locations.
      </div>

      <div className="section-title">Search</div>
      <input placeholder="Find text, titles, options…" value={query}
        onChange={(e) => setQuery(e.target.value)} />
      {hits.length > 0 && (
        <div className="list" style={{ marginTop: 6 }}>
          {hits.slice(0, 40).map((hit) => (
            <div key={hit.id} className="item" onClick={() => store.select(hit.id)}>
              <span className="pill">{hit.type}</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {hit.excerpt}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="section-title">Scenes</div>
      <div className="list">
        {store.nodes.filter((n) => n.type === "scene").map((scene) => (
          <div key={scene.id}
            className={`item ${store.selected === scene.id ? "active" : ""}`}
            onClick={() => store.select(scene.id)}>
            <span>{scene.data?.title || "Untitled"}</span>
            <span className="sub">
              {store.nodes.filter((n) => n.parent_id === scene.id).length}
            </span>
          </div>
        ))}
        {!store.nodes.some((n) => n.type === "scene") && (
          <div className="hint">No scenes yet.</div>
        )}
      </div>

      <div className="section-title">Health</div>
      <div className="list">
        <div className="item">
          <span className={`pill ${store.problems.some((p) => p.severity === "error") ? "bad" : "good"}`}>
            {store.problems.filter((p) => p.severity === "error").length} errors
          </span>
          <span className="pill warn">
            {store.problems.filter((p) => p.severity === "warning").length} warnings
          </span>
        </div>
        {staleCount > 0 && (
          <>
            <div className="item">
              <span className="pill warn">{staleCount} stale assets</span>
            </div>
            <button style={{ width: "100%", marginTop: 4 }}
              title="Queues a fresh image or voice line for every asset whose source text has changed since it was generated. Runs in the background."
              onClick={async () => {
              await api.regenerateStale(store.project!.id);
              store.notify("queued stale regeneration");
              store.refreshJobs();
            }}>
              Regenerate stale assets
            </button>
            <div className="hint">
              Run this before every build, or a voice line will not match its text.
            </div>
          </>
        )}
        {store.orphanCount > 0 && (
          <div className="item" onClick={() => store.setShowOrphans(!store.showOrphans)}
            style={{ cursor: "pointer" }}>
            <span className="pill">{store.orphanCount} parked</span>
            <span className="sub">{store.showOrphans ? "hide" : "show"}</span>
          </div>
        )}
      </div>

      {store.problems.length > 0 && (
        <div className="list" style={{ marginTop: 6 }}>
          {store.problems.slice(0, 12).map((problem, index) => (
            <div key={index} className="item" onClick={() => store.select(problem.node_id)}>
              <span className={problem.severity === "error" ? "pill bad" : "pill warn"}>
                {problem.severity === "error" ? "✗" : "⚠"}
              </span>
              <span style={{ fontSize: 11, overflow: "hidden" }}>{problem.message}</span>
            </div>
          ))}
        </div>
      )}

      {worldOpen && (
        <div className="modal-backdrop" onClick={() => setWorldOpen(false)}>
          <div className="modal" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
            <div className="head">
              <span className="title">World bible</span>
              <button style={{ marginLeft: "auto" }} onClick={() => setWorldOpen(false)}>
                Close
              </button>
            </div>
            <div className="content">
              <WorldBiblePanel />
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
