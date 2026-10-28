import { useState } from "react";
import { api } from "../lib/api";
import {
  backgroundFor, backgroundRenderKey, nearestSprite, spriteRenderKey, spriteSlot,
  type Backdrop, type Sprite,
} from "../lib/player";
import { useStore } from "../lib/store";
import type { Job, PreviewOp } from "../lib/types";
import Inspector, { Select } from "./Inspector";

/** What the Play view's edit panel is showing: the node inspector, one
 *  sprite on stage (by character id), or the backdrop. */
export type PanelTab = "node" | "background" | `sprite:${string}`;

/** A render queued from the player, keyed by spriteRenderKey /
 *  backgroundRenderKey. */
export interface Render {
  job: string;
  label: string;
}

/**
 * The editor beside the preview stage. The node tab is the canvas inspector
 * itself, pointed at the line on screen; the sprite and background tabs re-shoot
 * exactly the asset the stage is showing, or the one it wanted and fell back
 * from.
 */
export default function PlayEditPanel({
  tab, onTab, onClose, sprites, background, line, renders, onQueued, onOpenNode,
}: {
  tab: PanelTab;
  onTab: (tab: PanelTab) => void;
  onClose: () => void;
  sprites: Sprite[];
  background: Backdrop;
  line: PreviewOp | null;
  renders: Record<string, Render>;
  onQueued: (key: string, render: Render) => void;
  onOpenNode: (id: string) => void;
}) {
  const store = useStore();
  const selected = store.nodes.find((n) => n.id === store.selected);
  const nodeLabel = !selected ? "Node"
    : selected.type === "say" || selected.type === "narration" ? "Line"
      : selected.type[0].toUpperCase() + selected.type.slice(1);
  const sprite = tab.startsWith("sprite:")
    ? sprites.find((s) => `sprite:${s.character}` === tab)
    : undefined;
  const nameOf = (s: Sprite) =>
    store.characters.find((c) => c.id === s.character)?.display_name ?? s.name;

  return (
    <aside className="play-panel">
      <div className="play-panel-head">
        <div className="tabs scroll-x">
          <button className={tab === "node" ? "active" : ""} onClick={() => onTab("node")}
            title="Edit the node on screen — text, performance, voice, CG">
            {nodeLabel}
          </button>
          {sprites.map((s) => (
            <button key={s.character}
              className={tab === `sprite:${s.character}` ? "active" : ""}
              onClick={() => onTab(`sprite:${s.character}`)}
              title={`${nameOf(s)}'s sprite`}>
              {nameOf(s)}
            </button>
          ))}
          <button className={tab === "background" ? "active" : ""}
            onClick={() => onTab("background")}>
            Background
          </button>
        </div>
        <button className="ghost icon-btn" title="Close the editor (E)" onClick={onClose}>✕</button>
      </div>
      <div className="play-panel-body">
        {tab === "node" && <Inspector />}
        {sprite && (
          <SpriteEditor key={sprite.character} sprite={sprite} name={nameOf(sprite)} line={line}
            render={renders[spriteRenderKey(sprite)]} onQueued={onQueued}
            onOpenNode={onOpenNode} />
        )}
        {tab === "background" && (
          <BackgroundEditor background={background} renders={renders}
            onQueued={onQueued} onOpenNode={onOpenNode} />
        )}
      </div>
    </aside>
  );
}

function renderStatus(job: Job | undefined): string {
  if (!job || job.status === "queued" || job.status === "claimed") return "queued…";
  return `rendering ${Math.round((job.progress ?? 0) * 100)}%`;
}

function SpriteEditor({ sprite, name, line, render, onQueued, onOpenNode }: {
  sprite: Sprite;
  name: string;
  line: PreviewOp | null;
  render: Render | undefined;
  onQueued: (key: string, render: Render) => void;
  onOpenNode: (id: string) => void;
}) {
  const store = useStore();
  // Prompt nudges for this render only -- the same one-off fields the
  // character page's sprite lightbox offers, never saved to the character.
  const [positive, setPositive] = useState("");
  const [negative, setNegative] = useState("");
  const [sending, setSending] = useState(false);

  const character = store.characters.find((c) => c.id === sprite.character);
  const wanted = spriteSlot(sprite.outfit, sprite.pose, sprite.expression);
  const shown = character
    ? nearestSprite(character.assets.sprite_sheet, sprite.outfit, sprite.pose, sprite.expression)
    : null;
  const exact = shown?.slot === wanted;
  const outfit = character?.outfits.find((o) => o.id === sprite.outfit);
  const job = render ? store.jobs.find((j) => j.id === render.job) : undefined;
  const busy = sending || !!render;
  // Expression and pose are properties of the line, so they are only
  // editable here when the line on screen is this character speaking.
  const lineNode = line?.node && line.speaker === sprite.character
    ? store.nodes.find((n) => n.id === line.node && n.type === "say")
    : undefined;

  const queue = async () => {
    setSending(true);
    try {
      const { job_id } = await api.generateSprite(store.project!.id, {
        character_id: sprite.character,
        outfit_id: sprite.outfit,
        pose: sprite.pose,
        expression: sprite.expression,
        extra_positive: positive.trim(),
        extra_negative: negative.trim(),
      });
      onQueued(spriteRenderKey(sprite), { job: job_id, label: `${name}'s ${wanted} sprite` });
      await store.refreshJobs();
    } catch (e) {
      store.fail(String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="play-asset">
      <div className={`asset-preview${busy ? " busy" : ""}`}>
        {shown
          ? <img src={api.assetUrl(shown.hash)} alt={`${name} sprite`} />
          : <span className="hint">no sprite yet</span>}
        {render && <span className="stage-busy">{renderStatus(job)}</span>}
      </div>

      <div className="asset-title">
        <strong>{name}</strong>
        <span className="pill">{outfit?.name || sprite.outfit || "no outfit"}</span>
      </div>
      <table className="data">
        <tbody>
          <tr><th>Pose</th><td>{sprite.pose}</td></tr>
          <tr><th>Expression</th><td>{sprite.expression}</td></tr>
          <tr><th>Position</th><td>{sprite.position}</td></tr>
        </tbody>
      </table>
      {!character && <div className="hint bad">This character no longer exists.</div>}
      {character && !shown && (
        <div className="hint warn">
          Nothing generated for this outfit yet, so the stage shows a placeholder.
        </div>
      )}
      {shown && !exact && (
        <div className="hint warn">
          No <code>{wanted}</code> sprite yet. Showing the nearest one, <code>{shown.slot}</code>.
        </div>
      )}

      {lineNode && (
        <>
          <div className="section-title">This line</div>
          <div className="grid2">
            <Select label="Expression" value={lineNode.data?.expression ?? "neutral"}
              options={(store.vocab?.expressions ?? []).map((e) => [e, e] as [string, string])}
              onChange={(v) => store.patchNode(lineNode.id, { data: { expression: v } })} />
            <Select label="Pose" value={lineNode.data?.pose ?? "front"}
              options={(store.vocab?.poses ?? []).map((p) => [p, p] as [string, string])}
              onChange={(v) => store.patchNode(lineNode.id, { data: { pose: v } })} />
          </div>
          <div className="hint">
            Picks which sprite this line shows. If that sprite does not exist yet,
            generate it below.
          </div>
        </>
      )}

      <div className="section-title">{exact ? "Regenerate" : "Generate"}</div>
      <div className="field">
        <label>Extra positive tags</label>
        <input value={positive} disabled={busy} placeholder="hand on hip, windy"
          onChange={(e) => setPositive(e.target.value)} />
      </div>
      <div className="field">
        <label>Extra negative tags</label>
        <input value={negative} disabled={busy} placeholder="jacket, hat"
          onChange={(e) => setNegative(e.target.value)} />
      </div>
      <button className="primary wide" disabled={busy || !character} onClick={queue}>
        {render ? renderStatus(job)
          : exact ? "⟳ Regenerate this sprite" : "Generate this sprite"}
      </button>
      <div className="hint" style={{ marginTop: 6 }}>
        The extra tags apply to this render only. The result replaces
        the <code>{wanted}</code> slot, so every line that shows it gets the new image.
      </div>

      <div className="section-title">Open</div>
      <div className="play-links">
        {sprite.node && (
          <button onClick={() => onOpenNode(sprite.node!)}
            title="The node that brought this character on stage or last changed how they look">
            ✎ Node that set this sprite
          </button>
        )}
        {character && (
          <button onClick={() => store.focusCharacter(character.id, "sprites")}
            title="Leaves Play for the character page">
            All of {name}'s sprites →
          </button>
        )}
      </div>
    </div>
  );
}

function BackgroundEditor({ background, renders, onQueued, onOpenNode }: {
  background: Backdrop;
  renders: Record<string, Render>;
  onQueued: (key: string, render: Render) => void;
  onOpenNode: (id: string) => void;
}) {
  const store = useStore();
  const [sending, setSending] = useState(false);

  const location = store.locations.find((l) => l.id === background.id);
  const shown = location ? backgroundFor(location.variants, background.time) : null;
  // With no time of day the stage shows the location's first variant, so that
  // is the one to re-shoot; "day" is what batch fill would make otherwise.
  const variant = background.time || shown?.variant || "day";
  const exact = shown?.variant === variant;
  const key = location ? backgroundRenderKey(location.id, variant) : "";
  const render = key ? renders[key] : undefined;
  const job = render ? store.jobs.find((j) => j.id === render.job) : undefined;
  const busy = sending || !!render;

  const queue = async () => {
    if (!location) return;
    setSending(true);
    try {
      const { job_id } = await api.generateBackground(store.project!.id, {
        location_id: location.id,
        variant,
      });
      onQueued(key, { job: job_id, label: `${location.name} (${variant}) background` });
      await store.refreshJobs();
    } catch (e) {
      store.fail(String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="play-asset">
      <div className={`asset-preview wide${busy ? " busy" : ""}`}>
        {shown
          ? <img src={api.assetUrl(shown.hash)} alt={`${location?.name} background`} />
          : <span className="hint">no background</span>}
        {render && <span className="stage-busy">{renderStatus(job)}</span>}
      </div>

      {!background.id && (
        <div className="hint warn" style={{ marginTop: 8 }}>
          No background is set here. Pick a location on the Scene or Stage node
          that starts this stretch.
        </div>
      )}
      {background.id && !location && (
        <div className="hint bad" style={{ marginTop: 8 }}>
          Unknown location <code>{background.id}</code>. It may have been deleted.
        </div>
      )}

      {location && (
        <>
          <div className="asset-title">
            <strong>{location.name}</strong>
            <span className="pill">{variant}</span>
          </div>
          {!shown && (
            <div className="hint warn">Nothing generated for this location yet.</div>
          )}
          {shown && !exact && (
            <div className="hint warn">
              No <code>{variant}</code> variant yet. Showing <code>{shown.variant}</code>.
            </div>
          )}
          <button className="primary wide" disabled={busy} onClick={queue}
            style={{ marginTop: 10 }}>
            {render ? renderStatus(job)
              : exact ? `⟳ Regenerate ${variant}` : `Generate ${variant}`}
          </button>
          <div className="hint" style={{ marginTop: 6 }}>
            Replaces the {variant} variant of {location.name} in every scene that
            uses it.
          </div>
        </>
      )}

      <div className="section-title">Open</div>
      <div className="play-links">
        {background.node && (
          <button onClick={() => onOpenNode(background.node!)}
            title="The Scene or Stage node that set this location and time of day">
            ✎ Node that set this background
          </button>
        )}
        {location && (
          <button onClick={() => store.focusLocation(location.id)}
            title="Leaves Play for the Setting view">
            {location.name} in Setting →
          </button>
        )}
      </div>
    </div>
  );
}
