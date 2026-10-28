import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { api } from "../lib/api";
import { cgContext, type CgContext } from "../lib/cg";
import { InfoTip, LabelText } from "./InfoTip";
import MaskPainter from "./MaskPainter";
import TagInput from "./TagInput";
import { descendantIds, useStore } from "../lib/store";
import type { IRNode, StatGate } from "../lib/types";
import ConfirmDialog from "./ConfirmDialog";
import NextSceneLink from "./NextSceneLink";
import VariantStrip from "./VariantStrip";
import VoiceLineButton from "./VoiceLineButton";

export default function Inspector() {
  const store = useStore();
  const node = store.nodes.find((n) => n.id === store.selected);
  const [state, setState] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [steering, setSteering] = useState<null | "scene" | "regen">(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const belowCount = useMemo(
    () => (node ? descendantIds(store.nodes, store.edges, node.id).length : 0),
    [node?.id, store.nodes, store.edges],
  );

  useEffect(() => {
    setState(null);
    if (!node || !store.project) return;
    api.pathState(store.project.id, node.id).then(setState).catch(() => setState(null));
  }, [node?.id, store.nodes.length, store.edges.length]);

  if (!store.project) return <aside className="inspector" />;

  if (!node) {
    return (
      <aside className="inspector">
        <div className="section-title">Nothing selected</div>
        <p className="hint">
          Select a node to edit it. Drag from a node's right-hand port to wire the
          next one; ports on Choice and Condition nodes are per-branch.
        </p>
        <VariablesPanel />
      </aside>
    );
  }

  const patch = (data: any) => store.patchNode(node.id, { data });
  const problems = store.problems.filter((p) => p.node_id === node.id);

  const generate = async (fn: () => Promise<{ job_id: string }>) => {
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

  return (
    <aside className="inspector">
      <div className="row" style={{ marginBottom: 8 }}>
        <span className="pill">{node.type}</span>
        {store.labels[node.id] && (
          <span className="pill" title="Ren'Py label — stable across recompiles">
            {store.labels[node.id]}
          </span>
        )}
        <span style={{ marginLeft: "auto" }} />
        <button className="tiny danger" onClick={() => {
          if (belowCount > 0) setConfirmDelete(true);
          else store.removeNode(node.id, true);
        }}>Delete</button>
      </div>

      {confirmDelete && (
        <DeleteModal
          type={node.type}
          belowCount={belowCount}
          onClose={() => setConfirmDelete(false)}
          onConfirm={(cascade) => {
            setConfirmDelete(false);
            store.removeNode(node.id, !cascade, cascade);
          }}
        />
      )}

      {problems.map((p, i) => (
        <div key={i} className={`hint ${p.severity === "error" ? "bad" : "warn"}`}>
          {p.severity === "error" ? "✗" : "⚠"} {p.message}
        </div>
      ))}

      <NodeEditor node={node} patch={patch} />

      <VariantStrip node={node} />

      {!store.edges.some((e) => e.source === node.id) && (
        <NextSceneLink fromId={node.id} fromKind={node.type}
          label="Continues into"
          hint={"Nothing follows this node, so the story stops here. Send it on "
                + "into another scene — an edge into the next Scene node is all "
                + "a scene transition is. (An End node finishes the "
                + "playthrough instead.)"} />
      )}

      <div className="section-title">Generate</div>
      <div className="grid2">
        <button
          disabled={busy}
          onClick={() => generate(() =>
            api.generateScene(store.project!.id, { anchor_id: node.id, brief: "" }))}
          title="Two-pass: prose, then structuring. Context is the path to here."
        >
          Continue scene
        </button>
        <button
          disabled={busy}
          onClick={() => generate(() => api.regenerateFrom(store.project!.id, node.id))}
          title="Keeps everything upstream; parks the downstream subtree"
        >
          Regenerate here
        </button>
        <button disabled={busy} onClick={() => setSteering("scene")}
          title="Add a one-off steer to this generation">
          Continue with a note…
        </button>
        <button disabled={busy} onClick={() => setSteering("regen")}
          title="Regenerate with a one-off steer">
          Regenerate with a note…
        </button>
        <button onClick={() => store.playFromNode(node.id)}
          title="Play the preview starting at this node">
          ▶ Play from here
        </button>
        {node.data?._gen && (
          <button disabled={busy}
            onClick={() => generate(() => api.regenerateBlock(store.project!.id, node.id))}
            title="Redo the whole generation this node came from, from wherever it started">
            Regenerate this block
          </button>
        )}
        {node.type === "say" && (
          <VoiceLineButton node={node} busy={busy} onGen={generate} />
        )}
      </div>

      <ContinuationButtons node={node} busy={busy} generate={generate} />
      <ContextPreviewButton nodeId={node.id} />

      {(node.type === "choice" || node.type === "hub") && (
        <>
          <div className="section-title">
            Generate a {node.type === "hub" ? "topic" : "branch"}
          </div>
          {(node.data.options ?? []).map((opt: any) => (
            <button
              key={opt.id}
              style={{ width: "100%", marginBottom: 4, textAlign: "left" }}
              disabled={busy}
              onClick={() => generate(() => api.generateBranch(store.project!.id, {
                choice_id: node.id, option_id: opt.id,
              }))}
            >
              → {opt.label || opt.id}
            </button>
          ))}
        </>
      )}

      {steering && (
        <SteerModal
          mode={steering}
          initial={store.steerNotes[`${node.id}:${steering}`] ?? ""}
          onClose={() => setSteering(null)}
          onSubmit={async (instruction) => {
            const which = steering;
            setSteering(null);
            store.rememberSteerNote(`${node.id}:${which}`, instruction);
            await generate(() => (which === "scene"
              ? api.generateScene(store.project!.id, {
                  anchor_id: node.id, brief: "", instruction })
              : api.regenerateFrom(store.project!.id, node.id, "", instruction)));
          }}
        />
      )}

      {state && (
        <>
          <div className="section-title">
            <LabelText label="Resolved state at this node" info={
              <>Computed by walking root → here, so it reflects this branch
                only.</>} />
          </div>
          <table className="data" style={{ marginTop: 6 }}>
            <tbody>
              <tr><th>Location</th><td>{state.state.location ?? "—"}</td></tr>
              <tr><th>Time</th><td>{state.state.time_of_day ?? "—"}</td></tr>
              <tr><th>Present</th><td>{state.state.present.join(", ") || "—"}</td></tr>
              <tr>
                <th>Outfits</th>
                <td>{Object.entries(state.state.outfits).map(([k, v]) => `${k}:${v}`).join(", ") || "—"}</td>
              </tr>
              <tr>
                <th>Flags</th>
                <td>{Object.entries(state.state.flags).map(([k, v]) => `${k}=${v}`).join(", ") || "—"}</td>
              </tr>
              <tr><th>Path depth</th><td>{state.depth} nodes</td></tr>
            </tbody>
          </table>
        </>
      )}

      <Section title="Label" defaultOpen={false}
        badge={node.pinned_label ? "pinned" : undefined}>
        <div className="field">
          <label><LabelText label="Slug" info="Feeds the label name." /></label>
          <input
            value={node.slug}
            onChange={(e) => store.patchNode(node.id, { slug: e.target.value })}
          />
        </div>
        <Check label="Pin as a jump target" checked={node.pinned_label}
          onChange={(v) => store.patchNode(node.id, { pinned_label: v })}
          info={<>Only pinned nodes and Scenes get a label a Jump or Call can
            name, so pin anything you want to be a destination.</>} />
      </Section>
    </aside>
  );
}

/* ------------------------------------------------------------------ editors */

function NodeEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const vocab = store.vocab;
  const d = node.data ?? {};
  // A line playing over a held CG opens its Image section, because that is
  // where the switch to take the picture down lives.
  const cg = useMemo(() => cgContext(store.nodes, store.edges)(node.id),
    [store.nodes, store.edges, node.id]);
  const imageSection = {
    defaultOpen: !!d.cg || !!d.image_hash || !!cg.held,
    badge: d.image_hash ? "CG ready" : d.cg ? "flagged" : cg.held ? "under a held CG" : undefined,
  };

  switch (node.type) {
    case "scene":
      return (
        <>
          <Section title="Scene" info={
            <>A Scene emits <code>scene bg_…</code>, which clears the whole
              master layer — the reset-to-neutral the doc calls for.</>}>
            <Text label="Title" value={d.title} onChange={(v) => patch({ title: v })} />
            <div className="grid2">
              <Select label="Background" value={d.background ?? ""}
                options={[["", "—"], ...store.locations.map((l) => [l.id, l.name] as [string, string])]}
                onChange={(v) => patch({ background: v || null })} />
              <Select label="Time of day" value={d.time_of_day ?? ""}
                options={[["", "—"], ...(vocab?.time_of_day ?? []).map((t) => [t, t] as [string, string])]}
                onChange={(v) => patch({ time_of_day: v })} />
            </div>
            <div className="grid2">
              <Select label="Weather" value={d.weather ?? ""}
                options={[["", "—"], ...(vocab?.weather ?? []).map((t) => [t, t] as [string, string])]}
                onChange={(v) => patch({ weather: v })} />
              <Select label="Transition" value={d.transition ?? "dissolve"}
                options={(vocab?.transitions ?? []).map((t) => [t, t] as [string, string])}
                onChange={(v) => patch({ transition: v })} />
            </div>
            <RouteSelect label="Route" value={d.route ?? ""}
              onChange={(v) => patch({ route: v })}
              info={<>Optional. A Hub option or a Jump node can target “the next
                scene in this route the player has not reached yet” instead of a
                fixed node, so neither needs rewiring every time you add a
                scene. Order is the order the scenes sit on the canvas, top to
                bottom.</>} />
          </Section>

          <Section title="Brief">
            <Area label="What happens in this scene" rows={4} value={d.brief ?? ""}
              onChange={(v) => patch({ brief: v })} />
            <div className="field">
              <label><LabelText label="Characters involved" info={
                <>Leave all unchecked for automatic: whoever is on stage,
                  whoever has spoken on this path, and the player. Tick someone
                  only to bring in a character who has not appeared yet — the
                  writer cannot introduce someone it was never told
                  about.</>} /></label>
              {store.characters.map((c) => (
                <label key={c.id} className="row" style={{ gap: 6, marginBottom: 2 }}>
                  <input type="checkbox"
                    checked={(d.characters ?? []).includes(c.id)}
                    onChange={(e) => {
                      const cur: string[] = d.characters ?? [];
                      patch({
                        characters: e.target.checked
                          ? [...cur, c.id]
                          : cur.filter((x) => x !== c.id),
                      });
                    }} />
                  <span>{c.display_name}</span>
                </label>
              ))}
            </div>
            <Select label="Length" value={d.length ?? ""}
              options={[["", "—"], ["short", "short"], ["medium", "medium"],
                        ["long", "long"]]}
              onChange={(v) => patch({ length: v })} />
            <Area label="Must happen somewhere in the scene" rows={2}
              value={d.must_hit ?? ""} placeholder="the letter comes up"
              onChange={(v) => patch({ must_hit: v })}
              info={<>Describes the scene as a whole. It is written over several
                generations, and the writer is told not to rush the arc to reach
                this — a stretch that does not get there is correct.</>} />
            <Area label="Where it ends up (eventually)" rows={2}
              value={d.ends_with ?? ""} placeholder="she leaves without answering"
              onChange={(v) => patch({ ends_with: v })}
              info={<>Where the scene lands, not where this stretch lands. The
                writer is told not to rush the arc to reach it.</>} />
            <Check label="NSFW — write this scene in the adult register"
              checked={!!d.nsfw} onChange={(v) => patch({ nsfw: v })}
              info={<>Explicitly, in the same voice as everything else, no fade
                to black. It also unlocks each character&rsquo;s NSFW notes,
                which are left out of every other scene&rsquo;s prompt
                entirely. This scene only — nothing else in the project
                changes.</>} />
          </Section>

          <Section title="Music" defaultOpen={!!d.music || !!d.stop_music}>
            <Text label="Music file" value={d.music ?? ""}
              onChange={(v) => patch({ music: v })} />
            <Check label="Stop music at scene start" checked={!!d.stop_music}
              onChange={(v) => patch({ stop_music: v })} />
          </Section>
        </>
      );

    case "say":
      return (
        <>
          <Section title="Say">
            <Select label="Speaker" value={d.speaker ?? ""}
              options={[["", "—"], ...store.characters.map((c) => [c.id, c.display_name] as [string, string])]}
              onChange={(v) => patch({ speaker: v })} />
            <Area label="Text" rows={4} value={d.text ?? ""} onChange={(v) => patch({ text: v })} />
          </Section>

          <Section title="Performance" info={
            <>Written by the perform pass, which reads the whole scene at once.
              Expression picks the sprite; emotion drives the voice.</>}>
            <div className="grid2">
              <Select label="Expression" value={d.expression ?? "neutral"}
                options={(vocab?.expressions ?? []).map((e) => [e, e] as [string, string])}
                onChange={(v) => patch({ expression: v })} />
              <Select label="Emotion" value={d.emotion ?? "neutral"}
                options={(vocab?.emotions ?? []).map((e) => [e, e] as [string, string])}
                onChange={(v) => patch({ emotion: v })} />
            </div>
            <Select label="Pose (which sprite is shown)" value={d.pose ?? "front"}
              options={(vocab?.poses ?? []).map((p) => [p, p] as [string, string])}
              onChange={(v) => patch({ pose: v })} />
            <Range label="Intensity" value={d.intensity ?? 0.5}
              onChange={(v) => patch({ intensity: v })} />
            <Text label="Delivery" value={d.delivery ?? ""} placeholder="trails off at the end"
              onChange={(v) => patch({ delivery: v })} />
          </Section>

          <SoundSection node={node} patch={patch} />

          <Section title="Image" {...imageSection}>
            <Check label="Flag this node for an event CG" checked={!!d.cg}
              onChange={(v) => patch({ cg: v })} />
            <CgContinuity node={node} patch={patch} context={cg} />
            <Text label="Shot hint (the writer's rough note)" value={d.shot_hint ?? ""}
              placeholder="upper body, from below, wind"
              onChange={(v) => patch({ shot_hint: v })} />
            <CgField node={node} patch={patch} />
          </Section>

          {d.voice_path && (
            <Section title="Voice">
              <div className="field">
                <audio controls style={{ width: "100%" }}
                  src={d.voice_hash ? api.assetUrl(d.voice_hash) : undefined} />
                <div className="hint">{d.voice_path}</div>
              </div>
            </Section>
          )}
        </>
      );

    case "narration":
      return (
        <>
          <Section title="Narration" info={
            <>Narration is for what the sprites cannot show — entrances and
              exits, movement, the place, objects, time passing. A line that
              only describes a face is folded onto the sprite by the perform
              pass and dropped.</>}>
            <Area label="Text" rows={5} value={d.text ?? ""} onChange={(v) => patch({ text: v })} />
          </Section>

          <SoundSection node={node} patch={patch} />

          <Section title="Image" {...imageSection}>
            <Check label="Flag for an event CG" checked={!!d.cg}
              onChange={(v) => patch({ cg: v })} />
            <CgContinuity node={node} patch={patch} context={cg} />
            <Text label="Shot hint" value={d.shot_hint ?? ""}
              onChange={(v) => patch({ shot_hint: v })} />
            <CgField node={node} patch={patch} />
          </Section>
        </>
      );

    case "stage":
      return <StageEditor node={node} patch={patch} />;

    case "choice":
      return <ChoiceEditor node={node} patch={patch} />;

    case "hub":
      return <HubEditor node={node} patch={patch} />;

    case "condition":
      return <ConditionEditor node={node} patch={patch} />;

    case "set":
      return <SetEditor node={node} patch={patch} />;

    case "audio":
      return (
        <>
          <div className="section-title">Audio</div>
          <div className="grid2">
            <Select label="Channel" value={d.channel ?? "music"}
              options={[["music", "music"], ["sound", "sound"], ["voice", "voice"]]}
              onChange={(v) => patch({ channel: v })} />
            <Select label="Action" value={d.action ?? "play"}
              options={[["play", "play"], ["stop", "stop"], ["queue", "queue"]]}
              onChange={(v) => patch({ action: v })} />
          </div>
          <Text label="File" value={d.file ?? ""} placeholder="theme.ogg"
            onChange={(v) => patch({ file: v })} />
          <div className="grid2">
            <Num label="Fade in" value={d.fadein ?? 0} onChange={(v) => patch({ fadein: v })} />
            <Num label="Fade out" value={d.fadeout ?? 1} onChange={(v) => patch({ fadeout: v })} />
          </div>
        </>
      );

    case "transition":
      return (
        <>
          <div className="section-title">Transition</div>
          <Select label="Transition" value={d.transition ?? "dissolve"}
            options={(vocab?.transitions ?? []).map((t) => [t, t] as [string, string])}
            onChange={(v) => patch({ transition: v })} />
          <Num label="Pause (s)" value={d.pause ?? 0} onChange={(v) => patch({ pause: v })} />
        </>
      );

    case "raw":
      return (
        <>
          <div className="section-title">
            <LabelText label="Raw Ren'Py" info={
              <>Escape hatch for anything the IR does not model. Passed through
                verbatim.</>} />
          </div>
          <Area label="" rows={10} value={d.code ?? ""} onChange={(v) => patch({ code: v })}
            mono />
        </>
      );

    case "end":
      return (
        <>
          <div className="section-title">
            <LabelText label="End" info={
              <>End finishes the playthrough — <code>return</code>, or a jump to
                an ending label. It is <b>not</b> how you move to the next
                scene: for that, wire this scene&rsquo;s last line straight into
                the next Scene node (Script offers a <b>Go to scene…</b> picker
                at the end of the path), or use a Jump node if you want the hop
                to be a visible step.</>} />
          </div>
          <Select label="Mode" value={d.mode ?? "return"}
            options={[["return", "return"], ["ending", "jump to ending_<id>"], ["jump", "jump to label"]]}
            onChange={(v) => patch({ mode: v })} />
          {d.mode !== "return" && (
            <Text label="Ending id" value={d.ending_id ?? ""} onChange={(v) => patch({ ending_id: v })} />
          )}
          <Text label="Persistent unlocks (comma separated)"
            value={(d.unlock_persistent ?? []).join(", ")}
            placeholder="cg_beach_unlocked"
            onChange={(v) => patch({
              unlock_persistent: v.split(",").map((s) => s.trim()).filter(Boolean),
            })}
            info={<>Persistent flags survive save deletion — this is what
              gallery and replay unlocks use.</>} />
        </>
      );

    case "jump":
    case "call":
      return <JumpEditor node={node} patch={patch} />;

    default:
      return null;
  }
}

/**
 * The one-shot sound this beat makes.
 *
 * The perform pass writes a *description* ("knock at the door"); the file
 * arrives later, when there is audio to point at. Until then the compiler
 * emits a `# TODO sfx:` comment rather than a `play sound` that would break
 * the build on a missing asset — so an unresolved cue is a normal, safe state
 * to leave a node in, not an error.
 */
function SoundSection({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const d = node.data ?? {};
  const cue = d.sfx_cue ?? "";
  const file = d.sfx_file ?? "";
  return (
    <Section title="Sound" defaultOpen={!!cue || !!file}
      badge={file ? "file set" : cue ? "cue only" : undefined}>
      <Text label="Sound cue (description)" value={cue}
        placeholder="knock at the door"
        onChange={(v) => patch({ sfx_cue: v })} />
      <Text label="Sound file (under audio/sfx/)" value={file}
        placeholder="knock_01.ogg"
        onChange={(v) => patch({ sfx_file: v || null })} />
      {cue && !file && (
        <div className="hint">
          No file yet — this compiles to a comment, so the build still runs.
        </div>
      )}
    </Section>
  );
}

/**
 * The event CG for this node: the distilled prompt, the controls that decide
 * how it renders, and the image itself.
 *
 * The prompt is split in two because the wrapper puts the halves in different
 * places -- framing goes early (it decides what kind of picture this is) and
 * body/contact tags go after the identity block, where they stop competing
 * with the face and eye tags for the encoder's attention. Both are written by
 * the CG director and both are yours to edit; whatever is in these boxes is
 * what renders.
 *
 * Regenerating keeps the old take -- image.generate files every result as a
 * variant, so the previous CG is still in the variant strip. Deleting only
 * clears the node's reference; the file stays in the Gallery (and Ctrl+Z puts
 * the reference straight back).
 */
/** Who is in this CG, chosen by hand.
 *
 *  Empty means the automatic answer -- the speaker of the line plus whoever
 *  else the path says is on stage, capped at two. That is right most of the
 *  time and wrong exactly when a beat is about one person in a room with
 *  three people in it, which was previously impossible to ask for. */
/**
 * Which of the cast's own tags this shot is allowed to use.
 *
 * A tag in a prompt is a request to find room for the thing, so `brown
 * loafers` on a frame that ends at the shoulders does not get ignored -- it
 * gets shoes, or a pair of feet where they cannot be. Per shot rather than per
 * character, because that is what it is a property of: the same character two
 * beats later in a full-body shot still wants them, and the sheet the LoRA
 * trains on has to keep her whole.
 *
 * The list is the render's own `cast` breakdown, so it can never offer a
 * toggle for a tag this shot does not emit, or miss one it does.
 *
 * Two reasons a tag shows as off, and one list: the author switched it off, or
 * the framing does not reach it. The framing-cropped ones used to get a section
 * of their own, which listed them twice over -- once live in the cast list and
 * once struck through underneath -- because `out_of_frame` is a subset of that
 * character's identity and outfit, not a separate set of tags.
 */
function CgTagSwitches({ node, patch, cast }: {
  node: IRNode;
  patch: (d: any) => void;
  cast: { character_id: string; name: string; identity: string[]; outfit: string[];
          out_of_frame?: string[] }[];
}) {
  const d = node.data ?? {};
  const list: string[] = d.cg_hidden_tags ?? [];
  const hidden = new Set(list.map((t) => t.toLowerCase()));
  // The other switch, pulling the other way: tags the framing table would cut
  // that this shot wants anyway.
  const forcedList: string[] = d.cg_forced_tags ?? [];
  const forced = new Set(forcedList.map((t) => t.toLowerCase()));

  const without = (of: string[], key: string) =>
    of.filter((t) => t.toLowerCase() !== key);

  // One button per tag, one meaning. The two lists are kept mutually exclusive
  // so a tag can never be both switched off and forced back in -- which has no
  // sensible rendering and no sensible answer for what a click should do next.
  const toggle = (tag: string) => {
    const key = tag.toLowerCase();
    patch({
      cg_hidden_tags: hidden.has(key) ? without(list, key) : [...list, tag],
      cg_forced_tags: without(forcedList, key),
    });
  };

  const toggleForced = (tag: string) => {
    const key = tag.toLowerCase();
    patch({
      cg_forced_tags: forced.has(key)
        ? without(forcedList, key)
        : [...forcedList, tag],
      cg_hidden_tags: without(list, key),
    });
  };

  // Switches for tags nothing in this shot produces -- the cast changed, or an
  // outfit was re-tagged. Left silent they would look like they were doing
  // something.
  const live = new Set(
    cast.flatMap((c) => [...c.identity, ...c.outfit]).map((t) => t.toLowerCase()));
  const orphaned = list.filter((t) => !live.has(t.toLowerCase()));

  // What the framing removed without being asked, per character. `out_of_frame`
  // is a subset of that character's own identity+outfit, so these tags are
  // already in the lists below -- a separate section showed every one of them
  // twice, once looking live in the cast list and once struck through
  // underneath. The author's question is "is this tag in the shot", and it is
  // answered where the tag is.
  const croppedFor = (c: { out_of_frame?: string[] }) =>
    new Set((c.out_of_frame ?? []).map((t) => t.toLowerCase()));

  const badge = (tag: string, { stale = false, cropped = false } = {}) => {
    // Cropped is a switch too. FRAMING_CUTOFF is a fact about the words and
    // right nearly always, but "nearly" is the point: a close-up composed over
    // a shoulder really can show a skirt, and the table cannot know that. So
    // the automatic cut is an opinion the author can overrule per shot.
    if (cropped) return (
      <button
        key={tag}
        type="button"
        className="tag-toggle off cropped"
        title={"Out of frame at this framing, so it is left out of the prompt — "
          + "click to put it back in anyway."}
        onClick={() => toggleForced(tag)}
      >
        {tag}
      </button>
    );
    const key = tag.toLowerCase();
    if (forced.has(key)) return (
      <button
        key={tag}
        type="button"
        className="tag-toggle forced"
        title={"Kept in against the framing, which would have cut it — click to "
          + "let the framing have it back."}
        onClick={() => toggleForced(tag)}
      >
        {tag}
      </button>
    );
    const off = hidden.has(tag.toLowerCase());
    return (
      <button
        key={tag}
        className={`tag-toggle ${off ? "off" : ""} ${stale ? "stale" : ""}`}
        title={stale
          ? "Nothing in this shot produces this tag any more — click to drop the switch"
          : off
            ? "Off for this shot — click to let it back in"
            : "Click to keep it out of this shot"}
        onClick={() => toggle(tag)}
      >
        {tag}
      </button>
    );
  };

  if (!cast.length && !orphaned.length) return null;


  return (
    <div className="field">
      <label>
        <LabelText label="Tags in this shot" info={
          <>Click a tag to keep it out of <b>this CG only</b>. Every other
            render — the next CG, her sprites, the character sheet — still uses
            it, because a frame that ends at the shoulders is the thing that
            does not want shoes, not the character.
            <br /><br />
            A tag in dotted grey was cut by the framing rather than by you —
            this shot does not reach that part of the body. Click it to put it
            back in anyway, for when the framing table is wrong about a
            particular shot.</>} />
        {hidden.size > 0 && <span className="pill warn" style={{ marginLeft: 6 }}>
          {hidden.size} off
        </span>}
        {forced.size > 0 && <span className="pill" style={{ marginLeft: 6 }}>
          {forced.size} kept in
        </span>}
      </label>
      {cast.map((c) => {
        const cropped = croppedFor(c);
        return (
          <div key={c.character_id} style={{ marginBottom: 4 }}>
            <div className="hint" style={{ marginBottom: 2 }}>{c.name}</div>
            <div className="tag-toggles">
              {[...c.identity, ...c.outfit].map((t) =>
                badge(t, { cropped: cropped.has(t.toLowerCase()) }))}
            </div>
          </div>
        );
      })}
      {orphaned.length > 0 && (
        <>
          <div className="hint" style={{ marginBottom: 2 }}>Not in this shot any more</div>
          <div className="tag-toggles">{orphaned.map((t) => badge(t, { stale: true }))}</div>
        </>
      )}
      {(hidden.size > 0 || forced.size > 0) && (
        <div className="hint">
          <button className="link-ish"
            onClick={() => patch({ cg_hidden_tags: [], cg_forced_tags: [] })}>
            Clear {hidden.size + forced.size} override
            {hidden.size + forced.size === 1 ? "" : "s"}
          </button>
        </div>
      )}
    </div>
  );
}


/**
 * Re-sample a take instead of rolling a new one.
 *
 * The case: a CG whose composition, lighting and expression are all right and
 * whose left hand is wrong. Regenerating throws away the four good things to
 * fix the one bad one, and a seed gives you no way to keep them.
 *
 * The slider is *adherence*, not denoise, because that is the question an
 * author is actually asking -- "how much of this do I want to keep". The wire
 * format stays `denoise` (what KSampler calls it) and the inversion happens
 * here, in one place.
 */
function RefinePanel({ node, hash, busy, run, title = "Refine this take", info }: {
  node: IRNode;
  /** The image the sampler starts from -- a take on this node, or the CG of
   *  an earlier line. Whichever it is, the result is a take on `node`. */
  hash: string;
  busy: boolean;
  run: (fn: () => Promise<{ job_id: string }>, msg: string) => Promise<void>;
  title?: string;
  info?: ReactNode;
}) {
  const store = useStore();
  const [adherence, setAdherence] = useState(55);
  const [mask, setMask] = useState("");
  const [batch, setBatch] = useState(1);
  const denoise = Number(((100 - adherence) / 100).toFixed(2));

  return (
    <div className="field refine-panel">
      <label>
        <LabelText label={title} info={info ?? (
          <>Starts the sampler from this image rather than from noise, so the
            composition, palette and staging survive. Paint a region and only
            that part is re-sampled — everything outside it is carried through
            untouched, which is the difference between fixing a hand and
            re-rolling the picture hoping the hand improves.</>)} />
      </label>

      <MaskPainter src={api.assetUrl(hash)} onChange={setMask} />

      <label className="row" style={{ gap: 8, marginTop: 8 }}>
        <span style={{ width: 78 }}>Adherence</span>
        <input type="range" min={5} max={95} step={5} value={adherence}
          style={{ flex: 1 }}
          onChange={(e) => setAdherence(Number(e.target.value))} />
        <span className="mono" style={{ width: 34 }}>{adherence}%</span>
      </label>
      <div className="hint">
        {adherence >= 75
          ? "A polish: detail and texture change, everything else holds."
          : adherence >= 45
            ? "The subject and framing hold; most of the detail is rewritten."
            : "Loose — expect a different picture that rhymes with this one."}
        {" "}<span className="mono">denoise {denoise}</span>
      </div>

      <div className="row" style={{ gap: 6, marginTop: 8 }}>
        <button className="tiny primary" disabled={busy}
          onClick={() => run(
            () => api.refineCG(store.project!.id, {
              node_id: node.id, source_hash: hash,
              denoise, mask: mask || undefined, batch,
            }),
            mask ? "Refining the painted region" : "Refining the whole frame")}>
          {mask ? "Refine region" : "Refine"}{batch > 1 ? ` ×${batch}` : ""}
        </button>
        <span className="tabs batch-picker" title="Candidates per run">
          {[1, 2, 4].map((n) => (
            <button key={n} className={batch === n ? "active" : ""}
              disabled={busy} onClick={() => setBatch(n)}>×{n}</button>
          ))}
        </span>
        <span className="hint" style={{ marginLeft: "auto" }}>
          lands as a new take
        </span>
      </div>
    </div>
  );
}


function CgCast({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const d = node.data ?? {};
  const chosen: string[] = d.cg_subjects ?? [];
  const auto = chosen.length === 0;

  const toggle = (id: string) => {
    const next = chosen.includes(id)
      ? chosen.filter((c) => c !== id)
      : [...chosen, id];
    patch({ cg_subjects: next });
  };

  return (
    <div className="field">
      <label>In the shot</label>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <button className={`tiny ${auto ? "primary" : "ghost"}`}
          title="Work it out from the speaker and who the path says is on stage"
          onClick={() => patch({ cg_subjects: [] })}>
          Auto
        </button>
        {store.characters.map((c) => (
          <button key={c.id}
            className={`tiny ${chosen.includes(c.id) ? "primary" : "ghost"}`}
            title={c.is_player
              ? "The player character is normally the camera, not a subject"
              : undefined}
            onClick={() => toggle(c.id)}>
            {c.display_name}{c.is_player ? " (PC)" : ""}
          </button>
        ))}
      </div>
      <div className="hint">
        {auto
          ? "Auto: the speaker of this line, then whoever else is on stage, "
            + "capped at two."
          : `Exactly ${chosen.length === 1 ? "this character" : "these "
            + chosen.length + " characters"}, in this order — the first is the `
            + "focal one whose LoRA and face pass are used."}
        {!auto && chosen.length > 2 && (
          <> <b>Past two, tag-only prompting blends faces</b> — it needs
          regional conditioning to hold up, so expect to cull takes.</>
        )}
        {!auto && chosen.some((id) =>
          store.characters.find((c) => c.id === id)?.is_player) && (
          <> The player character is in this shot; in a first/second-person VN
          they are usually the camera instead.</>
        )}
      </div>
    </div>
  );
}


/** Queue a job from a button: a toast, a jobs refresh, and the buttons held
 *  disabled until the request is in. */
function useJobRunner() {
  const store = useStore();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<{ job_id: string }>, msg: string) => {
    setBusy(true);
    try {
      await fn();
      store.notify(msg);
      store.refreshJobs();
    } catch (e) {
      store.fail(String(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}


/** A line in a sentence, for naming it in a hint: who says it and how it starts. */
function lineExcerpt(node: IRNode, names: Record<string, string>): string {
  const text = String(node.data?.text ?? "").trim();
  const short = text.length > 48 ? `${text.slice(0, 46).trimEnd()}…` : text;
  const who = node.type === "say" && node.data?.speaker
    ? `${names[node.data.speaker] ?? node.data.speaker}: ` : "";
  return `${who}“${short || "…"}”`;
}


/**
 * Where this line sits in a run of CGs: the hold switch for its own CG, and
 * the CG before it -- still on screen here or not -- with what can be done
 * with that one.
 *
 * "Keep it on screen" is the earlier line's hold, set from this one, because
 * this is where the thought happens: on the line that wants the picture still
 * up. "Start from it" re-samples the earlier picture into a take on *this*
 * node, for two shots of one moment with something changed; the earlier line
 * is never touched.
 */
function CgContinuity({ node, patch, context }: {
  node: IRNode;
  patch: (d: any) => void;
  context: CgContext;
}) {
  const store = useStore();
  const { busy, run } = useJobRunner();
  const [starting, setStarting] = useState(false);
  const d = node.data ?? {};
  const { held, previous } = context;
  const source = previous ? store.nodes.find((n) => n.id === previous.node) : undefined;
  const names = useMemo(
    () => Object.fromEntries(store.characters.map((c) => [c.id, c.display_name])),
    [store.characters]);
  const distilled = !!(d.cg_camera || d.cg_action);

  // Whatever starts from the earlier picture has to land on a line that
  // compiles its CG, or the take is rendered and never shown.
  const runFlagged = (fn: () => Promise<{ job_id: string }>, msg: string) => {
    if (!d.cg) patch({ cg: true });
    return run(fn, msg);
  };

  const status = !source ? ""
    : held && d.cg
      ? (d.image_hash
        ? "Still up when this line starts; this line's own CG cross-fades over it."
        : "Still up when this line starts, standing in for this line's CG until it is rendered.")
      : held
        ? "Still on screen during this line."
        : source.data?.cg_hold
          ? "Held, but taken down before this line."
          : "Taken down after its own line.";

  return (
    <>
      {d.cg && (
        <Check label="Keep it on screen after this line" checked={!!d.cg_hold}
          onChange={(v) => patch({ cg_hold: v })}
          info={<>The lines after this one play over the picture. It stays up
            until the next CG replaces it (with a cross-fade), the scene or
            location changes, or a later line is set to clear it.</>} />
      )}

      {previous && source && (
        <div className="field cg-previous">
          <label>
            <LabelText label={held ? "Held CG" : "Previous CG"} info={
              <>The nearest CG above this line in the scene. Click the picture
                to go to its line.</>} />
          </label>
          <div className="cg-previous-row">
            <img className={held ? "" : "faded"} src={api.assetUrl(previous.image)} alt=""
              title="Go to the line this CG belongs to"
              onClick={() => store.focusNode(previous.node, store.view)} />
            <div className="cg-previous-text">
              <div className="hint">{lineExcerpt(source, names)}</div>
              <div className="hint">{status}</div>
            </div>
          </div>
          {held && !d.cg && (
            <Check label="Clear it before this line" checked={!!d.cg_clear}
              onChange={(v) => patch({ cg_clear: v })}
              info={<>Dissolves back to the sprites as this line starts.</>} />
          )}
          <div className="row" style={{ gap: 6, marginTop: 4, flexWrap: "wrap" }}>
            {!held && !source.data?.cg_hold && (
              <button className="tiny"
                title={d.cg
                  ? "Hold the earlier CG, so it stays up until this line's own CG cross-fades over it"
                  : "Hold the earlier CG through here. It stays up past this line too, until a CG, a scene change or a line set to clear it"}
                onClick={() => store.patchNode(previous.node, { data: { cg_hold: true } })}>
                Keep it on screen
              </button>
            )}
            <button className={`tiny ${starting ? "primary" : ""}`} disabled={busy}
              title="Render this line's CG from that picture instead of from noise — for two shots of one moment with something changed"
              onClick={() => setStarting(!starting)}>
              {starting ? "Close" : "Start this line's CG from it"}
            </button>
          </div>
          {starting && (
            <>
              {!distilled && (
                <div className="hint warn" style={{ marginTop: 6 }}>
                  This line has no distilled prompt yet, so the render follows its
                  shot hint alone. Distill it below first, so what changes follows
                  this beat.
                </div>
              )}
              <RefinePanel node={node} hash={previous.image} busy={busy} run={runFlagged}
                title="Start from the previous CG" info={
                  <>The earlier picture is the starting point and this line&rsquo;s
                    prompt says what it becomes. Paint the part that changes and
                    only that is redrawn; leave it unpainted and lower the
                    adherence to vary the whole frame. Every result is a take on
                    this line — the earlier line keeps its CG.</>} />
            </>
          )}
        </div>
      )}
    </>
  );
}


function CgField({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const { busy, run } = useJobRunner();
  const [preview, setPreview] = useState<null | Awaited<
    ReturnType<typeof api.cgPromptPreview>>>(null);
  const [noting, setNoting] = useState(false);
  const [refining, setRefining] = useState(false);
  const d = node.data ?? {};
  const hash = d.image_hash;
  const distilled = !!(d.cg_camera || d.cg_action);
  const batch = Math.max(1, Number(store.settings.cg_batch ?? 1) || 1);
  // Who is in this shot and what each of them puts in the prompt. Fetched
  // rather than derived: the cast depends on the path state at this node, and
  // the tag blocks come from the same assembly the renderer runs.
  const [cast, setCast] = useState<
    Awaited<ReturnType<typeof api.cgPromptPreview>>["cast"]>([]);

  useEffect(() => {
    let live = true;
    api.cgPromptPreview(store.project!.id, node.id)
      .then((p) => { if (live) setCast(p.cast ?? []); })
      .catch(() => { if (live) setCast([]); });
    return () => { live = false; };
  }, [node.id, d.cg_subjects, d.speaker]);

  const showPrompt = async () => {
    if (preview) return setPreview(null);
    try {
      setPreview(await api.cgPromptPreview(store.project!.id, node.id));
    } catch (e) {
      store.fail(String(e));
    }
  };

  return (
    <>
      <div className="section-title">Event CG</div>
      <div className="row" style={{ gap: 6, marginBottom: 6 }}>
        <button
          className="tiny"
          disabled={busy}
          title="Read this node, the beats around it, who is on stage and where — and distill it all into image tags"
          onClick={() => run(
            () => api.distillCgPrompt(store.project!.id, node.id),
            "distilling the shot…")}
        >
          {distilled ? "⟳ Re-distill prompt" : "Distill prompt"}
        </button>
        <button className="tiny" disabled={busy} onClick={() => setNoting(true)}
          title="Distill with a one-off direction of your own">
          …with a note
        </button>
      </div>
      {!distilled && (
        <div className="hint">
          Nothing distilled yet — this node would render from its shot hint
          alone, which is the segmenter's rough note rather than a read of the
          moment.
        </div>
      )}

      {(d.cg_description || distilled) && (
        <Area label="The shot, in a sentence" rows={3} value={d.cg_description ?? ""}
          onChange={(v) => patch({ cg_description: v })}
          info={<>Written by the director before it picked a single tag — the
            schema generates this field first, so every tag below is a
            translation of a phrase in it. If this sentence is wrong, the tags
            are wrong for a reason you can see; fix it here and
            re-distill.</>} />
      )}

      <CgCast node={node} patch={patch} />

      <CgTagSwitches node={node} patch={patch} cast={cast} />

      <TagInput label="Camera / framing" rows={2} mono value={d.cg_camera ?? ""}
        prefer={["perspective", "light"]}
        onChange={(v) => patch({ cg_camera: v })}
        hint="Shot size, angle, POV. Goes early in the prompt. Suggestions are
              scoped to framing and lighting tags — type two letters." />
      <TagInput label="Action &amp; staging" rows={3} mono value={d.cg_action ?? ""}
        prefer={["action", "posture", "body", "gaze", "expression", "detail"]}
        onChange={(v) => patch({ cg_action: v })}
        hint="What the bodies are doing: posture, contact, hands, gaze. Goes
              after the identity block. Never describe hair, eyes or clothes
              here — those come from the character record every time." />
      <TagInput label="Negative additions" value={d.cg_negative ?? ""}
        placeholder="smile, multiple views"
        onChange={(v) => patch({ cg_negative: v })} />
      <Select
        label="Character LoRA for this CG"
        value={d.cg_lora ?? ""}
        options={[
          ["", "Project default"],
          ...(store.vocab?.cg_lora_modes ?? ["auto", "none", "sprite", "cg"]).map(
            (m) => [m, m === "auto" ? "auto — CG LoRA if trained, else Sprite, else none"
              : m === "none" ? "none — tags + face pass only"
              : m === "sprite" ? "Sprite LoRA" : "CG LoRA"] as [string, string]),
        ]}
        onChange={(v) => patch({ cg_lora: v })}
        info={<>Loaded at the character's CG weight, lower than sprites use, so
          the LoRA still helps the likeness without dragging the pose back to
          the sheet's front-facing standing shot — the face-detail pass below
          does the rest of that work. Pick <b>none</b> to skip the LoRA for just
          this shot.</>} />

      <div className="row" style={{ gap: 6, marginTop: 6 }}>
        <button className="tiny" onClick={showPrompt}
          title="Exactly what this node would send to ComfyUI right now">
          {preview ? "Hide final prompt" : "Show final prompt"}
        </button>
        <button
          className="tiny primary"
          disabled={busy}
          title={batch > 1
            ? `Render ${batch} candidates in one run and keep them all as takes — `
              + "pick one from the strip below. The current image stays a take too."
            : "Render this CG with a new seed — the current one is kept as a take"}
          onClick={() => run(
            () => api.generateCG(store.project!.id, { node_id: node.id, batch }),
            batch > 1 ? `CG queued — ${batch} candidates` : "CG queued")}
        >
          {hash ? "⟳ Regenerate" : "Generate CG"}{batch > 1 ? ` ×${batch}` : ""}
        </button>
        {/* How many candidates one run produces. The cost of a CG is the round
            trip, not the sampling: four at once is one wait instead of four.
            Remembered project-wide, because it is a working style rather than
            a property of this node. */}
        <span className="tabs batch-picker" title="Candidates per run">
          {[1, 2, 4].map((n) => (
            <button key={n} className={batch === n ? "active" : ""}
              disabled={busy}
              onClick={() => store.setSetting("cg_batch", n)}>
              ×{n}
            </button>
          ))}
        </span>
        {hash && (
          <button className={`tiny ${refining ? "primary" : ""}`} disabled={busy}
            title="Re-sample this image instead of rolling a new one — keeps the
                   composition and fixes one part of it"
            onClick={() => setRefining(!refining)}>
            {refining ? "Close refine" : "Refine"}
          </button>
        )}
        {hash && (
          <button
            className="tiny danger"
            disabled={busy}
            title="Clear this node's CG. The image stays in the Gallery, and Ctrl+Z undoes it."
            onClick={() => patch({ image_hash: null, _image_text_hash: null })}
          >
            Delete
          </button>
        )}
      </div>

      {hash && refining && (
        <RefinePanel node={node} hash={hash} busy={busy} run={run} />
      )}

      {preview && (
        <div className="field" style={{ marginTop: 6 }}>
          <label>Positive</label>
          <pre className="script">{preview.positive}</pre>
          <label>Negative</label>
          <pre className="script">{preview.negative}</pre>
          {preview.face_positive && (
            <>
              <label>Face-detail pass</label>
              <pre className="script">{preview.face_positive}</pre>
            </>
          )}
          <div className="hint">
            In the shot: {preview.subjects.join(", ") || "nobody — the room only"}.
            {" "}Main LoRA: {preview.lora ? `${preview.lora} @ ${preview.lora_weight}` : "none"}.
            {" "}Face LoRA: {preview.face_lora
              ? `${preview.face_lora} @ ${preview.face_lora_weight}` : "none"}.
          </div>
        </div>
      )}

      {hash && (
        <div className="field">
          <img src={api.assetUrl(hash)} alt="generated asset" style={{ width: "100%", borderRadius: 6 }} />
        </div>
      )}

      {noting && (
        <SteerModal
          mode="regen"
          initial={store.steerNotes[`${node.id}:cg`] ?? ""}
          onClose={() => setNoting(false)}
          onSubmit={async (instruction) => {
            setNoting(false);
            store.rememberSteerNote(`${node.id}:cg`, instruction);
            await run(
              () => api.distillCgPrompt(store.project!.id, node.id, instruction),
              "distilling the shot…");
          }}
        />
      )}
    </>
  );
}

function StageEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const d = node.data ?? {};
  const toggle = (field: "show" | "hide", id: string) => {
    const current: string[] = d[field] ?? [];
    patch({ [field]: current.includes(id) ? current.filter((c) => c !== id) : [...current, id] });
  };

  return (
    <>
      <div className="section-title">Stage</div>
      <div className="grid2">
        <Select label="Move to location" value={d.location ?? ""}
          options={[["", "—"], ...store.locations.map((l) => [l.id, l.name] as [string, string])]}
          onChange={(v) => patch({ location: v || null })} />
        <Select label="Time of day" value={d.time_of_day ?? ""}
          options={[["", "—"], ...(store.vocab?.time_of_day ?? []).map((t) => [t, t] as [string, string])]}
          onChange={(v) => patch({ time_of_day: v })} />
      </div>
      <Check label="Reset the stage (emit a fresh scene)" checked={!!d.reset}
        onChange={(v) => patch({ reset: v })} />

      <div className="field">
        <label>Show</label>
        <div className="chip-grid">
          {store.characters.map((c) => (
            <span key={c.id}
              className={`chip ${(d.show ?? []).includes(c.id) ? "on" : ""}`}
              onClick={() => toggle("show", c.id)}>{c.display_name}</span>
          ))}
        </div>
      </div>
      <div className="field">
        <label>Hide</label>
        <div className="chip-grid">
          {store.characters.map((c) => (
            <span key={c.id}
              className={`chip ${(d.hide ?? []).includes(c.id) ? "on" : ""}`}
              onClick={() => toggle("hide", c.id)}>{c.display_name}</span>
          ))}
        </div>
      </div>

      <div className="section-title">
        <LabelText label="Per character" info={
          <>Outfits are objects on the character; a Stage node mutates the
            active one. Downstream nodes resolve it by walking the path.</>} />
      </div>
      {store.characters.map((c) => (
        <div key={c.id} className="grid3" style={{ marginBottom: 4 }}>
          <Select label={`${c.display_name} outfit`} value={(d.outfits ?? {})[c.id] ?? ""}
            options={[["", "—"], ...c.outfits.map((o) => [o.id, o.name] as [string, string])]}
            onChange={(v) => patch({
              outfits: { ...(d.outfits ?? {}), ...(v ? { [c.id]: v } : {}) },
            })} />
          <Select label="expression" value={(d.expressions ?? {})[c.id] ?? ""}
            options={[["", "—"], ...(store.vocab?.expressions ?? []).map((e) => [e, e] as [string, string])]}
            onChange={(v) => patch({
              expressions: { ...(d.expressions ?? {}), ...(v ? { [c.id]: v } : {}) },
            })} />
          <Select label="pose" value={(d.poses ?? {})[c.id] ?? ""}
            options={[["", "—"], ...(store.vocab?.poses ?? []).map((p) => [p, p] as [string, string])]}
            onChange={(v) => patch({
              poses: { ...(d.poses ?? {}), ...(v ? { [c.id]: v } : {}) },
            })} />
          <Select label="position" value={(d.positions ?? {})[c.id] ?? ""}
            options={[["", "—"], ["left", "left"], ["center", "center"], ["right", "right"]]}
            onChange={(v) => patch({
              positions: { ...(d.positions ?? {}), ...(v ? { [c.id]: v } : {}) },
            })} />
        </div>
      ))}
      <Section title="Music" defaultOpen={!!d.music_cue || !!d.music_file || !!d.stop_music}
        badge={d.music_file ? "file set" : d.music_cue ? "cue only" : undefined}
        info={<>Music is state, not an event, so it rides on the node that
          changes state. A one-shot sound belongs on the say or narration beat
          it accompanies instead.</>}>
        <Text label="Music cue (description)" value={d.music_cue ?? ""}
          placeholder="something colder, sparse piano"
          onChange={(v) => patch({ music_cue: v })} />
        <Text label="Music file (under audio/music/)" value={d.music_file ?? ""}
          placeholder="theme_cold.ogg"
          onChange={(v) => patch({ music_file: v || null })} />
        <Check label="Stop the music here" checked={!!d.stop_music}
          onChange={(v) => patch({ stop_music: v })} />
      </Section>
    </>
  );
}

function ChoiceEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const d = node.data ?? {};
  const options: any[] = d.options ?? [];
  const setOption = (index: number, changes: any) =>
    patch({ options: options.map((o, i) => (i === index ? { ...o, ...changes } : o)) });

  return (
    <>
      <div className="section-title">Choice</div>
      <VarDatalist />
      <Text label="Prompt" value={d.prompt ?? ""} onChange={(v) => patch({ prompt: v })} />
      <div className="grid2">
        <Select label="Mode" value={d.mode ?? "jump"}
          options={[["jump", "jump (one way)"], ["call", "call (returns)"]]}
          onChange={(v) => patch({ mode: v })} />
        <Text label="Menuset name" value={d.menuset ?? "menuset"}
          onChange={(v) => patch({ menuset: v })} />
      </div>
      <Check label="Exhaust options (hub scene / menuset)" checked={!!d.exhaust}
        onChange={(v) => patch({ exhaust: v })}
        info={<>The hub pattern: <code>menu (set=…)</code> hides taken options
          and the menu re-runs. Use with <code>call</code>.</>} />
      <Check label="Loop back to this menu after each option" checked={!!d.loop_back}
        onChange={(v) => patch({ loop_back: v })}
        info={<>Half of the hub pattern — the menu re-runs after each option
          returns, so pair it with <code>call</code>.</>} />

      <div className="section-title">Options</div>
      {options.map((opt, index) => (
        <div key={opt.id} className="card" style={{ padding: 8, marginBottom: 6 }}>
          <div className="row" style={{ marginBottom: 4 }}>
            <span className="pill">{opt.id}</span>
            <span style={{ marginLeft: "auto" }} />
            <button className="tiny danger"
              onClick={() => patch({ options: options.filter((_, i) => i !== index) })}>×</button>
          </div>
          <Text label="Label" value={opt.label} onChange={(v) => setOption(index, { label: v })} />
          <GateRows label="Show only if" gates={opt.requires ?? []}
            onChange={(next) => setOption(index, { requires: next })} />
          <Text label="…and this expression" value={opt.condition ?? ""}
            placeholder="knows_letter"
            onChange={(v) => setOption(index, { condition: v })}
            info={<>Both have to hold — the picked requirements and the
              expression are ANDed.</>} />
          <Text label="Branch hint (steers generation)" value={opt.branch_hint ?? ""}
            onChange={(v) => setOption(index, { branch_hint: v })} />

          <EffectRows label="Effects when chosen" effects={opt.effects ?? []}
            onChange={(next) => setOption(index, { effects: next })} />
        </div>
      ))}
      <button style={{ width: "100%" }} onClick={() => patch({
        options: [...options, {
          id: `o${options.length + 1}${Math.random().toString(36).slice(2, 5)}`,
          label: "New option", condition: "", branch_hint: "",
        }],
      })}>+ Add option</button>
    </>
  );
}

/**
 * The variable one effect writes, picked rather than typed.
 *
 * `affection_aiko` is composed from the stat schema and the character list --
 * it exists only because a StatDef and a Character do -- so knowing the naming
 * convention should not be the price of adding five affection. Anything not in
 * the list is still typeable: the graph gets variables from Set nodes too, and
 * a name that has not been used yet is not in `store.variables` at all.
 */
function VarPicker({ value, onChange, onCommit }: {
  value: string;
  onChange: (v: string) => void;
  onCommit?: (v: string) => void;
}) {
  const store = useStore();
  const statVars = store.variables.filter((v) => v.source === "stat");
  const plainVars = store.variables.filter((v) => v.source !== "stat");
  const known = new Set(store.variables.map((v) => v.name));
  const [typing, setTyping] = useState(false);
  // An unrecognised name is already custom; there is nothing to select it in.
  const custom = typing || (!!value && !known.has(value));

  if (custom) {
    return (
      <>
        <VarInput style={{ flex: 2 }} value={value}
          onChange={onChange} onCommit={onCommit} />
        <button className="tiny" title="pick from the list instead"
          onClick={() => { setTyping(false); onChange(""); }}>▾</button>
      </>
    );
  }
  return (
    <select style={{ flex: 2 }} value={value ?? ""}
      onChange={(e) => {
        if (e.target.value === "__type__") { setTyping(true); onChange(""); return; }
        onChange(e.target.value);
      }}>
      <option value="">— pick a variable —</option>
      {statVars.length > 0 && (
        <optgroup label="Character stats">
          {statVars.map((v) => (
            <option key={v.name} value={v.name}>{v.label || v.name}</option>
          ))}
        </optgroup>
      )}
      {plainVars.length > 0 && (
        <optgroup label="Story variables">
          {plainVars.map((v) => (
            <option key={v.name} value={v.name}>{v.name}</option>
          ))}
        </optgroup>
      )}
      <option value="__type__">type a name…</option>
    </select>
  );
}

/**
 * `[Aiko — Affection] [+=] [5]` rows.
 *
 * Set nodes, choice options, hub topics and condition branches all write
 * variables the same way and now all use this, so an increment reads the same
 * wherever it lives. The row shows the stat's range under it, because `+= 5`
 * on a 0–100 stat and on a 0–10 one are very different moves.
 */
export function EffectRows({ label, effects, onChange }: {
  label: string;
  effects: any[];
  onChange: (next: any[]) => void;
}) {
  const store = useStore();
  const stats = store.project?.character_stats ?? [];
  const set = (i: number, changes: any) =>
    onChange(effects.map((e, k) => (k === i ? { ...e, ...changes } : e)));

  return (
    <>
      <label style={{ fontSize: 11, color: "var(--muted)" }}>{label}</label>
      {effects.map((eff, i) => {
        const info = store.variables.find((v) => v.name === eff.var);
        const def = info?.source === "stat"
          ? stats.find((st) => st.id === info.stat_id) : undefined;
        const bands = def?.bands ?? [];
        const op = eff.op ?? "=";
        return (
          <div key={i} style={{ marginBottom: 4 }}>
            <div className="row">
              <VarPicker value={eff.var ?? ""}
                onChange={(v) => set(i, { var: v })}
                onCommit={(v) => store.ensureVariable(v)} />
              <select style={{ width: 56 }} value={op}
                onChange={(e) => set(i, { op: e.target.value })}>
                <option value="=">=</option>
                <option value="+=">+=</option>
                <option value="-=">-=</option>
              </select>
              <input style={{ flex: 2 }} value={eff.value ?? ""}
                placeholder={def ? (op === "=" ? def.default || "0" : "5") : "true"}
                list={bands.length && op === "=" ? `vn-ebands-${def!.id}` : undefined}
                onChange={(e) => set(i, { value: e.target.value })} />
              {bands.length > 0 && op === "=" && (
                <datalist id={`vn-ebands-${def!.id}`}>
                  {bands.map((b, bi) => (
                    <option key={bi} value={String(b.min)}>{b.label}</option>
                  ))}
                </datalist>
              )}
              <button className="tiny danger"
                onClick={() => onChange(effects.filter((_, k) => k !== i))}>×</button>
            </div>
            {def && (
              <div className="hint" style={{ marginTop: 2 }}>
                <code>{eff.var}</code>
                {def.min != null && def.max != null
                  ? ` · ${def.min}–${def.max}`
                  : ""}
                {op !== "=" && def.max != null
                  ? " · Ren’Py does not clamp — gate on the band, not the ceiling"
                  : ""}
              </div>
            )}
          </div>
        );
      })}
      <button className="tiny" onClick={() => onChange([
        ...effects, { var: "", op: "=", value: "true" },
      ])}>+ effect</button>
    </>
  );
}

const HUB_OPS: [string, string][] = [
  [">=", "≥"], [">", ">"], ["==", "="], ["!=", "≠"], ["<=", "≤"], ["<", "<"],
];

/**
 * The picked half of a gate: rows of `[character] Affection ≥ 20`.
 *
 * Shared by hub topics, choice options and condition branches, because all
 * three compile identically — every row ANDed together, then ANDed with
 * whatever expression the author typed. Picking rather than typing is the
 * whole point: a gate on a stat nobody declared is caught in validation
 * instead of quietly evaluating False for the rest of the game.
 *
 * The compiled variable name is shown under each row. Character stats only
 * exist as `{stat}_{character}`, so without that the author has no way to see
 * what a gate actually reads, and no way to match it to a Set node.
 */
export function GateRows({ label, gates, onChange }: {
  label: string;
  gates: StatGate[];
  onChange: (next: StatGate[]) => void;
}) {
  const store = useStore();
  const stats = store.project?.character_stats ?? [];
  const characters = store.characters;
  // Stat instances arrive in the variable list too; listing them twice would
  // let the same gate be built two ways, only one of which knows its character.
  const plainVars = store.variables.filter((v) => v.source !== "stat");
  const set = (i: number, changes: Partial<StatGate>) =>
    onChange(gates.map((g, k) => (k === i ? { ...g, ...changes } : g)));

  return (
    <>
      <label style={{ fontSize: 11, color: "var(--muted)" }}>{label}</label>
      {gates.map((gate, i) => {
        const def = stats.find((st) => st.id === gate.stat);
        const who = characters.find((c) => c.id === gate.character);
        // A per-character stat compiles to `{stat}_{character}`; a plain
        // variable is already its own name and has nobody to pick.
        const compiled = !gate.stat ? ""
          : def ? (gate.character ? `${gate.stat}_${gate.character}` : "")
          : gate.stat;
        const bands = def?.bands ?? [];
        return (
          <div key={i} style={{ marginBottom: 6 }}>
            <div className="row">
              <select style={{ flex: 2 }} value={gate.stat ?? ""}
                onChange={(e) => {
                  const next = e.target.value;
                  const isStat = stats.some((st) => st.id === next);
                  set(i, {
                    stat: next,
                    // A plain variable has no character, and keeping a stale
                    // one would compile a name nothing ever sets.
                    character: isStat
                      ? (gate.character || characters[0]?.id || "")
                      : "",
                  });
                }}>
                <option value="">— pick a stat —</option>
                {stats.length > 0 && (
                  <optgroup label="Character stats">
                    {stats.map((stat) => (
                      <option key={stat.id} value={stat.id}>{stat.name || stat.id}</option>
                    ))}
                  </optgroup>
                )}
                {plainVars.length > 0 && (
                  <optgroup label="Story variables">
                    {plainVars.map((v) => (
                      <option key={`v:${v.name}`} value={v.name}>{v.name}</option>
                    ))}
                  </optgroup>
                )}
              </select>
              <select style={{ flex: 1 }} value={gate.character ?? ""}
                disabled={!def}
                title={def ? "whose stat" : "story variables are not per-character"}
                onChange={(e) => set(i, { character: e.target.value })}>
                <option value="">(no one)</option>
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>{c.display_name}</option>
                ))}
              </select>
              <select style={{ width: 54 }} value={gate.op ?? ">="}
                onChange={(e) => set(i, { op: e.target.value })}>
                {HUB_OPS.map(([value, text]) => (
                  <option key={value} value={value}>{text}</option>
                ))}
              </select>
              <input style={{ width: 64 }} value={gate.value ?? ""} placeholder="20"
                list={bands.length ? `vn-bands-${gate.stat}` : undefined}
                onChange={(e) => set(i, { value: e.target.value })} />
              {bands.length > 0 && (
                <datalist id={`vn-bands-${gate.stat}`}>
                  {bands.map((b, bi) => (
                    <option key={bi} value={String(b.min)}>{b.label}</option>
                  ))}
                </datalist>
              )}
              <button className="tiny danger"
                onClick={() => onChange(gates.filter((_, k) => k !== i))}>×</button>
            </div>
            {gate.stat && (
              <div className="hint" style={{ marginTop: 2 }}>
                {compiled
                  ? <>reads <code>{compiled}</code>
                      {def && who ? ` — ${who.display_name}'s ${def.name || def.id}` : ""}</>
                  : <span className="warn">
                      ⚠ A character stat needs a character — pick whose
                      {" "}{def?.name || gate.stat} this is.
                    </span>}
              </div>
            )}
          </div>
        );
      })}
      <button className="tiny" onClick={() => onChange([
        ...gates,
        {
          stat: "", character: store.characters[0]?.id ?? "", op: ">=", value: "0",
        },
      ])}>+ requirement</button>
      {stats.length === 0 && (
        <div className="hint">
          No character stats declared yet — add them under Setting →
          Character stats and they show up here for every character.
        </div>
      )}
    </>
  );
}

/**
 * A Choice with structured gates.
 *
 * The stat, the character and the operator are all picked, never typed: that
 * is the whole reason a hub is its own node type rather than a choice with
 * conventions. A gate on a stat nobody declared is caught here and in
 * validation instead of quietly evaluating False for the rest of the game.
 */
function HubEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const d = node.data ?? {};
  const options: any[] = d.options ?? [];
  const stats = store.project?.character_stats ?? [];
  const routes = useRouteNames();

  const setOption = (index: number, changes: any) =>
    patch({ options: options.map((o, i) => (i === index ? { ...o, ...changes } : o)) });

  return (
    <>
      <div className="section-title">
        <LabelText label="Hub" info={
          <>A menu you come back to. Topics are gated on declared stats rather
            than typed expressions, and an unmet topic can either vanish or show
            greyed — greyed is what gives the player something to aim at.</>} />
      </div>
      <Text label="Prompt" value={d.prompt ?? ""} onChange={(v) => patch({ prompt: v })} />
      <div className="grid2">
        <Select label="Mode" value={d.mode ?? "call"}
          options={[["call", "call (topic returns)"], ["jump", "jump (one way)"]]}
          onChange={(v) => patch({ mode: v })} />
        <Text label="Menuset name" value={d.menuset ?? "hub_topics"}
          onChange={(v) => patch({ menuset: v })} />
      </div>
      <Check label="Drop a topic once it has been taken" checked={d.exhaust !== false}
        onChange={(v) => patch({ exhaust: v })} />
      <Check label="Come back to this menu after each topic"
        checked={d.loop_back !== false}
        onChange={(v) => patch({ loop_back: v })} />
      {d.mode === "jump" && d.loop_back !== false && (
        <div className="hint warn">
          ⚠ A <code>jump</code> topic never comes back, so nothing returns to
          the menu. Use <code>call</code> for a hub that loops.
        </div>
      )}
      {d.exhaust !== false && d.loop_back !== false
        && !options.some((o) => o.repeatable) && (
        <div className="hint warn">
          ⚠ Every topic drops off once taken and the menu always comes back, so
          it runs out — and Ren&rsquo;Py raises on a menu with nothing in it.
          Mark the way out as always available.
        </div>
      )}

      <div className="section-title">Topics</div>
      {options.map((opt, index) => {
        const requires: any[] = opt.requires ?? [];
        return (
          <div key={opt.id} className="card" style={{ padding: 8, marginBottom: 6 }}>
            <div className="row" style={{ marginBottom: 4 }}>
              <span className="pill">{opt.id}</span>
              <span style={{ marginLeft: "auto" }} />
              <button className="tiny danger"
                onClick={() => patch({ options: options.filter((_, i) => i !== index) })}>×</button>
            </div>
            <Text label="Label" value={opt.label ?? ""}
              onChange={(v) => setOption(index, { label: v })} />
            {d.exhaust !== false && (
              <Check label="Always available (never drops off the menu)"
                checked={!!opt.repeatable}
                onChange={(v) => setOption(index, { repeatable: v })} />
            )}

            <GateRows label="Needs" gates={requires}
              onChange={(next) => setOption(index, { requires: next })} />
            {requires.length > 0 && (
              <>
                <div className="grid2" style={{ marginTop: 6 }}>
                  <Select label="When it is not met" value={opt.locked ?? "hide"}
                    options={[["hide", "hide the topic"], ["grey", "show it greyed"]]}
                    onChange={(v) => setOption(index, { locked: v })}
                    info={<>Ren&rsquo;Py has no disabled menu item, so a greyed
                      topic compiles to a second entry that drops straight back
                      to this menu — clicking it costs a click and changes
                      nothing.</>} />
                  <Text label="Greyed label" value={opt.locked_label ?? ""}
                    placeholder={`${opt.label || "Topic"} (locked)`}
                    onChange={(v) => setOption(index, { locked_label: v })} />
                </div>
              </>
            )}

            <Select label="Goes to" value={opt.target_route ? "route" : "wired"}
              options={[["wired", "whatever the port is wired to"],
                        ["route", "the next unplayed scene in a route"]]}
              onChange={(v) => setOption(index, {
                target_route: v === "route" ? (routes[0] ?? "") : "",
              })} />
            {opt.target_route !== undefined && opt.target_route !== "" && (
              <>
                <div className="field">
                  <label>Route</label>
                  <input list="vn-routes" value={opt.target_route}
                    onChange={(e) => setOption(index, { target_route: e.target.value })} />
                  <datalist id="vn-routes">
                    {routes.map((r) => <option key={r} value={r} />)}
                  </datalist>
                </div>
                {!routes.includes(opt.target_route) && (
                  <div className="hint warn">
                    ⚠ No scene claims this route yet — set a Route on the scenes
                    that belong to it.
                  </div>
                )}
              </>
            )}

            <Text label="Branch hint (steers generation)" value={opt.branch_hint ?? ""}
              onChange={(v) => setOption(index, { branch_hint: v })} />

            <EffectRows label="Effects when chosen" effects={opt.effects ?? []}
              onChange={(next) => setOption(index, { effects: next })} />
          </div>
        );
      })}
      <VarDatalist />
      <button style={{ width: "100%" }} onClick={() => patch({
        options: [...options, {
          id: `o${options.length + 1}${Math.random().toString(36).slice(2, 5)}`,
          label: "New topic", requires: [], locked: "hide", locked_label: "",
          repeatable: false, branch_hint: "", effects: [], target_route: "",
        }],
      })}>+ Add topic</button>
      {stats.length === 0 && (
        <div className="hint" style={{ marginTop: 6 }}>
          No character stats declared yet — add them in Config so topics have
          something to gate on.
        </div>
      )}
    </>
  );
}

/**
 * Jump / Call. The destination is an *edge*, not a field — which is exact, but
 * left a drag across the canvas as the only way to retarget one, and the
 * canvas shows a scene at a time, so a hop to another scene was a drag you
 * could not make. The dropdown writes the same edge.
 */

/** Prefix marking a route entry in the destination select, where the other
 *  values are node ids. */
const ROUTE_PREFIX = "route:";

function JumpEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const d = node.data ?? {};
  const routes = useRouteNames();
  const route = String(d.target_route ?? "").trim();
  const scenes = store.nodes.filter((n) => n.type === "scene");
  const pinned = store.nodes.filter(
    (n) => n.pinned_label && n.type !== "scene" && n.id !== node.id);

  const targetOn = (handle: string) =>
    store.edges.find((e) => e.source === node.id && e.source_handle === handle)?.target ?? "";

  const wireTo = (handle: string, id: string) => {
    if (!id) {
      const edge = store.edges.find(
        (x) => x.source === node.id && x.source_handle === handle);
      if (edge) store.disconnect(edge.id);
      return;
    }
    store.connect(node.id, id, handle);
  };

  const nodeOptions = (
    <>
      <optgroup label="Scenes">
        {scenes.map((scene) => (
          <option key={scene.id} value={scene.id}>
            {scene.data?.title || "Untitled scene"}
          </option>
        ))}
      </optgroup>
      {pinned.length > 0 && (
        <optgroup label="Pinned labels">
          {pinned.map((n) => (
            <option key={n.id} value={n.id}>
              {String(n.data?.text ?? n.data?.prompt ?? n.slug ?? n.id).slice(0, 40)}
            </option>
          ))}
        </optgroup>
      )}
    </>
  );

  const picker = (handle: string, label: string, none = "— not wired —",
                  info?: ReactNode) => (
    <div className="field">
      <label><LabelText label={label} info={info} /></label>
      <select value={targetOn(handle)}
        onChange={(e) => wireTo(handle, e.target.value)}>
        <option value="">{none}</option>
        {nodeOptions}
      </select>
    </div>
  );

  /* The destination, which is either a node or a route. A route is not a
     node the graph can hold an edge to -- it resolves at play time to the
     first scene claiming it that this playthrough has not reached -- so it
     lives in the same select rather than as a second control the author has
     to know overrides the first. */
  const destination = (
    <div className="field">
      <label><LabelText label={node.type === "jump" ? "Goes to" : "Calls"} info={
        route
          ? <>Resolved when the player arrives, not at compile time: the first
              scene claiming “{route}” this playthrough has not played, in
              canvas order. Add a scene to the route and it slots in — nothing
              here needs rewiring.</>
          : <>Only Scenes and pinned nodes are listed, because those are the
              ones with a label you can count on. Drag the port on the canvas to
              target anything else — that pins a label for it.</>} /></label>
      <select value={route ? ROUTE_PREFIX + route : targetOn("out")}
        onChange={(e) => {
          const v = e.target.value;
          if (v.startsWith(ROUTE_PREFIX)) {
            patch({ target_route: v.slice(ROUTE_PREFIX.length) });
            return;
          }
          if (route) patch({ target_route: "" });
          wireTo("out", v);
        }}>
        <option value="">— not wired —</option>
        {routes.length > 0 && (
          <optgroup label="Routes">
            {routes.map((r) => (
              <option key={r} value={ROUTE_PREFIX + r}>
                next unplayed scene in “{r}”
              </option>
            ))}
          </optgroup>
        )}
        {nodeOptions}
      </select>
    </div>
  );

  return (
    <>
      <div className="section-title">
        <LabelText label={node.type} info={node.type === "jump"
          ? "One way: the story carries on at the target and never comes back."
          : "Calls the target, then continues down the “then →” port when it returns."} />
      </div>
      {destination}
      {route && picker("out",
        node.type === "jump" ? "Once the route is finished"
                             : "Once the route is finished, call",
        node.type === "jump" ? "— stop here —" : "— skip the call —",
        <>A route runs out. For a jump that is the end of the block unless you
          send it somewhere — back to the hub that dispatched here is the usual
          answer.</>)}
      {node.type === "call" && picker("after", "Then continues at")}
      <Text label="Raw label (optional)" value={d.target_label ?? ""}
        onChange={(v) => patch({ target_label: v })}
        info={<>A fallback for labels hand-written outside the graph. A wired
          target wins over it.</>} />
    </>
  );
}

function ConditionEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const store = useStore();
  const d = node.data ?? {};
  const branches: any[] = d.branches ?? [];
  const setBranch = (index: number, changes: any) => patch({
    branches: branches.map((b, i) => (i === index ? { ...b, ...changes } : b)),
  });
  const setExpr = (index: number, expr: string) => setBranch(index, { expr });
  return (
    <>
      <div className="section-title">
        <LabelText label="Condition" info={
          <>Gate on a character stat by picking it, or write a Python expression
            Ren&rsquo;Py evaluates — a branch with both needs both. A branch
            with neither is the <code>else</code>. Effects fire on the way
            through, so &ldquo;she notices — +5 affection&rdquo; needs no Set
            node.</>} />
      </div>
      <VarDatalist />
      {store.variables.length > 0 && (
        <div className="chip-grid" style={{ marginBottom: 6 }}>
          {store.variables.map((v) => (
            <span key={v.name} className="chip" title={v.note || `insert ${v.name}`}
              onClick={() => {
                const last = branches.length - 1;
                const target = last >= 0 ? last : 0;
                if (!branches.length) return;
                const cur = branches[target].expr || "";
                setExpr(target, cur ? `${cur} ${v.name}` : v.name);
              }}>{v.name}</span>
          ))}
        </div>
      )}
      {branches.map((branch, index) => {
        const gates: StatGate[] = branch.requires ?? [];
        const expr = (branch.expr ?? "").trim();
        return (
          <div key={branch.id} className="card" style={{ padding: 8, marginBottom: 6 }}>
            <div className="row" style={{ marginBottom: 4 }}>
              <span className="pill">
                {!gates.length && !expr ? "else" : index === 0 ? "if" : "elif"}
              </span>
              <span style={{ marginLeft: "auto" }} />
              <button className="tiny danger"
                onClick={() => patch({ branches: branches.filter((_, i) => i !== index) })}>×</button>
            </div>
            <GateRows label="Takes this branch when" gates={gates}
              onChange={(next) => setBranch(index, { requires: next })} />
            <div className="row" style={{ marginTop: 4, marginBottom: 6 }}>
              <input value={branch.expr ?? ""} list="vn-vars"
                placeholder={gates.length ? "…and this expression (optional)" : "affinity > 5"}
                onChange={(e) => setExpr(index, e.target.value)} />
            </div>
            <EffectRows label="Effects on the way through"
              effects={branch.effects ?? []}
              onChange={(next) => setBranch(index, { effects: next })} />
          </div>
        );
      })}
      <button style={{ width: "100%", marginTop: 4 }} onClick={() => patch({
        branches: [...branches, {
          id: `b${branches.length + 1}${Math.random().toString(36).slice(2, 5)}`,
          expr: "", requires: [], effects: [],
        }],
      })}>+ Add branch</button>
      <Check label="Emit a final else" checked={d.has_else !== false}
        onChange={(v) => patch({ has_else: v })} />
    </>
  );
}

function SetEditor({ node, patch }: { node: IRNode; patch: (d: any) => void }) {
  const d = node.data ?? {};
  const assignments: any[] = d.assignments ?? [];

  return (
    <>
      <div className="section-title">
        <LabelText label="Set" info={
          <>Every variable touched here gets a <code>default</code> statement in
            generated/flags.rpy, and joins the dropdown on other nodes.</>} />
      </div>
      <VarDatalist />
      <EffectRows label="Assignments" effects={assignments}
        onChange={(next) => patch({ assignments: next })} />
    </>
  );
}

/* -------------------------------------------------------------- sub-panels */

/**
 * Where the last generation thought the scene could go next.
 *
 * The prose pass ends a stretch that has *not* reached a decision with a short
 * list of directions. They are notes to the author, never story content and
 * never in the graph — clicking one just seeds the next generation with it, so
 * you steer the scene by picking rather than by typing a note every time.
 *
 * They stay on the node after it has been continued past, because trying a
 * different direction is exactly when you want them again — but the action
 * changes. Appending to a node that already has a successor splices a second
 * chain in *front* of existing content, which is never what "actually, go the
 * other way" means, so once the node is spent a click regenerates the
 * downstream instead.
 */
function ContinuationButtons({ node, busy, generate }: {
  node: IRNode;
  busy: boolean;
  generate: (fn: () => Promise<{ job_id: string }>) => Promise<void>;
}) {
  const store = useStore();
  const options: string[] = node.data?._continuations ?? [];
  if (!options.length) return null;

  // What this node already leads to, if anything. Its presence flips the
  // buttons from "append" to "replace what follows".
  const next = store.edges.find(
    (e) => e.source === node.id && e.source_handle === "out")?.target;

  return (
    <>
      <div className="section-title">
        {next ? "Instead, continue with" : "Continue with"}
      </div>
      {options.map((text, i) => (
        <button key={i} className="continuation" disabled={busy}
          title={next
            ? "Replace what follows with this direction — the old block is parked, not deleted"
            : "Generate the next stretch in this direction"}
          onClick={() => generate(() => next
            ? api.regenerateFrom(store.project!.id, next, "", "", text)
            : api.generateScene(store.project!.id, {
                anchor_id: node.id, continuation: text,
              }))}>
          {text}
        </button>
      ))}
      <div className="row">
        {next && (
          <span className="hint">
            Replaces the block below. Ctrl+Z puts it back.
          </span>
        )}
        <span style={{ marginLeft: "auto" }} />
        <button className="link-ish" disabled={busy}
          title="Drop these suggestions"
          onClick={() => store.patchNode(node.id, { data: { _continuations: [] } })}>
          Dismiss
        </button>
      </div>
    </>
  );
}

/** Shared with the Script view. `label` and `className` let it sit as a full
 *  width inspector button or a compact row action without forking the modal. */
export function ContextPreviewButton({ nodeId, label, className }: {
  nodeId: string; label?: string; className?: string;
}) {
  const store = useStore();
  const [preview, setPreview] = useState<any>(null);
  // The steer note is the whole of what the author typed, and it is the last
  // thing the model reads -- a preview that silently left it out was showing a
  // user turn the real generation never sends. "Continue with a note" stores it
  // under :scene, "Regenerate with a note" under :regen.
  const note = store.steerNotes[`${nodeId}:scene`]
    || store.steerNotes[`${nodeId}:regen`] || "";

  return (
    <>
      <button
        className={className}
        style={className ? undefined : { width: "100%", marginTop: 6 }}
        onClick={async () => {
          try {
            setPreview(await api.contextPreview(store.project!.id, nodeId, "", note));
          } catch (e) {
            store.fail(String(e));
          }
        }}
      >
        {label ?? "Preview the assembled prompt"}
      </button>
      {preview && (
        <div className="modal-backdrop" onClick={() => setPreview(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="head">
              <span className="title">Assembled context</span>
              <span className="pill">~{preview.token_estimate} tokens</span>
              <span style={{ marginLeft: "auto" }} />
              <button onClick={() => setPreview(null)}>Close</button>
            </div>
            <div className="content">
              <div className="hint" style={{ marginBottom: 10 }}>
                This is the path from root to the insertion node — not a chat log.
                Regenerating on another branch never sees this content.
              </div>
              <div className="section-title">System</div>
              <pre className="script">{preview.system}</pre>
              <div className="section-title">User</div>
              <pre className="script">{preview.user}</pre>
              <div className="hint" style={{ marginTop: 8 }}>
                {note
                  ? <>Includes the note remembered on this node ("{note}"). The
                      scene brief is not repeated here — it is in the system
                      prompt, under "Scene brief".</>
                  : <>The scene brief is not repeated here — it is in the
                      system prompt, under "Scene brief". A note typed into
                      "Continue with a note" appears in this block.</>}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// World bible now lives on its own tab (views/SettingView.tsx) -- this used
// to also render it, back when "nothing selected" was the only project-wide
// settings screen. Story variables stay here: they're read while wiring Set
// / Condition / Choice nodes on the canvas, which is exactly the context
// this panel appears in.
function VariablesPanel() {
  const store = useStore();
  const declared = store.variables.filter((v) => !v.derived);
  const derived = store.variables.filter((v) => v.derived);

  const commit = (rows: { name: string; type: any; default: string; note: string }[]) =>
    store.saveVariables(rows.filter((r) => r.name.trim()));

  const setAt = (index: number, changes: any) =>
    commit(declared.map((v, i) => (i === index
      ? { name: v.name, type: v.type, default: v.default, note: v.note, ...changes }
      : { name: v.name, type: v.type, default: v.default, note: v.note })));

  return (
    <>
      <div className="section-title">
        <LabelText label="Story variables" info={
          <>Named here so they can be picked from a dropdown on Set, Condition
            and Choice nodes instead of retyped. Names used on a node but not
            saved show as <em>inferred</em>.</>} />
      </div>
      {declared.map((v, index) => (
        <div key={index} className="row" style={{ marginBottom: 4, marginTop: 4 }}>
          <input style={{ flex: 2 }} value={v.name}
            onChange={(e) => setAt(index, { name: e.target.value })} />
          <select style={{ width: 64 }} value={v.type}
            onChange={(e) => setAt(index, { type: e.target.value })}>
            <option value="bool">bool</option>
            <option value="int">int</option>
            <option value="str">str</option>
          </select>
          <input style={{ width: 60 }} value={v.default} placeholder="default"
            onChange={(e) => setAt(index, { default: e.target.value })} />
          <button className="tiny danger"
            onClick={() => commit(declared.filter((_, i) => i !== index))}>×</button>
        </div>
      ))}
      <button className="tiny" onClick={() => commit([
        ...declared.map((v) => ({ name: v.name, type: v.type, default: v.default, note: v.note })),
        { name: `var_${declared.length + 1}`, type: "bool", default: "", note: "" },
      ])}>+ variable</button>
      {derived.length > 0 && (
        <div className="hint" style={{ marginTop: 6 }}>
          Inferred from nodes:{" "}
          {derived.map((v) => (
            <span key={v.name} className="chip" style={{ marginRight: 4 }}
              onClick={() => commit([
                ...declared.map((x) => ({ name: x.name, type: x.type, default: x.default, note: x.note })),
                { name: v.name, type: v.type, default: "", note: "" },
              ])}
              title="click to save">{v.name}</span>
          ))}
        </div>
      )}
    </>
  );
}

const PRESETS = [
  "shorter", "longer, more interiority", "colder and more clipped",
  "warmer, less guarded", "more subtext, less on the nose",
  "raise the tension", "end on a question", "less exposition",
];

/** Shared with the Script view, so a one-off steer looks and behaves the same
 *  wherever you ask for one. */
export function SteerModal({ mode, initial, onClose, onSubmit }: {
  mode: "scene" | "regen";
  initial?: string;
  onClose: () => void;
  onSubmit: (instruction: string) => void;
}) {
  const [text, setText] = useState(initial ?? "");
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
        <div className="head">
          <span className="title">
            {mode === "scene" ? "Continue with a note" : "Regenerate with a note"}
          </span>
        </div>
        <div className="content">
          <div className="field">
            <label>One-off instruction</label>
            <textarea rows={3} autoFocus value={text} placeholder="much shorter, and colder"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim()) {
                  onSubmit(text.trim());
                }
              }} />
          </div>
          <div className="chip-grid">
            {PRESETS.map((preset) => (
              <span key={preset} className="chip"
                onClick={() => setText(text ? `${text}, ${preset}` : preset)}>
                {preset}
              </span>
            ))}
          </div>
          <div className="hint" style={{ marginTop: 8 }}>
            Added to this generation only — it is not saved to the prompt
            template, so the next generation is unaffected. The text is
            remembered on this node, so you can hit the button again without
            retyping.
            {mode === "regen" && " The previous take is kept as a variant."}
          </div>
        </div>
        <div className="foot">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!text.trim()}
            onClick={() => onSubmit(text.trim())}>Generate</button>
        </div>
      </div>
    </div>
  );
}

function DeleteModal({ type, belowCount, onClose, onConfirm }: {
  type: string;
  belowCount: number;
  onClose: () => void;
  onConfirm: (cascade: boolean) => void;
}) {
  const [cascade, setCascade] = useState(false);
  return (
    <ConfirmDialog
      title={`Delete ${type} node`}
      confirmLabel={cascade ? `Delete ${belowCount + 1} nodes` : "Delete node"}
      onCancel={onClose}
      onConfirm={() => onConfirm(cascade)}
    >
      <div className="hint" style={{ marginBottom: 10 }}>
        This node has {belowCount} node{belowCount === 1 ? "" : "s"} downstream
        of it that nothing else reaches.
      </div>
      <label className="row" style={{ gap: 8, marginBottom: 6, cursor: "pointer" }}>
        <input type="radio" checked={!cascade} onChange={() => setCascade(false)} />
        <span>Delete only this node, reconnecting the path around it</span>
      </label>
      <label className="row" style={{ gap: 8, cursor: "pointer" }}>
        <input type="radio" checked={cascade} onChange={() => setCascade(true)} />
        <span>Delete this node and {belowCount} below it</span>
      </label>
      <div className="hint" style={{ marginTop: 10 }}>
        Undo restores every deleted node with its original id, so Ren'Py
        labels do not move.
      </div>
    </ConfirmDialog>
  );
}

/* ------------------------------------------------------------------ fields */

/** Shared <datalist> of the project's known variable names. */
export function VarDatalist() {
  const variables = useStore((s) => s.variables);
  return (
    <datalist id="vn-vars">
      {variables.map((v) => <option key={v.name} value={v.name} />)}
    </datalist>
  );
}

/** A variable-name input backed by the vn-vars datalist. `onCommit` fires on
 *  blur so a freshly typed name can be registered on the project. */
export function VarInput({ value, onChange, onCommit, placeholder, style }: {
  value: string;
  onChange: (v: string) => void;
  onCommit?: (v: string) => void;
  placeholder?: string;
  style?: CSSProperties;
}) {
  return (
    <input
      list="vn-vars"
      value={value ?? ""}
      placeholder={placeholder ?? "variable"}
      style={style}
      onChange={(e) => onChange(e.target.value)}
      onBlur={(e) => { const v = e.target.value.trim(); if (v) onCommit?.(v); }}
    />
  );
}

/**
 * A collapsible block in the inspector.
 *
 * `defaultOpen` is meant to be driven by whether the section holds anything
 * yet: a say node now carries content, performance, sound and image fields,
 * and showing all of them flat buries the two that matter for the line in
 * front of you. `badge` is for saying what is in a closed section without
 * opening it ("2 cues", "CG ready").
 */
export function Section({ title, children, defaultOpen = true, badge, info }: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  badge?: string;
  info?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="insp-section">
      {/* The tip sits beside the header rather than in it: a button inside a
          button is invalid, and the header is one. */}
      <div className="sec-head-row">
        <button className="sec-head" onClick={() => setOpen(!open)}
          aria-expanded={open}>
          <span className="caret">{open ? "▾" : "▸"}</span>
          <span>{title}</span>
          {badge && <><span className="spacer" /><span className="pill">{badge}</span></>}
        </button>
        {info && <InfoTip>{info}</InfoTip>}
      </div>
      {open && <div className="insp-section-body">{children}</div>}
    </div>
  );
}

/** Sentinel for the "type a name nobody has used yet" entry in a route picker. */
const NEW_ROUTE = "__new_route__";

/**
 * Every route name a scene claims, sorted.
 *
 * Routes have no registry: a route exists because scenes belong to it, and
 * its order is canvas order. That keeps adding a scene to a route to one
 * field, but it means the *name* has to agree everywhere -- which is why the
 * pickers below exist.
 */
export function useRouteNames(): string[] {
  const store = useStore();
  return useMemo(() => [...new Set(
    store.nodes
      .filter((n) => n.type === "scene" && String(n.data?.route ?? "").trim())
      .map((n) => String(n.data.route).trim()),
  )].sort(), [store.nodes]);
}

/**
 * Route membership, picked rather than typed.
 *
 * Typing was the old behaviour and it failed silently: "Aiko" and "aiko" are
 * two routes, and a hub or jump dispatching over one walks straight past
 * every scene in the other. A new route still costs one typed name -- the
 * picker just makes that a deliberate act instead of a typo.
 */
export function RouteSelect({ label, value, onChange, noneLabel = "— no route —", info }: {
  label: string; value: string; onChange: (v: string) => void; noneLabel?: string;
  info?: ReactNode;
}) {
  const routes = useRouteNames();
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const current = (value ?? "").trim();
  // A route this node claims that no *other* scene does yet is still a real
  // choice -- it must stay in the list or the select would show blank.
  const known = routes.includes(current) ? routes : [current, ...routes].filter(Boolean);

  const commit = () => {
    onChange(draft.trim());
    setDraft("");
    setTyping(false);
  };

  if (typing) {
    return (
      <div className="field">
        <label>{label}</label>
        <div className="row" style={{ gap: 6 }}>
          <input autoFocus value={draft} placeholder="aiko"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") { setDraft(""); setTyping(false); }
            }} />
          <button className="tiny" disabled={!draft.trim()} onClick={commit}>Add</button>
          <button className="tiny ghost"
            onClick={() => { setDraft(""); setTyping(false); }}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div className="field">
      <label><LabelText label={label} info={info} /></label>
      <select value={current}
        onChange={(e) => {
          if (e.target.value === NEW_ROUTE) { setDraft(""); setTyping(true); return; }
          onChange(e.target.value);
        }}>
        <option value="">{noneLabel}</option>
        {known.map((r) => <option key={r} value={r}>{r}</option>)}
        <option value={NEW_ROUTE}>＋ New route…</option>
      </select>
    </div>
  );
}

export function Text({ label, value, onChange, placeholder, info }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
  info?: ReactNode;
}) {
  return (
    <div className="field">
      {label && <label><LabelText label={label} info={info} /></label>}
      <input value={value ?? ""} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function Area({ label, value, onChange, rows = 3, mono, placeholder, info }: {
  label: string; value: string; onChange: (v: string) => void; rows?: number;
  mono?: boolean; placeholder?: string; info?: ReactNode;
}) {
  return (
    <div className="field">
      {label && <label><LabelText label={label} info={info} /></label>}
      <textarea rows={rows} value={value ?? ""} placeholder={placeholder}
        style={mono ? { fontFamily: "var(--mono)", fontSize: 12 } : undefined}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function Select({ label, value, options, onChange, info }: {
  label: string; value: string; options: [string, string][]; onChange: (v: string) => void;
  info?: ReactNode;
}) {
  return (
    <div className="field">
      {label && <label><LabelText label={label} info={info} /></label>}
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

export function Check({ label, checked, onChange, info }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; info?: ReactNode;
}) {
  return (
    <div className="field">
      {/* The tip is outside the <label>: clicking a label toggles its control,
          and a tip you cannot hover without ticking the box is worse than none. */}
      <div className="row" style={{ gap: 5 }}>
        <label className="row" style={{ gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={checked}
            onChange={(e) => onChange(e.target.checked)} />
          <span>{label}</span>
        </label>
        {info && <InfoTip>{info}</InfoTip>}
      </div>
    </div>
  );
}

export function Num({ label, value, onChange, step = 0.1, info }: {
  label: string; value: number; onChange: (v: number) => void; step?: number;
  info?: ReactNode;
}) {
  return (
    <div className="field">
      {label && <label><LabelText label={label} info={info} /></label>}
      <input type="number" step={step} value={value ?? 0}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)} />
    </div>
  );
}

export function Range({ label, value, onChange, min = 0, max = 1, step = 0.05 }: {
  label: string; value: number; onChange: (v: number) => void;
  min?: number; max?: number; step?: number;
}) {
  return (
    <div className="field">
      <label>{label}<span>{Number(value ?? 0).toFixed(2)}</span></label>
      <input type="range" min={min} max={max} step={step} value={value ?? 0}
        onChange={(e) => onChange(parseFloat(e.target.value))} />
    </div>
  );
}
