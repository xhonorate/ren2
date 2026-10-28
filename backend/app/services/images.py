"""Image generation: prompt assembly + ComfyUI job execution.

The discipline that makes character consistency work: the LLM only ever writes
the *variable* parts -- shot, expression, pose, action. Identity tags come from
the character record, every time, unchanged (doc S4).
"""
from __future__ import annotations

import base64
import binascii
import copy
import random
import re
from typing import Any, Callable, Optional

from .. import store
from ..clients import comfyui
from ..defaults import (CG_SET_ACTIONS, CG_SET_COMPANY, CG_VOCAB, SHEET_FRAMINGS,
                        SHEET_SCENES, TIME_TAGS, WEATHER_TAGS)
from ..models import Character, Location, Outfit
from . import assets, context as ctx_mod, workflows

# Expression enum -> booru tags. The LLM picks the enum value; this table owns
# the tags, so a model that invents "smizing" can never reach the prompt.
def sprite_slot(outfit_id: str, pose: str, expression: str) -> str:
    """The sprite_sheet key: outfit/pose/expression.

    One definition, because the sprite job, the Ren'Py compiler, the preview
    player and batch fill all have to agree on it -- they did not before, and
    the compiler spent its whole life looking up keys nothing ever wrote.
    """
    return f"{outfit_id}/{pose or 'front'}/{expression or 'neutral'}"


def parse_sprite_slot(slot: str) -> tuple[str, str, str]:
    """(outfit, pose, expression), tolerating the pre-pose `outfit/expression`
    form so a half-migrated character still resolves."""
    parts = (slot or "").split("/")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], "front", parts[1]
    return slot or "", "front", "neutral"


# Pose enum -> booru tags. Stance and gaze both live here: the sprite wrapper
# no longer hardcodes "standing, looking at viewer", so every pose has to say
# where the body is and where the eyes go, or the model picks for itself.
POSE_TAGS: dict[str, str] = {
    "front": "standing, facing viewer, looking at viewer",
    "three_quarter": "standing, three-quarter view, looking at viewer",
    "side": "standing, from side, profile, looking to the side",
    "back": "standing, from behind, facing away, looking back",
    "arms_crossed": "standing, crossed arms, looking at viewer",
    "hands_behind_back": "standing, arms behind back, looking at viewer",
    "leaning": "leaning against wall, standing, looking at viewer",
    "sitting": "sitting, looking at viewer",
}


# Pose enum -> negative tags. Two jobs:
#
# 1. Pose adherence. A character LoRA trained on the default sheet pulls hard
#    toward front-facing standing; naming the competing cue in the negative is
#    what lets the emphasised positive pose (image.sprite_wrapper) win.
# 2. Anti-duplicate. When the prompt names a feature that can't be seen from
#    the requested angle ("back tattoo" from identity_tags on a front pose),
#    SDXL tends to satisfy it by adding a second, rear-facing copy of the
#    character. The clone is almost always the rear view, so negating the rear
#    orientation for front-facing poses suppresses it. (The proper fix is
#    per-mark body zones -- Tier 3, not yet built -- so the invisible tag never
#    reaches the prompt; this keeps the clone out until then.)
#
# "facing viewer" is body orientation, not gaze, so negating it does not break
# the "looking at viewer" the pose tags keep. Do NOT add rear negatives to the
# poses that legitimately show the back (`side`, `back`).
#
# Keep each entry SHORT. The negative prompt has a fixed attention budget, and
# the quality terms at its head ("worst quality, bad anatomy, bad hands") are
# load-bearing for anime face/eye fidelity on Illustrious checkpoints --
# piling on orientation synonyms here visibly softens faces. One rear term is
# enough; the emphasised positive pose does the rest.
_REAR = "from behind"
POSE_NEGATIVES: dict[str, str] = {
    "front": _REAR,
    "three_quarter": "front view, facing viewer",
    "side": "front view, facing viewer",
    "back": "front view, facing viewer",
    "arms_crossed": _REAR,
    "hands_behind_back": _REAR,
    "leaning": "front view, standing straight",
    "sitting": "standing",
}

# Pose enum -> tags for an *event CG*, which is a different job from a sprite.
#
# POSE_TAGS is written for a sprite: it fixes stance AND gaze ("standing,
# facing viewer, looking at viewer") because a sprite is always a front-on
# cut-out. In a CG the camera is the director's decision, so the orientation
# poses contribute nothing and the gaze would fight whatever the shot asked
# for -- only the poses that describe what the *body* is doing survive here.
#
# Before this existed the handler passed the raw enum key ("front",
# "arms_crossed") straight into the CG prompt, so most CGs carried a literal
# underscore token that is not a Danbooru tag -- dead weight in the
# conditioning, and "front" in particular nudging every shot back to front-on.
POSE_CG_TAGS: dict[str, str] = {
    "front": "",
    "three_quarter": "",
    "side": "",
    "back": "",
    "arms_crossed": "crossed arms",
    "hands_behind_back": "arms behind back",
    "leaning": "leaning against wall",
    "sitting": "sitting",
}

EXPRESSION_TAGS: dict[str, str] = {
    "neutral": "expressionless",
    "smiling": "smile",
    "grinning": "grin, open mouth",
    "laughing": "laughing, open mouth, closed eyes",
    "soft_smile": "light smile, gentle expression",
    "smirking": "smirk",
    "frowning": "frown",
    "angry": "angry, furrowed brow",
    "glaring": "glaring, angry, half-closed eyes",
    "pouting": "pout",
    "crying": "crying, tears, sad",
    "teary": "tearing up, watery eyes",
    "surprised": "surprised, open mouth, wide-eyed",
    "wide_eyed": "wide-eyed, startled",
    "blushing": "blush",
    "embarrassed": "blush, embarrassed, averting eyes",
    "looking_away": "looking away, averting eyes",
    "eyes_closed": "closed eyes",
    "sleepy": "sleepy, half-closed eyes",
    "worried": "worried, concerned",
    "thinking": "thinking, hand on chin",
    "serious": "serious, closed mouth",
}


def identity_tags(character: Character, outfit: Optional[Outfit] = None,
                  include_count: bool = True) -> str:
    """Locked identity block. Never LLM-authored -- consistency collapses.

    `include_count` drops the leading 1girl/1boy so several characters can be
    described in one prompt under a single group count (see group_count_tag).

    bust/butt/hair/eyes/etc. always render regardless of outfit -- they're
    proportions, not surface marks, so clothing over them doesn't make the
    tag wrong. Distinguishing marks and accessories are surface-level (a
    tattoo, a piercing) and can go wrong under clothing the model can't see
    through, so each outfit carries its own on/off list for them
    (Outfit.hidden_marks / .hidden_accessories, set on the Outfits card) --
    e.g. a chest tattoo turned off for an outfit with a high collar. An
    outfit with nothing hidden (every outfit saved before this existed)
    shows everything, so old data renders exactly as before.
    """
    a = character.appearance
    parts: list[str] = []
    if character.gender and include_count:
        parts.append({"female": "1girl", "male": "1boy"}.get(character.gender.lower(),
                                                             character.gender))
    # Posture is deliberately NOT here. It is a body *pose*, not an identity
    # trait, and every caller of this function already fills a pose slot of its
    # own -- the director's action on a CG, the sheet's angle, POSE_TAGS on a
    # sprite. Baking "crossed arms" into the identity block meant it fought all
    # of them, and on a character-sheet batch the LoRA learned the stance as a
    # fact about the character. It stays on the Appearance tab, where the
    # writer model reads it as characterisation (context.compose_appearance).
    for field in (a.hair_color, a.hair_style, a.eye_color, a.eye_quality, a.face,
                  a.skin_tone, a.build, a.height, a.bust, a.butt):
        if field.tag:
            parts.append(field.tag)
    hidden_marks = set(outfit.hidden_marks) if outfit else set()
    hidden_accessories = set(outfit.hidden_accessories) if outfit else set()
    for extra in a.distinguishing:
        if extra.tag and extra.label not in hidden_marks:
            parts.append(extra.tag)
    for extra in a.accessories:
        if extra.tag and extra.label not in hidden_accessories:
            parts.append(extra.tag)
    return ", ".join(dict.fromkeys(p for p in parts if p))


def drop_hidden(tag_string: str, hidden: set[str]) -> str:
    """Remove a character's CG-hidden tags from an assembled tag block.

    Matched on the tag text rather than on which field produced it, because the
    text is what reaches the prompt: shoes come from an outfit's free-text tag
    block (`brown loafers`), the chest tattoo from a distinguishing mark, and a
    per-field switch would have to be built twice and would still miss the
    outfit string. The cost is that editing a tag's text un-hides it -- the
    badge simply comes back lit in the UI, which is the same behaviour as the
    per-outfit `hidden_marks` matching by label.

    The set comes from the node, so it applies to every subject in that shot:
    "this frame does not show feet" is a fact about the frame, and a second
    character standing in it does not get shoes either.
    """
    if not hidden or not tag_string:
        return tag_string
    kept = [t for t in (part.strip() for part in tag_string.split(","))
            if t and t.lower() not in hidden]
    return ", ".join(kept)


def _tag_list(block: str) -> list[str]:
    """A comma-separated tag block as individual tags, blanks dropped."""
    return [t.strip() for t in (block or "").split(",") if t.strip()]


def hidden_set(tags: Optional[list[str]]) -> set[str]:
    """A node's CG-hidden tag list, lowercased for matching."""
    return {t.strip().lower() for t in (tags or []) if t and t.strip()}


def resolve_outfit(character: Character, outfit_id: Optional[str]) -> Optional[Outfit]:
    oid = outfit_id or character.default_outfit
    match = next((o for o in character.outfits if o.id == oid), None)
    if match is None and character.outfits:
        match = character.outfits[0]
    return match


def outfit_tags(character: Character, outfit_id: Optional[str]) -> str:
    match = resolve_outfit(character, outfit_id)
    return match.tags if match else ""


# Windows drive/UNC ("C:\..." / "\\host\...") or POSIX ("/...") absolute path.
# A subfolder relative to ComfyUI/models/loras/ (e.g. "characters/aiko.safetensors")
# does not match this and is left alone -- that form is fine.
_ABS_PATH = re.compile(r"^([A-Za-z]:[\\/]|\\\\|/)")


def lora_slot(character: Character, slot: str = "sprite") -> tuple[str, float]:
    """(lora file, weight) for a named slot, or ("", 0) when it is empty.

    "sprite" is the sheet-trained bootstrap LoRA that sprites use. "cg" is the
    optional refinement trained on a varied dataset; it is never assumed to
    exist, so a character with no CG LoRA falls back to nothing rather than
    silently to the sprite one -- a CG that quietly used the flat LoRA after
    you asked for the varied one is exactly the confusion this slot exists to
    remove. The CG slot's weight is `cg_lora_weight` -- the same weight an
    event CG actually renders at -- rather than a separate stored value,
    because nothing else ever reads a CG LoRA at any other strength.
    """
    a = character.assets
    if slot == "cg":
        return (a.lora_cg or "", a.cg_lora_weight)
    if slot == "sprite":
        return (a.lora or "", a.lora_weight)
    return "", 0.0


def cg_lora_for(character: Optional[Character], mode: str = "") -> tuple[str, float]:
    """(lora file, weight) an event CG should load. ("", 0) means none.

    Defaults to `auto` (settings `cg_lora_mode`): use the CG LoRA if the
    character has one, else the Sprite LoRA, else render with no LoRA at all.
    A CG should get the same consistency benefit a LoRA gives sprites, but a
    character that has not reached that step yet must never block generation
    -- so "not configured" degrades to "no LoRA" rather than an error. `mode`
    is the per-node override; "" defers to the setting. Explicit "sprite"/"cg"
    do NOT fall back to each other (see the `auto` tests) -- only `auto` does.

    The face-detail pass (build_workflow's face_prompt, assemble_face_prompt)
    is what keeps the pose free of the sheet's front-facing prior; it runs
    whether or not this returns a LoRA, which is what makes loading one here
    safe rather than a tradeoff against posing freedom.
    """
    if character is None:
        return "", 0.0
    mode = (mode or store.get_setting("cg_lora_mode", "auto") or "auto").strip()
    if mode == "auto":
        for slot in ("cg", "sprite"):
            name, _ = lora_slot(character, slot)
            if name:
                return name, character.assets.cg_lora_weight
        return "", 0.0
    name, _ = lora_slot(character, mode)
    if not name:
        return "", 0.0
    return name, character.assets.cg_lora_weight


def lora_fragment(character: Character, slot: str = "sprite") -> tuple[str, str, str, float]:
    """(inline lora tag, lora filename, trigger, weight)."""
    lora, weight = lora_slot(character, slot)
    if not lora:
        return "", "", character.assets.trigger or "", character.assets.lora_weight
    if _ABS_PATH.match(lora):
        base = lora.replace("\\", "/").rsplit("/", 1)[-1]
        raise RuntimeError(
            f"{character.display_name}'s LoRA path is a filesystem path ({lora}), "
            "not a name ComfyUI can use -- LoraLoader resolves names relative to "
            f"ComfyUI/models/loras/. Copy the file there and set LoRA path to just "
            f"'{base}' (a subfolder under models/loras/ is fine; an absolute path "
            "is not). This is the value lora.train writes automatically -- it "
            "still needs this one manual step. See lora-training.md \u00a78."
        )
    name = lora.split("/")[-1].rsplit(".", 1)[0]
    return (f"<lora:{name}:{weight}>", lora, character.assets.trigger or "", weight)


# Tags in the global negative that mean "exactly one person in frame". They
# have to come back out when a CG is deliberately a two-hander, or the
# negative fights the prompt and you get one character (or a mangled blend).
_SOLO_NEGATIVES = {"multiple girls", "multiple boys", "multiple views", "solo",
                   "2girls", "2boys", "duo", "crowd"}


def negative_for(project, character: Optional[Character], location: Optional[Location],
                 outfit_negative: str = "", multi: bool = False,
                 extra_negative: str = "") -> str:
    tpl = store.active_template(project.id, "image.negative_global")
    parts = [tpl.body if tpl else "", project.global_negative]
    if character:
        parts.append(character.assets.negative)
        # Inside the None guard: a CG of the place itself has no character in
        # the shot (ctx.assemble_cg's "nobody" case), and dereferencing here
        # crashed the render for exactly the establishing shots the director
        # is told it may produce.
        parts.extend(extra.tag for extra in character.appearance.negatives)
    if location:
        parts.append(location.negative)
    parts.append(outfit_negative)
    parts.append(extra_negative)
    joined = ", ".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))
    if not multi:
        return joined
    kept = [t.strip() for t in joined.split(",")
            if t.strip() and t.strip().lower() not in _SOLO_NEGATIVES]
    return ", ".join(dict.fromkeys(kept))


# --------------------------------------------------------------------------
# prompt assembly per asset class
# --------------------------------------------------------------------------

def count_tag(genders: list[str]) -> str:
    """Booru count tags for a group of bodies -- 1girl / 2girls / 1girl, 1boy.

    Computed from the whole group, because concatenation cannot express it: two
    female bodies each contributing `1girl` dedupe back to a single `1girl`,
    which tells the renderer to draw one person out of two bodies' tags. That
    is also why a tag list written by hand per recipe will not do -- see
    `company_count`.
    """
    girls = sum(1 for g in genders if (g or "").lower() == "female")
    boys = sum(1 for g in genders if (g or "").lower() == "male")
    other = [g for g in genders if g and g.lower() not in ("female", "male")]
    parts = []
    if girls:
        parts.append("1girl" if girls == 1 else f"{girls}girls")
    if boys:
        parts.append("1boy" if boys == 1 else f"{boys}boys")
    parts.extend(dict.fromkeys(other))
    return ", ".join(parts)


def group_count_tag(subjects: list[Character]) -> str:
    """Booru count tags for everyone in the shot."""
    return count_tag([c.gender for c in subjects])


# --------------------------------------------------------------------------
# the player's own body
# --------------------------------------------------------------------------
# The player is the camera, so they are never a *subject*: no LoRA, no face, no
# identity block. But a POV shot is not a shot of nobody -- an arm enters
# frame, a hand gets held, a head leans on a chest -- and that body has to be
# accounted for, because a contact tag is a request for a second figure whether
# or not the prompt describes one.
#
# Leaving it undescribed is the bug this exists for. With `1girl` and her
# `school uniform` the only identity in the prompt, the renderer has exactly
# one wardrobe to dress the second body in, and the player came back wearing
# her clothes. Naming the body (`1boy`, `faceless male`) and giving it its own
# outfit tags is what Danbooru's own POV captions do, which is also what the
# checkpoint was trained on.

#: Tags that put the player's body in frame no matter who else is in the shot.
_VIEWER_BODY_TAGS = frozenset({
    "pov hands", "hand on viewer", "straddling viewer", "sitting on viewer",
    "reaching towards viewer", "incoming hug",
})

#: Contact needs two bodies. With one subject the second is the player; with
#: two the tag is about them and the camera can stay outside the frame.
_CONTACT_TAGS = frozenset(t.lower() for t in CG_VOCAB["contact"])

#: The player's head is in frame but turned, cropped or anonymous -- the three
#: answers the director is offered. Any of them means the shot has already
#: solved the face, so nothing further is forced in.
_FACE_SOLVED_TAGS = frozenset({
    "faceless", "faceless male", "faceless female", "faceless other",
    "head out of frame", "from behind", "back focus",
})


#: `(hug:1.3)` -- the emphasis the director writes, which has to come off before
#: a tag can be matched against anything. The contact tag is the one the prompt
#: specifically tells it to weight, so the weighted form is the common case
#: here, not the exception.
_WEIGHTED_TAG = re.compile(r"^\((?P<tag>.+):(?P<weight>\d*\.?\d+)\)$")


def split_weight(text: str) -> tuple[str, Optional[float]]:
    """`(hug:1.25)` -> ("hug", 1.25); `hug` -> ("hug", None)."""
    match = _WEIGHTED_TAG.match((text or "").strip())
    if not match:
        return (text or "").strip(), None
    try:
        return match.group("tag").strip(), float(match.group("weight"))
    except ValueError:
        return (text or "").strip(), None


def shot_tag_set(*blocks: str) -> set[str]:
    """Tag blocks as bare lowercase tags, emphasis stripped."""
    out: set[str] = set()
    for block in blocks:
        for part in (block or "").split(","):
            tag, _ = split_weight(part)
            if tag:
                out.add(tag.lower())
    return out


def prompt_sentence(text: str, limit: int = 240) -> str:
    """The director's English sentence, made safe to sit in a tag prompt.

    Three things have to happen to it. Parentheses are attention syntax, so one
    left unescaped silently reweights whatever it encloses. Newlines and runs
    of whitespace collapse, because the prompt is one line. And it is capped:
    natural language is *support* here, the prompt already runs to three CLIP
    chunks, and the director is told to write "a sentence or two" -- the first
    of which is the one carrying the staging.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    if len(flat) > limit:
        # A sentence boundary if there is a reasonable one, else a word break;
        # cutting mid-word leaves a fragment that conditions on nothing.
        cut = flat.rfind(". ", 0, limit)
        flat = flat[:cut + 1] if cut > 60 else flat[:limit].rsplit(" ", 1)[0]
    return flat.replace("(", r"\(").replace(")", r"\)")


def viewer_in_frame(camera: str, action: str, subject_count: int) -> bool:
    """Whether the player's own body is inside this frame."""
    tags = shot_tag_set(camera, action)
    if tags & _VIEWER_BODY_TAGS:
        return True
    return subject_count == 1 and bool(tags & _CONTACT_TAGS)


def faceless_tag(character: Optional[Character]) -> str:
    """`faceless male` / `faceless female` for whoever may not show a face.

    Falls back to bare `faceless` for a gender the pair does not cover, which
    is a real tag and still carries the only part that matters here.
    """
    gender = (character.gender or "").strip().lower() if character else ""
    if gender in ("female", "male"):
        return f"faceless {gender}"
    return "faceless"


# --------------------------------------------------------------------------
# what the framing actually shows
# --------------------------------------------------------------------------
# A tag in a prompt is a request to find room for the thing, not a fact the
# renderer may ignore. `brown loafers` on a shot that ends at the shoulders
# does not get dropped -- it gets shoes, or a pair of feet folded into a
# corner where they cannot be. This is the "per-mark body zones" the sprite
# negatives have referred to as unbuilt since they were written.
#
# Deliberately a lookup rather than a model decision. Which parts of a body a
# `close-up` contains is a fact about the words, identical in every scene, and
# nothing an LLM should be asked (or trusted) to re-derive per shot.

#: How far down the body a framing tag reaches. Higher includes everything
#: lower-numbered: a full body shot contains the face.
CUT_HEAD, CUT_CHEST, CUT_WAIST, CUT_KNEE, CUT_FEET = 0, 1, 2, 3, 4

FRAMING_CUTOFF: dict[str, int] = {
    "close-up": CUT_HEAD, "portrait": CUT_HEAD, "face focus": CUT_HEAD,
    "headshot": CUT_HEAD, "head out of frame": CUT_CHEST,
    "upper body": CUT_CHEST, "bust": CUT_CHEST, "chest focus": CUT_CHEST,
    "cowboy shot": CUT_KNEE,
    "full body": CUT_FEET, "wide shot": CUT_FEET, "lower body": CUT_FEET,
    "foot focus": CUT_FEET, "feet": CUT_FEET, "leg focus": CUT_FEET,
    "ass focus": CUT_FEET, "from behind": CUT_FEET,
}

#: How far down the body a *tag* sits. Anything not listed is CUT_HEAD, i.e.
#: always in frame -- the filter only ever removes what it is sure about.
_ZONE_WORDS: list[tuple[int, tuple[str, ...]]] = [
    (CUT_FEET, ("shoe", "loafer", "boot", "sneaker", "heel", "sandal",
                "mary jane", "slipper", "clog", "geta", "footwear", "barefoot",
                "sock", "thighhigh", "kneehigh", "legwear", "stocking",
                "pantyhose", "tights", "leggings", "ankle", "foot", "feet",
                "toe", "toenail")),
    (CUT_KNEE, ("skirt", "shorts", "trousers", "pants", "jeans", "hakama",
                "knee", "shin", "calf", "thigh", "garter")),
    (CUT_WAIST, ("hip", "waist", "belt", "navel", "ass", "buttock", "butt",
                 "groin", "crotch", "lap")),
]


def framing_cutoff(camera: str) -> int:
    """How far down the body this shot reaches. Widest framing tag wins.

    Widest, not narrowest, because the tags routinely disagree -- a director
    writes `close-up, full body` more often than it should -- and cropping on
    the tighter reading of an ambiguous shot is the destructive mistake. With
    no framing tag at all the answer is "everything": an unframed shot has not
    said the feet are out, so nothing is dropped.
    """
    tags_in = [t.strip().lower() for t in (camera or "").split(",") if t.strip()]
    hits = [FRAMING_CUTOFF[t] for t in tags_in if t in FRAMING_CUTOFF]
    return max(hits) if hits else CUT_FEET


def tag_cutoff(tag: str) -> int:
    """How far down the body a tag sits, CUT_HEAD when it is not body-bound."""
    text = f" {(tag or '').strip().lower()} "
    for level, words in _ZONE_WORDS:
        if any(word in text for word in words):
            return level
    return CUT_HEAD


def drop_out_of_frame(tag_string: str, cutoff: int,
                      forced: Optional[set[str]] = None) -> str:
    """Remove tags for parts of the body this framing does not contain.

    `forced` is the author overruling the table for this one shot. FRAMING_CUTOFF
    is a fact about the words and right nearly always, but "nearly" is why the
    inspector offers a switch: a `close-up` composed over a shoulder really can
    show a skirt, and the table cannot know that. A tag named here survives the
    cut it would otherwise lose.
    """
    if cutoff >= CUT_FEET or not tag_string:
        return tag_string
    forced = forced or set()
    kept = [t for t in (part.strip() for part in tag_string.split(","))
            if t and (tag_cutoff(t) <= cutoff or t.lower() in forced)]
    return ", ".join(kept)


# --------------------------------------------------------------------------
# emphasis
# --------------------------------------------------------------------------
# ComfyUI's stock CLIPTextEncode parses `(tag:1.15)`, so weighting costs no
# node and no dependency. What it does need is a rule for who assigns it, and
# the answer is not the LLM: a model has no calibration for what 1.3 does to a
# sampler, and a free decimal field is one more thing for it to invent.
#
# So emphasis is assigned by what a tag *is*, using the classifier that already
# sorts the dictionary (`services.tags.categorize`). The shape of the rule: the
# thing the shot is about goes up, the things that decorate it go down, and
# identity is never touched -- it is the one block that must land exactly as
# the character record wrote it.
CATEGORY_WEIGHT: dict[str, float] = {
    "action": 1.15,        # what the bodies are doing: the subject of the shot
    "posture": 1.15,
    "perspective": 1.1,    # framing decides what kind of picture this is
    "body": 1.05,
    "gaze": 1.05,
    "light": 0.95,         # atmosphere: present, not competing
    "detail": 0.95,
    "scene": 0.9,          # the room is the backdrop, not the point
}


def weigh(tag_string: str, enabled: bool = True) -> str:
    """Wrap each tag in its category's emphasis, leaving 1.0 tags bare.

    Escaping happens first (`tags.for_prompt`): a Danbooru tag like
    `heart_(symbol)` carries parentheses that are attention syntax in a prompt,
    and weighting an unescaped one produces a nested group around the wrong
    word.
    """
    from . import tags as tag_svc

    if not tag_string:
        return tag_string
    out: list[str] = []
    for part in tag_string.split(","):
        # The director's own emphasis comes off first. Escaping a tag that
        # still carries it turns `(hug:1.25)` into the literal characters
        # `\(hug:1.25\)`, and wrapping *that* produced
        # `(\(hug:1.25\):1.15)` -- the weight the director assigned to the one
        # tag the shot fails without, rendered as prompt text instead of as
        # attention. Every weighted tag it has ever written was mangled this
        # way, which is the whole "say which part is the point" half of
        # `system.cg_director` arriving as noise.
        tag, authored = split_weight(part)
        if not tag:
            continue
        escaped = tag_svc.for_prompt(tag)
        # An emphasis written for *this* shot outranks the category default,
        # which is a rule about tags in general. Multiplying the two would
        # compound past the point where SDXL distorts anatomy.
        if authored is not None:
            weight = max(0.5, min(1.4, authored))
        elif enabled:
            weight = CATEGORY_WEIGHT.get(tag_svc.categorize(tag), 1.0)
        else:
            weight = 1.0
        out.append(f"({escaped}:{weight:g})" if weight != 1.0 else escaped)
    return ", ".join(out)


def assemble_cg_prompt(project, subjects: list[Character], state,
                       locations: dict[str, Location], camera: str = "",
                       expression: str = "neutral", action: str = "",
                       extra_negative: str = "", lora_mode: str = "",
                       shot_hint: str = "",
                       hidden: Optional[set[str]] = None,
                       viewer: Optional[Character] = None,
                       description: str = "",
                       forced: Optional[set[str]] = None) -> dict[str, Any]:
    """Event CG for whoever is actually on stage.

    `subjects` is every character the shot should contain, already filtered by
    the caller (no player character -- the PC is the camera in a first/second
    person VN, and rendering their face is the single most common way a CG
    comes out wrong). An empty list is legitimate: a narration node describing
    only the room should render the room.

    `hidden` and `forced` are the two per-shot switches, and they pull opposite
    ways: `hidden` takes a tag out that the framing would have kept, `forced`
    keeps one the framing would have cut. Both are properties of this frame
    rather than of the character, which is why they live on the node -- the same
    character two beats later in a full-body shot wants her shoes back.

    `viewer` is the player character, and is *not* a subject: it contributes no
    identity, no face, no LoRA. It is consulted only when the shot's own tags
    say the player's body is in the frame (`viewer_in_frame`) -- a hug, a held
    hand, `pov hands` -- and then only for a count, a `faceless` tag and their
    own outfit, so the body the contact tag demands has something to bind to
    other than the other character's wardrobe.

    `camera` and `action` are the two halves of the distilled prompt the CG
    director writes (services/generation.cg_director_pass), editable per node in
    the inspector. They are separate fields because they go to opposite ends of
    the wrapper: framing decides what kind of picture this is and belongs up
    front, while body/contact tags belong after the identity block, where they
    stop competing with the face and eye tags for attention. `shot_hint` is the
    fallback for a node the director has never run on -- the segmenter's rough
    English note, which is what every CG used to be built from.

    `lora_mode` is the per-node LoRA override ("" defers to `cg_lora_mode`).
    """
    tpl = store.active_template(project.id, "image.positive_wrapper")
    style = store.active_template(project.id, "image.style_prefix")
    loc = locations.get(state.location or "")
    loc_tags, lighting = ctx_mod.location_tags(loc, state.time_of_day, state.weather)

    # The LoRA loader only takes one file, so only the focal character's can be
    # applied; the rest are carried by their tag blocks alone. A CG runs it at
    # the lower cg_lora_weight, or not at all -- see cg_lora_for.
    focal = subjects[0] if subjects else None
    lora_name, lora_weight = cg_lora_for(focal, lora_mode)
    lora_tag = ""
    trigger = focal.assets.trigger if focal else ""
    if focal and lora_name:
        stem = lora_name.split("/")[-1].rsplit(".", 1)[0]
        lora_tag = f"<lora:{stem}:{lora_weight}>"

    hidden = hidden or set()
    shot = camera.strip() or shot_hint.strip()
    # What this framing actually contains. Everything below the cut is dropped
    # from identity and outfit before either reaches the prompt -- see
    # `framing_cutoff`. The per-shot switches the inspector offers are the
    # manual override for where this table is wrong.
    cutoff = framing_cutoff(shot)
    weights = bool(store.get_setting("cg_tag_weights", True))

    # The player's body, when the shot's own tags put it in frame. Counted with
    # everyone else (one group count, or the renderer is told to draw one person
    # out of two casts) and refused a face in the same breath -- unless the
    # director has already solved the face its own way, with `from behind` or
    # `head out of frame`, in which case a second answer would only argue with
    # the first. A player the author cast as a subject by hand is not the
    # viewer any more and is left alone.
    cast_ids = {c.id for c in subjects}
    viewer = viewer if viewer is not None and viewer.id not in cast_ids else None
    show_viewer = viewer is not None and viewer_in_frame(shot, action, len(subjects))
    bodies = subjects + ([viewer] if show_viewer else [])

    identity_parts: list[str] = [group_count_tag(bodies)] if bodies else []
    if show_viewer and not shot_tag_set(shot, action) & _FACE_SOLVED_TAGS:
        identity_parts.append(faceless_tag(viewer))
    outfit_parts: list[str] = []
    # What each subject contributes, before and after the shot's own switches.
    # Returned alongside the prompt so the inspector can list exactly these as
    # toggles -- built here rather than recomputed there, because a switchboard
    # offering a tag the renderer does not emit is worse than none.
    forced = forced or set()
    cast: list[dict[str, Any]] = []
    for c in subjects:
        outfit = resolve_outfit(c, state.outfits.get(c.id))
        identity = identity_tags(c, outfit, include_count=False)
        outfit_tags = outfit.tags if outfit and outfit.tags else ""
        identity_parts.append(
            drop_out_of_frame(drop_hidden(identity, hidden), cutoff, forced))
        if outfit_tags:
            outfit_parts.append(
                drop_out_of_frame(drop_hidden(outfit_tags, hidden), cutoff, forced))
        cast.append({
            "character_id": c.id,
            "name": c.display_name,
            "identity": _tag_list(identity),
            "outfit": _tag_list(outfit_tags),
            # What the framing took out on its own, so the inspector can say so
            # rather than leaving the author wondering where the shoes went --
            # minus anything they have since put back by hand, which is in the
            # prompt and must not be listed as missing from it.
            "out_of_frame": [t for t in _tag_list(f"{identity}, {outfit_tags}")
                             if tag_cutoff(t) > cutoff and t.lower() not in forced],
        })

    # Their clothes, and nothing else of them. An arm reaching into frame is
    # wearing a sleeve, and if the prompt does not say whose the only sleeve on
    # offer is hers. Their identity block is deliberately skipped: hair, eyes
    # and face are what the camera cannot see.
    if show_viewer:
        worn = resolve_outfit(viewer, state.outfits.get(viewer.id))
        if worn and worn.tags:
            worn_tags = drop_out_of_frame(
                drop_hidden(worn.tags, hidden), cutoff, forced)
            if worn_tags:
                outfit_parts.append(worn_tags)

    positive = ctx_mod.render(
        tpl.body if tpl else "",
        quality_prefix=project.quality_prefix,
        style_tags=(style.body if style else project.style_tags),
        shot=weigh(shot, weights),
        lora=lora_tag,
        trigger=trigger if lora_tag else "",
        # Identity is never weighted: it is the one block that has to land
        # exactly as the character record wrote it.
        identity=", ".join(p for p in identity_parts if p),
        outfit=", ".join(p for p in outfit_parts if p),
        expression=EXPRESSION_TAGS.get(expression, expression or ""),
        pose=weigh(action.strip(), weights),
        location=weigh(loc_tags, weights),
        lighting=weigh(lighting, weights),
        # Never weighted and never escaped: it is a sentence, not a tag, and
        # the parentheses it may contain are its own punctuation.
        description=prompt_sentence(description) if store.get_setting(
            "cg_prompt_description", True) else "",
    )
    # `multi` counts bodies, not subjects. The global negative carries `2girls`
    # and `multiple girls` to hold an ordinary CG to one figure, and a POV
    # embrace is a two-body shot whose second body is the camera -- leaving
    # those in would have the negative fighting the count tag the positive just
    # computed, which costs either the embrace or the anatomy.
    return {"positive": _tidy(positive),
            "negative": negative_for(project, focal, loc, multi=len(bodies) > 1,
                                     extra_negative=extra_negative),
            "cast": cast}


def assemble_face_prompt(project, character: Character, state,
                         expression: str = "neutral",
                         hidden: Optional[set[str]] = None) -> dict[str, str]:
    """Prompt for the face-detail pass: identity and expression, nothing else.

    The detail pass re-renders only the face crop, so framing, location,
    lighting and body tags are all noise there -- and this is where the LoRA
    runs at full strength (`face_lora_weight`). That split is the point: the
    main pass gets a low or absent LoRA so the pose is free, and this pass puts
    the likeness back on the one region where the LoRA was actually reliable.
    """
    style = store.active_template(project.id, "image.style_prefix")
    outfit = resolve_outfit(character, state.outfits.get(character.id) if state else None)
    lora_name, _ = lora_slot(character, "cg" if character.assets.lora_cg else "sprite")
    weight = character.assets.face_lora_weight
    lora_tag = ""
    if lora_name:
        stem = lora_name.split("/")[-1].rsplit(".", 1)[0]
        lora_tag = f"<lora:{stem}:{weight}>"
    # Same hiding as the main CG pass: this crop is part of the same picture,
    # and a tag suppressed in one half and not the other is how the detailer
    # ends up arguing with the render underneath it.
    parts = [project.quality_prefix, (style.body if style else project.style_tags),
             "face focus, detailed face", lora_tag,
             character.assets.trigger if lora_tag else "",
             drop_hidden(identity_tags(character, outfit), hidden or set()),
             EXPRESSION_TAGS.get(expression, expression or "")]
    return {"positive": _tidy(", ".join(p for p in parts if p)),
            "negative": negative_for(project, character, None)}


def assemble_sprite_prompt(project, character: Character, outfit_id: str,
                           expression: str, pose: str = "front",
                           extra_positive: str = "", extra_negative: str = "",
                           angle: str = "", wrapper: str = "image.sprite_wrapper",
                           framing: str = "", scene: str = "") -> dict[str, str]:
    """`extra_positive` / `extra_negative` are free-text tweaks a person types
    when regenerating one slot that came out wrong ("hand on hip", "no jacket").
    Appended after the structured block and deduped by `_tidy` -- they never
    displace identity, pose or the LoRA trigger, and are not persisted onto the
    character, so they apply to this one job only.

    `angle` is the character-sheet path: a raw view tag ("from behind, looking
    back") that fills the pose slot verbatim instead of a POSE_TAGS lookup, so
    it gets the same (:1.25) emphasis the enum poses get. When set, the
    front-view default and POSE_NEGATIVES are both skipped -- the sheet needs
    the angle to vary freely across the batch, not fight a baked-in "facing
    viewer" (which, appended after an emphasised front pose, is what used to
    flatten every sheet image to front-on).

    `wrapper` picks the template. The character sheet passes
    `image.sheet_wrapper` and fills `framing`/`scene`, because it is building a
    LoRA *dataset*, not sprites: sharing the sprite wrapper meant every
    training image was `full body, simple background, white background`, and a
    LoRA learns that as a fact about the character (lora-training.md S4)."""
    tpl = store.active_template(project.id, wrapper)
    style = store.active_template(project.id, "image.style_prefix")
    lora_tag, _, trigger, _ = lora_fragment(character)
    outfit = resolve_outfit(character, outfit_id)
    positive = ctx_mod.render(
        tpl.body if tpl else "",
        quality_prefix=project.quality_prefix,
        style_tags=(style.body if style else project.style_tags),
        lora=lora_tag,
        trigger=trigger,
        identity=identity_tags(character, outfit),
        outfit=outfit.tags if outfit else "",
        pose=angle.strip() or POSE_TAGS.get(pose, POSE_TAGS["front"]),
        expression=EXPRESSION_TAGS.get(expression, expression),
        framing=framing,
        scene=scene,
    )
    if extra_positive.strip():
        positive = f"{positive}, {extra_positive.strip()}"
    pose_negative = "" if angle.strip() else POSE_NEGATIVES.get(pose, "")
    pose_negative = ", ".join(p for p in (pose_negative, extra_negative.strip()) if p)
    return {"positive": _tidy(positive),
            "negative": negative_for(project, character, None,
                                     outfit.negative if outfit else "",
                                     extra_negative=pose_negative)}


def portrait_lora(character: Character) -> tuple[str, float]:
    """(lora file, weight) a profile headshot should load, or ("", 0) for none.

    A headshot is all face, so it wants the strongest likeness available: the
    CG LoRA if the character has one (varied dataset, the better all-round
    fit), else the Sprite LoRA, else nothing. Run at `face_lora_weight` -- the
    same weight the CG face-detail pass uses, and for the same reason: the
    whole frame is the region that pass would have re-rendered.
    """
    name, _ = lora_slot(character, "cg" if character.assets.lora_cg else "sprite")
    return (name, character.assets.face_lora_weight) if name else ("", 0.0)


def assemble_portrait_prompt(project, character: Character, expression: str = "neutral",
                             outfit_id: Optional[str] = None) -> dict[str, str]:
    """A profile headshot for the character overview grid.

    Not a sprite (it never reaches the game and wants a portrait crop, not a
    full-body cut-out) and not an event CG (no scene, no director, no path
    state), so it gets its own small assembly: identity tags plus one
    expression, framed close, on a plain background. The expression is the
    caller's pick -- a soft smile or a glare conveys more of who the character
    is at a glance than a neutral face.
    """
    style = store.active_template(project.id, "image.style_prefix")
    outfit = resolve_outfit(character, outfit_id)
    lora_name, weight = portrait_lora(character)
    lora_tag = ""
    if lora_name:
        stem = lora_name.split("/")[-1].rsplit(".", 1)[0]
        lora_tag = f"<lora:{stem}:{weight}>"
    parts = [
        project.quality_prefix,
        (style.body if style else project.style_tags),
        "solo, portrait, upper body, looking at viewer, simple background",
        lora_tag,
        character.assets.trigger if lora_tag else "",
        identity_tags(character, outfit),
        outfit.tags if outfit else "",
        EXPRESSION_TAGS.get(expression, expression or ""),
    ]
    return {"positive": _tidy(", ".join(p for p in parts if p)),
            "negative": negative_for(project, character, None,
                                     outfit.negative if outfit else "")}


def assemble_background_prompt(project, location: Location, time_of_day: str = "",
                               weather: str = "") -> dict[str, str]:
    tpl = store.active_template(project.id, "image.background_wrapper")
    style = store.active_template(project.id, "image.style_prefix")
    positive = ctx_mod.render(
        tpl.body if tpl else "",
        quality_prefix=project.quality_prefix,
        style_tags=(style.body if style else project.style_tags),
        # Shared with the CG path, so one untagged location cannot mean a
        # described background and a location-less CG.
        location=ctx_mod.location_subject(location),
        lighting=TIME_TAGS.get(time_of_day, ""),
        weather=WEATHER_TAGS.get(weather, ""),
    )
    return {"positive": _tidy(positive),
            "negative": negative_for(project, None, location)}


def _tidy(prompt: str) -> str:
    parts = [p.strip() for p in prompt.replace("\n", " ").split(",")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def _settings_bundle() -> dict[str, Any]:
    node_classes = dict(workflows.DEFAULT_NODE_CLASSES)
    node_classes.update(store.get_setting("node_classes", {}) or {})
    return {
        "checkpoint": store.get_setting("image_checkpoint", "waiILLUSTRIOUS_v140.safetensors"),
        "steps": int(store.get_setting("image_steps", 28)),
        "cfg": float(store.get_setting("image_cfg", 5.0)),
        "sampler": store.get_setting("image_sampler", "euler"),
        "scheduler": store.get_setting("image_scheduler", "normal"),
        **node_classes,
    }


def _rewire(workflow: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    """Repoint every input link from node `old` to node `new`, slot for slot."""
    out: dict[str, Any] = {}
    for nid, node in workflow.items():
        inputs = {}
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and value[0] == old:
                inputs[key] = [new, value[1]]
            else:
                inputs[key] = value
        out[nid] = {**node, "inputs": inputs}
    return out


def get_workflow(name: str) -> dict[str, Any]:
    stored = (store.get_setting("workflows", {}) or {}).get(name)
    return copy.deepcopy(stored) if stored else copy.deepcopy(workflows.DEFAULT_WORKFLOWS[name])


def build_workflow(prompt: dict[str, str], *, width: int, height: int, seed: int,
                   prefix: str, lora: str = "", lora_weight: float = 0.7,
                   sprite: bool = False, rembg_available: bool = True,
                   face_prompt: Optional[dict[str, str]] = None,
                   face_lora: str = "", face_lora_weight: float = 0.9,
                   batch: int = 1) -> dict[str, Any]:
    """`face_prompt` appends the CG face-detail pass (workflows.CG_FACE_SUFFIX).

    It carries its own LoRA and weight because that is the whole point of the
    pass: the main render runs the character LoRA weak or not at all so the
    pose is free, and the face crop is re-rendered with it strong so the
    likeness comes back.

    `batch` samples that many images from one run. It is the latent's
    batch_size rather than N copies of the sampler graph, because a workflow is
    a template the author can edit on the config page and duplicating nodes
    inside an edited one is how you break it. The trade is that batch index i
    is not reproducible on its own: ComfyUI draws the whole batch's noise from
    one generator, so re-running at batch 1 with the same seed gives image 0,
    not image i. Takes record the index for that reason.
    """
    base = get_workflow("txt2img" if lora else "txt2img_no_lora")
    if sprite:
        suffix = get_workflow("sprite_suffix")
        if rembg_available:
            base = {**base, **suffix}
            base.pop("8", None)   # the transparent save replaces the flat one
    if face_prompt:
        suffix = get_workflow("cg_face_suffix")
        if not face_lora:
            # No LoRA for this character (the common case now that CGs default
            # to none): a LoraLoader with an empty lora_name is a hard ComfyUI
            # error, so drop it and hang the pass straight off the checkpoint.
            # The detail pass still earns its place -- it is a second, tighter
            # sampling of the face at high guide_size, which is where SDXL's
            # anime face quality actually comes from at CG resolutions.
            suffix.pop("30", None)
            suffix = _rewire(suffix, "30", "1")
        base = {**base, **suffix}
        base.pop("8", None)       # the detailed save replaces the flat one

    values = {
        **_settings_bundle(),
        "positive": prompt["positive"],
        "negative": prompt["negative"],
        "width": width,
        "height": height,
        "seed": seed,
        "prefix": prefix,
        "lora_name": lora,
        "lora_weight": lora_weight,
        "face_positive": (face_prompt or {}).get("positive", ""),
        "face_negative": (face_prompt or {}).get("negative", ""),
        "face_lora_name": face_lora,
        "face_lora_weight": face_lora_weight,
        # A fixed offset from the main seed, so re-rolling the CG re-rolls the
        # face with it and a locked seed still reproduces the whole image.
        "face_seed": (seed + 1) % (2 ** 31 - 1),
        "face_denoise": float(store.get_setting("cg_face_denoise", 0.45)),
        "batch_size": max(1, int(batch)),
    }
    filled = workflows.fill(base, values)
    # Templates saved before batching existed have a literal 1 where the token
    # now is, and the config page's whole point is that you can replace these
    # graphs -- so the caller's batch is stamped onto every latent rather than
    # left to depend on which version of the template is stored.
    if max(1, int(batch)) != 1:
        for node in filled.values():
            if "batch_size" in (node.get("inputs") or {}):
                node["inputs"]["batch_size"] = max(1, int(batch))
    return filled


#: How far a refinement may go. Above this the source image stops being a
#: source -- at denoise 1.0 the sampler starts from pure noise and img2img is
#: just txt2img with extra steps, which is a confusing thing for a slider
#: labelled "adherence" to be able to do.
MAX_REFINE_DENOISE = 0.95


def build_img2img_workflow(prompt: dict[str, str], *, source_image: str, seed: int,
                           prefix: str, denoise: float, lora: str = "",
                           lora_weight: float = 0.7, mask_image: str = "",
                           batch: int = 1) -> dict[str, Any]:
    """Refine an existing render instead of starting from noise.

    `source_image` and `mask_image` are filenames already uploaded into
    ComfyUI's input directory, not paths or hashes -- `LoadImage` resolves them
    there and nowhere else.

    With no mask the three mask nodes come out and the sampler is repointed at
    the batch node. Leaving them in with an empty filename is not an option:
    `LoadImage` fails the whole graph on a file it cannot find, so an unmasked
    refinement would error rather than refine.

    No width or height: the size comes from the source through `VAEEncode`, so
    a refinement always lands at the original's dimensions rather than at
    whatever the CG settings happen to say now.
    """
    base = get_workflow("img2img" if lora else "img2img_no_lora")
    if not mask_image:
        for nid in ("11", "12", "13"):
            base.pop(nid, None)
        base = _rewire(base, "13", "14")

    values = {
        **_settings_bundle(),
        "positive": prompt["positive"],
        "negative": prompt["negative"],
        "seed": seed,
        "prefix": prefix,
        "lora_name": lora,
        "lora_weight": lora_weight,
        "source_image": source_image,
        "mask_image": mask_image,
        "denoise": clamp_denoise(denoise),
        "batch_size": max(1, int(batch)),
    }
    filled = workflows.fill(base, values)
    if max(1, int(batch)) != 1:
        for node in filled.values():
            if "amount" in (node.get("inputs") or {}):
                node["inputs"]["amount"] = max(1, int(batch))
    return filled


async def stage_image(data: bytes, name: str) -> str:
    """Put bytes in ComfyUI's input dir and return the filename LoadImage wants.

    `LoadImage` reads from that directory and nowhere else, and our renders live
    in the content-addressed store -- so anything a graph loads has to be pushed
    across first. The same staging the TTS reference clip does.

    ComfyUI can answer with a subfolder of its own; the returned name carries it
    the way LoadImage expects, or a graph that uploaded fine still fails to
    find the file.
    """
    result = await comfyui.upload_image(data, name)
    stored = (result or {}).get("name") or name
    subfolder = (result or {}).get("subfolder") or ""
    return f"{subfolder}/{stored}" if subfolder else stored


async def stage_asset(asset_hash: str, name: str) -> str:
    """Stage a stored render into ComfyUI by its content hash."""
    path = assets.path_for(asset_hash)
    if path is None or not path.exists():
        raise RuntimeError(f"no stored image for {asset_hash[:12]} to refine")
    return await stage_image(path.read_bytes(), name)


def decode_mask(data_url: str) -> bytes:
    """The painted region, as PNG bytes.

    Arrives as a data URL because it is drawn on a canvas in the browser and a
    data URL is what `toDataURL` hands back. White is the region to regenerate;
    everything black is carried through the sampler untouched.
    """
    raw = (data_url or "").strip()
    if not raw:
        return b""
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("the painted region was not a readable PNG") from exc


def clamp_denoise(value: Any) -> float:
    """A refinement's denoise, held inside the range where it means something.

    The floor is not zero: at 0 the sampler returns the source unchanged, which
    looks like a broken render rather than a deliberate no-op.
    """
    try:
        denoise = float(value)
    except (TypeError, ValueError):
        denoise = 0.45
    return max(0.05, min(MAX_REFINE_DENOISE, denoise))


async def rembg_available() -> bool:
    classes = dict(workflows.DEFAULT_NODE_CLASSES)
    classes.update(store.get_setting("node_classes", {}) or {})
    try:
        info = await comfyui.object_info()
    except Exception:  # noqa: BLE001
        return False
    # Every node the sprite suffix needs: the remover, the session loader it
    # consumes, and the invert/join pair that turns its (RGB, MASK) pair back
    # into a transparent RGBA PNG (workflows.SPRITE_SUFFIX). Missing any of
    # them means the transparent path can't run, so fall back to the flat one.
    needed = [classes.get(k, "") for k in
              ("rembg_class", "rembg_session_class", "rembg_invert_class", "rembg_join_class")]
    return all((not name) or name in info for name in needed) and bool(needed[0])


async def face_detail_available() -> bool:
    """Whether this ComfyUI has the Impact Pack nodes the CG face pass needs.

    Same contract as rembg_available: checked before the job builds its
    workflow, so a missing custom node degrades to a plain CG with a note
    rather than a 400 from ComfyUI halfway through a batch.
    """
    classes = dict(workflows.DEFAULT_NODE_CLASSES)
    classes.update(store.get_setting("node_classes", {}) or {})
    try:
        info = await comfyui.object_info()
    except Exception:  # noqa: BLE001
        return False
    needed = [classes.get(k, "") for k in ("facedetailer_class", "detector_provider_class")]
    return all(bool(name) and name in info for name in needed)


async def run_and_store(workflow: dict[str, Any], on_progress=None,
                        meta: Optional[dict[str, Any]] = None,
                        meta_fn: Optional[Callable[[int], dict[str, Any]]] = None
                        ) -> list[str]:
    """Run a workflow, pull every output image, store content-addressed.

    `meta_fn` takes the batch index and returns that image's meta, for a run
    that produces several images which are not interchangeable -- a batch of
    CG candidates differs by batch index, and stamping them all with the same
    record would make the gallery unable to say which one you are looking at.
    """
    entry = await comfyui.run_workflow(workflow, on_progress=on_progress)
    hashes: list[str] = []
    for index, image in enumerate(comfyui.collect_images(entry)):
        data = await comfyui.view(image["filename"], image["subfolder"], image["type"])
        hashes.append(assets.put(data, kind="img", ext="png",
                                 meta=meta_fn(index) if meta_fn else (meta or {})))
    if not hashes:
        raise RuntimeError("ComfyUI finished but produced no images -- check the workflow's "
                           "SaveImage node")
    return hashes


def new_seed(locked: Optional[int] = None) -> int:
    return int(locked) if locked not in (None, "", 0, -1) else random.randint(1, 2**31 - 1)


def sheet_plan(angles: list[str], expressions: list[str],
               outfit_ids: Optional[list[str]] = None, count: int = 30,
               seed: Optional[int] = None) -> list[dict[str, str]]:
    """`count` sampled outfit/angle/expression combinations for the sheet.

    Deliberately NOT the full product: outfits x angles x expressions grows
    without anyone noticing (3 outfits x 6 angles x 5 expressions is 90
    images, and that is a modest character), while a LoRA wants 15-30 good
    images rather than every cell of a grid.

    Sampling is exhaustive-first: every distinct combination is used once
    before anything repeats, so a small grid still comes out evenly covered
    and only a `count` larger than the grid repeats at all. Seeded, so the
    same seed reproduces the same plan.
    """
    combos = [{"outfit_id": o, "angle": a, "expression": e}
              for o in (outfit_ids or [""]) for a in angles for e in expressions]
    if not combos or count < 1:
        return []
    rng = random.Random(seed)
    plan: list[dict[str, str]] = []
    while len(plan) < count:
        batch = combos[:]
        rng.shuffle(batch)
        plan.extend(batch[: count - len(plan)])
    return plan


def cg_set_plan(count: int, *, angles: list[str], expressions: list[str],
                outfit_ids: list[str], actions: list[str], company: list[str],
                framings: list[str], seed: Optional[int] = None
                ) -> list[dict[str, str]]:
    """`count` independently-sampled recipes for the CG training set.

    Independently, not as a product: these axes multiplied out is tens of
    thousands of cells while the dataset only ever wants a few dozen images,
    so each axis is drawn on its own per image. That covers the space far
    better at this sample size than walking any grid would.

    The scene (backdrop / location) is picked by the caller, since it needs
    the project's locations and time-of-day tags; everything else is here.
    """
    rng = random.Random(seed)

    def pick(options: list[str], fallback: list[str]) -> str:
        pool = [o for o in (options or fallback) if o]
        return rng.choice(pool) if pool else ""

    return [{
        "outfit_id": pick(outfit_ids, [""]),
        "angle": pick(angles, DEFAULT_SHEET_ANGLES),
        "expression": pick(expressions, DEFAULT_SHEET_EXPRESSIONS),
        "action": pick(actions, CG_SET_ACTIONS),
        "company": pick(company, CG_SET_COMPANY),
        "framing": pick(framings, SHEET_FRAMINGS),
    } for _ in range(max(0, count))]


#: The body a company entry puts in frame beside the subject, and its gender.
#: Faceless on purpose, so the only thing the entry has to declare is which
#: half of the pair the director will later ask for by name.
_COMPANY_GENDER = {"faceless male": "male", "faceless female": "female"}


def company_genders(company: str) -> list[str]:
    """The genders of the bodies a company entry adds beside the subject."""
    tags = shot_tag_set(company)
    return [gender for tag, gender in _COMPANY_GENDER.items() if tag in tags]


def company_count(character: Character, company: str) -> str:
    """The count tag for a CG-set frame: the subject plus whoever joins them.

    Worked out here rather than written into each `CG_SET_COMPANY` entry, which
    is what the entries used to be expected to do. A hardcoded `1girl, 1boy`
    is only right for a female subject, and the case it cannot express at all
    is a female subject beside a faceless *female* partner -- that is `2girls`,
    and no arrangement of per-body `1girl` tags adds up to it (see `count_tag`).
    """
    return count_tag([character.gender] + company_genders(company))


def assemble_cg_set_prompt(project, character: Character, outfit_id: str,
                           expression: str, angle: str = "", action: str = "",
                           company: str = "solo", framing: str = "",
                           scene: str = "") -> dict[str, str]:
    """One CG-LoRA training image (image.cg_set_wrapper).

    Separate from assemble_sprite_prompt because it builds the opposite kind
    of picture: varied framing and backdrop, the character mid-action, and
    often not alone.

    `company` names who else is in frame, and the count for the whole frame is
    computed from it (`company_count`). It also has to reach `negative_for`:
    the global negative's solo terms ("multiple girls", "2girls", ...) would
    otherwise fight every non-solo image in the set. The identity block drops
    its own 1girl/1boy on a company shot so there is exactly one count, and
    that one describes every body in the picture.
    """
    tpl = store.active_template(project.id, "image.cg_set_wrapper")
    style = store.active_template(project.id, "image.style_prefix")
    lora_tag, _, trigger, _ = lora_fragment(character)
    outfit = resolve_outfit(character, outfit_id)
    solo = "solo" in company
    counts = "" if solo else company_count(character, company)
    positive = ctx_mod.render(
        tpl.body if tpl else "",
        quality_prefix=project.quality_prefix,
        style_tags=(style.body if style else project.style_tags),
        company=", ".join(p for p in (counts, company) if p),
        framing=framing,
        lora=lora_tag,
        trigger=trigger,
        identity=identity_tags(character, outfit, include_count=solo),
        outfit=outfit.tags if outfit else "",
        pose=angle,
        action=action,
        expression=EXPRESSION_TAGS.get(expression, expression),
        scene=scene,
    )
    return {"positive": _tidy(positive),
            "negative": negative_for(project, character, None,
                                     outfit.negative if outfit else "",
                                     multi=not solo)}


# Every entry has to name BODY orientation, not just gaze. "front view" is not
# a tag the anime checkpoints were trained on, so an emphasised
# "(front view, looking at viewer:1.25)" landed as gaze alone: the head turned
# to camera on a body still angled away, which is a three-quarter shot, not the
# front view the sheet asked for. "facing viewer" is the tag that moves the
# body, and it is what POSE_TAGS["front"] has always used for sprites.
DEFAULT_SHEET_ANGLES = [
    "facing viewer, looking at viewer",
    "from side, profile",
    "from behind, looking back",
    "three-quarter view",
    "from above",
    "from below",
]

DEFAULT_SHEET_EXPRESSIONS = ["neutral", "smiling", "angry", "crying", "surprised"]
