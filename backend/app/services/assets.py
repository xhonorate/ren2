"""Content-addressed asset storage.

assets/img/<sha256>.png -- dedup for free, and regeneration never destroys the
previous take (doc S6).
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Optional

from .. import db, store
from . import staleness

KIND_DIRS = {
    "img": "img",
    "audio": "audio",
    "voice_ref": "voice_ref",
    "sheet": "sheets",
}


def _dir_for(kind: str) -> Path:
    return db.ASSET_DIR / KIND_DIRS.get(kind, kind)


def path_for(asset_hash: str) -> Optional[Path]:
    row = db.one("SELECT * FROM assets WHERE hash = ?", (asset_hash,))
    if not row:
        return None
    path = _dir_for(row["kind"]) / f"{asset_hash}.{row['ext']}"
    return path if path.exists() else None


def put(data: bytes, kind: str = "img", ext: str = "png",
        meta: Optional[dict[str, Any]] = None) -> str:
    digest = hashlib.sha256(data).hexdigest()
    folder = _dir_for(kind)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{digest}.{ext}"
    if not target.exists():
        target.write_bytes(data)
    db.execute(
        "INSERT OR REPLACE INTO assets (hash, kind, ext, meta, created_at) VALUES (?,?,?,?,?)",
        (digest, kind, ext, db.dumps(meta or {}), store.now()),
    )
    return digest


def put_file(src: Path, kind: str = "img", meta: Optional[dict[str, Any]] = None) -> str:
    return put(Path(src).read_bytes(), kind=kind, ext=Path(src).suffix.lstrip(".") or "bin",
               meta=meta)


def get_meta(asset_hash: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM assets WHERE hash = ?", (asset_hash,))
    if not row:
        return {}
    return {"hash": row["hash"], "kind": row["kind"], "ext": row["ext"],
            "meta": db.loads(row["meta"], {}), "created_at": row["created_at"]}


def copy_to(asset_hash: str, dest: Path) -> bool:
    src = path_for(asset_hash)
    if src is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def usage_report() -> dict[str, Any]:
    rows = db.query("SELECT kind, COUNT(*) AS n FROM assets GROUP BY kind")
    total = 0
    for folder in KIND_DIRS.values():
        path = db.ASSET_DIR / folder
        if path.exists():
            total += sum(f.stat().st_size for f in path.glob("*") if f.is_file())
    return {"by_kind": {r["kind"]: r["n"] for r in rows}, "bytes": total}


# --------------------------------------------------------------------------
# gallery inventory
# --------------------------------------------------------------------------

def _referenced(project_id: str) -> dict[str, list[dict[str, Any]]]:
    """Every place a hash is used, so the gallery can show it and refuse to
    delete anything still referenced.

    A reference that the generator can re-shoot in place also carries a `regen`
    descriptor -- the job to run and the payload to run it with. It is written
    here, next to the field the hash was read out of, because that field *is*
    what says how the asset was made: a `regen` worked out later from the role
    string would be a second, drifting copy of this table.
    """
    from .. import store as store_mod
    from . import images as images_mod   # local: images imports this module

    uses: dict[str, list[dict[str, Any]]] = {}

    def note(asset_hash: Optional[str], *,
             regen: Optional[dict[str, Any]] = None, **where: Any) -> None:
        if not asset_hash:
            return
        if regen:
            # Stable identity for one reference, so the gallery can ask for
            # exactly this one to be redone rather than every use of the file.
            where["regen"] = {
                **regen, "ref": f"{where['kind']}:{where['id']}:{where['role']}"}
        uses.setdefault(asset_hash, []).append(where)

    for node in store_mod.list_nodes(project_id):
        d = node.data or {}
        note(d.get("image_hash"), kind="node", id=node.id, role="image",
             label=(d.get("text") or node.type.value)[:60],
             regen={"action": "cg", "payload": {"node_id": node.id}})
        note(d.get("voice_hash"), kind="node", id=node.id, role="voice",
             label=(d.get("text") or node.type.value)[:60],
             regen={"action": "voice", "payload": {"node_id": node.id, "seed": 0}})
        note(d.get("sfx_hash"), kind="node", id=node.id, role="sfx",
             label=(d.get("sfx_cue") or d.get("text") or node.type.value)[:60])
        for variant in node.variants or []:
            data = variant if isinstance(variant, dict) else variant.model_dump()
            note(data.get("asset_hash"), kind="node", id=node.id, role="take",
                 label=f"take on {node.type.value}")

    for character in store_mod.list_characters(project_id):
        note(character.assets.profile_image, kind="character", id=character.id,
             role="profile image", label=character.display_name,
             regen={"action": "portrait", "payload": {"character_id": character.id}})
        for slot, asset_hash in (character.assets.sprite_sheet or {}).items():
            outfit_id, pose, expression = images_mod.parse_sprite_slot(slot)
            note(asset_hash, kind="character", id=character.id, role=f"sprite {slot}",
                 label=character.display_name,
                 regen={"action": "sprite", "payload": {
                     "character_id": character.id, "slot": slot,
                     "outfit_id": outfit_id, "pose": pose, "expression": expression}})
        for item in character.assets.sheet_pool or []:
            note(item.hash, kind="character", id=character.id,
                 role=f"sprite pool ({item.outfit_id or 'default'}/{item.angle.split(',')[0]}"
                      f"/{item.expression})",
                 label=character.display_name)
        for item in character.assets.cg_pool or []:
            note(item.hash, kind="character", id=character.id,
                 role=f"CG pool ({item.outfit_id or 'default'}/{item.angle.split(',')[0]}"
                      f"/{item.expression})",
                 label=character.display_name)
        clip = character.voice.reference_clip or ""
        if "/" in clip:
            note(clip.split("/")[-1].split(".")[0], kind="character", id=character.id,
                 role="voice reference", label=character.display_name)
        for take in character.voice.audition or []:
            for digest in (take.hash, take.preview_hash):
                note(digest, kind="character", id=character.id,
                     role=f"audition ({take.emotion})", label=character.display_name)

    for location in store_mod.list_locations(project_id):
        for variant, asset_hash in (location.variants or {}).items():
            note(asset_hash, kind="location", id=location.id, role=f"bg {variant}",
                 label=location.name,
                 regen={"action": "background", "payload": {
                     "location_id": location.id, "variant": variant}})

    for row in db.query("SELECT key, value FROM settings WHERE key LIKE 'sheet:%'"):
        sheet = db.loads(row["value"], {}) or {}
        for item in sheet.get("items", []):
            note(item.get("hash"), kind="sheet", id=row["key"].split(":", 1)[1],
                 role="character sheet", label=sheet.get("character_id", ""))

    return uses


#: Which job re-shoots each kind of reference, and what it is called in the
#: dock. The handlers all write the new hash back onto the same field the old
#: one came from, which is what makes "regenerate" from the gallery replace the
#: content on the node rather than pile up a second asset beside it.
REGEN_JOBS = {
    "cg": ("image.generate", "regenerate CG"),
    "voice": ("tts.line", "re-voice line"),
    "sprite": ("image.sprite", "regenerate sprite"),
    "background": ("image.background", "regenerate background"),
    "portrait": ("image.profile", "regenerate portrait"),
}


def regen_targets(project_id: str, asset_hash: str,
                  ref: Optional[str] = None) -> list[dict[str, Any]]:
    """The references to this asset that can be re-shot in place.

    `ref` narrows it to one of them (the `regen.ref` key the gallery shows
    against each row); without it, every regenerable use of the file is a
    target -- an identical image content-addressed onto two nodes is two
    renders, because each node gets its own new take.
    """
    out = []
    for use in _referenced(project_id).get(asset_hash, []):
        regen = use.get("regen")
        if not regen or regen.get("action") not in REGEN_JOBS:
            continue
        if ref and regen.get("ref") != ref:
            continue
        out.append(use)
    return out


def _character_index(project_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """`(char_id -> display name, node_id -> speaker char_id)` for the project.

    The node map is what lets a say node's voice line (whose own meta only
    carries `source`, the raw clip) still be filed under the character speaking
    it."""
    from .. import store as store_mod

    names = {c.id: c.display_name for c in store_mod.list_characters(project_id)}
    node_speaker: dict[str, str] = {}
    for node in store_mod.list_nodes(project_id):
        speaker = (node.data or {}).get("speaker")
        if speaker in names:
            node_speaker[node.id] = speaker
    return names, node_speaker


def _character_for(asset_hash: str, meta: dict[str, Any], refs: list[dict[str, Any]],
                   names: dict[str, str], node_speaker: dict[str, str],
                   meta_by_hash: dict[str, dict[str, Any]],
                   uses: dict[str, list[dict[str, Any]]],
                   seen: Optional[set[str]] = None) -> Optional[str]:
    """Best guess at which character an asset belongs to, or None.

    Order: an explicit `meta.character` tag, then a character that references it
    directly (sprite/profile/audition), then the speaker of a node that uses it,
    then whatever the clip this one was encoded from resolves to."""
    cid = meta.get("character")
    if cid in names:
        return cid
    for ref in refs:
        if ref.get("kind") == "character" and ref.get("id") in names:
            return ref["id"]
    for ref in refs:
        if ref.get("kind") == "node" and node_speaker.get(ref.get("id", "")):
            return node_speaker[ref["id"]]
    seen = seen or {asset_hash}
    source = meta.get("source")
    if source and source not in seen:
        seen.add(source)
        return _character_for(source, meta_by_hash.get(source, {}), uses.get(source, []),
                              names, node_speaker, meta_by_hash, uses, seen)
    return None


#: Semantic asset types the gallery filters on. The `kind` column only records
#: which folder the bytes live in (img / audio / voice_ref), which is not the
#: question anyone asks of a gallery -- "sprite or CG?", "voice line or SFX?" --
#: so the type is derived from what generated the asset (its meta) and from what
#: still references it.
ASSET_TYPES = ("sprite", "cg", "background", "portrait", "voice", "voice_ref", "sfx", "other")

#: Types that can belong to a character. The gallery only offers the character
#: filter while the type selection overlaps this set: a background or an SFX has
#: no character to be filtered by, so the control would only ever empty the grid.
CHARACTER_TYPES = ("sprite", "cg", "portrait", "voice", "voice_ref")

#: Filter tokens for "has no character" / "is in no scene". Spelled with the
#: dunder guard so they can never collide with a real character or node id.
NO_CHARACTER = "__none__"
NO_SCENE = "__none__"

#: `role` prefixes written by `_referenced`, specific before general so
#: "sprite pool" is tested ahead of "sprite".
_ROLE_TYPES: tuple[tuple[str, str], ...] = (
    ("profile image", "portrait"),
    ("voice reference", "voice_ref"),
    ("audition", "voice_ref"),
    ("character sheet", "sprite"),
    ("sprite pool", "sprite"),
    ("CG pool", "cg"),
    ("sprite", "sprite"),
    ("bg ", "background"),
    ("image", "cg"),
    ("voice", "voice"),
    ("sfx", "sfx"),
)


def _type_for(kind: str, meta: dict[str, Any], refs: list[dict[str, Any]],
              meta_by_hash: dict[str, dict[str, Any]],
              uses: dict[str, list[dict[str, Any]]],
              seen: Optional[set[str]] = None) -> str:
    """Best guess at what an asset *is*, strongest signal first.

    A generator tag in `meta` wins, because it still holds once the asset falls
    out of use -- which is exactly when someone goes looking for it in the
    gallery. Only then do current references decide, and last of all the clip
    this one was encoded from (a speed-corrected ogg carries nothing but
    `source`)."""
    if meta.get("location"):
        return "background"
    if meta.get("profile"):
        return "portrait"
    if meta.get("sheet"):
        return "sprite"
    if meta.get("cg_set"):
        return "cg"
    if kind == "voice_ref":
        return "voice_ref"
    if kind == "audio" and ("emotion" in meta or "resolved" in meta):
        return "voice"

    for ref in refs:
        role = ref.get("role", "")
        for prefix, asset_type in _ROLE_TYPES:
            if role.startswith(prefix):
                return asset_type
        # A take is whatever its medium is: an image take is another CG, an
        # audio take another reading of the line.
        if role == "take":
            return "cg" if kind == "img" else "voice"

    if kind == "img":
        if meta.get("pose") or meta.get("outfit"):
            return "sprite"
        if meta.get("node"):
            return "cg"

    source = meta.get("source")
    seen = seen or set()
    if source and source not in seen:
        seen.add(source)
        resolved = _type_for(kind, meta_by_hash.get(source, {}), uses.get(source, []),
                             meta_by_hash, uses, seen)
        if resolved != "other":
            return resolved

    return "sfx" if kind == "audio" else "other"


def _scene_index(project_id: str) -> tuple[dict[str, str], dict[str, str],
                                           dict[str, set[str]]]:
    """`(scene id -> title, node id -> scene id, location id -> scene ids)`.

    The location map is what lets a background -- which no node references, only
    a Location does -- still answer "which scene is this for?": every scene that
    stages that location claims it."""
    from .. import store as store_mod
    from ..graph import Graph

    nodes = store_mod.list_nodes(project_id, include_orphaned=False)
    graph = Graph.build(nodes, store_mod.list_edges(project_id))
    titles = {s.id: ((s.data or {}).get("title") or "Untitled scene")
              for s in graph.scenes()}

    node_scene: dict[str, str] = {}
    location_scenes: dict[str, set[str]] = {}
    for node in nodes:
        scene = graph.owning_scene(node.id)
        if scene is None:
            continue
        node_scene[node.id] = scene.id
        data = node.data or {}
        for location_id in (data.get("background"), data.get("location")):
            if location_id:
                location_scenes.setdefault(location_id, set()).add(scene.id)
    return titles, node_scene, location_scenes


def _scenes_for(meta: dict[str, Any], refs: list[dict[str, Any]],
                node_scene: dict[str, str], location_scenes: dict[str, set[str]],
                meta_by_hash: dict[str, dict[str, Any]],
                uses: dict[str, list[dict[str, Any]]],
                seen: Optional[set[str]] = None) -> list[str]:
    """Every scene an asset belongs to -- usually one, but a background shared
    by three scenes belongs to all three."""
    found: set[str] = set()
    for ref in refs:
        if ref.get("kind") == "node":
            scene_id = node_scene.get(ref.get("id", ""))
            if scene_id:
                found.add(scene_id)
        elif ref.get("kind") == "location":
            found |= location_scenes.get(ref.get("id", ""), set())

    # The node or location a generation was *for*, which outlives the reference:
    # a re-rolled CG is off the node but is still that scene's asset.
    node_id = meta.get("node")
    if node_id in node_scene:
        found.add(node_scene[node_id])
    found |= location_scenes.get(meta.get("location") or "", set())

    source = meta.get("source")
    seen = seen or set()
    if not found and source and source not in seen:
        seen.add(source)
        found.update(_scenes_for(meta_by_hash.get(source, {}), uses.get(source, []),
                                 node_scene, location_scenes, meta_by_hash, uses, seen))
    return sorted(found)


def _facet(rows: list[dict[str, Any]], keep, axis: str, values) -> dict[str, int]:
    """How many assets each value of one filter would match.

    Counted with every *other* filter applied but not this one, so the number
    beside an option says what picking it would give you rather than what the
    current selection already gives."""
    counts: dict[str, int] = {}
    for row in rows:
        if not keep(row, axis):
            continue
        for value in values(row):
            counts[value] = counts.get(value, 0) + 1
    return counts


def inventory(project_id: str, kind: Optional[str] = None, only_unused: bool = False, *,
              types: Optional[list[str]] = None,
              characters: Optional[list[str]] = None,
              scenes: Optional[list[str]] = None,
              used: str = "all",
              offset: int = 0, limit: int = 0) -> dict[str, Any]:
    """One page of the gallery, plus the counts its filter bar needs.

    `types`, `characters` and `scenes` are OR within themselves and AND across
    each other -- "sprites or CGs, of Mika, in scene 3". `used` is one of
    all/used/unused. `limit` 0 means no paging at all, which is what a non-UI
    caller wants; the gallery always asks for a page, because a project with a
    few thousand assets is otherwise a few thousand <img> tags in one response.

    `total`/`bytes`/`unused` describe the whole filtered set, not the page.
    `library_total`/`library_unused` ignore every filter -- that is what the
    purge button acts on, so it must not move when a filter changes."""
    uses = _referenced(project_id)
    names, node_speaker = _character_index(project_id)
    scene_titles, node_scene, location_scenes = _scene_index(project_id)
    meta_by_hash = {r["hash"]: db.loads(r["meta"], {})
                    for r in db.query("SELECT hash, meta FROM assets")}

    rows: list[dict[str, Any]] = []
    sql = "SELECT * FROM assets"
    params: tuple = ()
    if kind:
        sql += " WHERE kind = ?"
        params = (kind,)
    sql += " ORDER BY created_at DESC"

    for row in db.query(sql, params):
        path = _dir_for(row["kind"]) / f"{row['hash']}.{row['ext']}"
        references = uses.get(row["hash"], [])
        if only_unused and references:
            continue
        meta = db.loads(row["meta"], {})
        char_id = _character_for(row["hash"], meta, references, names, node_speaker,
                                 meta_by_hash, uses)
        scene_ids = _scenes_for(meta, references, node_scene, location_scenes,
                                meta_by_hash, uses)
        rows.append({
            "hash": row["hash"], "kind": row["kind"], "ext": row["ext"],
            "type": _type_for(row["kind"], meta, references, meta_by_hash, uses),
            "meta": meta, "created_at": row["created_at"],
            "bytes": path.stat().st_size if path.exists() else 0,
            "exists": path.exists(),
            "used_by": references, "use_count": len(references),
            "character_id": char_id,
            "character_name": names.get(char_id) if char_id else None,
            "scene_ids": scene_ids,
            "scene_names": [scene_titles.get(s, "") for s in scene_ids],
        })

    # Roster order, newest first within a character. The gallery groups by
    # character, and a group only survives paging if that character's assets are
    # contiguous in the sequence the pages are cut from. Python's sort is
    # stable, so the created_at DESC the query already applied is preserved
    # inside each group.
    roster = {cid: i for i, cid in enumerate(names)}
    rows.sort(key=lambda r: (roster.get(r["character_id"] or "", len(roster)),
                             r["character_id"] or ""))

    type_set = {t for t in (types or []) if t}
    char_set = {c for c in (characters or []) if c}
    scene_set = {s for s in (scenes or []) if s}

    def keep(row: dict[str, Any], axis: str = "") -> bool:
        if axis != "type" and type_set and row["type"] not in type_set:
            return False
        if axis != "character" and char_set:
            if (row["character_id"] or NO_CHARACTER) not in char_set:
                return False
        if axis != "scene" and scene_set:
            if not scene_set.intersection(row["scene_ids"] or [NO_SCENE]):
                return False
        if axis != "used":
            if used == "used" and not row["use_count"]:
                return False
            if used == "unused" and row["use_count"]:
                return False
        return True

    matched = [r for r in rows if keep(r)]
    page = matched[offset:offset + limit] if limit else matched[offset:]

    return {
        "assets": page,
        "total": len(matched),
        "bytes": sum(r["bytes"] for r in matched),
        "unused": sum(1 for r in matched if not r["use_count"]),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(matched),
        "library_total": len(rows),
        "library_unused": sum(1 for r in rows if not r["use_count"]),
        "facets": {
            "types": _facet(rows, keep, "type", lambda r: [r["type"]]),
            "characters": _facet(rows, keep, "character",
                                 lambda r: [r["character_id"] or NO_CHARACTER]),
            "scenes": _facet(rows, keep, "scene",
                             lambda r: r["scene_ids"] or [NO_SCENE]),
            "used": {
                "used": sum(1 for r in rows if keep(r, "used") and r["use_count"]),
                "unused": sum(1 for r in rows if keep(r, "used") and not r["use_count"]),
            },
        },
        "scenes": [{"id": sid, "title": title} for sid, title in scene_titles.items()],
    }


def clear_references(asset_hash: str) -> list[dict[str, Any]]:
    """Unset every field pointing at `asset_hash`, in every project.

    The counterpart to `_referenced`, and deliberately written against the same
    list of places: a reference this misses becomes a node that still claims an
    image, a build that copies a file that is not there, and a missing-assets
    scan that walks straight past the gap. Deleting the file without this left
    the graph describing a library it no longer had.

    Cleared, not merely blanked: a node loses `image_hash` *and* drops back to
    `AssetStatus.none`, so it reads as unset rather than as ready-but-broken,
    and `services.batch.scan` picks it up again as missing. The generation
    stamps go with it -- an `_image_text_hash` with no image behind it would
    make the next render look current the moment it lands.

    Not project-scoped, because the file is not: one delete takes the bytes out
    from under every project that referenced them, so every project has to be
    told.
    """
    from .. import store as store_mod

    cleared: list[dict[str, Any]] = []

    def note(kind: str, project_id: str, item_id: str, role: str) -> None:
        cleared.append({"kind": kind, "project_id": project_id, "id": item_id, "role": role})

    for project in store_mod.list_projects():
        pid = project.id
        refs = staleness.voice_refs(pid)

        for node in store_mod.list_nodes(pid):
            data = dict(node.data or {})
            touched = False

            # Each entry is (hash field, the companion fields that only mean
            # anything while that hash resolves -- the compiled path, and the
            # staleness stamps taken when the asset was made).
            for field, companions, role in (
                ("image_hash", ("_image_text_hash",), "image"),
                ("voice_hash", ("voice_path", "_audio_text_hash", "_audio_voice_id"), "voice"),
                ("sfx_hash", ("sfx_file",), "sfx"),
            ):
                if data.get(field) != asset_hash:
                    continue
                data.pop(field, None)
                for companion in companions:
                    data.pop(companion, None)
                touched = True
                note("node", pid, node.id, role)

            # A CG node keeps its `cg` flag: the beat is still meant to have a
            # picture, which is exactly why the scan should now report it.

            variants = list(node.variants or [])
            kept = [v for v in variants
                    if (v if isinstance(v, dict) else v.model_dump()
                        ).get("asset_hash") != asset_hash]
            if len(kept) != len(variants):
                node.variants = kept
                touched = True
                note("node", pid, node.id, "take")

            if touched:
                node.data = data
                staleness.evaluate(node, refs)
                store_mod.update_node(pid, node)

        for character in store_mod.list_characters(pid):
            touched = False
            if character.assets.profile_image == asset_hash:
                character.assets.profile_image = None
                touched = True
                note("character", pid, character.id, "profile image")

            for slot in [s for s, h in (character.assets.sprite_sheet or {}).items()
                         if h == asset_hash]:
                character.assets.sprite_sheet.pop(slot, None)
                touched = True
                note("character", pid, character.id, f"sprite {slot}")

            for pool in ("sheet_pool", "cg_pool"):
                items = getattr(character.assets, pool) or []
                kept_items = [i for i in items if i.hash != asset_hash]
                if len(kept_items) != len(items):
                    setattr(character.assets, pool, kept_items)
                    touched = True
                    note("character", pid, character.id, pool.replace("_", " "))

            clip = character.voice.reference_clip or ""
            if "/" in clip and clip.split("/")[-1].split(".")[0] == asset_hash:
                # Losing the clip un-casts the voice, and that is the honest
                # state: IndexTTS-2.5 has nothing left to clone. `batch.scan`
                # already reports an uncast character as blocked rather than
                # queueing lines that would fail one by one.
                character.voice.reference_clip = None
                touched = True
                note("character", pid, character.id, "voice reference")

            auditions = character.voice.audition or []
            kept_takes = [t for t in auditions
                          if asset_hash not in (t.hash, t.preview_hash)]
            if len(kept_takes) != len(auditions):
                character.voice.audition = kept_takes
                touched = True
                note("character", pid, character.id, "audition")

            if touched:
                store_mod.save_character(pid, character)

        for location in store_mod.list_locations(pid):
            gone = [v for v, h in (location.variants or {}).items() if h == asset_hash]
            for variant in gone:
                location.variants.pop(variant, None)
                note("location", pid, location.id, f"bg {variant}")
            if gone:
                store_mod.save_location(pid, location)

    # Character sheets live in settings rather than per project, so they are
    # swept once, outside the project loop.
    for row in db.query("SELECT key, value FROM settings WHERE key LIKE 'sheet:%'"):
        sheet = db.loads(row["value"], {}) or {}
        items = sheet.get("items", [])
        kept_sheet = [i for i in items if i.get("hash") != asset_hash]
        if len(kept_sheet) != len(items):
            sheet["items"] = kept_sheet
            store.set_setting(row["key"], sheet)
            cleared.append({"kind": "sheet", "project_id": "",
                            "id": row["key"].split(":", 1)[1],
                            "role": "character sheet"})

    return cleared


def delete(asset_hash: str, project_id: Optional[str] = None, force: bool = False) -> dict[str, Any]:
    """Delete an asset, and unset everything that pointed at it.

    Refuses by default while anything still references it; `force` deletes
    anyway and then clears those references, which is what makes forcing safe
    to offer at all. The node goes back to unset -- no hash, status `none` --
    rather than keeping a reference whose file is gone, so the missing-assets
    scan finds it again and a build never copies a file that is not there.
    """
    if project_id and not force:
        references = _referenced(project_id).get(asset_hash, [])
        if references:
            return {"deleted": False, "reason": "still referenced", "used_by": references}
    row = db.one("SELECT * FROM assets WHERE hash = ?", (asset_hash,))
    if not row:
        return {"deleted": False, "reason": "not found"}
    path = _dir_for(row["kind"]) / f"{asset_hash}.{row['ext']}"
    if path.exists():
        path.unlink()
    db.execute("DELETE FROM assets WHERE hash = ?", (asset_hash,))
    # After the index row is gone, so nothing can re-note the hash we are in
    # the middle of unsetting.
    cleared = clear_references(asset_hash)
    return {"deleted": True, "hash": asset_hash, "cleared": cleared,
            "cleared_count": len(cleared)}


def _referenced_anywhere() -> set[str]:
    """Every hash referenced by any project.

    The bulk 'delete unused' is not scoped to one project the way single delete
    is -- an asset the open project has stopped using may still be on a node in
    another project, and clearing it out from under that project would be a
    silent data loss."""
    from .. import store as store_mod

    referenced: set[str] = set()
    for project in store_mod.list_projects():
        referenced.update(_referenced(project.id))
    return referenced


def delete_unused() -> dict[str, Any]:
    """Delete every asset that nothing in any project references -- the file on
    disk and the index row both."""
    referenced = _referenced_anywhere()
    deleted: list[str] = []
    freed = 0
    errors: list[dict[str, str]] = []
    for row in db.query("SELECT * FROM assets"):
        if row["hash"] in referenced:
            continue
        path = _dir_for(row["kind"]) / f"{row['hash']}.{row['ext']}"
        size = path.stat().st_size if path.exists() else 0
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            # A locked file (open in a viewer, say) should not abort the sweep.
            errors.append({"hash": row["hash"], "error": str(exc)})
            continue
        db.execute("DELETE FROM assets WHERE hash = ?", (row["hash"],))
        deleted.append(row["hash"])
        freed += size
    return {"deleted": deleted, "count": len(deleted), "bytes": freed, "errors": errors}
