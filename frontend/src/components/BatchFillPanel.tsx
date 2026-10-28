import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { MissingReport } from "../lib/types";

/** Order matters and mirrors the backend: a fill run queues these top to
 *  bottom, so CG direction sits above the CGs that read it. */
const KINDS: {
  id: string; label: string; field: keyof MissingReport["counts"];
  /** Seconds a job of this kind takes. A director pass is one short LLM
   *  call; everything else is a frame or a voice line. */
  cost: number;
  hint?: string;
}[] = [
  { id: "backgrounds", label: "Backgrounds", field: "backgrounds", cost: 15 },
  { id: "sprites", label: "Sprites", field: "sprites", cost: 15 },
  { id: "cg_prompts", label: "CG shot direction", field: "cg_prompts", cost: 8,
    hint: "Runs the CG director over flagged CG nodes with no shot hint, "
        + "camera or action set, and writes the tags onto the node — before "
        + "the CGs below are rendered, so they are shot from a real frame "
        + "instead of the identity block alone. Nodes you have already "
        + "written a shot for are left alone." },
  { id: "cgs", label: "Event CGs", field: "cgs", cost: 15 },
  { id: "voice", label: "Voice lines", field: "voice", cost: 5 },
];

/**
 * Queue everything the project is still missing. Built for a run you start and
 * leave: a fully voiced VN is hours of GPU time, not an interactive wait.
 */
export default function BatchFillPanel() {
  const store = useStore();
  const [report, setReport] = useState<MissingReport | null>(null);
  const [kinds, setKinds] = useState<string[]>(KINDS.map((k) => k.id));
  const [includeStale, setIncludeStale] = useState(true);
  const [limit, setLimit] = useState(0);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!store.project) return;
    setLoading(true);
    try {
      setReport(await api.missingReport(store.project.id, includeStale));
    } catch (e) {
      store.fail(String(e));
    } finally {
      setLoading(false);
    }
  }, [store.project?.id, includeStale]);

  useEffect(() => { load(); }, [load]);

  if (!store.project) return null;

  const selectedTotal = report
    ? KINDS.filter((k) => kinds.includes(k.id))
        .reduce((sum, k) => sum + report.counts[k.field], 0)
    : 0;

  const seconds = report
    ? KINDS.filter((k) => kinds.includes(k.id))
        .reduce((sum, k) => sum + report.counts[k.field] * k.cost, 0)
    : 0;

  return (
    <div className="card">
      <div className="card-head">
        <span className="name">Fill missing assets</span>
        {report && <span className="pill">{report.counts.total} missing</span>}
        <span className="spacer" />
        <button title="Walks the script again and recounts what is missing. Run it after editing scenes or adding characters."
          onClick={load} disabled={loading}>{loading ? "…" : "Rescan"}</button>
      </div>

      <div className="hint" style={{ marginBottom: 10 }}>
        Scoped to what the script actually uses — only sprite combinations some
        node shows, and only the time-of-day background variants some scene asks
        for. The full cross product would be hours of frames nothing displays.
      </div>

      <div className="grid2" style={{ gap: 10 }}>
        <div>
          {KINDS.map((entry) => {
            const count = report?.counts[entry.field] ?? 0;
            return (
              <div key={entry.id}>
                <label className="row batch-kind" style={{ gap: 8 }}
                  title={entry.hint}>
                  <input type="checkbox" checked={kinds.includes(entry.id)}
                    onChange={(e) => setKinds(e.target.checked
                      ? [...kinds, entry.id]
                      : kinds.filter((k) => k !== entry.id))} />
                  <span style={{ flex: 1 }}>{entry.label}</span>
                  <span className={`pill ${count ? "" : "good"}`}>{count}</span>
                  {count > 0 && (
                    <button className="tiny ghost"
                      onClick={(e) => {
                        e.preventDefault();
                        setExpanded(expanded === entry.id ? null : entry.id);
                      }}>{expanded === entry.id ? "hide" : "list"}</button>
                  )}
                </label>
                {entry.hint && kinds.includes(entry.id) && count > 0 && (
                  <div className="hint" style={{ margin: "-2px 0 6px 24px" }}>
                    {entry.hint}
                  </div>
                )}
              </div>
            );
          })}

          <label className="row" style={{ gap: 6, marginTop: 8 }}>
            <input type="checkbox" checked={includeStale}
              onChange={(e) => setIncludeStale(e.target.checked)} />
            <span>also redo stale assets</span>
          </label>

          <div className="field" style={{ marginTop: 6 }}>
            <label>Cap this run (0 = everything)</label>
            <input type="number" min={0} step={10} value={limit}
              onChange={(e) => setLimit(parseInt(e.target.value, 10) || 0)} />
          </div>
        </div>

        <div>
          <div className="section-title">This run</div>
          <table className="data">
            <tbody>
              <tr><th>jobs to queue</th><td>{limit ? Math.min(limit, selectedTotal) : selectedTotal}</td></tr>
              <tr><th>rough time</th><td>{formatDuration(seconds)}</td></tr>
            </tbody>
          </table>
          <div className="hint" style={{ marginTop: 6 }}>
            Estimated at ~5s a voice line, ~15s an image and ~8s a director
            pass on one 4090. Retries and rejects in practice at least double it.
          </div>

          {report && report.blocked.length > 0 && (
            <>
              <div className="section-title">Blocked</div>
              {report.blocked.map((reason, i) => (
                <div key={i} className="hint warn">⚠ {reason}</div>
              ))}
            </>
          )}

          <button className="primary" style={{ width: "100%", marginTop: 10 }}
            disabled={selectedTotal === 0}
            onClick={async () => {
              const out = await api.fillMissing(store.project!.id, {
                kinds, include_stale: includeStale, limit,
              });
              store.notify("batch queued — safe to leave running");
              store.refreshJobs();
            }}>
            Queue {limit ? Math.min(limit, selectedTotal) : selectedTotal} jobs
          </button>
          <div className="hint" style={{ marginTop: 4 }}>
            GPU jobs run one at a time behind a single lock, so this will not
            thrash VRAM. Close the browser if you like; the queue is in SQLite.
          </div>
        </div>
      </div>

      {expanded && report && (
        <div style={{ marginTop: 10 }}>
          <div className="section-title">{expanded}</div>
          <div className="list" style={{ maxHeight: 220, overflowY: "auto" }}>
            {expanded === "sprites" && report.sprites.map((item, i) => (
              <div key={i} className="item">
                <span className="pill">{item.character_id}</span>
                <span>{item.slot}</span>
              </div>
            ))}
            {expanded === "backgrounds" && report.backgrounds.map((item, i) => (
              <div key={i} className="item">
                <span className="pill">{item.location_id}</span>
                <span>{item.variant}</span>
              </div>
            ))}
            {expanded === "cg_prompts" && report.cg_prompts.map((item, i) => (
              <div key={i} className="item" onClick={() => {
                store.select(item.node_id);
                store.setView("canvas");
              }}>
                <span className="pill">undirected</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{item.text}</span>
              </div>
            ))}
            {expanded === "cgs" && report.cgs.map((item, i) => (
              <div key={i} className="item" onClick={() => {
                store.select(item.node_id);
                store.setView("canvas");
              }}>
                <span className="pill">{item.reason}</span>
                {item.needs_direction && <span className="pill">undirected</span>}
                <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{item.text}</span>
              </div>
            ))}
            {expanded === "voice" && report.voice.map((item, i) => (
              <div key={i} className="item" onClick={() => {
                store.select(item.node_id);
                store.setView("canvas");
              }}>
                <span className="pill">{item.speaker}</span>
                <span className="pill">{item.reason}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{item.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}
