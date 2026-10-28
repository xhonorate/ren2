import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { MissingReport } from "../lib/types";

type SoundGap = MissingReport["sounds"][number];
type Filter = "all" | "sfx" | "music";

const FILTERS: [Filter, string][] = [
  ["all", "Everything"],
  ["sfx", "One-shot sound"],
  ["music", "Music"],
];

const rowKey = (gap: SoundGap) => `${gap.node_id}:${gap.kind}`;

/**
 * Every sound cue with no file behind it.
 *
 * The one kind of missing asset nothing in the pipeline can generate: the
 * perform pass writes a *description* of the sound a beat makes, and a real
 * file only ever arrives by hand. Until one does, the cue compiles to a
 * `# TODO sfx:` comment — harmless to the build, and invisible unless you go
 * looking. This is the view over them, so a project's sound can be sourced in
 * one pass instead of by grepping the compiled script.
 */
export default function MissingAudioPanel() {
  const store = useStore();
  const [report, setReport] = useState<MissingReport | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!store.project) return;
    setLoading(true);
    try {
      setReport(await api.missingReport(store.project.id));
    } catch (e) {
      store.fail(String(e));
    } finally {
      setLoading(false);
    }
  }, [store.project?.id]);

  useEffect(() => {
    load();
  }, [load]);

  if (!store.project) return null;

  const all = report?.sounds ?? [];
  const gaps = all.filter((gap) => filter === "all" || gap.kind === filter);
  const counts = {
    sfx: all.filter((gap) => gap.kind === "sfx").length,
    music: all.filter((gap) => gap.kind === "music").length,
  };

  /** Attach a file to one cue. Goes through the store rather than the API so
   *  it lands in the undo stack like any other edit. */
  const attach = async (gap: SoundGap) => {
    const file = (drafts[rowKey(gap)] ?? "").trim();
    if (!file) return;
    await store.patchNode(gap.node_id, { data: { [gap.field]: file } });
    setDrafts((current) => {
      const next = { ...current };
      delete next[rowKey(gap)];
      return next;
    });
    load();
  };

  const copyList = async () => {
    const text = gaps
      .map((gap) => [gap.kind, gap.scene || "—", gap.cue].join("\t"))
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      store.notify(`${gaps.length} cues copied`);
    } catch {
      store.notify("no clipboard access — select the list by hand");
    }
  };

  let lastScene: string | null = null;

  return (
    <div className="card">
      <div className="card-head">
        <span className="name">Missing audio</span>
        <span className={`pill ${all.length ? "warn" : "good"}`}>
          {all.length} unsourced
        </span>
        <span className="spacer" />
        <button onClick={copyList} disabled={!gaps.length}>
          Copy list
        </button>
        <button onClick={load} disabled={loading}>
          {loading ? "…" : "Rescan"}
        </button>
      </div>

      <div className="hint" style={{ marginBottom: 10 }}>
        Nothing generates these — the perform pass writes what a beat should
        sound like, and the file comes from you. An unsourced cue compiles to a{" "}
        <code># TODO sfx:</code> comment, so the build stays green and the
        silence is easy to miss. Drop a filename in and it becomes a real{" "}
        <code>play</code> statement: sounds live under{" "}
        <code>game/audio/sfx/</code>, music under <code>game/audio/music/</code>.
      </div>

      <div className="row" style={{ marginBottom: 8 }}>
        {FILTERS.map(([id, label]) => (
          <button
            key={id}
            className={filter === id ? "primary" : ""}
            onClick={() => setFilter(id)}
          >
            {label}
            {id !== "all" && ` (${counts[id]})`}
          </button>
        ))}
      </div>

      {gaps.length === 0 ? (
        <div className="hint good">
          {all.length === 0
            ? "Every cue in the script has a file behind it."
            : "Nothing of this kind is missing."}
        </div>
      ) : (
        <div className="list" style={{ maxHeight: 420, overflowY: "auto" }}>
          {gaps.map((gap) => {
            const key = rowKey(gap);
            const scene = gap.scene || "(no scene)";
            const heading = scene !== lastScene ? scene : null;
            lastScene = scene;
            return (
              <div key={key}>
                {heading && <div className="section-title">{heading}</div>}
                <div className="item" style={{ alignItems: "flex-start", gap: 8 }}>
                  <span className={`pill ${gap.kind === "music" ? "" : "warn"}`}>
                    {gap.kind}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500 }}>{gap.cue}</div>
                    {gap.text && (
                      <div
                        className="hint"
                        style={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {gap.text}
                      </div>
                    )}
                  </div>
                  <input
                    style={{ width: 160 }}
                    placeholder={gap.kind === "music" ? "theme.ogg" : "door.ogg"}
                    value={drafts[key] ?? ""}
                    onChange={(e) =>
                      setDrafts({ ...drafts, [key]: e.target.value })
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") attach(gap);
                    }}
                    onBlur={() => attach(gap)}
                  />
                  <button
                    className="tiny ghost"
                    title="show this beat on the canvas"
                    onClick={() => {
                      store.select(gap.node_id);
                      store.setView("canvas");
                    }}
                  >
                    go
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
