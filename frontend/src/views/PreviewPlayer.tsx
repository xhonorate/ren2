import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PlayEditPanel, { type PanelTab, type Render } from "../components/PlayEditPanel";
import { api } from "../lib/api";
import {
  EMPTY_STAGE, FROM_NODE, applyEffect, backgroundFor, backgroundRenderKey, evaluate, gatesMet,
  nearestSprite, nextInRoute, requirementText, run, spriteRenderKey,
  type Backdrop, type Frame, type RunResult, type Sprite, type Stage, type StageCg,
} from "../lib/player";
import { bandLabelFor, bandLabels, describeFlag } from "../lib/stats";
import { useStore } from "../lib/store";
import { useIsNarrow } from "../lib/useMediaQuery";
import type { PreviewOp, PreviewProgram } from "../lib/types";

const VOICE_KEY = "vn:previewVoice";
const PANEL_KEY = "vn:playPanel";

/** Job statuses a render queued from the player is finished in. */
const TERMINAL = new Set(["done", "error", "cancelled"]);

/** Voice is on unless it was turned off -- remembered across visits. */
function readVoiceOn(): boolean {
  try {
    return localStorage.getItem(VOICE_KEY) !== "off";
  } catch {
    return true;
  }
}

/** Whether the edit panel was left open -- remembered across visits. */
function readPanelOpen(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) === "open";
  } catch {
    return false;
  }
}

/**
 * Interprets the preview program compiled from the same IR as the Ren'Py
 * build, so a branch can be tested in a second instead of launching Ren'Py.
 */
export default function PreviewPlayer() {
  const store = useStore();
  const narrow = useIsNarrow();
  const [program, setProgram] = useState<PreviewProgram | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [stack, setStack] = useState<Frame[]>([]);
  const [flags, setFlags] = useState<Record<string, any>>({});
  const [background, setBackground] = useState<Backdrop>(EMPTY_STAGE.background);
  const [sprites, setSprites] = useState<Sprite[]>([]);
  const [cg, setCg] = useState<StageCg | null>(null);
  const [line, setLine] = useState<PreviewOp | null>(null);
  const [menu, setMenu] = useState<PreviewOp | null>(null);
  const [ended, setEnded] = useState<string | null>(null);
  const [taken, setTaken] = useState<Set<string>>(new Set());
  // Which labels this run has entered. Only route scenes matter, but tracking
  // every label is simpler and costs nothing -- it is what lets a hub option
  // targeting a route resolve to the next scene the player has not seen.
  const [visited, setVisited] = useState<Set<string>>(new Set());
  const [history, setHistory] = useState<{ name: string | null; text: string }[]>([]);
  const [overrides, setOverrides] = useState<Record<string, any>>({});
  const [showFlags, setShowFlags] = useState(false);
  // Bumped each time playback puts a new line or menu up -- and not when an
  // edit re-runs the one already there, which is what keeps the voice from
  // restarting on every keystroke in the inspector.
  const [shownSeq, setShownSeq] = useState(0);

  // -- editing what is on screen ---------------------------------------------
  const [panel, setPanel] = useState<PanelTab | null>(() => (readPanelOpen() ? "node" : null));
  const [renders, setRenders] = useState<Record<string, Render>>({});
  // The line on screen was deleted or regenerated out from under the player.
  const [detached, setDetached] = useState(false);
  // The stage as it stood before the node on screen ran its ops (RunResult).
  const before = useRef<Stage>(EMPTY_STAGE);
  // Edited since the program was compiled: Restart recompiles instead of
  // replaying the copy it has, whose from-node block may be stale.
  const dirty = useRef(false);
  const resyncToken = useRef(0);

  // Which stat just changed band, and what it was before. A number moving from
  // 38 to 41 is the moment the writing is supposed to change register, and it
  // is the one thing a row of raw integers cannot show you -- so it is called
  // out for a few seconds rather than left to be noticed.
  const stats = store.project?.character_stats ?? [];
  const bands = useMemo(() => bandLabels(stats, flags), [stats, flags]);
  const seenBands = useRef<Record<string, string>>({});
  const [crossed, setCrossed] = useState<Record<string, string>>({});

  useEffect(() => {
    const moved: Record<string, string> = {};
    for (const [name, label] of Object.entries(bands)) {
      const was = seenBands.current[name];
      if (was !== undefined && was !== label) moved[name] = was;
    }
    seenBands.current = bands;
    if (!Object.keys(moved).length) return;
    setCrossed(moved);
    const timer = setTimeout(() => setCrossed({}), 6000);
    return () => clearTimeout(timer);
  }, [bands]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [voiceOn, setVoiceOn] = useState(readVoiceOn);
  const fromNode = store.playFrom.nodeId;

  // The voice follows the line on screen. Advancing cuts it off, as Ren'Py
  // does by default, and unmuting replays the current line from the start. A
  // re-voiced line has a new hash, so it plays the new take straight away.
  useEffect(() => {
    const audio = audioRef.current;
    const clip = line?.voice;
    if (!audio || !clip || !voiceOn) return;
    audio.src = api.assetUrl(clip);
    // Rejected when the browser has not seen a click on the page yet; the
    // next line, which is advanced by one, plays normally.
    audio.play().catch(() => {});
    return () => { audio.pause(); };
  }, [shownSeq, line?.voice, voiceOn]);

  const toggleVoice = () => {
    const next = !voiceOn;
    setVoiceOn(next);
    try {
      localStorage.setItem(VOICE_KEY, next ? "on" : "off");
    } catch { /* private mode: the choice just lasts this session */ }
  };

  const load = useCallback(async () => {
    if (!store.project) return;
    try {
      const next = await api.preview(store.project.id, fromNode ?? undefined);
      setProgram(next);
      setError(null);
      dirty.current = false;
      setOverrides(store.playFrom.flags ?? {});
      reset(next, undefined, store.playFrom.flags ?? {});
    } catch (e) {
      setError(String(e));
    }
  }, [store.project?.id, fromNode, JSON.stringify(store.playFrom.flags)]);

  useEffect(() => { load(); }, [load]);

  function reset(next: PreviewProgram, entry?: string, flagOverrides?: Record<string, any>) {
    const start = entry ?? next.entry;
    setFlags({ ...next.flags, ...(flagOverrides ?? overrides) });
    setSprites([]);
    setCg(null);
    setBackground(EMPTY_STAGE.background);
    before.current = EMPTY_STAGE;
    setLine(null);
    setMenu(null);
    setEnded(null);
    setDetached(false);
    setTaken(new Set());
    setVisited(new Set(start ? [start] : []));
    setHistory([]);
    setStack(start && next.blocks[start] ? [{ ops: next.blocks[start], index: 0, label: start }] : []);
  }

  /** Start over. After an edit the program is recompiled first, so the
   *  replay is of the story as it is now. */
  const restart = async (entry?: string, flagOverrides?: Record<string, any>) => {
    if (!program) return;
    if (!dirty.current || !store.project) {
      reset(program, entry, flagOverrides);
      return;
    }
    try {
      const next = await api.preview(store.project.id, fromNode ?? undefined);
      setProgram(next);
      dirty.current = false;
      reset(next, entry, flagOverrides);
    } catch (e) {
      setError(String(e));
    }
  };

  /** Commit a run. `fresh` is playback moving on; otherwise it is the line
   *  already on screen being re-run after an edit. */
  const apply = (result: RunResult, fresh: boolean) => {
    const { stop } = result;
    setVisited(result.seen);
    if (result.notes.length) {
      setHistory((h) => [...h, ...result.notes.map((text) => ({ name: null, text }))]);
    }
    if (stop.kind === "halt") {
      setEnded(stop.message);
      setStack([]);
      return;
    }
    setFlags(result.flags);
    setBackground(result.stage.background);
    setSprites(result.stage.sprites);
    setCg(result.stage.cg);
    if (stop.kind === "end") {
      setEnded(stop.message);
      setStack([]);
      return;
    }
    setStack(result.frames);
    before.current = result.before;
    if (stop.kind === "say") {
      setLine(stop.op);
      setMenu(null);
      if (fresh) {
        setHistory((h) => [...h.slice(-60), { name: stop.op.name ?? null, text: stop.op.text }]);
      }
    } else {
      setMenu(stop.op);
      setLine(null);
    }
    if (fresh) {
      setDetached(false);
      setShownSeq((n) => n + 1);
    }
  };

  const step = useCallback(() => {
    if (!program || !stack.length) return;
    apply(run(program, { frames: stack, flags, stage: { background, sprites, cg }, visited }), true);
  }, [program, stack, flags, background, sprites, cg, visited]);

  // -- re-running the line on screen after an edit -----------------------------
  // Read through a ref: the resync lands after a network round trip, and has
  // to check what is on screen *then*, not what was when it was scheduled.
  const latest = useRef({ program, stack, flags, line, menu, ended, visited });
  latest.current = { program, stack, flags, line, menu, ended, visited };

  /**
   * Recompile from the node on screen and run it again in place, against the
   * stage this playthrough built. Everything already played -- flags, visited
   * scenes, taken options -- is kept, so an edit shows up without replaying
   * the route to get back here.
   */
  const resync = useCallback(async () => {
    const pid = store.project?.id;
    const now = latest.current;
    const nodeId: string | undefined = (now.line ?? now.menu)?.node;
    if (!pid || !now.program || now.ended || !nodeId || !now.stack.length) return;
    const token = ++resyncToken.current;
    let next: PreviewProgram;
    try {
      next = await api.preview(pid, nodeId);
    } catch {
      return;   // a failed refresh leaves the line as it was; Recompile retries
    }
    const cur = latest.current;
    if (token !== resyncToken.current || cur.ended || !cur.program || !cur.stack.length
        || (cur.line ?? cur.menu)?.node !== nodeId) return;
    dirty.current = true;

    const block = next.entry === FROM_NODE ? next.blocks[FROM_NODE] : undefined;
    if (!block) {
      setDetached(true);
      return;
    }
    setDetached(false);
    // Skip the restored setup: the stage this run built is the right one, and
    // the canonical path the setup is resolved from may not be the one played.
    const start = Math.max(0, block.findIndex((op) => !op.restored));
    const top = cur.stack[cur.stack.length - 1];
    const frames = [...cur.stack.slice(0, -1), { ops: block, index: start, label: top.label }];
    // Keep this program's own entry, so Restart and the scene picker still
    // mean what they meant before the edit.
    const blocks = { ...next.blocks };
    if (cur.program.blocks[FROM_NODE]) blocks[FROM_NODE] = cur.program.blocks[FROM_NODE];
    else delete blocks[FROM_NODE];
    const merged: PreviewProgram = { ...next, entry: cur.program.entry, blocks };
    setProgram(merged);
    apply(run(merged, {
      frames, flags: cur.flags, stage: before.current, visited: cur.visited,
    }), false);
  }, [store.project?.id]);

  // Any edit -- typed in the inspector, a finished voice or CG job, a
  // regenerated sprite -- re-runs the line on screen once things settle.
  useEffect(() => {
    const timer = setTimeout(resync, 450);
    return () => clearTimeout(timer);
  }, [store.nodes, store.edges, store.characters, store.locations, resync]);

  // Advance into the first line once a program loads.
  useEffect(() => {
    if (program && stack.length && !line && !menu && !ended) step();
  }, [program, stack.length, line, menu, ended, step]);

  const advance = useCallback(() => {
    if (line && !menu) { setLine(null); step(); }
  }, [line, menu, step]);

  // -- the edit panel ---------------------------------------------------------
  const shownNode: string | undefined = (line ?? menu)?.node;

  const openPanel = (tab: PanelTab) => {
    if (tab === "node" && shownNode) store.select(shownNode);
    setPanel(tab);
    try { localStorage.setItem(PANEL_KEY, "open"); } catch { /* private mode */ }
  };
  const closePanel = () => {
    setPanel(null);
    try { localStorage.setItem(PANEL_KEY, "closed"); } catch { /* private mode */ }
  };
  const openNode = (id: string) => {
    store.select(id);
    setPanel("node");
  };

  // While the panel is open the inspector follows playback onto each new line.
  useEffect(() => {
    if (panel && shownNode) store.select(shownNode);
  }, [shownSeq]);

  // A sprite tab whose character has left the stage falls back to the node.
  const tab: PanelTab | null = panel?.startsWith("sprite:")
    && !sprites.some((s) => `sprite:${s.character}` === panel) ? "node" : panel;

  // Renders queued from the panel. Kept here rather than in the panel so the
  // stage can badge the sprite or backdrop that is being re-shot.
  useEffect(() => {
    const keys = Object.keys(renders);
    if (!keys.length) return;
    const byId = new Map(store.jobs.map((job) => [job.id, job]));
    const finished = keys.filter((key) => TERMINAL.has(byId.get(renders[key].job)?.status ?? ""));
    if (!finished.length) return;
    for (const key of finished) {
      // A failure is already a toast from the job event stream.
      if (byId.get(renders[key].job)?.status === "done") store.notify(`${renders[key].label} ready`);
    }
    setRenders((current) => {
      const next = { ...current };
      for (const key of finished) delete next[key];
      return next;
    });
  }, [store.jobs, renders]);

  const renderBadge = (key: string) => {
    const render = renders[key];
    if (!render) return null;
    const job = store.jobs.find((j) => j.id === render.job);
    return (
      <span className="stage-busy">
        {job?.status === "running" ? `rendering ${Math.round(job.progress * 100)}%` : "queued…"}
      </span>
    );
  };

  // A visual novel advances on space or enter, not only on a click. The
  // textbox is a div, so the key handling has to be wired up by hand.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)) return;
      // Keys pressed while working in the editor belong to the editor.
      if (target?.closest?.(".play-panel, .modal-backdrop")) return;
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) store.redo(); else store.undo();
        return;
      }
      if (mod && event.key.toLowerCase() === "y") {
        event.preventDefault();
        store.redo();
        return;
      }
      if (mod || event.altKey) return;
      if (event.key === " " || event.key === "Enter") {
        // A keyboard-focused sprite opens itself instead.
        if (target?.closest?.("[role='button']")) return;
        event.preventDefault();
        advance();
      } else if (event.key.toLowerCase() === "e") {
        if (panel) closePanel(); else openPanel("node");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, panel, shownNode]);

  const choose = (option: any) => {
    if (!program || !option.target) return;
    const block = program.blocks[option.target];
    if (!block) return;
    setTaken(new Set([...taken, option.id]));
    if (option.effects?.length) {
      setFlags((state) => {
        const next = { ...state };
        for (const eff of option.effects) {
          if (eff.var) applyEffect(next, eff.var, eff.op, eff.value);
        }
        return next;
      });
    }
    setMenu(null);
    setVisited((seen) => new Set(seen).add(option.target));
    const frames = [...stack];
    if (menu?.mode === "call") {
      frames.push({ ops: block, index: 0, label: option.target });
    } else {
      frames.pop();
      frames.push({ ops: block, index: 0, label: option.target });
    }
    setStack(frames);
    setLine(null);
  };

  if (!store.project) return null;

  if (error) {
    return <div className="view"><div className="hint bad">{error}</div></div>;
  }
  if (!program) return <div className="view"><p className="hint">Compiling preview…</p></div>;

  // What is drawn is read from the live characters, locations and nodes, so a
  // regenerated sprite, a new take or a retyped line appears the moment the
  // store has it -- the resync above catches up on everything else.
  const location = background.id
    ? store.locations.find((l) => l.id === background.id)
    : undefined;
  const backdrop = background.id
    ? backgroundFor(location?.variants ?? program.locations[background.id]?.variants,
                    background.time)
    : null;
  const lineNode = line?.node ? store.nodes.find((n) => n.id === line.node) : undefined;
  const lineText: string = typeof lineNode?.data?.text === "string" ? lineNode.data.text : line?.text;
  // A held CG belongs to the line it started on, which is the node to read the
  // picture from -- and to open when it is clicked.
  const cgNode = cg?.node ? store.nodes.find((n) => n.id === cg.node) : undefined;
  const cgImage: string | null = cgNode ? cgNode.data?.image_hash ?? null : cg?.image ?? null;
  const cgHeld = !!cg?.node && cg.node !== line?.node;
  const bgKey = background.id
    ? backgroundRenderKey(background.id, background.time || backdrop?.variant || "day")
    : "";

  const player = (
    <div className="player">
      <div className="stage">
        <div className={`bg${backdrop ? "" : " empty"}${tab === "background" ? " picked" : ""}`}
          style={backdrop ? { backgroundImage: `url(${api.assetUrl(backdrop.hash)})` } : undefined}
          title="Click to edit or regenerate the background"
          onClick={() => openPanel("background")}>
          {!backdrop && (background.id
            ? `no background generated for ${background.id}${background.time ? ` / ${background.time}` : ""}`
            : "no background set")}
        </div>
        {bgKey && renderBadge(bgKey)}
        <div className="sprites">
          {sprites.map((sprite) => {
            const character = store.characters.find((c) => c.id === sprite.character);
            const name = character?.display_name ?? sprite.name;
            const hash = character
              ? nearestSprite(character.assets.sprite_sheet, sprite.outfit, sprite.pose,
                              sprite.expression)?.hash ?? null
              : sprite.sprite;
            const tabId: PanelTab = `sprite:${sprite.character}`;
            return (
              <div key={sprite.character} role="button" tabIndex={0}
                className={`sprite ${sprite.position}${tab === tabId ? " picked" : ""}`}
                title={`Click to edit or regenerate ${name}'s sprite`}
                // A mouse click must not leave the sprite focused, or the
                // next Space re-opens it instead of advancing.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => openPanel(tabId)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openPanel(tabId);
                  }
                }}>
                {renderBadge(spriteRenderKey(sprite))}
                {hash ? (
                  <img src={api.assetUrl(hash)} alt={name} />
                ) : (
                  <div className="placeholder">
                    <strong>{name}</strong>
                    <span>{sprite.outfit || "—"}</span>
                    <span>{sprite.expression}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {cgImage && (
          // Over the sprites, as the build's zorder puts it: a character staged
          // while a CG is held is behind the picture, not in front of it.
          <img className="stage-cg" src={api.assetUrl(cgImage)} alt="event CG"
            title={cgHeld
              ? "Held from an earlier line — click to edit that line's CG"
              : "Click to edit or regenerate this CG"}
            onClick={() => (cg?.node ? openNode(cg.node) : openPanel("node"))} />
        )}
      </div>

      <div className="textbox" onClick={advance}>
        {ended && <div className="line"><em>— {ended} —</em></div>}

        {detached && (
          <div className="hint warn" style={{ marginBottom: 6 }}>
            This line is no longer in the story — it was deleted or regenerated.
            Restart, or play on to keep going from the copy on screen.
          </div>
        )}

        {line && (
          <>
            {line.name && <div className="who" style={{ color: line.color ?? undefined }}>{line.name}</div>}
            <div className={`line ${line.speaker ? "" : "narration"}`}>{lineText}</div>
            <div className="hint" style={{ marginTop: 8 }}>click or press space to continue</div>
          </>
        )}

        {menu && (
          <>
            {menu.prompt && <div className="line">{menu.prompt}</div>}
            <div className="menu-options">
              {menu.options.map((option: any) => {
                if (menu.exhaust && !option.repeatable && taken.has(option.id)) {
                  return null;
                }
                // A hub's gates arrive structured, so they are compared field
                // by field -- a Python expression is not a JavaScript one.
                const target = option.route
                  ? nextInRoute(program, option.route, visited)
                  : option.target;
                const met = gatesMet(option.requires, flags)
                  && (menu.hub
                    ? !(option.route && !target)
                    : evaluate(option.condition, flags));
                // Ren'Py hides an unmet option unless the hub asked for grey.
                if (menu.hub && !met && option.locked !== "grey") return null;
                const locked = menu.hub && !met;
                const usable = met && !!target;
                return (
                  <button key={option.id}
                    className={usable ? "" : "disabled"}
                    disabled={!usable}
                    onClick={() => choose({ ...option, target })}
                    title={locked || !met
                      ? requirementText(option)
                      : (!target ? "this option goes nowhere yet" : option.condition)}>
                    {locked
                      ? (option.locked_label || `${option.label} (locked)`)
                      : option.label}
                    {!locked && !target && !option.route && " — unwired"}
                    {!locked && !target && option.route && " — route finished"}
                  </button>
                );
              })}
            </div>
          </>
        )}

        {!line && !menu && !ended && <div className="hint">…</div>}
      </div>

      <div className="player-bar">
        <button className={panel ? "primary" : ""} aria-pressed={!!panel}
          disabled={!shownNode && !panel}
          onClick={() => (panel ? closePanel() : openPanel("node"))}
          title={panel ? "Close the editor (E)"
            : "Edit what is on screen — text, voice, CG, expression and pose (E)"}>
          ✎ {panel ? "Editing" : menu ? "Edit choice" : "Edit line"}
        </button>
        <button className={voiceOn ? "" : "voice-off"} aria-pressed={voiceOn} onClick={toggleVoice}
          title={voiceOn ? "Mute voice lines" : "Play voice lines"}>
          {voiceOn ? "🔊 Voice" : "🔇 Voice off"}
        </button>
        <button onClick={() => { load(); }}>Recompile</button>
        {fromNode && (
          <span className="pill" title="playing from a specific node">
            from node
            <button className="tiny ghost" onClick={() => store.playFromNode(null)}>×</button>
          </span>
        )}
        <button onClick={() => setShowFlags(!showFlags)}>
          {showFlags ? "Hide flags" : "Set flags"}
        </button>
        <select style={{ width: 210 }} value="" onChange={(e) => {
          if (e.target.value) restart(e.target.value);
        }}>
          <option value="">Start from…</option>
          {program.scenes.map((scene) => (
            <option key={scene.id} value={scene.label}>{scene.title}</option>
          ))}
        </select>
        <button onClick={() => restart()}>Restart</button>
        <span className="hint">
          {stack.length ? `in ${stack[stack.length - 1].label}` : "stopped"}
        </span>
        <div className="flagbar">
          {Object.entries(flags).map(([key, value]) => {
            const was = crossed[key];
            return (
              <span key={key} className={`pill${was ? " crossed" : ""}`}
                title={was ? `just crossed: ${was} → ${bands[key]}` : undefined}>
                {key}={describeFlag(stats, key, value)}
                {was && <> · {was} → {bands[key]}</>}
              </span>
            );
          })}
        </div>
      </div>
      {showFlags && (
        <div className="flag-editor">
          <div className="section-title">
            Starting flags — test a branch without earning it first
          </div>
          <div className="flag-grid">
            {Object.keys({ ...program.flags, ...overrides }).map((key) => {
              const value = overrides[key] ?? program.flags[key];
              const isBool = typeof (program.flags[key] ?? value) === "boolean";
              return (
                <label key={key} className="row" style={{ gap: 6 }}>
                  <span className="mono" style={{ flex: 1 }}>{key}</span>
                  {isBool ? (
                    <input type="checkbox" checked={Boolean(value)}
                      onChange={(e) => setOverrides({ ...overrides, [key]: e.target.checked })} />
                  ) : (
                    <input style={{ width: 90 }} value={String(value ?? "")}
                      onChange={(e) => {
                        const raw = e.target.value;
                        const num = Number(raw);
                        setOverrides({
                          ...overrides,
                          [key]: raw !== "" && !Number.isNaN(num) ? num : raw,
                        });
                      }} />
                  )}
                  {/* Dialling a number to reach a branch is exactly where the
                      band matters: 39 and 41 gate the same option differently. */}
                  {!isBool && (
                    <span className="hint" style={{ width: 68 }}>
                      {bandLabelFor(stats, key, value)}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" onClick={() => restart(undefined, overrides)}>
              Restart with these flags
            </button>
            <button onClick={() => { setOverrides({}); restart(undefined, {}); }}>
              Reset to defaults
            </button>
          </div>
        </div>
      )}
      <audio ref={audioRef} style={{ display: "none" }} />
    </div>
  );

  const editor = tab && (
    <PlayEditPanel tab={tab} onTab={openPanel} onClose={closePanel}
      sprites={sprites} background={background} line={line}
      renders={renders}
      onQueued={(key, render) => setRenders((current) => ({ ...current, [key]: render }))}
      onOpenNode={openNode} />
  );

  if (narrow) {
    return (
      <>
        {player}
        {editor && (
          <>
            <div className="drawer-scrim" onClick={closePanel} />
            <div className="drawer right play-drawer">{editor}</div>
          </>
        )}
      </>
    );
  }
  return (
    <div className="play-wrap">
      {player}
      {editor}
    </div>
  );
}
