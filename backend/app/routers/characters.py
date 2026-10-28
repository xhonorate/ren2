"""Characters, locations, and the voice-casting flow."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError

from .. import store
from ..graph import slugify
from ..models import Character, Location
from ..services import assets as asset_svc, images, staleness, tts

router = APIRouter()


class CharacterCreate(BaseModel):
    display_name: str
    age: int
    id: Optional[str] = None
    gender: str = "female"
    is_player: bool = False


@router.get("/projects/{project_id}/characters")
def list_characters(project_id: str) -> list[Character]:
    return store.list_characters(project_id)


@router.post("/projects/{project_id}/characters")
def create_character(project_id: str, body: CharacterCreate) -> Character:
    cid = slugify(body.id or body.display_name, "chr")
    if store.get_character(project_id, cid):
        raise HTTPException(409, f"character id '{cid}' already exists")
    try:
        character = Character(
            id=cid, display_name=body.display_name, age=body.age,
            gender=body.gender, is_player=body.is_player,
        )
    except ValidationError as exc:
        # The 18+ floor lives on the schema, so this is where it surfaces.
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    character.assets.trigger = f"chr_{cid}"
    if body.is_player:
        character.voice.voiced = False
    return store.save_character(project_id, character)


@router.put("/projects/{project_id}/characters/{char_id}")
def save_character(project_id: str, char_id: str, body: dict[str, Any]) -> Character:
    body["id"] = char_id
    try:
        character = Character.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    # Audition takes are server-owned: the cast job writes them, and the clip
    # endpoints clear them. A client draft loaded before a test finished would
    # otherwise post a stale (usually empty) list and orphan the clips.
    existing = store.get_character(project_id, char_id)
    if existing is not None:
        character.voice.audition = existing.voice.audition
    return store.save_character(project_id, character)


@router.delete("/projects/{project_id}/characters/{char_id}")
def delete_character(project_id: str, char_id: str) -> dict[str, bool]:
    store.delete_character(project_id, char_id)
    return {"deleted": True}


@router.get("/projects/{project_id}/characters/{char_id}/prompt-preview")
def prompt_preview(project_id: str, char_id: str, outfit_id: str = "",
                   expression: str = "neutral") -> dict[str, Any]:
    """Exactly what the image model will receive -- identity tags included."""
    character = store.get_character(project_id, char_id)
    project = store.get_project(project_id)
    if character is None or project is None:
        raise HTTPException(404, "character not found")
    resolved_outfit_id = outfit_id or character.default_outfit
    prompt = images.assemble_sprite_prompt(project, character, resolved_outfit_id, expression)
    lora_tag, lora_file, trigger, weight = images.lora_fragment(character)
    outfit = images.resolve_outfit(character, resolved_outfit_id)
    return {
        **prompt,
        "identity_tags": images.identity_tags(character, outfit),
        "outfit_tags": outfit.tags if outfit else "",
        "lora": {"tag": lora_tag, "file": lora_file, "trigger": trigger, "weight": weight},
        "gated": character.age < 18,
    }


# --------------------------------------------------------------------------
# voice casting -- one-time, then frozen
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/characters/{char_id}/voice-clip")
async def upload_voice_clip(project_id: str, char_id: str,
                            file: UploadFile = File(...)) -> dict[str, Any]:
    """Freeze the timbre reference. One clean 8-15s mono clip (doc S5)."""
    character = store.get_character(project_id, char_id)
    if character is None:
        raise HTTPException(404, "character not found")
    data = await file.read()
    ext = (file.filename or "clip.wav").rsplit(".", 1)[-1].lower() or "wav"
    saved = tts.save_reference_clip(char_id, data, ext)
    # Any stored emotion-range test previews the *old* voice, so it is stale the
    # moment the clip changes -- drop it rather than leave misleading audio on
    # the tab (and its clips in the gallery).
    dropped = tts.discard_audition(character, keep={saved["hash"]})
    character.voice.reference_clip = saved["reference_clip"]
    store.save_character(project_id, character)
    # Every line already rendered came out of the clip just replaced, and none
    # of their text changed -- so this is the only moment they can be marked.
    # Swept here rather than lazily so the badges are already right when the
    # canvas reloads.
    counts = staleness.refresh_project(project_id)
    return {**saved, "bytes": len(data), "stale_takes_removed": dropped,
            "lines_stale": counts["audio_stale"],
            "note": "changing this later re-voices every line this character has"}


class AdoptClip(BaseModel):
    hash: str
    ext: str = "wav"


@router.post("/projects/{project_id}/characters/{char_id}/voice-clip/adopt")
def adopt_candidate(project_id: str, char_id: str, body: AdoptClip) -> Character:
    """Promote a stored clip to the character's permanent timbre prompt.

    No UI calls this: an audition take is IndexTTS-2.5's re-read of the clip the
    character already has, so adopting one trades a clean original for a
    second-generation clone of itself. Kept for API callers that stage a clip
    of their own (upload it, then adopt by hash).
    """
    character = store.get_character(project_id, char_id)
    if character is None:
        raise HTTPException(404, "character not found")
    if asset_svc.path_for(body.hash) is None:
        raise HTTPException(404, "clip not found")
    tts.discard_audition(character, keep={body.hash})
    character.voice.reference_clip = f"voice_ref/{body.hash}.{body.ext}"
    saved = store.save_character(project_id, character)
    staleness.refresh_project(project_id)      # as in the upload route above
    return saved


@router.get("/projects/{project_id}/characters/{char_id}/emotion-preview")
def emotion_preview(project_id: str, char_id: str, emotion: str = "neutral",
                    intensity: float = 0.6) -> dict[str, Any]:
    character = store.get_character(project_id, char_id)
    if character is None:
        raise HTTPException(404, "character not found")
    return tts.resolve_emotion(emotion, intensity, character)


# --------------------------------------------------------------------------
# locations
# --------------------------------------------------------------------------

class LocationCreate(BaseModel):
    name: str
    id: Optional[str] = None
    tags: str = ""


@router.get("/projects/{project_id}/locations")
def list_locations(project_id: str) -> list[Location]:
    return store.list_locations(project_id)


@router.post("/projects/{project_id}/locations")
def create_location(project_id: str, body: LocationCreate) -> Location:
    lid = slugify(body.id or body.name, "loc")
    location = Location(id=lid, name=body.name, tags=body.tags)
    return store.save_location(project_id, location)


@router.put("/projects/{project_id}/locations/{loc_id}")
def save_location(project_id: str, loc_id: str, body: dict[str, Any]) -> Location:
    body["id"] = loc_id
    return store.save_location(project_id, Location.model_validate(body))


@router.delete("/projects/{project_id}/locations/{loc_id}")
def delete_location(project_id: str, loc_id: str) -> dict[str, bool]:
    store.delete_location(project_id, loc_id)
    return {"deleted": True}
