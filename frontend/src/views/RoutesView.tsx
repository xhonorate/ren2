import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useStore, writeActiveScene, type View } from "../lib/store";
import type { PacingRoute, PacingScene, PacingSpeaker } from "../lib/types";

/**
 * Route balance: how long each route is, who is in it, and what is left to do.
 *
 * The reason this is its own view rather than a panel on Build: balancing is
 * comparison, and a comparison needs every route on screen at once. The
 * numbers come from `services/pacing.py` -- nothing is counted here, so the
 * bars and the compiler can never disagree about what a route contains.
 *
 * Scene rows are links. A route list you cannot act from is a report; being
 * able to open the scene you just found is what makes it a workspace.
 */

const TRUNK = "— no route —";

function minutes(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function pct(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}

/** A share bar: the whole point of the view, so it stays plain and comparable. */
function Bar({ value, max, tone }: { value: number; max: number; tone?: string }) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="route-bar">
      <div className="route-bar-fill" style={{
        width: `${width}%`,
        background: tone ?? "var(--accent)",
      }} />
    </div>
  );
}

function CastChips({ cast }: { cast: PacingSpeaker[] }) {
  if (!cast.length) return <span className="hint">nobody speaks yet</span>;
  return (
    <div className="route-cast">
      {cast.map((person) => (
        <span key={person.id} className="chip"
          title={`${person.lines} lines · ${person.words} words`}>
          <span className="route-dot" style={{ background: person.color }} />
          {person.name}
          {person.share_pct !== undefined && (
            <span className="route-share">{person.share_pct}%</span>
          )}
        </span>
      ))}
    </div>
  );
}

export default function RoutesView() {
  const store = useStore();
  const [report, setReport] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [adding, setAdding] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");

  const refresh = async () => {
    if (!store.project) return;
    try {
      setReport(await api.pacing(store.project.id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => { refresh(); }, [store.project?.id, store.nodes]);

  // Every bar is scaled against the longest route, so "half the length" reads
  // as half the bar rather than needing the numbers to be compared by eye.
  const longest = useMemo(
    () => Math.max(1, ...(report?.routes ?? []).map((r: PacingRoute) => r.words)),
    [report],
  );

  /** Open a scene in the view that suits what you are about to do to it. */
  const openScene = (sceneId: string, view: View) => {
    writeActiveScene(store.project!.id, sceneId);
    store.select(sceneId);
    store.setView(view);
  };

  /**
   * A scene created here is tagged on the way in. Adding one by hand and
   * forgetting the tag is the failure this exists to prevent: an untagged
   * scene is invisible to every hub that dispatches over the route, and
   * nothing complains, because claiming no route is legal.
   */
  const addScene = async (route: string) => {
    const title = newTitle.trim() || "New scene";
    // Placed below the route's last scene, because canvas order *is* route
    // order -- both here and in the compiled `renpy` route table.
    const siblings = store.nodes.filter(
      (n) => n.type === "scene" && ((n.data as any)?.route ?? "") === route,
    );
    const y = siblings.length ? Math.max(...siblings.map((n) => n.y)) + 220 : 80;
    const x = siblings.length ? siblings[0].x : 80;
    await store.addNode("scene", { title, route }, { x, y });
    setAdding(null);
    setNewTitle("");
    store.notify(route ? `“${title}” added to ${route}` : `“${title}” added`);
    refresh();
  };

  const renameRoute = async (from: string) => {
    const to = renameTo.trim();
    if (!to || to === from) { setRenaming(null); return; }
    try {
      const out = await api.renameRoute(store.project!.id, from, to);
      // Hubs dispatch over the name too, so the rename carries them; saying
      // how many moved is the only sign that happened.
      store.notify(`${from} → ${to} (${out.updated} nodes updated)`);
      setRenaming(null);
      await store.refreshGraph();
      refresh();
    } catch (e) {
      store.fail(String(e));
    }
  };

  if (!store.project) {
    return <div className="view"><p className="lede">Open a project first.</p></div>;
  }
  if (error) {
    return <div className="view"><div className="hint bad">{error}</div></div>;
  }
  if (!report) {
    return <div className="view"><p className="lede">Measuring…</p></div>;
  }

  const t = report.totals;

  return (
    <div className="view">
      <h2>Routes</h2>
      <p className="lede">
        Every scene measured across all of its branches — the path nobody walks
        still had to be written, and still gets voiced. Playtime is the word
        count at {report.words_per_minute} words a minute, so it is worth
        comparing routes with and not much else.
      </p>

      <div className="card">
        <div className="card-head">
          <span className="name">Whole story</span>
          <span className="spacer" />
          <span className="pill">{t.scenes} scenes</span>
          <span className="pill">{t.words.toLocaleString()} words</span>
          <span className="pill">≈ {minutes(t.seconds)}</span>
        </div>
        <div className="route-stats">
          <Stat label="lines" value={t.lines.toLocaleString()} />
          <Stat label="words / line" value={t.words_per_line} />
          <Stat label="choices"
            value={t.lines_per_choice === null
              ? `${t.choices}`
              : `${t.choices} · 1 per ${t.lines_per_choice} lines`} />
          <Stat label="voiced" value={pct(t.voiced_pct)}
            hint={`${t.voiced} of ${t.voiceable} voiceable lines`} />
          <Stat label="imaged" value={pct(t.imaged_pct)}
            hint={`${t.cgs_done + t.backgrounds_done} of ${t.cgs + t.backgrounds} CGs and backgrounds`} />
          <Stat label="deepest branch" value={t.max_depth} />
        </div>
      </div>

      {report.routes.map((route: PacingRoute) => (
        <div className="card" key={route.name || "__trunk__"}>
          <div className="card-head">
            {renaming === route.name ? (
              <>
                <input autoFocus value={renameTo} style={{ maxWidth: 200 }}
                  onChange={(e) => setRenameTo(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") renameRoute(route.name);
                    if (e.key === "Escape") setRenaming(null);
                  }} />
                <button className="tiny primary"
                  onClick={() => renameRoute(route.name)}>Rename</button>
                <button className="tiny ghost"
                  onClick={() => setRenaming(null)}>Cancel</button>
              </>
            ) : (
              <>
                <span className="name">{route.name || TRUNK}</span>
                {route.name && (
                  <button className="tiny ghost"
                    title="Renames the route on every scene claiming it and every hub option targeting it — doing only one leaves the other pointing at a route nobody belongs to"
                    onClick={() => { setRenaming(route.name); setRenameTo(route.name); }}>
                    rename
                  </button>
                )}
              </>
            )}
            <span className="spacer" />
            <span className="pill">{route.scenes} scenes</span>
            <span className="pill">{route.words.toLocaleString()} words</span>
            <span className="pill">≈ {minutes(route.seconds)}</span>
            <span className={`pill ${route.voiced_pct === 100 ? "good" : ""}`}
              title={`${route.voiced} of ${route.voiceable} voiceable lines`}>
              {pct(route.voiced_pct)} voiced
            </span>
          </div>

          <Bar value={route.words} max={longest}
            tone={route.name ? "var(--accent)" : "var(--dim)"} />
          <div className="hint" style={{ margin: "4px 0 10px" }}>
            {route.lines.toLocaleString()} lines · {route.words_per_line} words a
            line · {route.choices} choices
            {route.lines_per_choice !== null
              && ` (one every ${route.lines_per_choice} lines)`}
            {" · "}deepest branch {route.max_depth}
            {" · "}{pct(route.imaged_pct)} imaged
          </div>

          <CastChips cast={route.cast} />

          <table className="route-table">
            <tbody>
              {route.scenes_list.map((scene: PacingScene) => (
                <tr key={scene.id}>
                  <td>
                    <button className="linkish"
                      title="Open this scene on the canvas"
                      onClick={() => openScene(scene.id, "canvas")}>
                      {scene.title}
                    </button>
                    {scene.nsfw && <span className="pill purple">nsfw</span>}
                  </td>
                  <td className="num">{scene.words.toLocaleString()} w</td>
                  <td className="num">{scene.lines} lines</td>
                  <td className="num">{minutes(scene.seconds)}</td>
                  <td className="num"
                    title={`${scene.choices} choices, ${scene.options} options, deepest branch ${scene.depth}`}>
                    {scene.choices ? `${scene.choices} ⑂` : "—"}
                  </td>
                  <td className="num"
                    title={`${scene.voiced} of ${scene.voiceable} voiceable lines`}>
                    {pct(scene.voiced_pct)}
                  </td>
                  <td className="scene-cast">
                    {scene.speakers.slice(0, 4).map((s) => (
                      <span key={s.id} className="route-dot"
                        style={{ background: s.color }}
                        title={`${s.name}: ${s.lines} lines`} />
                    ))}
                  </td>
                  <td className="num">
                    <button className="tiny ghost"
                      title="Open this scene in the Script view, to write rather than wire"
                      onClick={() => openScene(scene.id, "script")}>
                      script
                    </button>
                  </td>
                </tr>
              ))}
              {!route.scenes_list.length && (
                <tr><td className="hint">No scenes yet.</td></tr>
              )}
            </tbody>
          </table>

          {adding === route.name ? (
            <div className="row" style={{ marginTop: 8 }}>
              <input autoFocus placeholder="Scene title" value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addScene(route.name);
                  if (e.key === "Escape") setAdding(null);
                }} />
              <button className="primary" onClick={() => addScene(route.name)}>
                Add
              </button>
              <button className="ghost" onClick={() => setAdding(null)}>Cancel</button>
            </div>
          ) : (
            <button className="tiny" style={{ marginTop: 8 }}
              title={route.name
                ? `Creates a scene already tagged for ${route.name}, at the end of the route`
                : "Creates a scene claiming no route"}
              onClick={() => { setAdding(route.name); setNewTitle(""); }}>
              ＋ New scene in {route.name || "the trunk"}
            </button>
          )}
        </div>
      ))}

      {!report.routes.length && (
        <div className="card">
          <div className="hint">
            No scenes yet. A route needs no setting up: it exists as soon as a
            scene claims it in the inspector's <strong>route</strong> field.
          </div>
        </div>
      )}

      <h3>Cast across the story</h3>
      <div className="card">
        <table className="route-table">
          <tbody>
            {report.characters.map((person: any) => (
              <tr key={person.id}>
                <td>
                  <span className="route-dot" style={{ background: person.color }} />
                  {person.name}
                  {person.is_player && <span className="pill">player</span>}
                </td>
                <td style={{ width: "28%" }}>
                  <Bar value={person.words} max={Math.max(1, report.characters[0]?.words ?? 1)}
                    tone={person.color} />
                </td>
                <td className="num">{person.share_pct}%</td>
                <td className="num">{person.lines.toLocaleString()} lines</td>
                <td className="num">{person.scenes} scenes</td>
                <td className="num"
                  title={person.is_player
                    ? "Player lines are never voiced"
                    : `${person.voiced} of ${person.voiceable} voiceable lines`}>
                  {pct(person.voiced_pct)} voiced
                </td>
                <td className="hint">
                  {person.routes.length ? person.routes.join(", ") : "trunk only"}
                </td>
              </tr>
            ))}
            {!report.characters.length && (
              <tr><td className="hint">No characters yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: {
  label: string; value: string | number; hint?: string;
}) {
  return (
    <div className="route-stat" title={hint}>
      <div className="route-stat-value">{value}</div>
      <div className="route-stat-label">{label}</div>
    </div>
  );
}
