"""JSON schema for the structuring pass.

LM Studio enforces this at the token level via llama.cpp grammar sampling, so
`emotion` and `expression` always come back as valid enum values that image and
TTS generation can consume directly (doc S2).
"""
from __future__ import annotations

from typing import Any, Optional

from ..models import EMOTIONS, EXPRESSIONS, POSES


def scene_schema(character_ids: list[str], location_ids: list[str],
                 outfit_ids: Optional[list[str]] = None,
                 time_of_day: Optional[list[str]] = None,
                 variables: Optional[list[str]] = None) -> dict[str, Any]:
    speakers = character_ids or ["narrator"]
    locations = location_ids or ["unspecified"]
    # Flag writes are restricted to variables the project has declared. An
    # invented variable name silently becomes a new `default` in the compiled
    # game and nothing ever reads it, so it is worse than no flag at all.
    variables = variables or []
    assignment = {
        "type": "object",
        "properties": {
            "var": {"type": "string", "enum": variables},
            "op": {"type": "string", "enum": ["=", "+=", "-="]},
            "value": {"type": "string"},
        },
        "required": ["var", "op", "value"],
        "additionalProperties": False,
    }
    # Constrained so the model can't invent an outfit id that build_nodes would
    # silently drop -- a hallucinated outfit used to mean the character just
    # stayed in whatever they were wearing, with no error anywhere.
    outfits = outfit_ids or []
    outfit_value = {"type": "string", "enum": outfits} if outfits else {"type": "string"}

    # Segmentation only. `expressions` / `poses` used to live on the stage node
    # and `expression` / `emotion` / `intensity` / `delivery` / `pose` /
    # `shot_hint` / `cg` on the say node -- the writer now marks all of that on
    # the line as it writes it (generation.extract_performance). A cold model
    # choosing from an enum per passage, with no view of what it assigned three
    # nodes ago, is what made every line come back `neutral`.
    stage_node = {
        "type": "object",
        "properties": {
            "type": {"const": "stage"},
            "location": {"type": "string", "enum": locations},
            "time_of_day": ({"type": "string", "enum": time_of_day} if time_of_day
                            else {"type": "string"}),
            "weather": {"type": "string"},
            "present": {"type": "array", "items": {"type": "string", "enum": speakers}},
            "hide": {"type": "array", "items": {"type": "string", "enum": speakers}},
            "outfits": {"type": "object", "additionalProperties": outfit_value},
        },
        "required": ["type", "present"],
        "additionalProperties": False,
    }
    narration_node = {
        "type": "object",
        "properties": {
            "type": {"const": "narration"},
            "text": {"type": "string"},
        },
        "required": ["type", "text"],
        "additionalProperties": False,
    }
    say_node = {
        "type": "object",
        "properties": {
            "type": {"const": "say"},
            "speaker": {"type": "string", "enum": speakers},
            "text": {"type": "string"},
        },
        "required": ["type", "speaker", "text"],
        "additionalProperties": False,
    }

    set_node = {
        "type": "object",
        "properties": {
            "type": {"const": "set"},
            "assignments": {"type": "array", "minItems": 1, "items": assignment},
        },
        "required": ["type", "assignments"],
        "additionalProperties": False,
    }

    node_kinds = [stage_node, narration_node, say_node]
    option_props: dict[str, Any] = {
        "label": {"type": "string"},
        "branch_hint": {"type": "string"},
    }
    # Only offer flag machinery when the project has variables to write; an
    # empty enum makes the grammar unsatisfiable.
    if variables:
        node_kinds.append(set_node)
        option_props["effects"] = {"type": "array", "items": assignment}
        option_props["condition"] = {"type": "string"}

    return {
        "type": "object",
        "properties": {
            "scene_title": {"type": "string"},
            "nodes": {
                "type": "array",
                "minItems": 1,
                "items": {"anyOf": node_kinds},
            },
            "choice": {
                "type": ["object", "null"],
                "properties": {
                    "prompt": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": option_props,
                            "required": ["label", "branch_hint"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["prompt", "options"],
                "additionalProperties": False,
            },
        },
        "required": ["scene_title", "nodes"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------
# casting
# --------------------------------------------------------------------------

def casting_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["line", "description"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------
# CG director
# --------------------------------------------------------------------------

def cg_prompt_schema(nsfw: bool = False) -> dict[str, Any]:
    """The director's output: one English sentence, then tags for four slots.

    **Why there is no `enum` here any more.** There used to be one, and it was
    the right call at the time: with free-form strings, 47% of the tag uses
    across 44 directed nodes were tags that do not exist -- `warm atmosphere`
    eleven times, `gentle embrace` eight. A grammar-level enum made that
    impossible rather than discouraged.

    It bought that with two costs that turned out to be larger. The palette is
    276 tags and Danbooru has six figures of them, so the director could not
    say `window`, `wet clothes` or `school desk` at all -- not for lack of
    knowing, but because the grammar could not emit them. And an enum with
    `minItems` forces the model to produce N members whether or not it has N
    things to say; when it runs out it emits whatever token is cheapest, which
    is what "it adds random tags that don't make sense" looks like from the
    outside. Those two are the same knob pulled in opposite directions: you
    cannot raise the floor for density without buying more filler.

    What replaced it is a check rather than a cage. `services/tags` holds the
    real Danbooru dictionary, imported at >=100 posts (`DEFAULT_MIN_COUNT`) --
    below that a checkpoint has seen a handful of images, which is
    indistinguishable from noise -- and `generation.clean_cg_tags` drops or
    snaps anything the corpus does not know. So invention is still caught; it
    is caught after the fact, by data, instead of by shrinking what can be
    said. The curated palette survives as the *suggested* vocabulary in the
    prompt, where it primes good choices without forbidding the rest.

    No `minItems` at all: a model that is not forced to fill a slot stops
    inventing filler for it, and the prompt asks for density in the place
    density belongs.

    `nsfw` no longer changes the grammar -- the register is carried by which
    palette the prompt shows (`defaults.cg_vocab_block`) and enforced after
    the fact by `clean_cg_tags`, which drops adult vocabulary from a scene
    that did not ask for it. The parameter stays because every caller passes
    it and the guarantee it names still holds; only where it is implemented
    moved.
    """
    def tag_list(high: int) -> dict[str, Any]:
        return {"type": "array", "items": {"type": "string"}, "maxItems": high}

    return {
        "type": "object",
        "properties": {
            # FIRST on purpose. llama.cpp builds its JSON grammar in property
            # order, so this field is generated before any tag is chosen and
            # every tag after it is conditioned on it. That is chain-of-thought
            # for the cost of one property: picking tags straight out of a
            # schema suppresses the reasoning that decides what the shot even
            # is, and this is where that reasoning goes instead.
            "shot_description": {"type": "string"},
            "camera": tag_list(8),
            "action": tag_list(16),
            "mood": tag_list(10),
            # Never reaches the positive prompt, and the useful ones are often
            # ordinary quality words rather than shot vocabulary.
            "negative": {"type": "string"},
        },
        "required": ["shot_description", "camera", "action", "mood", "negative"],
        "additionalProperties": False,
    }


# The unconstrained shape, kept for the tests that assert what the fields mean
# rather than which values are legal.
CG_PROMPT_SCHEMA: dict[str, Any] = cg_prompt_schema()


# --------------------------------------------------------------------------
# roleplay: scene-opening proposals
# --------------------------------------------------------------------------
#
# Constrained because the output is *picked from* in a UI rather than read, so
# the three ideas have to arrive as three separate strings. Asked for in prose a
# model reliably returns two of them inside one bullet, or numbers them and then
# adds a fourth "or alternatively", and splitting that back apart in code is
# guesswork. `minItems` is 1 rather than 3 on purpose: a first scene with no
# history to go on genuinely has less to offer, and an under-filled list beats a
# padded one.
RP_SEED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["ideas"],
    "additionalProperties": False,
}
