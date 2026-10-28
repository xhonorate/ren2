"""Staleness tracking.

Without this you will ship a build where the voice line does not match the
displayed text (doc S6). Every generated asset records the text hash it was
made from; when the text moves on, the asset goes stale.

A voice line has a second input the text hash cannot see: the character's
reference clip. IndexTTS-2.5 takes its timbre from that clip and nothing else, so
recasting a voice mid-project leaves every line already rendered in the old
timbre -- audio that still matches its text perfectly and is still wrong. The
clip that produced a line is stamped next to its text hash, and a line whose
character now points at a different clip goes stale on the same badge.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import store
from ..graph import text_hash
from ..models import AssetStatus, Character, Node


def voice_id(character: Optional[Character]) -> str:
    """The part of a character's voice that, when it changes, invalidates audio.

    Only the reference clip: it is what IndexTTS-2.5 clones, and swapping it is a
    deliberate recast rather than a tuning tweak. `emotion_scale`, `speed` and
    `pitch` also move the output, but they are nudged repeatedly while casting,
    and marking every line in the game stale on each nudge would make the badge
    noise rather than signal.
    """
    from .tts import clip_digest      # local: tts imports half the services

    if character is None:
        return ""
    return clip_digest(character.voice.reference_clip or "")


def voice_refs(project_id: str) -> dict[str, str]:
    """character id -> current voice id, for the audio half of `evaluate`."""
    return {c.id: voice_id(c) for c in store.list_characters(project_id)}


def stamp(node: Node, kind: str, voice: str = "") -> Node:
    """Record what this asset was generated from.

    The text hash for every kind, plus -- for audio -- the reference clip that
    supplied the timbre, which no hash of the node's own text can capture.
    """
    data = dict(node.data or {})
    data[f"_{kind}_text_hash"] = text_hash(node)
    if kind == "audio":
        data["_audio_voice_id"] = voice
    node.data = data
    return node


def audio_stale_reason(node: Node, refs: Optional[dict[str, str]] = None) -> str:
    """Why this node's voice line is stale, or "" if it is not.

    Split out because the two causes want different fixes: re-voice one line
    after an edit, or re-voice everything a character says after a recast.
    """
    data = node.data or {}
    if not data.get("voice_path"):
        return ""
    if data.get("_audio_text_hash") != text_hash(node):
        return "text changed"
    if refs is not None:
        stamped = data.get("_audio_voice_id")
        # Absent means the line predates this tracking. `migrate_voice_stamps`
        # backfills those from the clip in place at upgrade, so treating an
        # unstamped line as current here cannot hide a recast that came later.
        if stamped is not None and stamped != refs.get(data.get("speaker") or "", ""):
            return "voice clip changed"
    return ""


def evaluate(node: Node, refs: Optional[dict[str, str]] = None) -> Node:
    """`refs` is the project's character-id -> voice-id map from `voice_refs`.

    Optional because most callers evaluate a node they have just built, which
    has no voice line to invalidate; omitting it only skips the recast check.
    """
    current = text_hash(node)
    data = node.data or {}

    if data.get("image_hash"):
        stamped = data.get("_image_text_hash")
        node.image_status = (AssetStatus.ready if stamped == current else AssetStatus.stale)
    elif node.image_status not in (AssetStatus.queued, AssetStatus.running,
                                   AssetStatus.error):
        node.image_status = AssetStatus.none

    if data.get("voice_path"):
        node.audio_status = (AssetStatus.stale if audio_stale_reason(node, refs)
                             else AssetStatus.ready)
    elif node.audio_status not in (AssetStatus.queued, AssetStatus.running,
                                   AssetStatus.error):
        node.audio_status = AssetStatus.none

    if data.get("text"):
        node.text_status = AssetStatus.ready
    elif node.text_status not in (AssetStatus.queued, AssetStatus.running, AssetStatus.error):
        node.text_status = AssetStatus.none

    node.text_hash = current
    return node


def refresh_project(project_id: str) -> dict[str, int]:
    counts = {"image_stale": 0, "audio_stale": 0, "checked": 0}
    refs = voice_refs(project_id)
    for node in store.list_nodes(project_id):
        before = (node.image_status, node.audio_status, node.text_status)
        evaluate(node, refs)
        if (node.image_status, node.audio_status, node.text_status) != before:
            store.update_node(project_id, node)
        counts["checked"] += 1
        if node.image_status == AssetStatus.stale:
            counts["image_stale"] += 1
        if node.audio_status == AssetStatus.stale:
            counts["audio_stale"] += 1
    return counts


def stale_nodes(project_id: str) -> dict[str, list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    refs = voice_refs(project_id)
    for node in store.list_nodes(project_id, include_orphaned=False):
        evaluate(node, refs)
        entry = {"id": node.id, "type": node.type.value,
                 "text": (node.data or {}).get("text", "")[:80]}
        if node.image_status == AssetStatus.stale:
            images.append(entry)
        if node.audio_status == AssetStatus.stale:
            audio.append({**entry, "reason": audio_stale_reason(node, refs)})
    return {"images": images, "audio": audio}


def migrate_voice_stamps() -> int:
    """Stamp the reference clip onto voice lines generated before it was tracked.

    They are assumed to have come from the clip the character carries now --
    the only thing that can be known, and right in every case except a recast
    that already happened, whose lines were already wrong before this ran.
    Without the backfill every existing line would stay unstamped forever, so
    the first recast after upgrading would flag nothing.
    """
    stamped = 0
    for project in store.list_projects():
        refs = voice_refs(project.id)
        for node in store.list_nodes(project.id):
            data = node.data or {}
            if not data.get("voice_path") or "_audio_voice_id" in data:
                continue
            node.data = {**data,
                         "_audio_voice_id": refs.get(data.get("speaker") or "", "")}
            store.update_node(project.id, node)
            stamped += 1
    return stamped
