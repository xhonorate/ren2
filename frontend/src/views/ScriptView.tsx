import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { ContextPreviewButton, SteerModal } from "../components/Inspector";
import NextSceneLink from "../components/NextSceneLink";
import VariantStrip from "../components/VariantStrip";
import VoiceLineButton from "../components/VoiceLineButton";
import { heldAlong } from "../lib/cg";
import { readActiveScene, useStore, writeActiveScene } from "../lib/store";
import type { IRNode, NodeType, ScriptRow } from "../lib/types";

/**
 * Linear editing surface over the graph.
 *
 * Rows stay bound to their node ids: typing patches the node in place, so a
 * rewrite never moves a Ren'Py label. Branching is handled by walking one path
 * at a time — a Choice row shows every option and lets you follow one.
 *
 * Parity with the canvas inspector is deliberate: everything the inspector can
 * do to a line, a row can do too — performance fields, sound cue, CG flag,
 * notes, variants, the whole generation menu — because being sent to another
 * view mid-sentence is what breaks a writing session.
 */

/** What a freshly inserted node of each type needs to be valid and useful.
 *  Kept beside the insert menu rather than in the store: these are *authoring*
 *  defaults for a hand-added node, not the shape the IR requires. */
const NEW_NODE: Record<string, (ctx: { speaker: string | null }) => any> = {
  say: ({ speaker }) => ({ speaker, text: "", expression: "neutral",
                           emotion: "neutral", intensity: 0.5, pose: "front" }),
  narration: () => ({ text: "" }),
  choice: () => ({ prompt: "", mode: "jump", options: [
    { id: "o1", label: "", condition: "", branch_hint: "", effects: [] },
    { id: "o2", label: "", condition: "", branch_hint: "", effects: [] },
  ] }),
  stage: () => ({ show: [], hide: [], outfits: {}, expressions: {}, poses: {} }),
  set: () => ({ assignments: [{ var: "", op: "=", value: "" }] }),
  condition: () => ({ branches: [{ id: "b1", expr: "" }], has_else: true }),
  audio: () => ({ channel: "music", action: "play", file: "", fadeout: 1, fadein: 0 }),
  end: () => ({ mode: "return", ending_id: "", unlock_persistent: [] }),
};

const INSERTABLE: [string, string][] = [
  ["say", "Dialogue"], ["narration", "Narration"], ["choice", "Choice"],
  ["stage", "Staging"], ["set", "Set a flag"], ["condition", "Condition"],
  ["audio", "Audio"], ["end", "End"],
];
export default function ScriptView() {
  const store = useStore();
  const [rows, setRows] = useState<ScriptRow[]>([]);
  const [scenes, setScenes] = useState<{ id: string; title: string }[]>([]);
  // Shared with Canvas (lib/store.readActiveScene), so moving between views
  // keeps you in the scene you were working on.
  const [startId, setStartId] = useState<string | null>(
    () => (store.project ? readActiveScene(store.project.id) : null));
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [pasting, setPasting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const focusNext = useRef<string | null>(null);

  // Rows that play over a CG held from an earlier row -- walked along these
  // rows, so it follows the branches picked here.
  const underHeldCg = useMemo(() => {
    const byId = new Map(store.nodes.map((n) => [n.id, n]));
    const sequence = rows.map((r) => (r.node_id ? byId.get(r.node_id) : undefined))
      .filter((n): n is IRNode => !!n);
    const held = heldAlong(sequence);
    return new Set(sequence.filter((_, i) => held[i]).map((n) => n.id));
  }, [rows, store.nodes]);

  const load = useCallback(async () => {
    if (!store.project) return;
    setLoading(true);
    try {
      const out = await api.script(store.project.id, { start_id: startId, choices });
      setRows(out.rows);
      setScenes(out.scenes);
      if (!startId && out.start) setStartId(out.start);
      setChoices((current) => ({ ...out.taken, ...current }));
    } catch (e) {
      store.fail(String(e));
    } finally {
      setLoading(false);
    }
  }, [store.project?.id, startId, JSON.stringify(choices)]);

  useEffect(() => { load(); }, [load]);

  // Reload when generation or another view changes the graph underneath us.
  useEffect(() => { load(); }, [store.nodes.length, store.edges.length]);

  useEffect(() => {
    if (!focusNext.current) return;
    const id = focusNext.current;
    focusNext.current = null;
    requestAnimationFrame(() => {
      const el = document.querySelector<HTMLTextAreaElement>(`[data-row="${id}"]`);
      el?.focus();
      el?.setSelectionRange(el.value.length, el.value.length);
    });
  }, [rows]);

  if (!store.project) return null;

  const patchRow = (row: ScriptRow, data: any) => {
    if (!row.node_id) return;
    setRows((current) => current.map((r) =>
      r.node_id === row.node_id ? { ...r, ...data } : r));
    store.patchNode(row.node_id, { data }, `script:${row.node_id}`);
  };

  /** Notes and the label pin live on the node, not in `data`, so they need
   *  their own path -- patching them as data would bury them where nothing
   *  reads them. */
  const patchNodeField = (row: ScriptRow, patch: any) => {
    if (!row.node_id) return;
    setRows((current) => current.map((r) =>
      r.node_id === row.node_id ? { ...r, ...patch } : r));
    store.patchNode(row.node_id, patch, `script-node:${row.node_id}`);
  };

  const addAfter = async (row: ScriptRow, kind: string) => {
    if (!row.node_id) return;
    setBusy(true);
    try {
      const make = NEW_NODE[kind] ?? (() => ({}));
      const data = make({
        speaker: lastSpeaker(rows, row) ?? store.characters[0]?.id ?? null,
      });
      const node = await store.insertAfter(row.node_id, "out",
                                           kind as NodeType, data);
      // Only the text kinds get focus -- landing the cursor in a choice or a
      // stage node would put it somewhere with nothing to type.
      if (node && (kind === "say" || kind === "narration")) focusNext.current = node.id;
      await load();
    } finally {
      setBusy(false);
    }
  };

  const removeRow = async (row: ScriptRow, cascade = false) => {
    if (!row.node_id) return;
    setBusy(true);
    try {
      // heal=true rejoins the path across the gap; cascade takes everything
      // below with it, which is the canvas's "delete below" behaviour.
      await store.removeNode(row.node_id, !cascade, cascade);
      await load();
    } finally {
      setBusy(false);
    }
  };

  /** Every generation action a row can start. Queued, then the job dock and
   *  the graph subscription bring the result back. */
  const runGen = async (fn: () => Promise<{ job_id: string }>) => {
    setBusy(true);
    try {
      await fn();
      store.notify("queued");
      store.refreshJobs();
    } catch (e) {
      store.fail(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = async (event: React.KeyboardEvent, row: ScriptRow, index: number) => {
    const el = event.target as HTMLTextAreaElement;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      await addAfter(row, row.kind === "say" ? "say" : "narration");
    } else if (event.key === "Backspace" && el.value === "" && rows.length > 1) {
      event.preventDefault();
      const previous = rows.slice(0, index).reverse().find((r) => r.editable);
      await removeRow(row);
      if (previous?.node_id) focusNext.current = previous.node_id;
    } else if (event.key === "ArrowUp" && el.selectionStart === 0) {
      const previous = rows.slice(0, index).reverse().find((r) => r.editable);
      if (previous?.node_id) {
        event.preventDefault();
        focusNext.current = previous.node_id;
        setRows([...rows]);
      }
    } else if (event.key === "ArrowDown" && el.selectionStart === el.value.length) {
      const next = rows.slice(index + 1).find((r) => r.editable);
      if (next?.node_id) {
        event.preventDefault();
        focusNext.current = next.node_id;
        setRows([...rows]);
      }
    }
  };

  const wordCount = useMemo(
    () => rows.reduce((total, r) =>
      total + (r.kind === "say" || r.kind === "narration"
        ? (r.text || "").trim().split(/\s+/).filter(Boolean).length : 0), 0),
    [rows]);

  return (
    <div className="view script-view">
      <div className="script-bar">
        <select value={startId ?? ""} style={{ width: 220 }}
          onChange={(e) => {
            setStartId(e.target.value);
            setChoices({});
            writeActiveScene(store.project!.id, e.target.value);
          }}>
          {scenes.map((scene) => (
            <option key={scene.id} value={scene.id}>{scene.title}</option>
          ))}
        </select>
        <button onClick={load} disabled={loading}>{loading ? "…" : "Reload"}</button>
        <button onClick={() => setPasting("")}>Paste script…</button>
        <button onClick={async () => {
          const out = await api.scriptExport(store.project!.id,
            { start_id: startId, choices });
          await navigator.clipboard.writeText(out.text).catch(() => undefined);
          store.notify("script copied to the clipboard");
        }}>Copy as text</button>
        <span className="spacer" />
        <span className="hint">{wordCount} words on this path</span>
        <button disabled={!store.canUndo} onClick={() => store.undo().then(load)}
          title={store.undoLabel ? `Undo ${store.undoLabel}` : "Nothing to undo"}>↶</button>
        <button disabled={!store.canRedo} onClick={() => store.redo().then(load)}
          title={store.redoLabel ? `Redo ${store.redoLabel}` : "Nothing to redo"}>↷</button>
      </div>

      <div className="script-doc">
        {rows.length === 0 && !loading && (
          <p className="hint">
            Nothing on this path yet. Add a Scene on the canvas, or paste a
            script here.
          </p>
        )}

        {rows.map((row, index) => (
          <Row
            key={row.node_id ?? `row-${index}`}
            row={row}
            index={index}
            characters={store.characters}
            selected={store.selected === row.node_id}
            busy={busy}
            problems={store.problems.filter((p) => p.node_id === row.node_id)}
            nextId={rows[index + 1]?.node_id ?? null}
            underHeldCg={!!row.node_id && underHeldCg.has(row.node_id)}
            onSelect={() => row.node_id && store.select(row.node_id)}
            onPatch={(data) => patchRow(row, data)}
            onPatchNode={(patch) => patchNodeField(row, patch)}
            onKeyDown={(e) => onKeyDown(e, row, index)}
            onAdd={(kind) => addAfter(row, kind)}
            onDelete={(cascade) => removeRow(row, cascade)}
            onFollow={(optionId) => setChoices({ ...choices, [row.node_id!]: optionId })}
            onGenerateBranch={(optionId) => runGen(() =>
              api.generateBranch(store.project!.id, {
                choice_id: row.node_id, option_id: optionId,
              }))}
            onPlayHere={() => row.node_id && store.playFromNode(row.node_id)}
            onGen={runGen}
          />
        ))}

        {rows.length > 0 && <ScriptTail rows={rows} busy={busy}
          onAdd={addAfter} onGen={runGen} />}
      </div>

      {pasting !== null && (
        <PasteModal
          text={pasting}
          onText={setPasting}
          onClose={() => setPasting(null)}
          anchorId={rows[rows.length - 1]?.node_id ?? null}
          onDone={async () => { setPasting(null); await load(); }}
        />
      )}
    </div>
  );
}

/** Actions at the end of the path: add a node of any kind, or generate on. */
function ScriptTail({ rows, busy, onAdd, onGen }: {
  rows: ScriptRow[];
  busy: boolean;
  onAdd: (row: ScriptRow, kind: string) => void;
  onGen: (fn: () => Promise<{ job_id: string }>) => Promise<void>;
}) {
  const store = useStore();
  const [steering, setSteering] = useState(false);
  const last = rows[rows.length - 1];
  const lastEditable = [...rows].reverse().find((r) => r.editable) ?? last;

  return (
    <div className="script-tail">
      <InsertMenu label="+ line" busy={busy}
        onPick={(kind) => onAdd(lastEditable, kind)} />
      <button disabled={busy || !last?.node_id} onClick={() => onGen(() =>
        api.generateScene(store.project!.id, { anchor_id: last.node_id! }))}>
        Continue with AI
      </button>
      <button disabled={busy || !last?.node_id} onClick={() => setSteering(true)}
        title="Add a one-off note to this generation">
        Continue with a note…
      </button>
      {last?.node_id && <ContextPreviewButton nodeId={last.node_id}
        className="ghost" label="Preview prompt" />}
      <NextSceneLink fromId={last?.node_id ?? null} fromKind={last?.kind} />

      {steering && last?.node_id && (
        <SteerModal mode="scene"
          initial={store.steerNotes[last.node_id] ?? ""}
          onClose={() => setSteering(false)}
          onSubmit={(instruction) => {
            setSteering(false);
            store.rememberSteerNote(last.node_id!, instruction);
            onGen(() => api.generateScene(store.project!.id, {
              anchor_id: last.node_id!, instruction,
            }));
          }} />
      )}
    </div>
  );
}

/** A "+" that can add any node type, not only the two text ones. Without it
 *  the Script view cannot write a branching scene at all -- you had to go to
 *  the canvas just to drop in a choice. */
function InsertMenu({ label, busy, onPick }: {
  label: string; busy: boolean; onPick: (kind: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="insert-menu">
      <button className={label === "+" ? "tiny ghost" : ""} disabled={busy}
        title="Insert a node below" onClick={() => setOpen(!open)}>{label}</button>
      {open && (
        <>
          <div className="menu-scrim" onClick={() => setOpen(false)} />
          <div className="menu">
            {INSERTABLE.map(([kind, name]) => (
              <button key={kind} onClick={() => { setOpen(false); onPick(kind); }}>
                {name}
              </button>
            ))}
          </div>
        </>
      )}
    </span>
  );
}

function lastSpeaker(rows: ScriptRow[], row: ScriptRow): string | null {
  const index = rows.findIndex((r) => r.node_id === row.node_id);
  for (let i = index; i >= 0; i -= 1) {
    if (rows[i].kind === "say" && rows[i].speaker) return rows[i].speaker;
  }
  return null;
}

/* ------------------------------------------------------------------- rows */

function Row({ row, index, characters, selected, busy, problems, nextId, underHeldCg,
               onSelect, onPatch, onPatchNode, onKeyDown,
               onAdd, onDelete, onFollow, onGenerateBranch, onPlayHere, onGen }: {
  row: ScriptRow;
  index: number;
  characters: any[];
  selected: boolean;
  busy: boolean;
  problems: { severity: string; message: string }[];
  nextId: string | null;
  underHeldCg: boolean;
  onSelect: () => void;
  onPatch: (data: any) => void;
  onPatchNode: (patch: any) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  onAdd: (kind: string) => void;
  onDelete: (cascade: boolean) => void;
  onFollow: (optionId: string) => void;
  onGenerateBranch: (optionId: string) => void;
  onPlayHere: () => void;
  onGen: (fn: () => Promise<{ job_id: string }>) => Promise<void>;
}) {
  const store = useStore();
  const [open, setOpen] = useState(false);

  const badges = (
    <span className="row-badges">
      {row.is_merge && <span className="pill" title="branches rejoin here">merge</span>}
      {row.label && <span className="pill mono" title="Ren'Py label">{row.label}</span>}
      {row.cg && <span className="pill purple" title="flagged for an event CG">CG</span>}
      {row.cg && row.cg_hold && (
        <span className="pill purple" title="This CG stays up for the lines after it">hold</span>
      )}
      {underHeldCg && !row.cg && (row.cg_clear
        ? <span className="pill purple" title="Takes the held CG down before this line">CG ✕</span>
        : <span className="pill purple" title="Plays over a CG held from an earlier line">⇣ CG</span>)}
      {row.sfx_cue && !row.statuses?.sfx && (
        <span className="pill teal" title={`sound: ${row.sfx_cue}`}>♫</span>
      )}
      {row.notes && <span className="pill" title={row.notes}>note</span>}
      {row.variant_count > 1 && (
        <span className="pill" title={`${row.variant_count} takes`}>
          {row.variant_count} takes
        </span>
      )}
      {row.statuses?.audio === "stale" && <span className="pill warn">voice stale</span>}
      {row.statuses?.image === "stale" && <span className="pill warn">image stale</span>}
      {row.has_voice && row.statuses?.audio === "ready" && <span className="pill good">♪</span>}
      {problems.map((p, i) => (
        <span key={i} className={`pill ${p.severity === "error" ? "bad" : "warn"}`}
          title={p.message}>{p.severity === "error" ? "!" : "?"}</span>
      ))}
    </span>
  );

  // Suggestions from the last generation. Rendered under whatever row carries
  // them, and switching to "replace" once the row has been continued past --
  // appending there would splice a chain in front of existing content.
  const continuations = row.continuations?.length > 0 && row.node_id && (
    <div className="row-continuations">
      <span className="hint">{nextId ? "Instead, continue with" : "Continue with"}</span>
      {row.continuations.map((text, i) => (
        <button key={i} className="continuation" disabled={busy}
          title={nextId
            ? "Replace what follows with this direction — the old block is parked, not deleted"
            : "Generate the next stretch in this direction"}
          onClick={() => onGen(() => nextId
            ? api.regenerateFrom(store.project!.id, nextId, "", "", text)
            : api.generateScene(store.project!.id, {
                anchor_id: row.node_id!, continuation: text,
              }))}>
          {text}
        </button>
      ))}
      <button className="link-ish" disabled={busy}
        onClick={() => onPatch({ _continuations: [] })}>Dismiss</button>
    </div>
  );

  if (row.kind === "scene") {
    return (
      <div className={`script-row scene ${selected ? "on" : ""}`} onClick={onSelect}>
        <div className="row-head">
          <span className="tag">scene</span>
          {badges}
          <span className="spacer" />
          <span className="row-actions">
            <button className={`tiny ghost ${open ? "on" : ""}`} title="details"
              onClick={() => setOpen(!open)}>⋯</button>
            <button className="tiny ghost" onClick={onPlayHere}>▶ play here</button>
            <InsertMenu label="+" busy={busy} onPick={onAdd} />
          </span>
        </div>
        <input className="scene-title" value={row.text}
          onChange={(e) => onPatch({ title: e.target.value })} />
        {row.meta && <div className="hint">{row.meta}</div>}
        {open && (
          <RowDetails row={row} busy={busy} onPatch={onPatch}
            onPatchNode={onPatchNode} onGen={onGen} />
        )}
        {continuations}
      </div>
    );
  }

  if (row.kind === "choice") {
    return (
      <div className={`script-row choice ${selected ? "on" : ""}`} onClick={onSelect}>
        <div className="row-head">
          <span className="tag">choice</span>
          {badges}
          <span className="spacer" />
          <span className="hint">{row.meta}</span>
        </div>
        <input className="choice-prompt" value={row.text} placeholder="What do you say?"
          onChange={(e) => onPatch({ prompt: e.target.value })} />
        <div className="options">
          {row.options.map((option) => (
            <div key={option.id}
              className={`option ${row.selected_option === option.id ? "following" : ""}`}>
              <button className="follow" title="follow this branch"
                onClick={() => onFollow(option.id)}>
                {row.selected_option === option.id ? "●" : "○"}
              </button>
              <input value={option.label}
                onChange={(e) => onPatch({
                  options: row.options.map((o) => (o.id === option.id
                    ? { ...o, label: e.target.value } : o)),
                })} />
              <input className="cond" value={option.condition} placeholder="if…"
                onChange={(e) => onPatch({
                  options: row.options.map((o) => (o.id === option.id
                    ? { ...o, condition: e.target.value } : o)),
                })} />
              {option.wired
                ? <span className="pill">wired</span>
                : <button className="tiny" disabled={busy}
                    onClick={() => onGenerateBranch(option.id)}>
                    generate
                  </button>}
            </div>
          ))}
        </div>
        <div className="row-actions">
          <button className={`tiny ghost ${open ? "on" : ""}`} title="details"
            onClick={() => setOpen(!open)}>⋯</button>
          <button className="tiny ghost" title="play from here" onClick={onPlayHere}>▶</button>
          <InsertMenu label="+" busy={busy} onPick={onAdd} />
          <button className="tiny ghost danger" title="delete (shift: and everything below)"
            disabled={busy} onClick={(e) => onDelete(e.shiftKey)}>×</button>
        </div>
        {open && (
          <RowDetails row={row} busy={busy} onPatch={onPatch}
            onPatchNode={onPatchNode} onGen={onGen} />
        )}
      </div>
    );
  }

  if (row.kind === "say" || row.kind === "narration") {
    const isSay = row.kind === "say";
    return (
      <div className={`script-row ${row.kind} ${selected ? "on" : ""}`} onClick={onSelect}>
        <div className="row-head">
          {isSay ? (
            <select className="speaker-select" value={row.speaker ?? ""}
              style={{ color: row.speaker_color ?? undefined }}
              onChange={(e) => onPatch({ speaker: e.target.value })}>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>{c.display_name}</option>
              ))}
            </select>
          ) : (
            <span className="tag">narration</span>
          )}
          {isSay && (
            <button className="tiny ghost attrs" onClick={() => setOpen(!open)}>
              {row.expression !== "neutral" || row.emotion !== "neutral"
                ? `${row.expression}, ${row.emotion} ${row.intensity.toFixed(1)}`
                : "add delivery"}
            </button>
          )}
          {badges}
          <span className="spacer" />
          <span className="row-actions">
            <button className={`tiny ghost ${open ? "on" : ""}`} title="details"
              onClick={() => setOpen(!open)}>⋯</button>
            <button className="tiny ghost" title="play from here" onClick={onPlayHere}>▶</button>
            <InsertMenu label="+" busy={busy} onPick={onAdd} />
            <button className="tiny ghost danger" title="delete line"
              disabled={busy} onClick={(e) => onDelete(e.shiftKey)}>×</button>
          </span>
        </div>

        <textarea
          data-row={row.node_id ?? ""}
          className={isSay ? "line say-line" : "line narration-line"}
          rows={1}
          value={row.text}
          placeholder={isSay ? "…" : "narration"}
          onChange={(e) => {
            onPatch({ text: e.target.value });
            e.target.style.height = "auto";
            e.target.style.height = `${e.target.scrollHeight}px`;
          }}
          onKeyDown={onKeyDown}
          ref={(el) => {
            if (el) {
              el.style.height = "auto";
              el.style.height = `${el.scrollHeight}px`;
            }
          }}
        />

        {row.image_hash && (
          // The shot this beat got, at reading size. Clicking opens it on the
          // canvas, where the CG panel is.
          <button className="row-thumb" title="Open this node's CG in the inspector"
            onClick={(e) => {
              e.stopPropagation();
              if (row.node_id) store.focusNode(row.node_id);
            }}>
            <img src={api.assetUrl(row.image_hash)} loading="lazy" decoding="async" alt="" />
          </button>
        )}

        {open && (
          <RowDetails row={row} busy={busy} onPatch={onPatch} underHeldCg={underHeldCg}
            onPatchNode={onPatchNode} onGen={onGen} />
        )}
        {continuations}
      </div>
    );
  }

  if (row.kind === "loop") {
    return <div className="script-row structural"><span className="tag">↻</span> {row.meta}</div>;
  }

  // stage / set / condition / audio / end. Their fields are still edited on the
  // canvas -- each needs a different editor and cramming five of them into a
  // row would make the writing surface worse to write in. Everything that is
  // *not* type-specific is here, though: details, insert, delete, generate.
  return (
    <div className={`script-row structural ${selected ? "on" : ""}`} onClick={onSelect}>
      <div className="row-head">
        <span className="tag">{row.kind}</span>
        <span className="structural-text">{row.text || row.meta}</span>
        {(row.kind === "condition" || row.kind === "hub") && (
          <span className="options inline">
            {row.options.map((branch) => (
              <button key={branch.id}
                className={`tiny ${row.selected_option === branch.id ? "primary" : ""}`}
                // A hub's gate is structured and edited in the inspector; here
                // it is what tells you why a topic is locked.
                title={branch.condition || undefined}
                onClick={() => onFollow(branch.id)}>
                {branch.label}
                {branch.condition ? " 🔒" : ""}
              </button>
            ))}
          </span>
        )}
        {badges}
        <span className="spacer" />
        <span className="row-actions">
          <button className={`tiny ghost ${open ? "on" : ""}`} title="details"
            onClick={() => setOpen(!open)}>⋯</button>
          <button className="tiny ghost" title="play from here" onClick={onPlayHere}>▶</button>
          <InsertMenu label="+" busy={busy} onPick={onAdd} />
          <button className="tiny ghost danger" title="delete (shift: and everything below)"
            disabled={busy} onClick={(e) => onDelete(e.shiftKey)}>×</button>
        </span>
      </div>
      {open && (
        <RowDetails row={row} busy={busy} onPatch={onPatch}
          onPatchNode={onPatchNode} onGen={onGen} />
      )}
      {continuations}
    </div>
  );
}

/**
 * Everything the canvas inspector holds for a line, inline under the row:
 * performance, sound, the CG flag, notes, the label pin, takes, and the whole
 * generation menu. The point is that writing a scene never sends you to
 * another view mid-sentence.
 */
function RowDetails({ row, busy, onPatch, onPatchNode, onGen, underHeldCg = false }: {
  row: ScriptRow;
  busy: boolean;
  underHeldCg?: boolean;
  onPatch: (data: any) => void;
  onPatchNode: (patch: any) => void;
  onGen: (fn: () => Promise<{ job_id: string }>) => Promise<void>;
}) {
  const store = useStore();
  const [steering, setSteering] = useState<null | "scene" | "regen">(null);
  const isSay = row.kind === "say";
  const node = store.nodes.find((n) => n.id === row.node_id);

  return (
    <div className="row-details" onClick={(e) => e.stopPropagation()}>
      {isSay && (
        <>
          <div className="section-title">Performance</div>
          <div className="attr-row">
            <select value={row.expression}
              onChange={(e) => onPatch({ expression: e.target.value })}>
              {(store.vocab?.expressions ?? []).map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <select value={row.emotion} onChange={(e) => onPatch({ emotion: e.target.value })}>
              {(store.vocab?.emotions ?? []).map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <select value={row.pose || "front"}
              onChange={(e) => onPatch({ pose: e.target.value })}>
              {(store.vocab?.poses ?? []).map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <input type="range" min={0} max={1} step={0.05} value={row.intensity}
              onChange={(e) => onPatch({ intensity: parseFloat(e.target.value) })} />
            <span className="hint">{row.intensity.toFixed(2)}</span>
            <input placeholder="delivery note" value={row.delivery}
              onChange={(e) => onPatch({ delivery: e.target.value })} />
          </div>
        </>
      )}

      {(isSay || row.kind === "narration") && (
        <>
          <div className="section-title">Sound and image</div>
          <div className="attr-row">
            <input placeholder="sound cue — knock at the door" value={row.sfx_cue}
              onChange={(e) => onPatch({ sfx_cue: e.target.value })} />
            <label className="row" style={{ gap: 4 }}>
              <input type="checkbox" checked={row.cg}
                onChange={(e) => onPatch({ cg: e.target.checked })} />
              <span className="hint">event CG</span>
            </label>
            {row.cg && (
              <label className="row" style={{ gap: 4 }}
                title="The lines after this one play over the CG, until the next CG, a scene change or a line set to clear it">
                <input type="checkbox" checked={row.cg_hold}
                  onChange={(e) => onPatch({ cg_hold: e.target.checked })} />
                <span className="hint">keep on screen</span>
              </label>
            )}
            {underHeldCg && !row.cg && (
              <label className="row" style={{ gap: 4 }}
                title="Dissolve the held CG back to the sprites as this line starts">
                <input type="checkbox" checked={row.cg_clear}
                  onChange={(e) => onPatch({ cg_clear: e.target.checked })} />
                <span className="hint">clear held CG</span>
              </label>
            )}
            <input placeholder="shot hint" value={row.shot_hint}
              onChange={(e) => onPatch({ shot_hint: e.target.value })} />
          </div>
        </>
      )}

      <div className="section-title">Node</div>
      <div className="attr-row">
        <input placeholder="notes to yourself" value={row.notes}
          onChange={(e) => onPatchNode({ notes: e.target.value })} />
        <label className="row" style={{ gap: 4 }}>
          <input type="checkbox" checked={row.pinned_label}
            onChange={(e) => onPatchNode({ pinned_label: e.target.checked })} />
          <span className="hint">pin as jump target</span>
        </label>
      </div>

      {node && <VariantStrip node={node} />}

      <div className="section-title">Generate</div>
      <div className="attr-row wrap">
        <button disabled={busy} onClick={() => onGen(() =>
          api.regenerateFrom(store.project!.id, row.node_id!))}
          title="Keeps everything upstream; parks the subtree below">
          Regenerate from here
        </button>
        {row.gen_id && (
          <button disabled={busy} onClick={() => onGen(() =>
            api.regenerateBlock(store.project!.id, row.node_id!))}
            title="Redo the whole generation this line came from">
            Regenerate this block
          </button>
        )}
        <button disabled={busy} onClick={() => setSteering("regen")}>
          Regenerate with a note…
        </button>
        <button disabled={busy} onClick={() => setSteering("scene")}>
          Continue with a note…
        </button>
        {isSay && <VoiceLineButton node={node} busy={busy} onGen={onGen} />}
        <ContextPreviewButton nodeId={row.node_id!} className="" label="Preview prompt" />
      </div>

      {steering && (
        <SteerModal mode={steering}
          initial={store.steerNotes[row.node_id!] ?? ""}
          onClose={() => setSteering(null)}
          onSubmit={(instruction) => {
            const mode = steering;
            setSteering(null);
            store.rememberSteerNote(row.node_id!, instruction);
            onGen(() => mode === "regen"
              ? api.regenerateFrom(store.project!.id, row.node_id!, "", instruction)
              : api.generateScene(store.project!.id, {
                  anchor_id: row.node_id!, instruction,
                }));
          }} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ paste */

function PasteModal({ text, onText, onClose, anchorId, onDone }: {
  text: string;
  onText: (v: string) => void;
  onClose: () => void;
  anchorId: string | null;
  onDone: () => void;
}) {
  const store = useStore();
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!text.trim()) { setPreview(null); return; }
    const timer = setTimeout(async () => {
      try {
        setPreview(await api.scriptParse(store.project!.id, text));
      } catch {
        setPreview(null);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [text]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="head">
          <span className="title">Paste a script</span>
          {preview && <span className="pill">{preview.count} nodes</span>}
          <span className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!preview?.count || !anchorId || busy}
            onClick={async () => {
              setBusy(true);
              try {
                const out = await api.scriptImport(store.project!.id, {
                  text, anchor_id: anchorId!,
                });
                store.notify(`added ${out.count} nodes`);
                await store.refreshGraph();
                onDone();
              } catch (e) {
                store.fail(String(e));
              } finally {
                setBusy(false);
              }
            }}>Append {preview?.count ?? 0} nodes</button>
        </div>
        <div className="content">
          <div className="grid2" style={{ alignItems: "start" }}>
            <div>
              <textarea rows={20} value={text} autoFocus
                placeholder={"AIKO (looking_away, melancholy 0.6): You came.\n\n"
                  + "The door sticks, then gives all at once.\n\n"
                  + "$ affinity += 1\n\n"
                  + "? What do you say?\n- Push her on it\n- Ask about it [knows_letter]"}
                onChange={(e) => onText(e.target.value)}
                style={{ fontFamily: "var(--mono)", fontSize: 12 }} />
              <div className="hint">
                <code>NAME:</code> a line · a bare paragraph is narration ·
                {" "}<code>$</code> sets a flag · <code>?</code> a choice with
                {" "}<code>-</code> options · <code>&gt;</code> stage directions ·
                {" "}<code>```</code> raw Ren'Py.
              </div>
            </div>
            <div>
              <div className="section-title">Preview</div>
              {!preview && <div className="hint">Type or paste to see what this becomes.</div>}
              {preview && (
                <>
                  <div className="list">
                    {preview.nodes.map((node: any, i: number) => (
                      <div key={i} className="item">
                        <span className="pill">{node.type}</span>
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                                       whiteSpace: "nowrap" }}>
                          {node.data.text ?? node.data.prompt ?? node.data.title
                            ?? JSON.stringify(node.data).slice(0, 50)}
                        </span>
                      </div>
                    ))}
                  </div>
                  {preview.warnings.map((warning: string, i: number) => (
                    <div key={i} className="hint warn">⚠ {warning}</div>
                  ))}
                  <div className="hint" style={{ marginTop: 8 }}>
                    Importing only ever creates new nodes — nothing existing is
                    rewritten, so no label moves.
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
