import { useEffect, useState } from "react";
import { api } from "../lib/api";
import TagInput from "../components/TagInput";
import ConfirmDialog from "../components/ConfirmDialog";
import { Area, Text } from "../components/Inspector";
import { useStore } from "../lib/store";
import type { StatDef } from "../lib/types";

/**
 * World bible + locations, together: both are project-wide facts the graph
 * itself doesn't hold anywhere. Split out of the Characters view and the
 * canvas inspector's "nothing selected" panel so neither has to double as a
 * project-settings screen.
 */
export default function SettingView() {
  const store = useStore();
  if (!store.project) return null;

  return (
    <div className="view">
      <h2>Setting</h2>
      <p className="lede">
        The world bible steers every prose generation. Locations are what a
        Stage node moves a scene to, and what backgrounds and event CGs
        render against.
      </p>

      <div className="setting-split">
        <div className="card">
          <WorldBiblePanel />
        </div>
        <div className="card">
          <div className="section-title">Locations</div>
          <LocationList />
        </div>
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <CharacterStatsPanel />
      </div>
    </div>
  );
}

/**
 * The character-stat schema: declared once here, instantiated for every
 * character as `{id}_{character_id}` (`affection_diana`).
 *
 * Bands are what make a static character card dynamic without asking you to
 * write a paragraph per relationship stage — the writer is told
 * "Affection 34 (warming)" and calibrates from there. `In card` off keeps a
 * pure content pointer (route progress) out of the prose prompt while still
 * compiling to a variable choices can gate on.
 */
export function CharacterStatsPanel() {
  const store = useStore();
  const project = store.project!;
  const [stats, setStats] = useState<StatDef[]>(project.character_stats ?? []);

  useEffect(() => setStats(project.character_stats ?? []), [project.id]);

  const save = async (next: StatDef[]) => {
    setStats(next);
    await store.patchProject({ character_stats: next });
  };
  const edit = (i: number, patch: Partial<StatDef>) =>
    save(stats.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  return (
    <>
      <div className="section-title">Character stats</div>
      <div className="hint" style={{ marginBottom: 10 }}>
        Persist for the whole playthrough, one variable per character. Choice
        effects and conditions can read and write them, and the writer is told
        the current band on each character's card. Keep affection (how she
        behaves) separate from route progress (which scenes are unlocked) —
        collapsing them makes every affection choice cosmetic.
      </div>

      {stats.map((stat, i) => (
        <div key={i} className="card" style={{ marginBottom: 8 }}>
          <div className="grid3">
            <Text label="Id (becomes affection_diana)" value={stat.id}
              onChange={(v) => edit(i, { id: v.trim().replace(/\s+/g, "_") })} />
            <Text label="Display name" value={stat.name}
              onChange={(v) => edit(i, { name: v })} />
            <div className="field">
              <label>Type</label>
              <select value={stat.type}
                onChange={(e) => edit(i, { type: e.target.value as StatDef["type"] })}>
                <option value="int">int</option>
                <option value="bool">bool</option>
                <option value="str">str</option>
              </select>
            </div>
          </div>
          <div className="grid3">
            <Text label="Default" value={stat.default}
              onChange={(v) => edit(i, { default: v })} />
            <Text label="Min" value={stat.min == null ? "" : String(stat.min)}
              onChange={(v) => edit(i, { min: v === "" ? null : Number(v) })} />
            <Text label="Max" value={stat.max == null ? "" : String(stat.max)}
              onChange={(v) => edit(i, { max: v === "" ? null : Number(v) })} />
          </div>
          <label className="row" style={{ gap: 6, marginBottom: 6 }}>
            <input type="checkbox" checked={stat.in_card}
              onChange={(e) => edit(i, { in_card: e.target.checked })} />
            <span>Tell the writer this stat's current band</span>
          </label>

          <div className="section-title">Bands</div>
          {(stat.bands ?? []).map((band, bi) => (
            <div key={bi} className="grid3" style={{ marginBottom: 4 }}>
              <Text label="From" value={String(band.min)}
                onChange={(v) => edit(i, {
                  bands: stat.bands.map((b, j) =>
                    j === bi ? { ...b, min: Number(v) || 0 } : b),
                })} />
              <Text label="Label" value={band.label}
                onChange={(v) => edit(i, {
                  bands: stat.bands.map((b, j) => (j === bi ? { ...b, label: v } : b)),
                })} />
              <div className="row" style={{ alignItems: "flex-end" }}>
                <Text label="Note (optional)" value={band.note}
                  onChange={(v) => edit(i, {
                    bands: stat.bands.map((b, j) => (j === bi ? { ...b, note: v } : b)),
                  })} />
                <button onClick={() => edit(i, {
                  bands: stat.bands.filter((_, j) => j !== bi),
                })}>✕</button>
              </div>
            </div>
          ))}
          <div className="row">
            <button onClick={() => edit(i, {
              bands: [...(stat.bands ?? []), { min: 0, label: "", note: "" }],
            })}>+ band</button>
            <span style={{ marginLeft: "auto" }} />
            <button onClick={() => save(stats.filter((_, j) => j !== i))}>
              Remove stat
            </button>
          </div>
        </div>
      ))}

      <button onClick={() => save([...stats, {
        id: "", name: "", type: "int", default: "0", min: 0, max: 100,
        note: "", bands: [], in_card: true,
      }])}>+ stat</button>
    </>
  );
}

/** Also used by the canvas sidebar's "Edit world bible…" quick modal, so
 * wiring a graph never has to leave the canvas for a one-line change. */
export function WorldBiblePanel() {
  const store = useStore();
  const project = store.project!;
  const [world, setWorld] = useState(project.world);

  useEffect(() => setWorld(project.world), [project.id]);

  const save = async (next: typeof world) => {
    setWorld(next); // keep typing responsive
    // ...and push the saved value back into the store, or this panel reverts
    // to the stale `store.project.world` next time `project.id` changes.
    await store.patchProject({ world: next });
  };

  return (
    <>
      <div className="section-title">World bible</div>
      <Area label="Setting" rows={2} value={world.setting}
        onChange={(v) => save({ ...world, setting: v })} />
      <div className="grid2">
        <Text label="Era" value={world.era} onChange={(v) => save({ ...world, era: v })} />
        <Text label="POV" value={world.pov} onChange={(v) => save({ ...world, pov: v })} />
      </div>
      <Text label="Tense" value={world.tense} onChange={(v) => save({ ...world, tense: v })} />
      <div className="hint">
        No content rating here on purpose: register is a property of a scene,
        not of a story. Each scene&rsquo;s <b>NSFW</b> checkbox carries it —
        tick it and the writer works in the adult register for that scene and
        no other.
      </div>
      <Area label="Style notes" rows={4} value={world.style_notes}
        onChange={(v) => save({ ...world, style_notes: v })} />
      <Area label="Themes" rows={2} value={world.themes}
        onChange={(v) => save({ ...world, themes: v })} />
    </>
  );
}

function LocationList() {
  const store = useStore();
  const [name, setName] = useState("");
  // In the store, not local state, so the gallery can send you straight to
  // the location a background belongs to.
  const selected = store.selectedLocationId;
  const setSelected = store.selectLocation;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const location = store.locations.find((l) => l.id === selected);

  return (
    <>
      <div className="list">
        {store.locations.map((l) => (
          <div
            key={l.id}
            className={`item ${selected === l.id ? "active" : ""}`}
            onClick={() => setSelected(l.id)}
          >
            <span>{l.name}</span>
            <span className="sub">{Object.keys(l.variants).length || "—"}</span>
          </div>
        ))}
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <input
          placeholder="New location"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          disabled={!name.trim()}
          onClick={async () => {
            await api.createLocation(store.project!.id, { name });
            setName("");
            store.refreshCharacters();
          }}
        >
          +
        </button>
      </div>

      {location && (
        <div className="card" style={{ marginTop: 8 }}>
          <TagInput
            label="Tags"
            value={location.tags}
            prefer={["scene", "light", "object"]}
            hint="Pasted into every background and every CG set here. Left
                  blank the location's name stands in, which works but is
                  vaguer than real tags."
            onChange={async (v) => {
              await api.saveLocation(store.project!.id, {
                ...location,
                tags: v,
              });
              store.refreshCharacters();
            }}
          />
          <div className="chip-grid" style={{ marginBottom: 6 }}>
            {(store.vocab?.time_of_day ?? []).map((time) => (
              <span
                key={time}
                className={`chip ${location.variants[time] ? "on" : ""}`}
                onClick={async () => {
                  await api.generateBackground(store.project!.id, {
                    location_id: location.id,
                    variant: time,
                  });
                  store.notify(`background queued: ${time}`);
                  store.refreshJobs();
                }}
              >
                {time}
              </span>
            ))}
          </div>
          <div className="hint">
            Click a time of day to generate that variant.
          </div>
          <div className="thumbs" style={{ marginTop: 6 }}>
            {Object.entries(location.variants).map(([variant, hash]) => (
              <div
                key={variant}
                className="thumb"
                style={{ aspectRatio: "16/9" }}
              >
                <img src={api.assetUrl(hash)} loading="lazy" alt="location background" />
                <div className="cap">{variant}</div>
              </div>
            ))}
          </div>
          <button
            className="tiny danger"
            style={{ marginTop: 6 }}
            onClick={() => setConfirmDelete(true)}
          >
            Delete location
          </button>
          {confirmDelete && (
            <ConfirmDialog
              title={`Delete ${location.name}?`}
              confirmLabel="Delete location"
              onCancel={() => setConfirmDelete(false)}
              onConfirm={async () => {
                setConfirmDelete(false);
                await api.deleteLocation(store.project!.id, location.id);
                setSelected(null);
                store.refreshCharacters();
              }}
            >
              <div className="hint">
                Removes the location and every generated background variant.
                A Stage node still pointing at it will read as unknown until
                reassigned.
              </div>
            </ConfirmDialog>
          )}
        </div>
      )}
    </>
  );
}
