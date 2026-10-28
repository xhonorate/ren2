"""Repository layer: rows <-> IR models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import db
from .defaults import DEFAULT_CHARACTER_STATS, DEFAULT_TEMPLATES
from .graph import text_hash
from .models import (
    Character,
    Edge,
    Location,
    Node,
    NodeType,
    ProjectMeta,
    PromptTemplate,
    RPConversation,
    RPMessage,
    RPTake,
    RPVoiceClip,
    SheetPoolItem,
    StatDef,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}{raw}" if prefix else raw


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------

def list_projects() -> list[ProjectMeta]:
    rows = db.query("SELECT * FROM projects ORDER BY updated_at DESC")
    return [_project_from_row(r) for r in rows]


def _project_from_row(row) -> ProjectMeta:
    data = db.loads(row["data"], {})
    data.update({"id": row["id"], "name": row["name"],
                 "created_at": row["created_at"], "updated_at": row["updated_at"]})
    return ProjectMeta.model_validate(data)


def get_project(project_id: str) -> Optional[ProjectMeta]:
    row = db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    return _project_from_row(row) if row else None


def create_project(name: str) -> ProjectMeta:
    pid = new_id("p_")
    # Seeded rather than left empty: the stat is what keeps a character card
    # current, and a project with none reports the day-one relationship for the
    # whole story. Declaring it once covers every character, now and later --
    # `graph.stat_defaults` instantiates it per character on read.
    meta = ProjectMeta(
        id=pid, name=name, created_at=now(), updated_at=now(),
        character_stats=[StatDef(**s) for s in DEFAULT_CHARACTER_STATS],
    )
    db.execute(
        "INSERT INTO projects (id, name, data, created_at, updated_at) VALUES (?,?,?,?,?)",
        (pid, name, db.dumps(meta.model_dump(exclude={"id", "name", "created_at", "updated_at"})),
         meta.created_at, meta.updated_at),
    )
    seed_templates(pid)
    return meta


def save_project(meta: ProjectMeta) -> ProjectMeta:
    meta.updated_at = now()
    db.execute(
        "UPDATE projects SET name = ?, data = ?, updated_at = ? WHERE id = ?",
        (meta.name, db.dumps(meta.model_dump(exclude={"id", "name", "created_at", "updated_at"})),
         meta.updated_at, meta.id),
    )
    return meta


def delete_project(project_id: str) -> None:
    # Messages hang off conversations rather than off the project, so they have
    # to go first and by conversation id -- a project_id delete would leave them
    # orphaned forever.
    db.execute(
        "DELETE FROM rp_messages WHERE conv_id IN"
        " (SELECT id FROM rp_conversations WHERE project_id = ?)",
        (project_id,),
    )
    for table in ("nodes", "edges", "characters", "locations", "templates", "jobs",
                  "summaries", "rp_conversations"):
        db.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def touch(project_id: str) -> None:
    db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), project_id))


# --------------------------------------------------------------------------
# nodes + edges
# --------------------------------------------------------------------------

def _node_from_row(row) -> Node:
    return Node(
        id=row["id"],
        type=NodeType(row["type"]),
        data=db.loads(row["data"], {}),
        x=row["x"],
        y=row["y"],
        parent_id=row["parent_id"],
        pinned_label=bool(row["pinned_label"]),
        slug=row["slug"],
        notes=row["notes"],
        text_hash=row["text_hash"],
        text_status=row["text_status"],
        image_status=row["image_status"],
        audio_status=row["audio_status"],
        variants=db.loads(row["variants"], []),
        active_variant=row["active_variant"],
        orphaned=bool(row["orphaned"]),
    )


def list_nodes(project_id: str, include_orphaned: bool = True) -> list[Node]:
    sql = "SELECT * FROM nodes WHERE project_id = ?"
    if not include_orphaned:
        sql += " AND orphaned = 0"
    return [_node_from_row(r) for r in db.query(sql + " ORDER BY created_at", (project_id,))]


def get_node(node_id: str) -> Optional[Node]:
    row = db.one("SELECT * FROM nodes WHERE id = ?", (node_id,))
    return _node_from_row(row) if row else None


def insert_node(project_id: str, node: Node) -> Node:
    node.text_hash = text_hash(node)
    db.execute(
        """INSERT INTO nodes (id, project_id, type, data, x, y, parent_id, pinned_label,
                              slug, notes, text_hash, text_status, image_status, audio_status,
                              variants, active_variant, orphaned, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (node.id, project_id, node.type.value, db.dumps(node.data), node.x, node.y,
         node.parent_id, int(node.pinned_label), node.slug, node.notes, node.text_hash,
         node.text_status.value if hasattr(node.text_status, "value") else node.text_status,
         node.image_status.value if hasattr(node.image_status, "value") else node.image_status,
         node.audio_status.value if hasattr(node.audio_status, "value") else node.audio_status,
         db.dumps([v.model_dump() if hasattr(v, "model_dump") else v for v in node.variants]),
         node.active_variant, int(node.orphaned), now()),
    )
    touch(project_id)
    return node


def update_node(project_id: str, node: Node) -> Node:
    node.text_hash = text_hash(node)
    db.execute(
        """UPDATE nodes SET type=?, data=?, x=?, y=?, parent_id=?, pinned_label=?, slug=?,
                             notes=?, text_hash=?, text_status=?, image_status=?, audio_status=?,
                             variants=?, active_variant=?, orphaned=?
           WHERE id = ? AND project_id = ?""",
        (node.type.value, db.dumps(node.data), node.x, node.y, node.parent_id,
         int(node.pinned_label), node.slug, node.notes, node.text_hash,
         node.text_status.value if hasattr(node.text_status, "value") else node.text_status,
         node.image_status.value if hasattr(node.image_status, "value") else node.image_status,
         node.audio_status.value if hasattr(node.audio_status, "value") else node.audio_status,
         db.dumps([v.model_dump() if hasattr(v, "model_dump") else v for v in node.variants]),
         node.active_variant, int(node.orphaned), node.id, project_id),
    )
    touch(project_id)
    return node


def move_nodes(project_id: str, positions: list[dict[str, Any]]) -> None:
    db.executemany(
        "UPDATE nodes SET x = ?, y = ? WHERE id = ? AND project_id = ?",
        [(p["x"], p["y"], p["id"], project_id) for p in positions],
    )


def delete_node(project_id: str, node_id: str) -> None:
    db.execute("DELETE FROM nodes WHERE id = ? AND project_id = ?", (node_id, project_id))
    db.execute(
        "DELETE FROM edges WHERE project_id = ? AND (source = ? OR target = ?)",
        (project_id, node_id, node_id),
    )
    touch(project_id)


def set_orphaned(project_id: str, node_ids: list[str], value: bool) -> None:
    db.executemany(
        "UPDATE nodes SET orphaned = ? WHERE id = ? AND project_id = ?",
        [(int(value), nid, project_id) for nid in node_ids],
    )
    touch(project_id)


def list_edges(project_id: str) -> list[Edge]:
    rows = db.query("SELECT * FROM edges WHERE project_id = ?", (project_id,))
    return [
        Edge(id=r["id"], source=r["source"], target=r["target"],
             source_handle=r["source_handle"], label=r["label"])
        for r in rows
    ]


def insert_edge(project_id: str, edge: Edge) -> Edge:
    # One outgoing edge per handle: reconnecting a port replaces it.
    db.execute(
        "DELETE FROM edges WHERE project_id = ? AND source = ? AND source_handle = ?",
        (project_id, edge.source, edge.source_handle),
    )
    db.execute(
        "INSERT OR REPLACE INTO edges (id, project_id, source, target, source_handle, label)"
        " VALUES (?,?,?,?,?,?)",
        (edge.id, project_id, edge.source, edge.target, edge.source_handle, edge.label),
    )
    touch(project_id)
    return edge


def delete_edge(project_id: str, edge_id: str) -> None:
    db.execute("DELETE FROM edges WHERE id = ? AND project_id = ?", (edge_id, project_id))
    touch(project_id)


# --------------------------------------------------------------------------
# characters + locations
# --------------------------------------------------------------------------

def list_characters(project_id: str) -> list[Character]:
    rows = db.query("SELECT * FROM characters WHERE project_id = ?", (project_id,))
    return [Character.model_validate(db.loads(r["data"], {})) for r in rows]


def get_character(project_id: str, char_id: str) -> Optional[Character]:
    row = db.one("SELECT * FROM characters WHERE project_id = ? AND id = ?", (project_id, char_id))
    return Character.model_validate(db.loads(row["data"], {})) if row else None


def save_character(project_id: str, character: Character) -> Character:
    db.execute(
        "INSERT OR REPLACE INTO characters (id, project_id, data) VALUES (?,?,?)",
        (character.id, project_id, db.dumps(character.model_dump())),
    )
    touch(project_id)
    return character


def migrate_missing_default_outfit() -> int:
    """Give every character with outfits a real default_outfit.

    An empty default is not neutral: sprite generation falls back to
    `outfits[0]`, so a character whose first outfit happens to be the nude one
    silently generates their whole sprite set nude, and the compiler's show
    statements name an outfit the sprite sheet has no key for. Prefers the
    first outfit that is not the nude/none entry.
    """
    fixed = 0
    for project in list_projects():
        for character in list_characters(project.id):
            if character.default_outfit or not character.outfits:
                continue
            ids = [o.id for o in character.outfits]
            character.default_outfit = next((i for i in ids if i != "none"), ids[0])
            save_character(project.id, character)
            fixed += 1
    return fixed


def migrate_scene_tone_to_nsfw() -> int:
    """Carry a scene's old `tone` over to the `nsfw` flag that replaced it.

    Tone was an enum with `explicit` as one of its values, which made the adult
    register and what the scene actually *is* into one axis -- picking explicit
    threw away "tense" or "tender". Splitting them leaves the old string
    stranded on every existing scene, and a scene that was marked explicit and
    silently stops being is the one direction this must not fail in.

    Every other tone value is dropped: nothing reads it any more, and the
    prompt line that did is gone from the template.
    """
    moved = 0
    for project in list_projects():
        for node in list_nodes(project.id):
            if node.type != NodeType.scene or "tone" not in (node.data or {}):
                continue
            data = dict(node.data)
            tone = str(data.pop("tone", "") or "").strip().lower()
            data["nsfw"] = bool(data.get("nsfw")) or tone == "explicit"
            node.data = data
            update_node(project.id, node)
            moved += 1
    return moved


def migrate_superseded_settings() -> list[str]:
    """Upgrade settings still sitting on a default that turned out to be wrong.

    Only replaces a value that still matches a known previous default exactly
    (defaults.SUPERSEDED_SETTINGS). A value the user edited is never touched --
    this exists to fix defaults nobody chose, not to overwrite choices. That
    distinction is the whole reason this is a value-match rather than a blanket
    re-seed.
    """
    from .defaults import DEFAULT_SETTINGS, SUPERSEDED_SETTINGS

    changed: list[str] = []
    for key, superseded in SUPERSEDED_SETTINGS.items():
        current = get_setting(key, None)
        if current is None or key not in DEFAULT_SETTINGS:
            continue
        if any(current == old for old in superseded) and current != DEFAULT_SETTINGS[key]:
            set_setting(key, DEFAULT_SETTINGS[key])
            changed.append(key)
    return changed


def migrate_superseded_templates() -> list[str]:
    """Re-adopt the shipped default for prompts still sitting on a bad one.

    The sibling of `migrate_superseded_settings`, and deliberately the same
    shape: replaces a stored body ONLY when it hashes to a known previous
    default (defaults.SUPERSEDED_TEMPLATES). A prompt the author edited never
    matches, so it is never touched.

    `active_template` already adopts an improved default in place, but only for
    a seeded v1 that has no version 2. Any project that has been through one
    template revision is on v2+ and that path cannot fire, which is how a live
    project can stay on a prompt the default already fixed. This closes that
    gap without touching authored versions or the version history.
    """
    import hashlib

    from .defaults import DEFAULT_TEMPLATES, SUPERSEDED_TEMPLATES

    changed: list[str] = []
    for key, superseded in SUPERSEDED_TEMPLATES.items():
        spec = DEFAULT_TEMPLATES.get(key)
        if not spec:
            continue
        stale = set(superseded)
        for row in db.query(
            "SELECT id, project_id, body FROM templates WHERE key = ? AND active = 1", (key,)
        ):
            if hashlib.sha256(row["body"].encode()).hexdigest() not in stale:
                continue
            db.execute(
                "UPDATE templates SET body = ?, params = ? WHERE id = ?",
                (spec["body"], db.dumps(spec.get("params", {})), row["id"]),
            )
            changed.append(f"{row['project_id']}:{key}")
    return changed


def migrate_sprite_slots_to_poses() -> int:
    """Move `outfit/expression` sprite keys to `outfit/pose/expression`.

    Every sprite generated before poses existed was a front view, so the old
    key means exactly `outfit/front/expression`. Without this the art is still
    on disk but nothing looks it up, which is how the sheet-pool change lost a
    day's work. Idempotent: three-part keys are left alone.
    """
    migrated = 0
    for project in list_projects():
        for character in list_characters(project.id):
            sheet = character.assets.sprite_sheet or {}
            rekeyed: dict[str, str] = {}
            changed = False
            for slot, asset_hash in sheet.items():
                parts = slot.split("/")
                if len(parts) == 2:
                    rekeyed[f"{parts[0]}/front/{parts[1]}"] = asset_hash
                    changed = True
                else:
                    rekeyed[slot] = asset_hash
            if changed:
                character.assets.sprite_sheet = rekeyed
                save_character(project.id, character)
                migrated += 1
    return migrated


def migrate_orphaned_sheet_batches() -> int:
    """One-time recovery for character-sheet images generated before
    CharAssets.sheet_pool existed. They used to live in a `sheet:{batch_id}`
    settings row pointed to by `character.assets.sheet_batch_id`; once that
    field was removed from the model, pydantic silently dropped the pointer
    on load and the images -- still on disk, still in the settings row --
    became unreachable from the UI. This finds every such row, folds its
    images into the owning character's sheet_pool (deduped by hash, so it's
    safe to call more than once), and removes the row once merged.

    Runs at every startup (see main.py) -- cheap no-op once migrated.
    """
    migrated = 0
    for row in db.query("SELECT key, value FROM settings WHERE key LIKE 'sheet:%'"):
        batch = db.loads(row["value"], None)
        if not isinstance(batch, dict) or "items" not in batch:
            continue
        char_id = batch.get("character_id")
        seed = batch.get("seed", 0)
        owner_found = False
        for project in list_projects():
            character = get_character(project.id, char_id) if char_id else None
            if character is None:
                continue
            owner_found = True
            existing = {item.hash for item in character.assets.sheet_pool}
            added = False
            for entry in batch.get("items") or []:
                h = entry.get("hash")
                if not h or h in existing:
                    continue
                character.assets.sheet_pool.append(SheetPoolItem(
                    hash=h,
                    outfit_id=entry.get("outfit_id", ""),
                    angle=entry.get("angle", ""),
                    expression=entry.get("expression", ""),
                    seed=entry.get("seed", seed),
                ))
                existing.add(h)
                added = True
            if added:
                save_character(project.id, character)
                migrated += 1
        if owner_found:
            db.execute("DELETE FROM settings WHERE key = ?", (row["key"],))
    return migrated


def delete_character(project_id: str, char_id: str) -> None:
    db.execute("DELETE FROM characters WHERE project_id = ? AND id = ?", (project_id, char_id))
    touch(project_id)


def list_locations(project_id: str) -> list[Location]:
    rows = db.query("SELECT * FROM locations WHERE project_id = ?", (project_id,))
    return [Location.model_validate(db.loads(r["data"], {})) for r in rows]


def save_location(project_id: str, location: Location) -> Location:
    db.execute(
        "INSERT OR REPLACE INTO locations (id, project_id, data) VALUES (?,?,?)",
        (location.id, project_id, db.dumps(location.model_dump())),
    )
    touch(project_id)
    return location


def delete_location(project_id: str, loc_id: str) -> None:
    db.execute("DELETE FROM locations WHERE project_id = ? AND id = ?", (project_id, loc_id))
    touch(project_id)


# --------------------------------------------------------------------------
# prompt templates (versioned, with diff against the previous version)
# --------------------------------------------------------------------------

def seed_templates(project_id: str) -> None:
    for key, spec in DEFAULT_TEMPLATES.items():
        if db.one("SELECT id FROM templates WHERE project_id = ? AND key = ?", (project_id, key)):
            continue
        db.execute(
            "INSERT INTO templates (id, project_id, key, version, body, params, active, created_at)"
            " VALUES (?,?,?,?,?,?,1,?)",
            (new_id("t_"), project_id, key, 1, spec["body"], db.dumps(spec.get("params", {})), now()),
        )


def list_templates(project_id: str, key: Optional[str] = None) -> list[PromptTemplate]:
    if key:
        rows = db.query(
            "SELECT * FROM templates WHERE project_id = ? AND key = ? ORDER BY version DESC",
            (project_id, key),
        )
    else:
        rows = db.query(
            "SELECT * FROM templates WHERE project_id = ? ORDER BY key, version DESC",
            (project_id,),
        )
    return [
        PromptTemplate(id=r["id"], key=r["key"], version=r["version"], body=r["body"],
                       params=db.loads(r["params"], {}), active=bool(r["active"]),
                       created_at=r["created_at"])
        for r in rows
    ]


def active_template(project_id: str, key: str) -> Optional[PromptTemplate]:
    row = db.one(
        "SELECT * FROM templates WHERE project_id = ? AND key = ? AND active = 1"
        " ORDER BY version DESC LIMIT 1",
        (project_id, key),
    )
    if not row:
        spec = DEFAULT_TEMPLATES.get(key)
        if not spec:
            return None
        seed_templates(project_id)
        return PromptTemplate(id="default", key=key, version=1, body=spec["body"],
                              params=spec.get("params", {}))

    spec = DEFAULT_TEMPLATES.get(key)
    if spec and row["version"] == 1 and row["body"] != spec["body"]:
        count = db.one(
            "SELECT COUNT(*) AS n FROM templates WHERE project_id = ? AND key = ?",
            (project_id, key),
        )
        if count and count["n"] == 1:
            # The seeded copy was never edited (no version 2 exists) and the
            # shipped default has since improved -- adopt it in place so existing
            # projects get prompt fixes without a manual reset.
            db.execute(
                "UPDATE templates SET body = ?, params = ? WHERE id = ?",
                (spec["body"], db.dumps(spec.get("params", {})), row["id"]),
            )
            return PromptTemplate(id=row["id"], key=key, version=1, body=spec["body"],
                                  params=spec.get("params", {}), active=True,
                                  created_at=row["created_at"])

    return PromptTemplate(id=row["id"], key=row["key"], version=row["version"], body=row["body"],
                          params=db.loads(row["params"], {}), active=bool(row["active"]),
                          created_at=row["created_at"])


def save_template_version(project_id: str, key: str, body: str,
                          params: dict[str, Any]) -> PromptTemplate:
    """New version, never an overwrite -- so you can A/B without losing the
    one that worked (doc S2 'Prompt wrapper config page')."""
    row = db.one(
        "SELECT MAX(version) AS v FROM templates WHERE project_id = ? AND key = ?",
        (project_id, key),
    )
    version = (row["v"] or 0) + 1
    db.execute("UPDATE templates SET active = 0 WHERE project_id = ? AND key = ?", (project_id, key))
    tid = new_id("t_")
    db.execute(
        "INSERT INTO templates (id, project_id, key, version, body, params, active, created_at)"
        " VALUES (?,?,?,?,?,?,1,?)",
        (tid, project_id, key, version, body, db.dumps(params), now()),
    )
    return PromptTemplate(id=tid, key=key, version=version, body=body, params=params,
                          active=True, created_at=now())


def activate_template(project_id: str, template_id: str) -> None:
    row = db.one("SELECT key FROM templates WHERE id = ?", (template_id,))
    if not row:
        return
    db.execute("UPDATE templates SET active = 0 WHERE project_id = ? AND key = ?",
               (project_id, row["key"]))
    db.execute("UPDATE templates SET active = 1 WHERE id = ?", (template_id,))


# --------------------------------------------------------------------------
# rolling summary cache (doc S2)
# --------------------------------------------------------------------------

def get_summary(project_id: str, upstream_hash: str) -> Optional[str]:
    row = db.one(
        "SELECT text FROM summaries WHERE project_id = ? AND upstream_hash = ?",
        (project_id, upstream_hash),
    )
    return row["text"] if row else None


def summaries_for_nodes(project_id: str, node_ids: list[str]) -> dict[str, dict[str, str]]:
    """Cached summaries ending at any of these nodes: node id -> hash -> text."""
    out: dict[str, dict[str, str]] = {}
    if not node_ids:
        return out
    marks = ",".join("?" * len(node_ids))
    for row in db.query(
            f"SELECT node_id, upstream_hash, text FROM summaries"
            f" WHERE project_id = ? AND node_id IN ({marks})",
            (project_id, *node_ids)):
        out.setdefault(row["node_id"], {})[row["upstream_hash"]] = row["text"]
    return out


def put_summary(project_id: str, node_id: str, upstream_hash: str, text: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO summaries (id, project_id, node_id, upstream_hash, text, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (f"{project_id}:{upstream_hash}", project_id, node_id, upstream_hash, text, now()),
    )


# --------------------------------------------------------------------------
# settings (backend endpoints, model names, sampling defaults)
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# roleplay conversations + messages
# --------------------------------------------------------------------------

def _conv_from_row(row) -> RPConversation:
    return RPConversation(
        id=row["id"], project_id=row["project_id"], character_id=row["character_id"],
        cast=db.loads(row["cast"], []), title=row["title"],
        scene_context=row["scene_context"], summary=row["summary"],
        summary_edited=bool(row["summary_edited"]), state=row["state"],
        hidden=bool(row["hidden"]), sort_key=row["sort_key"],
        location_id=row["location_id"], time_of_day=row["time_of_day"],
        nsfw=bool(row["nsfw"]), created_at=row["created_at"], updated_at=row["updated_at"],
    )


def list_conversations(project_id: str, character_id: Optional[str] = None,
                       include_hidden: bool = True) -> list[RPConversation]:
    """Conversations in chronological order, oldest first.

    `character_id` matches the primary character OR anyone in `cast`, so a scene
    with two people in it appears in both of their histories. The cast match is
    done in Python rather than in SQL: `cast` is a JSON array, and a LIKE over it
    would match a character id that happens to be a substring of another's.
    """
    rows = db.query(
        "SELECT * FROM rp_conversations WHERE project_id = ? ORDER BY sort_key, created_at",
        (project_id,),
    )
    out = [_conv_from_row(r) for r in rows]
    if character_id:
        out = [c for c in out if character_id in c.everyone()]
    if not include_hidden:
        out = [c for c in out if not c.hidden]
    return out


def get_conversation(conv_id: str) -> Optional[RPConversation]:
    row = db.one("SELECT * FROM rp_conversations WHERE id = ?", (conv_id,))
    return _conv_from_row(row) if row else None


def insert_conversation(conv: RPConversation) -> RPConversation:
    conv.created_at = conv.created_at or now()
    conv.updated_at = now()
    if not conv.sort_key:
        # Seeded from the clock so the default order is chronological, and a
        # float so a later drag can land a row between two neighbours.
        #
        # Forced past whatever is already there, because the clock alone is not
        # enough: `created_at` is written to whole seconds, so two conversations
        # started in the same second got the same key -- and then neither one
        # was "earlier on the list" than the other, which silently emptied the
        # memory block for both. A manual reorder also rewrites these to 0, 1,
        # 2..., so a new row has to beat the list rather than the epoch.
        highest = db.one(
            "SELECT MAX(sort_key) AS k FROM rp_conversations WHERE project_id = ?",
            (conv.project_id,),
        )
        ceiling = (highest["k"] if highest and highest["k"] is not None else 0.0)
        conv.sort_key = max(_epoch(conv.created_at), float(ceiling) + 1.0)
    db.execute(
        """INSERT INTO rp_conversations (id, project_id, character_id, cast, title,
                scene_context, summary, summary_edited, state, hidden, sort_key,
                location_id, time_of_day, nsfw, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (conv.id, conv.project_id, conv.character_id, db.dumps(conv.cast), conv.title,
         conv.scene_context, conv.summary, int(conv.summary_edited), conv.state,
         int(conv.hidden), conv.sort_key, conv.location_id, conv.time_of_day,
         int(conv.nsfw), conv.created_at, conv.updated_at),
    )
    touch(conv.project_id)
    return conv


def save_conversation(conv: RPConversation) -> RPConversation:
    conv.updated_at = now()
    db.execute(
        """UPDATE rp_conversations SET character_id=?, cast=?, title=?, scene_context=?,
                summary=?, summary_edited=?, state=?, hidden=?, sort_key=?,
                location_id=?, time_of_day=?, nsfw=?, updated_at=?
           WHERE id = ?""",
        (conv.character_id, db.dumps(conv.cast), conv.title, conv.scene_context,
         conv.summary, int(conv.summary_edited), conv.state, int(conv.hidden),
         conv.sort_key, conv.location_id, conv.time_of_day, int(conv.nsfw),
         conv.updated_at, conv.id),
    )
    touch(conv.project_id)
    return conv


def delete_conversation(conv_id: str) -> None:
    db.execute("DELETE FROM rp_messages WHERE conv_id = ?", (conv_id,))
    db.execute("DELETE FROM rp_conversations WHERE id = ?", (conv_id,))


def _epoch(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _message_from_row(row) -> RPMessage:
    return RPMessage(
        id=row["id"], conv_id=row["conv_id"], seq=row["seq"], role=row["role"],
        text=row["text"],
        takes=[RPTake.model_validate(t) for t in db.loads(row["takes"], [])],
        active_take=row["active_take"],
        voice=[RPVoiceClip.model_validate(v) for v in db.loads(row["voice"], [])],
        created_at=row["created_at"],
    )


def list_messages(conv_id: str) -> list[RPMessage]:
    return [_message_from_row(r) for r in db.query(
        "SELECT * FROM rp_messages WHERE conv_id = ? ORDER BY seq", (conv_id,))]


def get_message(message_id: str) -> Optional[RPMessage]:
    row = db.one("SELECT * FROM rp_messages WHERE id = ?", (message_id,))
    return _message_from_row(row) if row else None


def next_seq(conv_id: str) -> int:
    row = db.one("SELECT MAX(seq) AS s FROM rp_messages WHERE conv_id = ?", (conv_id,))
    return int((row["s"] if row and row["s"] is not None else -1)) + 1


def insert_message(message: RPMessage) -> RPMessage:
    message.created_at = message.created_at or now()
    db.execute(
        """INSERT INTO rp_messages (id, conv_id, seq, role, text, takes, active_take,
                voice, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (message.id, message.conv_id, message.seq, message.role, message.text,
         db.dumps([t.model_dump() for t in message.takes]), message.active_take,
         db.dumps([v.model_dump() for v in message.voice]), message.created_at),
    )
    return message


def save_message(message: RPMessage) -> RPMessage:
    db.execute(
        """UPDATE rp_messages SET seq=?, role=?, text=?, takes=?, active_take=?, voice=?
           WHERE id = ?""",
        (message.seq, message.role, message.text,
         db.dumps([t.model_dump() for t in message.takes]), message.active_take,
         db.dumps([v.model_dump() for v in message.voice]), message.id),
    )
    return message


def delete_message(message_id: str) -> None:
    db.execute("DELETE FROM rp_messages WHERE id = ?", (message_id,))


def delete_messages_from(conv_id: str, seq: int) -> list[str]:
    """Drop this message and everything after it. Returns what went.

    The rewind primitive. Unlike the graph's version there is nothing to park:
    a conversation is a line with no branches, so a message after the rewind
    point cannot be reachable any other way.
    """
    rows = db.query("SELECT id FROM rp_messages WHERE conv_id = ? AND seq >= ?",
                    (conv_id, seq))
    db.execute("DELETE FROM rp_messages WHERE conv_id = ? AND seq >= ?", (conv_id, seq))
    return [r["id"] for r in rows]


def get_setting(key: str, default: Any = None) -> Any:
    row = db.one("SELECT value FROM settings WHERE key = ?", (key,))
    return db.loads(row["value"], default) if row else default


def set_setting(key: str, value: Any) -> None:
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, db.dumps(value)))


def all_settings() -> dict[str, Any]:
    return {r["key"]: db.loads(r["value"], None) for r in db.query("SELECT * FROM settings")}
