"""Roleplay: a chat with the cast, stored outside the graph.

Every endpoint here works in conversation ids and message ids. Nothing takes a
node id, nothing returns one, and nothing this router writes is ever walked,
linearized, staled or compiled -- which is the whole point of replacing Improv,
where the two were the same thing.

The one deliberate exception is `/port`, which builds graph nodes from a finished
conversation. It is a one-way copy: the new scene has no link back, and editing
either afterwards does not touch the other.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..jobs.queue import enqueue
from ..models import Persona, RPConversation, RPMessage
from ..services import roleplay, vram

router = APIRouter()


def _project(project_id: str):
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _conversation(project_id: str, conv_id: str) -> RPConversation:
    conv = store.get_conversation(conv_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(404, "conversation not found")
    return conv


def _shape(conv: RPConversation, messages: Optional[list[RPMessage]] = None,
           ported: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """One conversation for the client, with the title it should be listed under.

    `ported` is passed in rather than looked up, because the list endpoint needs
    it once for the whole page and a scan per row would read every node in the
    project once per conversation.
    """
    out = conv.model_dump()
    out["title"] = roleplay.title_for(conv, messages or [])
    out["untitled"] = not conv.title.strip()
    out["message_count"] = len(messages) if messages is not None else None
    out["scene_id"] = (ported or {}).get(conv.id)
    return out


# --------------------------------------------------------------------------
# who you can talk to
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/rp/characters")
def characters(project_id: str) -> dict[str, Any]:
    """The cast, with their profile image and how much history each one has.

    The player character is excluded from the pickable list -- you cannot have a
    conversation with yourself -- but is returned separately, because the persona
    falls back to them and the config panel shows what it borrowed.
    """
    _project(project_id)
    project = store.get_project(project_id)
    people = store.list_characters(project_id)
    by_id = {c.id: c for c in people}
    conversations = store.list_conversations(project_id)
    persona = roleplay.resolve_persona(project, by_id)

    out = []
    for character in sorted(people, key=lambda c: c.display_name.lower()):
        if character.is_player:
            continue
        mine = [c for c in conversations if character.id in c.everyone()]
        out.append({
            "id": character.id,
            "name": character.display_name,
            "color": character.color,
            "profile_image": character.assets.profile_image,
            "summary": character.personality.summary,
            "voiced": bool(character.voice.voiced and character.voice.reference_clip),
            "conversations": len(mine),
            "last_at": mine[-1].updated_at if mine else None,
        })
    player = next((c for c in people if c.is_player), None)
    return {
        "characters": out,
        "persona": persona.model_dump(),
        "player": ({"id": player.id, "name": player.display_name} if player else None),
    }


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/rp/conversations")
def list_conversations(project_id: str, character_id: str = "") -> dict[str, Any]:
    """Chronological, oldest first. Hidden ones are included and flagged."""
    _project(project_id)
    ported = roleplay.ported_scenes(project_id)
    out = []
    for conv in store.list_conversations(project_id, character_id or None):
        messages = store.list_messages(conv.id)
        out.append(_shape(conv, messages, ported))
    return {"conversations": out}


class NewConversation(BaseModel):
    character_id: str
    cast: list[str] = []
    scene_context: str = ""
    title: str = ""
    nsfw: bool = False
    location_id: Optional[str] = None
    time_of_day: Optional[str] = None


@router.post("/projects/{project_id}/rp/conversations")
def create_conversation(project_id: str, body: NewConversation) -> dict[str, Any]:
    _project(project_id)
    if store.get_character(project_id, body.character_id) is None:
        raise HTTPException(404, "no such character")
    known = {c.id for c in store.list_characters(project_id)}
    conv = RPConversation(
        id=store.new_id("rc_"), project_id=project_id, character_id=body.character_id,
        cast=[c for c in body.cast if c in known and c != body.character_id],
        title=body.title.strip(), scene_context=body.scene_context.strip(),
        nsfw=body.nsfw, location_id=body.location_id, time_of_day=body.time_of_day,
    )
    store.insert_conversation(conv)
    return _shape(conv, [])


@router.get("/projects/{project_id}/rp/conversations/{conv_id}")
def get_conversation(project_id: str, conv_id: str) -> dict[str, Any]:
    conv = _conversation(project_id, conv_id)
    messages = store.list_messages(conv_id)
    return {**_shape(conv, messages, roleplay.ported_scenes(project_id)),
            "messages": [m.model_dump() for m in messages]}


class PatchConversation(BaseModel):
    title: Optional[str] = None
    scene_context: Optional[str] = None
    summary: Optional[str] = None
    hidden: Optional[bool] = None
    state: Optional[str] = None
    nsfw: Optional[bool] = None
    cast: Optional[list[str]] = None
    location_id: Optional[str] = None
    time_of_day: Optional[str] = None


@router.patch("/projects/{project_id}/rp/conversations/{conv_id}")
def patch_conversation(project_id: str, conv_id: str,
                       body: PatchConversation) -> dict[str, Any]:
    conv = _conversation(project_id, conv_id)
    fields = body.model_dump(exclude_none=True)
    if "summary" in fields:
        # A hand-edited summary is content, not a cache entry: the flag is what
        # stops ending the scene again from overwriting the author's words.
        conv.summary_edited = True
    if "cast" in fields:
        known = {c.id for c in store.list_characters(project_id)}
        fields["cast"] = [c for c in fields["cast"]
                          if c in known and c != conv.character_id]
    for key, value in fields.items():
        setattr(conv, key, value)
    store.save_conversation(conv)
    return _shape(conv, store.list_messages(conv_id))


@router.delete("/projects/{project_id}/rp/conversations/{conv_id}")
def delete_conversation(project_id: str, conv_id: str) -> dict[str, bool]:
    _conversation(project_id, conv_id)
    store.delete_conversation(conv_id)
    return {"deleted": True}


class Reorder(BaseModel):
    ids: list[str]


@router.post("/projects/{project_id}/rp/conversations/reorder")
def reorder(project_id: str, body: Reorder) -> dict[str, int]:
    """Rewrite `sort_key` to the given order.

    Whole-list rather than one-row, because dragging changes what "earlier"
    means for every scene after the one that moved -- and "earlier on the list"
    is exactly what decides which summaries a later scene remembers.
    """
    _project(project_id)
    mine = {c.id: c for c in store.list_conversations(project_id)}
    for index, conv_id in enumerate(body.ids):
        conv = mine.get(conv_id)
        if conv is None:
            continue
        conv.sort_key = float(index)
        store.save_conversation(conv)
    return {"reordered": len([i for i in body.ids if i in mine])}


# --------------------------------------------------------------------------
# turns
# --------------------------------------------------------------------------

class Turn(BaseModel):
    # Empty means "carry on without me" -- an explicit move. No message row is
    # written for it, which is how `assemble_turn` tells a silence apart from a
    # message that has not been answered yet.
    text: str = ""


@router.post("/projects/{project_id}/rp/conversations/{conv_id}/turn")
def turn(project_id: str, conv_id: str, body: Turn) -> dict[str, Any]:
    conv = _conversation(project_id, conv_id)
    text = body.text.strip()
    message: Optional[RPMessage] = None
    if text:
        message = RPMessage(id=store.new_id("m_"), conv_id=conv.id,
                            seq=store.next_seq(conv.id), role="player", text=text,
                            created_at=store.now())
        store.insert_message(message)
    # Re-opened by taking another turn: an ended scene is ended because it was
    # summarized, and adding to it makes that summary stale.
    if conv.state == "ended":
        conv.state = "open"
        store.save_conversation(conv)
    job_id = enqueue(project_id, "rp.reply", {"conv_id": conv.id},
                     label="roleplay reply")
    return {"job_id": job_id,
            "message": message.model_dump() if message is not None else None}


class Regenerate(BaseModel):
    # The reply to redo. Empty means the last one.
    message_id: str = ""


@router.post("/projects/{project_id}/rp/conversations/{conv_id}/regenerate")
def regenerate(project_id: str, conv_id: str, body: Regenerate) -> dict[str, Any]:
    """Another take on a reply. The one showing is kept as a swipe."""
    conv = _conversation(project_id, conv_id)
    messages = store.list_messages(conv_id)
    target = None
    if body.message_id:
        target = next((m for m in messages if m.id == body.message_id), None)
        if target is None:
            raise HTTPException(404, "no such message")
        if target.role != "cast":
            raise HTTPException(409, "only a reply can be regenerated")
    else:
        target = next((m for m in reversed(messages) if m.role == "cast"), None)
        if target is None:
            raise HTTPException(409, "there is no reply here to regenerate")

    # Anything after the reply being redone is a continuation of a reply that is
    # about to change, so it cannot be kept.
    dropped = [m.id for m in messages if m.seq > target.seq]
    for message_id in dropped:
        store.delete_message(message_id)
    job_id = enqueue(project_id, "rp.reply",
                     {"conv_id": conv.id, "message_id": target.id},
                     label="roleplay retake")
    return {"job_id": job_id, "message_id": target.id, "dropped": dropped}


@router.get("/projects/{project_id}/rp/conversations/{conv_id}/preview")
def preview(project_id: str, conv_id: str) -> dict[str, Any]:
    """Exactly what the next reply would be sent, verbatim.

    Here from the first version rather than added later for debugging: the prompt
    is the thing in this mode that decides whether the output is any good, and
    tuning it blind -- reading replies and guessing what the model was looking
    at -- is how Improv's prompt ended up the way it did.
    """
    conv = _conversation(project_id, conv_id)
    return roleplay.assemble_turn(project_id, conv)


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------

class PatchMessage(BaseModel):
    text: Optional[str] = None
    active_take: Optional[str] = None


@router.patch("/projects/{project_id}/rp/messages/{message_id}")
def patch_message(project_id: str, message_id: str,
                  body: PatchMessage) -> dict[str, Any]:
    message = store.get_message(message_id)
    if message is None:
        raise HTTPException(404, "message not found")
    _conversation(project_id, message.conv_id)

    if body.active_take is not None:
        take = next((t for t in message.takes if t.id == body.active_take), None)
        if take is None:
            raise HTTPException(404, "no such take")
        message.active_take = take.id
        message.text = take.text
        message.voice = []             # the clips on file speak the other take
    if body.text is not None:
        message.text = body.text
        # An edit supersedes whichever take was showing, and is kept as one of
        # its own so arrowing away and back does not lose it.
        if message.active_take:
            for take in message.takes:
                if take.id == message.active_take:
                    take.text = body.text
        message.voice = []
    store.save_message(message)
    return message.model_dump()


@router.delete("/projects/{project_id}/rp/messages/{message_id}")
def delete_message(project_id: str, message_id: str,
                   rewind: bool = False) -> dict[str, Any]:
    """Delete one message, or (`rewind`) it and everything after it.

    Nothing is parked, unlike the graph's rewind: a conversation is a line with
    no branches, so a message after the cut cannot be reachable another way and
    there is nothing for a holding area to protect.
    """
    message = store.get_message(message_id)
    if message is None:
        raise HTTPException(404, "message not found")
    _conversation(project_id, message.conv_id)
    if rewind:
        return {"deleted": store.delete_messages_from(message.conv_id, message.seq)}
    store.delete_message(message_id)
    return {"deleted": [message_id]}


# --------------------------------------------------------------------------
# ending a scene, and starting the next one
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/rp/conversations/{conv_id}/end")
def end_conversation(project_id: str, conv_id: str, force: bool = False) -> dict[str, Any]:
    """Summarize the scene so later ones remember it."""
    conv = _conversation(project_id, conv_id)
    if not store.list_messages(conv_id):
        raise HTTPException(409, "there is nothing in this conversation yet")
    job_id = enqueue(project_id, "rp.summarize",
                     {"conv_id": conv.id, "force": force}, label="end scene")
    return {"job_id": job_id}


@router.post("/projects/{project_id}/rp/seed")
def seed(project_id: str, character_id: str, count: int = 3) -> dict[str, Any]:
    """Propose openings for the next scene with this character."""
    _project(project_id)
    if store.get_character(project_id, character_id) is None:
        raise HTTPException(404, "no such character")
    job_id = enqueue(project_id, "rp.seed",
                     {"character_id": character_id, "count": count},
                     label="scene ideas")
    return {"job_id": job_id}


class Port(BaseModel):
    # Off by default: the transcript is already in house format, so the tidy-up
    # is a chance to lose a line rather than a required step. See the rp.port
    # handler.
    polish: bool = False


@router.post("/projects/{project_id}/rp/conversations/{conv_id}/port")
def port(project_id: str, conv_id: str, body: Port = Port()) -> dict[str, Any]:
    """Copy this conversation into the graph as a new scene.

    The only endpoint in this router that touches a node, and it only ever makes
    them. Allowed on an open conversation -- porting mid-scene is a reasonable
    thing to want -- but not on an empty one.
    """
    conv = _conversation(project_id, conv_id)
    if not store.list_messages(conv_id):
        raise HTTPException(409, "there is nothing in this conversation yet")
    job_id = enqueue(project_id, "rp.port",
                     {"conv_id": conv.id, "polish": body.polish},
                     label="port to scene")
    return {"job_id": job_id}


# --------------------------------------------------------------------------
# persona
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/rp/vram")
def vram_check(project_id: str) -> dict[str, Any]:
    """Whether real-time voice can fit alongside whatever the LLM is holding.

    Advisory and read live off the cards, because the answer changes with the
    context length the LLM was loaded at and with whatever else is on the GPU.
    Asking beforehand is the difference between "voice is off" and a session that
    runs out of memory halfway through a scene.
    """
    _project(project_id)
    report = vram.roleplay_voice_fit()
    report["tts_enabled"] = bool(store.get_setting("rp_tts", False))
    report["coresident"] = bool(store.get_setting("rp_coresident", True))
    return report


@router.get("/projects/{project_id}/rp/persona")
def get_persona(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    characters = {c.id: c for c in store.list_characters(project_id)}
    stored = getattr(project, "roleplay", None)
    return {
        # What is actually stored, so the form shows empty fields as empty.
        "persona": (stored.persona if stored is not None else Persona()).model_dump(),
        # And what a prompt would use, so the form can show the fallback as
        # placeholder text rather than leaving the author guessing.
        "resolved": roleplay.resolve_persona(project, characters).model_dump(),
    }


@router.put("/projects/{project_id}/rp/persona")
def put_persona(project_id: str, body: Persona) -> dict[str, Any]:
    project = _project(project_id)
    project.roleplay.persona = body
    store.save_project(project)
    characters = {c.id: c for c in store.list_characters(project_id)}
    return {"persona": body.model_dump(),
            "resolved": roleplay.resolve_persona(project, characters).model_dump()}
