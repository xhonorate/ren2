"""Intermediate representation for the VN authoring tool.

The IR is the source of truth. Ren'Py is a one-way compile target (design doc
decision 1) -- nothing in here is ever recovered by parsing .rpy back.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, field_validator


# --------------------------------------------------------------------------
# enums
# --------------------------------------------------------------------------

class NodeType(str, Enum):
    scene = "scene"
    say = "say"
    narration = "narration"
    stage = "stage"
    choice = "choice"
    hub = "hub"
    condition = "condition"
    set = "set"
    jump = "jump"
    call = "call"
    audio = "audio"
    transition = "transition"
    raw = "raw"
    end = "end"


class AssetStatus(str, Enum):
    none = "none"        # nothing generated
    queued = "queued"
    running = "running"
    ready = "ready"
    stale = "stale"      # upstream text changed after generation
    error = "error"


# Constrained enums: the structuring pass emits these under a JSON schema, so
# they are always valid downstream inputs for image + TTS. (doc S2, S5)
EMOTIONS = [
    "neutral", "happy", "excited", "affectionate", "amused", "smug",
    "melancholy", "sad", "crying", "lonely", "anxious", "afraid",
    "angry", "irritated", "mean", "disgusted", "surprised", "confused",
    "embarrassed", "flustered", "tired", "determined", "cold", "whisper",
]

EXPRESSIONS = [
    "neutral", "smiling", "grinning", "laughing", "soft_smile", "smirking",
    "frowning", "angry", "glaring", "pouting", "crying", "teary",
    "surprised", "wide_eyed", "blushing", "embarrassed", "looking_away",
    "eyes_closed", "sleepy", "worried", "thinking", "serious",
]

# Sprite body poses. Deliberately short: every pose multiplies the sprite set
# by outfits x expressions, and only combinations the script actually uses are
# ever generated. "front" is the default and the fallback for anything the
# writer model does not specify.
POSES = [
    "front", "three_quarter", "side", "back",
    "arms_crossed", "hands_behind_back", "leaning", "sitting",
]

TRANSITIONS = [
    "none", "dissolve", "fade", "pixellate",
    "wipeleft", "wiperight", "moveinleft", "irisin",
]

# Which character LoRA an event CG loads. Sprites always use the Sprite LoRA
# slot -- they *are* the flat front-facing look it was trained on. A CG wants
# a LoRA too, for the same consistency it gives sprites, but must never be
# blocked by a character that has not been trained yet -- so the default is
# `auto`: use the CG LoRA if the character has one (trained on a varied
# dataset, the better fit for a CG), else the Sprite LoRA, else render with no
# LoRA at all. The face-detail pass runs regardless of which of those
# happens, and is what keeps the pose free of the sheet's front-facing prior
# even when a LoRA is loaded (see images.cg_lora_for, images.assemble_face_prompt).
#
# Named "sprite" / "cg" rather than "v1" / "v2" on purpose: those slots are
# fixed categories, while an author training several passes of either one
# will want their *own* "v1", "v2", "v3" naming for the output file (the
# `output_name` field on lora.train) -- reusing that vocabulary here would
# collide with it.
#
# "" on a node means "whatever the project setting says" (cg_lora_mode).
CG_LORA_MODES = ["auto", "none", "sprite", "cg"]


# --------------------------------------------------------------------------
# per-node payloads
# --------------------------------------------------------------------------

# A switch added after nodes were already being saved. Those nodes have no key
# for it, so the inspector's undo writes the "before" value back as null --
# which a plain `bool` rejects, failing the undo of the very first toggle.
LateFlag = Annotated[bool, BeforeValidator(lambda v: False if v is None else v)]

class GenMeta(BaseModel):
    """Provenance for any generated artifact (doc S2 'Regeneration')."""
    model: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    seed: Optional[int] = None
    prompt_hash: Optional[str] = None
    timestamp: Optional[str] = None


class Variant(BaseModel):
    """One take. Regeneration never destroys the previous take (doc S6)."""
    id: str
    kind: Literal["text", "image", "audio"]
    payload: dict[str, Any] = Field(default_factory=dict)
    asset_hash: Optional[str] = None
    meta: GenMeta = Field(default_factory=GenMeta)
    created_at: Optional[str] = None


class SceneData(BaseModel):
    title: str = "Untitled scene"
    background: Optional[str] = None        # location id
    time_of_day: Optional[str] = None
    weather: Optional[str] = None
    transition: str = "dissolve"
    music: Optional[str] = None
    stop_music: bool = False
    brief: str = ""                          # user's scene brief for the LLM
    # --- structured brief -------------------------------------------------
    # Rendered as constraints on the scene *as a whole*, with an explicit note
    # that it is written across several passes -- otherwise the writer rushes
    # the entire arc into the first generation trying to hit `ends_with`.
    #
    # `characters` empty means "auto": everyone on stage plus everyone who has
    # spoken on this path plus the player, which is what context assembly
    # already computes. It is an override for a character who has not appeared
    # yet -- the writer cannot bring someone in it was never told about.
    characters: list[str] = Field(default_factory=list)
    length: str = ""                         # "short" | "medium" | "long"
    must_hit: str = ""                       # beats the scene has to land
    ends_with: str = ""                      # where it ends up, eventually
    # The adult register, per scene. A flag rather than one value of a tone
    # enum, because the two were never the same axis: a scene is tender or
    # confrontational *and* explicit or not, and folding them into one dropdown
    # meant picking "explicit" threw away whatever the scene actually was. It
    # stays per scene rather than per project -- a story with one explicit
    # scene in it is not an explicit story.
    #
    # What it gates is text, not behaviour: `{% if nsfw %}` blocks in the prose
    # template and the NSFW subsection of every character card render only for
    # a scene that sets this, and are absent from the prompt otherwise.
    nsfw: bool = False
    # Which route this scene belongs to, if any. Hub options and Jump/Call
    # nodes both dispatch over it: either can target "the next unplayed scene
    # in route X" instead of a fixed node, and this is the membership they
    # read. No separate registry -- a route exists because scenes claim it,
    # and its order is the order the scenes sit on the canvas.
    route: str = ""


class SayData(BaseModel):
    speaker: Optional[str] = None            # character id
    text: str = ""
    expression: str = "neutral"
    # The speaker's body pose for this line. Sprites are keyed on
    # outfit/pose/expression, so this picks which sprite is shown.
    pose: str = "front"
    emotion: str = "neutral"
    intensity: float = 0.5
    delivery: str = ""
    voice_override: Optional[str] = None
    shot_hint: str = ""
    cg: bool = False                         # flag this node for an event CG
    # Leave this CG up after the line, so the lines after it play over it. It
    # stays until another CG replaces it, a `scene` clears the stage, or a
    # later line sets `cg_clear` -- the rule lives in graph.resolve_state.
    cg_hold: LateFlag = False
    # Take a held CG down before this line: the way back to the sprites
    # without a scene change.
    cg_clear: LateFlag = False
    # --- distilled CG prompt (services/generation.cg_director_pass) ---------
    # What the director decided the shot *is*, in one English sentence, written
    # before it picks a single tag. It is chain-of-thought made durable: the
    # schema generates this property first, so every tag after it is chosen
    # having already committed to a reading of the beat. Kept on the node
    # because it is the only human-readable record of why these tags.
    cg_description: str = ""
    cg_camera: str = ""      # framing + angle + POV tags; early in the prompt
    cg_action: str = ""      # what the bodies are doing; after the identity block
    cg_negative: str = ""    # per-node negative additions
    cg_lora: str = ""        # "" = project default, else one of CG_LORA_MODES
    # Who is in the shot, set by hand in the inspector. Empty means "work it
    # out from who is on stage" (services.context.cg_subjects), which is right
    # most of the time and wrong exactly when a beat is about one person in a
    # busy room.
    cg_subjects: list[str] = Field(default_factory=list)
    # Tags from the cast's own records that THIS shot must not use, matched on
    # the tag text. A tag in a prompt is a request to find room for the thing,
    # so `brown loafers` on a frame that ends at the shoulders does not get
    # ignored -- it gets shoes, or a pair of feet where they cannot be. Per
    # node, because that is what it is a property of: the same character in a
    # full-body shot two beats later still wants them. Empty (every node saved
    # before this existed) hides nothing.
    cg_hidden_tags: list[str] = Field(default_factory=list)
    # The same switch pulling the other way: tags the framing table would cut
    # that this shot wants anyway. `images.FRAMING_CUTOFF` is a fact about the
    # words and right nearly always, but a close-up composed over a shoulder
    # really can show a skirt, and a lookup table cannot know that. Kept
    # mutually exclusive with `cg_hidden_tags` by the inspector.
    cg_forced_tags: list[str] = Field(default_factory=list)
    # --- one-shot sound (services/generation.extract_performance) -----------
    # A description of the sound this beat makes ("knock at the door"), written
    # by the writer's `SFX:` line. `sfx_file` is filled in later when real audio
    # exists; until then the compiler emits a TODO comment rather than a
    # `play sound` that would break the build on a missing file.
    sfx_cue: str = ""
    sfx_file: Optional[str] = None
    sfx_hash: Optional[str] = None
    voice_path: Optional[str] = None
    voice_hash: Optional[str] = None     # content-addressed source of voice_path
    image_hash: Optional[str] = None


class NarrationData(BaseModel):
    text: str = ""
    shot_hint: str = ""
    cg: bool = False
    cg_hold: LateFlag = False
    cg_clear: LateFlag = False
    cg_description: str = ""
    cg_camera: str = ""
    cg_action: str = ""
    cg_negative: str = ""
    cg_lora: str = ""
    # Who is in the shot, set by hand in the inspector. Empty means "work it
    # out from who is on stage" (services.context.cg_subjects), which is right
    # most of the time and wrong exactly when a beat is about one person in a
    # busy room.
    cg_subjects: list[str] = Field(default_factory=list)
    # Tags from the cast's own records that THIS shot must not use, matched on
    # the tag text. A tag in a prompt is a request to find room for the thing,
    # so `brown loafers` on a frame that ends at the shoulders does not get
    # ignored -- it gets shoes, or a pair of feet where they cannot be. Per
    # node, because that is what it is a property of: the same character in a
    # full-body shot two beats later still wants them. Empty (every node saved
    # before this existed) hides nothing.
    cg_hidden_tags: list[str] = Field(default_factory=list)
    # The same switch pulling the other way: tags the framing table would cut
    # that this shot wants anyway. `images.FRAMING_CUTOFF` is a fact about the
    # words and right nearly always, but a close-up composed over a shoulder
    # really can show a skirt, and a lookup table cannot know that. Kept
    # mutually exclusive with `cg_hidden_tags` by the inspector.
    cg_forced_tags: list[str] = Field(default_factory=list)
    sfx_cue: str = ""
    sfx_file: Optional[str] = None
    sfx_hash: Optional[str] = None
    image_hash: Optional[str] = None


class StageData(BaseModel):
    """Mutates path state: location, who is present, outfits, expressions."""
    location: Optional[str] = None
    time_of_day: Optional[str] = None
    weather: Optional[str] = None
    transition: str = "none"
    show: list[str] = Field(default_factory=list)     # character ids entering
    hide: list[str] = Field(default_factory=list)     # character ids leaving
    outfits: dict[str, str] = Field(default_factory=dict)      # char -> outfit id
    expressions: dict[str, str] = Field(default_factory=dict)  # char -> expression
    poses: dict[str, str] = Field(default_factory=dict)        # char -> pose
    positions: dict[str, str] = Field(default_factory=dict)    # char -> left/center/right
    reset: bool = False                                # emit `scene` (clears layer)
    # --- music ------------------------------------------------------------
    # Music is *state*, not an event, and `stage` is the state-mutation node --
    # `resolve_state` already tracks `state.music`, and SceneData already
    # carries the same pair. A one-shot sound belongs on the say/narration beat
    # it accompanies instead (SayData.sfx_cue).
    music_cue: str = ""                            # from the writer's MUSIC: line
    music_file: Optional[str] = None                   # resolved later
    stop_music: bool = False


class StatCondition(BaseModel):
    """One structured gate: `{stat, op, value}`.

    Shared by choice options, hub topics and condition branches -- all three
    compile through the same guard, so all three declare it.

    Deliberately not a free-text expression. The UI validates `stat` against
    the project's declared stats (and `character` against the cast), so a gate
    cannot silently read a variable that does not exist -- the failure mode
    of a mistyped `affection_dianna >= 20`, which Ren'Py evaluates as False
    forever without ever complaining.

    `character` empty means a plain project variable rather than a per-character
    stat, so a gate can also read `chapter_two_unlocked`.
    """
    stat: str = ""
    character: str = ""
    op: Literal["==", "!=", ">", ">=", "<", "<="] = ">="
    value: str = "0"


class ChoiceOption(BaseModel):
    id: str
    label: str
    requires: list[StatCondition] = Field(default_factory=list)
    condition: str = ""          # Ren'Py `if` clause, ANDed with `requires`
    branch_hint: str = ""        # steers LLM generation down this branch
    effects: list[dict[str, str]] = Field(default_factory=list)  # {var, op, value} applied when chosen


class ChoiceData(BaseModel):
    prompt: str = ""
    options: list[ChoiceOption] = Field(default_factory=list)
    mode: Literal["jump", "call"] = "jump"
    exhaust: bool = False        # menuset pattern: hide already-taken options
    menuset: str = "menuset"
    loop_back: bool = False      # after a `call` option, jump back to this menu


class HubOption(BaseModel):
    """One topic on a hub menu.

    `locked` is the difference between the two ways a VN hub treats an option
    you have not earned: `hide` drops it from the menu entirely, `grey` shows
    it as an unpickable line saying what it wants -- which is most of the point
    of a hub in a stat-driven game, since an option you cannot see gives you
    nothing to aim at.
    """
    id: str
    label: str = ""
    requires: list[StatCondition] = Field(default_factory=list)
    locked: Literal["hide", "grey"] = "hide"
    locked_label: str = ""       # shown greyed when `locked` is "grey"
    # Survives `exhaust`: the topic stays on the menu however often it is
    # taken. What a hub's way out needs -- an exhausting hub that loops runs
    # out of options eventually, and Ren'Py raises on a menu with nothing in
    # it, so something has to stay pickable.
    repeatable: bool = False
    branch_hint: str = ""        # steers LLM generation down this topic
    effects: list[dict[str, str]] = Field(default_factory=list)
    # Destination. Empty means "whatever the port is wired to". Set to a route
    # name and the option instead goes to the next scene in that route the
    # player has not reached yet (SceneData.route), which is what a route-select
    # hub wants -- the alternative is rewiring the hub after every new scene.
    target_route: str = ""


class HubData(BaseModel):
    """A Choice with structured conditions and a locked-option story.

    The defaults are the hub pattern rather than the choice pattern: `call` so
    a topic returns, `loop_back` so the menu comes round again, `exhaust` so a
    topic already taken drops out of the list.
    """
    prompt: str = ""
    options: list[HubOption] = Field(default_factory=list)
    mode: Literal["jump", "call"] = "call"
    exhaust: bool = True
    menuset: str = "hub_topics"
    loop_back: bool = True


class ConditionBranch(BaseModel):
    id: str
    expr: str = ""               # empty on the final `else`
    requires: list[StatCondition] = Field(default_factory=list)
    # Fire on the way through the branch, so "she notices -- +5 affection"
    # needs no Set node hanging off it.
    effects: list[dict[str, str]] = Field(default_factory=list)


class ConditionData(BaseModel):
    branches: list[ConditionBranch] = Field(default_factory=list)
    has_else: bool = True


class SetData(BaseModel):
    assignments: list[dict[str, str]] = Field(default_factory=list)  # {var, op, value}


class JumpData(BaseModel):
    target_node: Optional[str] = None
    target_label: Optional[str] = None
    # Destination by route rather than by wire. Set to a route name and the
    # node goes to the first scene in that route this playthrough has not
    # played yet (SceneData.route), resolved when it is reached rather than at
    # compile time. The wired target becomes the fallback for a route that has
    # already been played out. Same dispatch a hub option gets.
    target_route: str = ""


class AudioData(BaseModel):
    channel: Literal["music", "sound", "voice"] = "music"
    action: Literal["play", "stop", "queue"] = "play"
    file: Optional[str] = None
    fadeout: float = 1.0
    fadein: float = 0.0
    loop: bool = True


class TransitionData(BaseModel):
    transition: str = "dissolve"
    pause: float = 0.0


class RawData(BaseModel):
    code: str = ""


class EndData(BaseModel):
    mode: Literal["return", "jump", "ending"] = "return"
    ending_id: str = ""
    unlock_persistent: list[str] = Field(default_factory=list)


DATA_MODELS: dict[str, type[BaseModel]] = {
    NodeType.scene: SceneData,
    NodeType.say: SayData,
    NodeType.narration: NarrationData,
    NodeType.stage: StageData,
    NodeType.choice: ChoiceData,
    NodeType.hub: HubData,
    NodeType.condition: ConditionData,
    NodeType.set: SetData,
    NodeType.jump: JumpData,
    NodeType.call: JumpData,
    NodeType.audio: AudioData,
    NodeType.transition: TransitionData,
    NodeType.raw: RawData,
    NodeType.end: EndData,
}


# --------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------

class Node(BaseModel):
    id: str
    type: NodeType
    data: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    parent_id: Optional[str] = None      # owning Scene container (UI grouping)
    pinned_label: bool = False           # user-pinned jump target
    slug: str = ""
    notes: str = ""
    text_hash: str = ""
    text_status: AssetStatus = AssetStatus.none
    image_status: AssetStatus = AssetStatus.none
    audio_status: AssetStatus = AssetStatus.none
    variants: list[Variant] = Field(default_factory=list)
    active_variant: Optional[str] = None
    orphaned: bool = False               # holding area for regenerate-from-node

    def payload(self) -> BaseModel:
        model = DATA_MODELS[self.type]
        return model.model_validate(self.data)


class Edge(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str = "out"   # choice option id / condition branch id / "out"
    label: str = ""


# --------------------------------------------------------------------------
# characters
# --------------------------------------------------------------------------

class TaggedField(BaseModel):
    """Every appearance field carries a human label AND a booru tag (doc S3)."""
    label: str = ""
    tag: str = ""


class Outfit(BaseModel):
    id: str
    name: str
    tags: str = ""
    negative: str = ""
    # Distinguishing-mark / accessory labels (Appearance.distinguishing /
    # .accessories) that this outfit hides from the image prompt -- e.g. a
    # chest tattoo turned off for an outfit with a high collar, so it isn't
    # drawn on top of the clothing. Matched by label since TaggedField has no
    # separate id. Empty (every outfit saved before this existed) hides
    # nothing, so old data renders exactly as before.
    hidden_marks: list[str] = Field(default_factory=list)
    hidden_accessories: list[str] = Field(default_factory=list)


class Appearance(BaseModel):
    hair_color: TaggedField = Field(default_factory=TaggedField)
    hair_style: TaggedField = Field(default_factory=TaggedField)
    eye_color: TaggedField = Field(default_factory=TaggedField)
    eye_quality: TaggedField = Field(default_factory=TaggedField)
    face: TaggedField = Field(default_factory=TaggedField)
    skin_tone: TaggedField = Field(default_factory=TaggedField)
    build: TaggedField = Field(default_factory=TaggedField)
    height: TaggedField = Field(default_factory=TaggedField)
    bust: TaggedField = Field(default_factory=TaggedField)
    butt: TaggedField = Field(default_factory=TaggedField)
    rest_expression: TaggedField = Field(default_factory=TaggedField)
    posture: TaggedField = Field(default_factory=TaggedField)
    accessories: list[TaggedField] = Field(default_factory=list)
    distinguishing: list[TaggedField] = Field(default_factory=list)
    negatives: list[TaggedField] = Field(default_factory=list)


class Personality(BaseModel):
    summary: str = ""
    speech_style: str = ""
    speech_register: str = ""
    wants: str = ""
    fears: str = ""
    relationship_to_player: str = ""
    loves: str = ""
    likes: str = ""
    dislikes: str = ""
    background: str = ""
    hobbies: str = ""
    # Only ever rendered into the card for a scene flagged nsfw
    # (services/context.character_card). Kept out of every other prompt, so a
    # character can carry this without it colouring how they are written in the
    # forty scenes that are not.
    nsfw: str = ""


class AuditionTake(BaseModel):
    """One stored emotion-range preview, kept so the Voice tab still shows the
    last test after you navigate away. `hash` is the raw clip, `preview_hash`
    the speed-corrected ogg that actually plays."""
    hash: str
    preview_hash: str = ""
    emotion: str = "neutral"
    intensity: float = 0.6
    line: str = ""
    ext: str = "wav"


class VoiceCfg(BaseModel):
    # A sourcing brief for a human, never a model input: IndexTTS-2.5 clones a
    # clip and has no described-voice mode, and the character card template
    # renders personality.speech_style, not this. Paste it into a described-voice
    # model (Chatterbox) to make a seed clip.
    description: str = ""
    reference_clip: Optional[str] = None
    emotion_scale: float = 0.7      # per-character intensity cap (doc S5)
    voiced: bool = True
    pitch: float = 1.0
    speed: float = 1.0
    # Last emotion-range test. Replaced wholesale by the next run (the old
    # clips are deleted with it) and cleared when the reference clip changes,
    # since previews of a voice you no longer use are just clutter.
    audition: list[AuditionTake] = Field(default_factory=list)


class SheetPoolItem(BaseModel):
    """One candidate LoRA-training image. `caption` is filled in by the WD14
    auto-caption job and persists here so re-visiting the character or
    generating another batch never re-captions what's already done."""
    hash: str
    outfit_id: str = ""
    angle: str = ""
    expression: str = ""
    # CG-set only, and empty on every sheet image: the sheet is deliberately
    # one composition (solo, full body, white) because it trains the LoRA that
    # draws sprites. These are what the CG set varies instead -- what the body
    # is doing, who else is in frame, how close, and where. Recorded per image
    # so the cull grid can show what varied and "regenerate this one" can pin
    # the exact recipe back.
    action: str = ""
    company: str = ""
    framing: str = ""
    scene: str = ""
    seed: int = 0
    caption: str = ""


class CharAssets(BaseModel):
    # The Sprite LoRA -- trained on the character sheet (flat, front-facing,
    # white background), and what every sprite generation always loads.
    lora: Optional[str] = None
    trigger: str = ""
    lora_weight: float = 0.7
    # The CG LoRA -- trained on a *varied* dataset (the Sprite LoRA's own
    # output, used to shoot CG-style images at other angles, in other places,
    # then culled: see the CG LoRA tab / image.cg_set). Purely opt-in: an event
    # CG uses it only once it exists (CG_LORA_MODES `auto`), and it shares the
    # same trigger word as the Sprite LoRA -- they are two takes on one
    # character, not two characters.
    lora_cg: Optional[str] = None
    # CGs run the character LoRA (whichever slot) *weaker* than sprites do.
    # A sheet-trained LoRA at sprite strength drags every CG back toward
    # front-facing standing; the face-detail pass is what restores the likeness
    # the lower weight gives up, and it runs at its own, higher weight because
    # it only ever touches the face crop.
    cg_lora_weight: float = 0.55
    face_lora_weight: float = 0.9
    # The Sprite LoRA's bootstrap working set: every character-sheet image
    # generated, across every run, until the user deletes one
    # (services/images.py never replaces this on a new sheet job -- it only
    # appends). Training reads this whole pool rather than a hand-picked
    # subset (doc lora-training.md).
    sheet_pool: list[SheetPoolItem] = Field(default_factory=list)
    # The CG LoRA's own working set -- shot with the Sprite LoRA (image.cg_set),
    # not the sheet job, and never mixed with sheet_pool: the two LoRAs train
    # on different datasets and are culled, captioned and trained separately.
    cg_pool: list[SheetPoolItem] = Field(default_factory=list)
    sprite_sheet: dict[str, str] = Field(default_factory=dict)  # "outfit/expr" -> hash
    negative: str = ""
    # A single "headshot" render for the character overview grid -- identity
    # tags plus one expression, framed as an upper-body portrait. Loads the CG
    # LoRA if the character has one, else the Sprite LoRA, else no LoRA. Never
    # reaches the compiled game; it is an authoring-tool convenience only.
    profile_image: Optional[str] = None


class Character(BaseModel):
    id: str
    display_name: str
    age: int = 18
    gender: str = "female"
    is_player: bool = False
    color: str = "#c8c8ff"
    appearance: Appearance = Field(default_factory=Appearance)
    outfits: list[Outfit] = Field(default_factory=list)
    default_outfit: str = ""
    personality: Personality = Field(default_factory=Personality)
    voice: VoiceCfg = Field(default_factory=VoiceCfg)
    assets: CharAssets = Field(default_factory=CharAssets)
    # Starting values for the project's character stats, by stat id. Anything
    # absent uses the StatDef's own default, so a character needs an entry here
    # only where they differ -- a childhood friend starting at affection 30.
    stat_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("age")
    @classmethod
    def _adult_floor(cls, v: int) -> int:
        # Hard 18+ floor enforced at the schema level (doc S3 'Age handling').
        # Nothing reaches the image pipeline without passing through here.
        if v is None or v < 18:
            raise ValueError("age must be 18 or older")
        return v


# --------------------------------------------------------------------------
# project-level records
# --------------------------------------------------------------------------

class Location(BaseModel):
    id: str
    name: str
    tags: str = ""
    variants: dict[str, str] = Field(default_factory=dict)   # "day"/"night" -> hash
    negative: str = ""


class VariableDef(BaseModel):
    """A story variable the author tracks, so it can be picked from a dropdown
    instead of retyped. Auto-registered from Set nodes and choice effects."""
    name: str
    type: Literal["bool", "int", "str"] = "bool"
    default: str = ""
    note: str = ""


class StatBand(BaseModel):
    """A named range on a stat, from `min` up to the next band's `min`.

    Exists so the writer can be told the *current* relationship instead of the
    day-one one, without the author having to write a paragraph per stage: the
    label alone is enough to render "Right now: warming (34/100)", and `note`
    is opt-in flavour for the characters that earn it.
    """
    min: int = 0
    label: str = ""
    note: str = ""


class StatDef(BaseModel):
    """A per-character value that persists for the whole playthrough.

    Declared once on the project and instantiated for every character as
    `{id}_{character_id}` (`affection_diana`) -- the convention hand-written
    story variables already used, so nothing reads worse in the compiled game.

    Deliberately separate from route progress even though both are ints:
    affection is *state* (how she behaves, which options unlock), progress is a
    *content pointer* (which scenes are reachable). Collapsing them makes
    "how she talks to you" a function of how many scenes you have watched,
    which turns every affection choice cosmetic.
    """
    id: str
    name: str = ""
    type: Literal["int", "bool", "str"] = "int"
    default: str = "0"
    min: Optional[int] = 0
    max: Optional[int] = 100
    note: str = ""
    bands: list[StatBand] = Field(default_factory=list)
    # Whether the writer model is told this stat's current band. Off for stats
    # that gate content but should not steer prose -- route progress being the
    # obvious one.
    in_card: bool = True

    def var_for(self, character_id: str) -> str:
        return f"{self.id}_{character_id}"

    def band_for(self, value: Any) -> Optional[StatBand]:
        """The band `value` falls in -- the last one whose `min` it clears."""
        if not self.bands:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        match = None
        for band in sorted(self.bands, key=lambda b: b.min):
            if numeric >= band.min:
                match = band
        return match


class WorldBible(BaseModel):
    setting: str = ""
    era: str = ""
    # No `rating`: content register is per scene now (SceneData.nsfw), because
    # one number over a whole story cannot describe a story with one explicit
    # scene in it. Old projects keep whatever they had in the database column;
    # nothing reads it.
    pov: str = "second person"
    tense: str = "present"
    style_notes: str = ""
    themes: str = ""


class Persona(BaseModel):
    """Who the reader is, in Roleplay mode.

    Separate from the `is_player` Character on purpose, even though it overlaps
    it. That character exists to be compiled -- it carries outfits, a sprite
    sheet it never uses, and a booru-tagged appearance for an image pipeline
    that never draws it, because in a first-person VN the player is the camera.
    What a conversation needs is the opposite: a name the cast can say out loud
    and a body they can react to.

    Empty fields fall back to the `is_player` character
    (services.roleplay.resolve_persona), so a project that has one needs to fill
    nothing in here.
    """
    name: str = ""
    # Physical description, in prose. Read by the writer model, never by the
    # image pipeline -- so it is written for a reader, not as tags.
    description: str = ""
    # Anything else that should be true of the reader in every scene: age,
    # occupation, how the cast know them. Free text, appended to the card.
    notes: str = ""


class RolePlay(BaseModel):
    """Per-project Roleplay settings.

    Here rather than in the global `settings` table because a persona belongs to
    a story: the same install running two projects has two different readers.
    The mode-wide knobs (budgets, reply length, TTS) stay in `settings` with the
    other engine configuration -- they are about this machine, not this story.
    """
    persona: Persona = Field(default_factory=Persona)


class ProjectMeta(BaseModel):
    id: str
    name: str
    world: WorldBible = Field(default_factory=WorldBible)
    variables: list[VariableDef] = Field(default_factory=list)
    # The stat schema, instantiated per character. Declared variables win on a
    # name collision (see graph.merged_flag_defaults), so a project that
    # already hand-declared `affection_diana` keeps working unchanged.
    character_stats: list[StatDef] = Field(default_factory=list)
    roleplay: RolePlay = Field(default_factory=RolePlay)
    style_tags: str = "anime style, detailed background"
    quality_prefix: str = "masterpiece, best quality, newest, absurdres, highres"
    global_negative: str = (
        "worst quality, low quality, lowres, bad anatomy, bad hands, "
        "text, watermark, signature, jpeg artifacts"
    )
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PromptTemplate(BaseModel):
    id: str
    key: str                     # e.g. "system.prose_gen"
    version: int = 1
    body: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    active: bool = True
    created_at: Optional[str] = None


# --------------------------------------------------------------------------
# roleplay (db.rp_conversations / db.rp_messages)
# --------------------------------------------------------------------------

class RPTake(BaseModel):
    """One take of a cast reply -- a SillyTavern swipe.

    Same idea as `Variant`, and deliberately not the same class: a Variant
    carries an asset hash, a GenMeta and a payload dict because it stands for an
    image or a voice clip as often as for text. A take is only ever text.
    """
    id: str
    text: str
    model: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class RPVoiceClip(BaseModel):
    """One spoken line inside a cast reply.

    `line` is the index of the dialogue line within the message, so a reply with
    three spoken lines has three clips and they play in that order. Narration is
    never voiced -- there is nobody to voice it.
    """
    line: int
    character_id: str
    hash: str
    emotion: str = "neutral"
    intensity: float = 0.5


class RPMessage(BaseModel):
    id: str
    conv_id: str
    seq: int
    role: Literal["player", "cast"]
    text: str = ""
    takes: list[RPTake] = Field(default_factory=list)
    active_take: Optional[str] = None
    voice: list[RPVoiceClip] = Field(default_factory=list)
    created_at: Optional[str] = None


class RPConversation(BaseModel):
    id: str
    project_id: str
    character_id: str
    # Everyone else in the scene. The primary character is never repeated here.
    cast: list[str] = Field(default_factory=list)
    title: str = ""
    scene_context: str = ""
    summary: str = ""
    summary_edited: bool = False
    state: Literal["open", "ended"] = "open"
    hidden: bool = False
    sort_key: float = 0.0
    location_id: Optional[str] = None
    time_of_day: Optional[str] = None
    nsfw: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def everyone(self) -> list[str]:
        """The full cast, primary first, deduped."""
        out = [self.character_id]
        out += [c for c in self.cast if c and c != self.character_id]
        return out
