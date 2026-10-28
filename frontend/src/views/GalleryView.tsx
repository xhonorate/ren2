import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import ConfirmDialog from "../components/ConfirmDialog";
import type { AssetType, GalleryAsset, GalleryResponse } from "../lib/types";

/** One page. Small enough that a cold gallery paints immediately, large enough
 *  that a full screen of thumbs is one request. */
const PAGE = 48;

/** Backend sentinel for "belongs to no character" / "is in no scene". */
const NONE = "__none__";

const TYPES: { id: AssetType; label: string }[] = [
  { id: "sprite", label: "Sprites" },
  { id: "cg", label: "CG images" },
  { id: "background", label: "Backgrounds" },
  { id: "portrait", label: "Portraits" },
  { id: "voice", label: "Voice lines" },
  { id: "voice_ref", label: "Voice refs" },
  { id: "sfx", label: "SFX" },
  { id: "other", label: "Other" },
];

const TYPE_LABELS = Object.fromEntries(TYPES.map((t) => [t.id, t.label]));

/** Types that can belong to a character. The character filter is only offered
 *  while the type selection overlaps this set -- a background or an SFX has no
 *  character, so the control could only ever empty the grid. */
const CHARACTER_TYPES = new Set<string>(
  ["sprite", "cg", "portrait", "voice", "voice_ref"]);

const USED: { id: Filters["used"]; label: string }[] = [
  { id: "all", label: "all" },
  { id: "used", label: "used" },
  { id: "unused", label: "unused" },
];

const UNATTRIBUTED = " unattributed";

/** Job statuses a queued regeneration is finished in. */
const TERMINAL = new Set(["done", "error", "cancelled"]);

/** What "regenerate" will actually do, per kind of reference — the tooltip on
 *  each row, so nothing is re-rendered on a guess about which field moves. */
const REGEN_HINT: Record<string, string> = {
  cg: "Re-render this CG and put the new image on the node. The current one is kept as a take.",
  voice: "Re-voice this line onto the node, from its text and the character's reference clip.",
  sprite: "Re-render this sprite into the same outfit/pose/expression slot.",
  background: "Re-render this background into the same location and time-of-day variant.",
  portrait: "Re-render this character's profile headshot.",
};

interface Filters {
  types: string[];
  characters: string[];
  scenes: string[];
  used: "all" | "used" | "unused";
}

const NO_FILTERS: Filters = { types: [], characters: [], scenes: [], used: "all" };

interface Group {
  key: string;
  name: string;
  assets: GalleryAsset[];
}

/** The uses of an asset a generator can re-shoot. An SFX, a voice reference
 *  and a training-pool image come from the author, so they have none. */
function regenerable(asset: GalleryAsset): GalleryAsset["used_by"] {
  return asset.used_by.filter((use) => use.regen);
}

/** Add or drop `value` from a list -- the toggle every chip row needs. */
function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export default function GalleryView() {
  const store = useStore();
  // `assets` accumulates across pages; `meta` is always the newest response,
  // and carries the counts for the whole filtered set rather than this page.
  const [assets, setAssets] = useState<GalleryAsset[]>([]);
  const [meta, setMeta] = useState<GalleryResponse | null>(null);
  // One object, so a fetch depends on one identity that only changes when the
  // user actually touches a control -- an array per filter would re-fetch on
  // every render.
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [selected, setSelected] = useState<GalleryAsset | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [purging, setPurging] = useState(false);
  // A referenced asset can only go via an explicit force, so that force gets
  // its own confirm -- the button sits one tap away from "Close".
  const [confirmForce, setConfirmForce] = useState<GalleryAsset | null>(null);
  // Regenerations queued from this view. Watched so the grid refreshes itself
  // when the last of them lands -- an asset that has been replaced on its node
  // is a different picture and a different use count, and leaving the old one
  // on screen is how you end up regenerating it twice.
  const [pending, setPending] = useState<string[]>([]);
  const [regenerating, setRegenerating] = useState(false);

  // Guards the auto-loader: an IntersectionObserver fires again before React
  // has re-rendered with the new page, which would request the same offset
  // twice and append it twice.
  const inFlight = useRef(false);
  const sentinel = useRef<HTMLDivElement | null>(null);

  const fetchPage = useCallback(async (offset: number) => {
    if (!store.project || inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const res = await api.gallery(store.project.id, { ...filters, offset, limit: PAGE });
      setMeta(res);
      setAssets((prev) => (offset === 0 ? res.assets : [...prev, ...res.assets]));
    } catch (e) {
      store.fail(String(e));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [store.project?.id, filters]);

  // Any filter change starts the list over at page 0.
  useEffect(() => { fetchPage(0); }, [fetchPage]);

  const hasMore = !!meta?.has_more;

  // Fetch the next page as the end of the list comes into view, a screen
  // early. The button inside the sentinel is the fallback when the observer
  // never fires (no layout, no support).
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasMore || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && !inFlight.current) fetchPage(assets.length);
    }, { rootMargin: "600px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, assets.length, loading, fetchPage]);

  const totalMb = useMemo(() => ((meta?.bytes ?? 0) / 1e6).toFixed(1), [meta]);

  // The character filter is meaningless for a purely background/SFX selection,
  // so it is hidden there -- and cleared with it, because a filter nobody can
  // see silently emptying the grid is the worst of both.
  const charFilterRelevant = filters.types.length === 0
    || filters.types.some((t) => CHARACTER_TYPES.has(t));

  const setTypes = (id: string) => setFilters((f) => {
    const types = toggle(f.types, id);
    const relevant = types.length === 0 || types.some((t) => CHARACTER_TYPES.has(t));
    return { ...f, types, characters: relevant ? f.characters : [] };
  });

  // Assets grouped by character, in roster order, with everything that could
  // not be attributed to one collected at the end. The backend orders its rows
  // the same way, so a group stays contiguous as pages append rather than
  // re-shuffling under the reader. The backend only ever reports a
  // character_id that is on the current roster, so there is no
  // deleted-character case to handle here.
  const groups = useMemo<Group[]>(() => {
    const buckets = new Map<string, GalleryAsset[]>();
    for (const asset of assets) {
      const key = asset.character_id ?? UNATTRIBUTED;
      const bucket = buckets.get(key);
      if (bucket) bucket.push(asset);
      else buckets.set(key, [asset]);
    }
    const out: Group[] = [];
    for (const character of store.characters) {
      const list = buckets.get(character.id);
      if (list?.length) {
        out.push({ key: character.id, name: character.display_name, assets: list });
      }
    }
    const loose = buckets.get(UNATTRIBUTED);
    if (loose?.length) out.push({ key: UNATTRIBUTED, name: "Unattributed", assets: loose });
    return out;
  }, [assets, store.characters]);

  // Job ids that have actually appeared in the dock. A queued id that has not
  // arrived yet is still running; one that was there and is gone was cleared,
  // which is as finished as it gets.
  const seenJobs = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!pending.length) return;
    const byId = new Map(store.jobs.map((job) => [job.id, job]));
    for (const id of pending) if (byId.has(id)) seenJobs.current.add(id);
    const finished = pending.every((id) => {
      const job = byId.get(id);
      return job ? TERMINAL.has(job.status) : seenJobs.current.has(id);
    });
    if (!finished) return;
    const failed = pending.filter((id) => byId.get(id)?.status === "error").length;
    setPending([]);
    fetchPage(0);
    store.notify(failed
      ? `${failed} regeneration${failed === 1 ? "" : "s"} failed — see the job dock`
      : "regenerated — the node now points at the new file");
  }, [store.jobs, pending, fetchPage]);

  // Keep the open detail modal on the newest copy of its row: after a
  // regeneration the old asset is still here, but with one fewer use.
  useEffect(() => {
    if (!selected) return;
    const fresh = assets.find((a) => a.hash === selected.hash);
    if (fresh && fresh !== selected) setSelected(fresh);
  }, [assets]);

  if (!store.project) return null;

  /** Open whatever uses this asset, in the view that edits it. */
  const goToUse = (use: GalleryAsset["used_by"][number]) => {
    const role = use.role || "";
    if (use.kind === "node") store.focusNode(use.id);
    else if (use.kind === "character") {
      store.focusCharacter(use.id,
        role.startsWith("sprite pool") ? "sprite-lora"
          : role.startsWith("CG pool") ? "cg-lora"
          : role.startsWith("sprite") ? "sprites"
          : role.startsWith("voice") || role.startsWith("audition") ? "voice"
          : "general");
    } else if (use.kind === "location") store.focusLocation(use.id);
    else return;                 // a sheet row has nowhere of its own to open
    setSelected(null);
  };

  /** Re-shoot the asset. `ref` picks one use of it; empty redoes every one. */
  const regenerate = async (asset: GalleryAsset, ref = "") => {
    setRegenerating(true);
    try {
      const res = await api.regenerateAsset(store.project!.id, asset.hash, ref);
      setPending((p) => [...p, ...res.job_ids]);
      store.notify(res.count === 1
        ? "regeneration queued"
        : `${res.count} regenerations queued`);
      store.refreshJobs();
    } catch (e) {
      store.fail(String(e));
    } finally {
      setRegenerating(false);
    }
  };

  const remove = async (asset: GalleryAsset, force = false) => {
    try {
      const res = await api.deleteGalleryAsset(store.project!.id, asset.hash, force);
      store.notify(res.cleared_count
        ? `asset deleted · ${res.cleared_count} reference(s) unset`
        : "asset deleted");
      setSelected(null);
      fetchPage(0);
      // The delete unsets whatever pointed at the file, so the nodes and
      // characters in memory are now a version behind: a say node that still
      // claimed a voice line would keep showing the badge for audio that no
      // longer exists.
      if (res.cleared_count) {
        await Promise.all([store.refreshGraph(), store.refreshCharacters()]);
      }
    } catch (e) {
      store.fail(String(e));
    }
  };

  const purgeUnused = async () => {
    setPurging(true);
    try {
      const res = await api.deleteUnusedAssets(store.project!.id);
      store.notify(
        `deleted ${res.count} asset${res.count === 1 ? "" : "s"}` +
        (res.bytes ? ` · freed ${(res.bytes / 1e6).toFixed(1)} MB` : ""));
      if (res.errors.length) store.fail(`${res.errors.length} file(s) could not be removed`);
      setConfirmPurge(false);
      setSelected(null);
      fetchPage(0);
    } catch (e) {
      store.fail(String(e));
    } finally {
      setPurging(false);
    }
  };

  const renderThumb = (asset: GalleryAsset) => (
    <div key={asset.hash}
      className={`thumb ${selected?.hash === asset.hash ? "on" : ""}`}
      style={{ aspectRatio: asset.kind === "img" ? "2 / 3" : "3 / 1" }}
      title={[TYPE_LABELS[asset.type] ?? asset.type, ...asset.scene_names].join(" · ")}
      onClick={() => setSelected(asset)}>
      {asset.kind === "img" ? (
        // Off-screen thumbs are the whole cost of this page: never fetch one
        // until the browser is about to paint it.
        <img src={api.assetUrl(asset.hash)} loading="lazy" decoding="async"
          alt={`${asset.type} asset`} />
      ) : (
        <div className="audio-tile">
          <span>♪</span>
          {/* preload="none" -- a controls element otherwise fetches metadata for
              every clip on the page the moment it mounts. */}
          <audio controls preload="none" src={api.assetUrl(asset.hash)}
            onClick={(e) => e.stopPropagation()} />
        </div>
      )}
      {regenerable(asset).length > 0 && (
        <button className="regen" disabled={regenerating || pending.length > 0}
          title={regenerable(asset).length > 1
            ? `Re-shoot all ${regenerable(asset).length} uses of this file`
            : REGEN_HINT[regenerable(asset)[0].regen!.action]}
          onClick={(e) => { e.stopPropagation(); regenerate(asset); }}>
          ⟳
        </button>
      )}
      <div className="cap">
        {asset.use_count === 0
          ? <span style={{ color: "var(--warn)" }}>unused</span>
          : `${asset.use_count} use${asset.use_count > 1 ? "s" : ""}`}
        {" · "}{(asset.bytes / 1024).toFixed(0)} KB
      </div>
    </div>
  );

  const typeCounts = meta?.facets.types ?? {};
  const charCounts = meta?.facets.characters ?? {};
  const sceneCounts = meta?.facets.scenes ?? {};
  const filtered = filters.types.length > 0 || filters.characters.length > 0
    || filters.scenes.length > 0 || filters.used !== "all";

  return (
    <div className="view">
      <h2>Asset gallery</h2>
      <p className="lede">
        Every generated file, content-addressed and grouped by the character it
        belongs to. Identical images collapse to one entry automatically, and
        anything still referenced cannot be deleted by accident.
      </p>

      <div className="filter-bar">
        <div className="filter-row">
          <span className="filter-label">Type</span>
          <div className="chip-grid">
            {TYPES.map((entry) => (
              <button key={entry.id}
                className={`chip ${filters.types.includes(entry.id) ? "on" : ""}`}
                onClick={() => setTypes(entry.id)}>
                {entry.label}
                <span className="chip-count">{typeCounts[entry.id] ?? 0}</span>
              </button>
            ))}
          </div>
        </div>

        {charFilterRelevant && (
          <div className="filter-row">
            <span className="filter-label">Character</span>
            <div className="chip-grid">
              {store.characters.map((character) => (
                <button key={character.id}
                  className={`chip ${filters.characters.includes(character.id) ? "on" : ""}`}
                  onClick={() => setFilters((f) =>
                    ({ ...f, characters: toggle(f.characters, character.id) }))}>
                  {character.display_name}
                  <span className="chip-count">{charCounts[character.id] ?? 0}</span>
                </button>
              ))}
              <button
                className={`chip ${filters.characters.includes(NONE) ? "on" : ""}`}
                title="Assets that resolve to no character -- backgrounds, SFX, anything untagged"
                onClick={() => setFilters((f) =>
                  ({ ...f, characters: toggle(f.characters, NONE) }))}>
                Unattributed
                <span className="chip-count">{charCounts[NONE] ?? 0}</span>
              </button>
            </div>
          </div>
        )}

        <div className="filter-row">
          <span className="filter-label">Scene</span>
          <select value={filters.scenes[0] ?? ""}
            onChange={(e) => setFilters((f) =>
              ({ ...f, scenes: e.target.value ? [e.target.value] : [] }))}>
            <option value="">Every scene</option>
            {(meta?.scenes ?? []).map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.title} ({sceneCounts[scene.id] ?? 0})
              </option>
            ))}
            <option value={NONE}>No scene ({sceneCounts[NONE] ?? 0})</option>
          </select>

          <div className="tabs">
            {USED.map((entry) => (
              <button key={entry.id}
                className={filters.used === entry.id ? "active" : ""}
                onClick={() => setFilters((f) => ({ ...f, used: entry.id }))}>
                {entry.label}
              </button>
            ))}
          </div>

          <button title="Re-reads the asset folder and recounts what still references each file"
            onClick={() => fetchPage(0)} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
          {filtered && (
            <button onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>
          )}

          <span className="spacer" />
          {meta && (
            <span className="hint">
              {filtered ? `${meta.total} of ${meta.library_total}` : `${meta.total}`} assets
              {" · "}{totalMb} MB · {meta.unused} unused
            </span>
          )}
          {meta && meta.library_unused > 0 && (
            <button className="danger" disabled={purging}
              title="Deletes every file nothing references, off disk and out of the index, whatever the filters say. Store-wide: an asset another project still uses is left alone."
              onClick={() => setConfirmPurge(true)}>
              Delete {meta.library_unused} unused
            </button>
          )}
        </div>
      </div>

      {meta && meta.total === 0 && (
        <p className="hint">
          {filtered
            ? "Nothing matches these filters."
            : "Nothing here yet — generate a background or a sprite first."}
        </p>
      )}

      {groups.map((group) => {
        // The backend's per-character count is for the whole filtered set, so
        // a group that is still paging in says so rather than lying low.
        const total = charCounts[group.key === UNATTRIBUTED ? NONE : group.key]
          ?? group.assets.length;
        return (
          <section key={group.key} className="gallery-group">
            <h3>
              {group.name}
              <span className="hint" style={{ fontWeight: 400 }}>
                {group.assets.length < total
                  ? `${group.assets.length} of ${total} assets`
                  : `${total} asset${total === 1 ? "" : "s"}`}
                {group.assets.some((a) => a.use_count === 0) &&
                  ` · ${group.assets.filter((a) => a.use_count === 0).length} unused`}
              </span>
            </h3>
            <div className="thumbs">
              {group.assets.map(renderThumb)}
            </div>
          </section>
        );
      })}

      <div ref={sentinel} className="gallery-more">
        {hasMore && (
          <button onClick={() => fetchPage(assets.length)} disabled={loading}>
            {loading ? "Loading…" : `Load ${Math.min(PAGE, (meta?.total ?? 0) - assets.length)} more`}
          </button>
        )}
        {meta && assets.length > 0 && (
          <span className="hint">showing {assets.length} of {meta.total}</span>
        )}
      </div>

      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="head">
              <span className="title mono">{selected.hash.slice(0, 16)}…</span>
              <span className="pill">{TYPE_LABELS[selected.type] ?? selected.type}</span>
              {selected.character_name && (
                <span className="pill purple">{selected.character_name}</span>
              )}
              {selected.scene_names.map((name, i) => (
                <span key={i} className="pill">{name}</span>
              ))}
              <span className="spacer" />
              <button onClick={() => setSelected(null)}>Close</button>
            </div>
            <div className="content">
              <div className="split">
                <div className="right">
                  {selected.kind === "img"
                    ? <img src={api.assetUrl(selected.hash)} alt="selected asset"
                        style={{ width: "100%", borderRadius: 6 }} />
                    : <audio controls src={api.assetUrl(selected.hash)}
                        style={{ width: "100%" }} />}
                </div>
                <div className="left" style={{ width: 300 }}>
                  <div className="section-title">Used by</div>
                  {selected.used_by.length === 0 && (
                    <div className="hint warn">
                      Nothing references this. Safe to delete.
                    </div>
                  )}
                  <div className="list">
                    {selected.used_by.map((use, i) => (
                      <div key={i} className="item use-row">
                        <span className="pill">{use.kind}</span>
                        <button className="use-open"
                          title={use.kind === "node"
                            ? "Open this node on the canvas, with its inspector"
                            : use.kind === "character"
                              ? "Open this character"
                              : use.kind === "location"
                                ? "Open this location under Setting"
                                : "No editor for this reference"}
                          disabled={!["node", "character", "location"].includes(use.kind)}
                          onClick={() => goToUse(use)}>
                          {use.role}: {use.label}
                        </button>
                        {use.regen && (
                          <button className="tiny" disabled={regenerating || pending.length > 0}
                            title={REGEN_HINT[use.regen.action]
                              ?? "Re-shoot this asset onto whatever uses it"}
                            onClick={() => regenerate(selected, use.regen!.ref)}>
                            ⟳
                          </button>
                        )}
                      </div>
                    ))}
                  </div>

                  <div className="section-title">Details</div>
                  <table className="data">
                    <tbody>
                      <tr><th>type</th><td>{TYPE_LABELS[selected.type] ?? selected.type}</td></tr>
                      <tr><th>size</th><td>{(selected.bytes / 1024).toFixed(1)} KB</td></tr>
                      <tr><th>created</th><td>{(selected.created_at ?? "").slice(0, 16)}</td></tr>
                      {selected.character_name && (
                        <tr><th>character</th><td>{selected.character_name}</td></tr>
                      )}
                      {selected.scene_names.length > 0 && (
                        <tr><th>scene</th><td>{selected.scene_names.join(", ")}</td></tr>
                      )}
                      {selected.meta?.seed !== undefined && (
                        <tr><th>seed</th><td>{selected.meta.seed}</td></tr>
                      )}
                      {selected.meta?.emotion && (
                        <tr><th>emotion</th><td>{selected.meta.emotion}</td></tr>
                      )}
                    </tbody>
                  </table>
                  {selected.meta?.prompt && (
                    <>
                      <div className="section-title">Prompt</div>
                      <pre className="script" style={{ maxHeight: 160 }}>
                        {selected.meta.prompt}
                      </pre>
                    </>
                  )}
                </div>
              </div>
            </div>
            <div className="foot">
              {selected.use_count > 0 && (
                <span className="hint" style={{ marginRight: "auto" }}>
                  Referenced in {selected.use_count} place(s) — deleting needs a force.
                </span>
              )}
              {regenerable(selected).length > 0 && (
                <button className="primary" disabled={regenerating || pending.length > 0}
                  title={regenerable(selected).length > 1
                    ? "Re-shoot every use of this file. Each one gets its own new take — an "
                      + "identical image on two nodes is two renders."
                    : REGEN_HINT[regenerable(selected)[0].regen!.action]}
                  onClick={() => regenerate(selected)}>
                  {pending.length > 0 ? "Regenerating…"
                    : regenerable(selected).length > 1
                      ? `⟳ Regenerate all ${regenerable(selected).length}`
                      : "⟳ Regenerate"}
                </button>
              )}
              <button className="danger"
                title={selected.use_count > 0
                  ? "Still referenced — deleting unsets all of those references, so it asks to confirm."
                  : "Nothing references this file. Deleting it is safe."}
                onClick={() => {
                  if (selected.use_count > 0) setConfirmForce(selected);
                  else remove(selected);
                }}>
                {selected.use_count > 0 ? "Force delete…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmForce && (
        <ConfirmDialog
          title="Force delete a referenced asset?"
          confirmLabel="Force delete"
          onCancel={() => setConfirmForce(null)}
          onConfirm={() => {
            const asset = confirmForce;
            setConfirmForce(null);
            remove(asset, true);
          }}
        >
          <div className="hint">
            {confirmForce.use_count} place(s) still point at this file. Deleting it
            removes the file from disk and unsets every one of those references —
            the nodes, characters and locations go back to having no asset there,
            and turn up again under Build → Fill missing assets. Prompts, seeds
            and settings are kept, so regenerating restores them. This cannot be
            undone.
          </div>
        </ConfirmDialog>
      )}

      {confirmPurge && (
        <ConfirmDialog
          title="Delete unused assets?"
          confirmLabel="Delete unused"
          busy={purging}
          onCancel={() => setConfirmPurge(false)}
          onConfirm={purgeUnused}
        >
          <div className="hint">
            Permanently removes every asset that nothing references — from the
            gallery and from the filesystem — regardless of the filters above.
            Assets still used by any node, character or location (in any
            project) are left alone. This cannot be undone.
          </div>
        </ConfirmDialog>
      )}
    </div>
  );
}
