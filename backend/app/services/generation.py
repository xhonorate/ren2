"""Two-pass generation.

Pass 1 writes prose with no schema and full creative sampling -- this is where
writing quality lives. Pass 2 feeds that back with a JSON schema at temp 0.2
and the instruction not to rewrite anything. Heavy grammar constraint
measurably degrades prose, so the two jobs never share a call (doc S2).

Performance -- which face a line is worn with, how it lands, what it sounds
like -- is written by pass 1 as inline notation and applied here in code
(`extract_performance` / `apply_performance`). It used to be a third pass that
annotated the segmented nodes by index, which is the one thing a model cannot
be trusted with: a single miscount put every expression on its neighbour's
line. Staging is reconciled in code for the same reason.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .. import store
from ..clients import lmstudio
from .. import defaults as defaults_mod
from ..defaults import CG_VOCAB, CG_VOCAB_NSFW, TIME_OF_DAY
from ..graph import Graph, slugify
from ..models import (EMOTIONS, EXPRESSIONS, POSES, Edge, GenMeta, Node, NodeType,
                      Variant)
from . import context as ctx_mod, placement
from .schemas import CG_PROMPT_SCHEMA, cg_prompt_schema, scene_schema


class GenerationError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# the two passes
# --------------------------------------------------------------------------

async def prose_pass(project_id: str, assembled: ctx_mod.AssembledContext,
                     on_text=None) -> tuple[str, GenMeta]:
    """Pass 1. With `on_text`, streamed: see `lmstudio.chat_stream`."""
    tpl = store.active_template(project_id, "system.prose_gen")
    params = dict((tpl.params if tpl else {}) or {})
    model = store.get_setting("lm_studio_prose_model", "")
    if on_text is None:
        text = await lmstudio.chat(assembled.messages(), model=model, params=params)
    else:
        text = await lmstudio.chat_stream(assembled.messages(), model=model,
                                          params=params, on_text=on_text)
    meta = GenMeta(model=model or "(loaded model)", params=params,
                   seed=params.get("seed"),
                   prompt_hash=_hash(assembled.system + assembled.user),
                   timestamp=store.now())
    return text.strip(), meta


async def structure_pass(project_id: str, prose: str, character_ids: list[str],
                         location_ids: list[str],
                         state_block: str = "",
                         outfits_by_character: Optional[dict[str, list[str]]] = None,
                         variables: Optional[list[str]] = None,
                         stat_schema: str = "",
                         ) -> tuple[dict[str, Any], GenMeta]:
    """Segment prose into nodes.

    `state_block` is what is already true when the passage starts. Without it
    the pass cannot tell an actual staging change from state that already
    holds, so it either re-declares everything or stages nothing.
    """
    tpl = store.active_template(project_id, "system.structuring")
    params = dict((tpl.params if tpl else {}) or {})
    # A 7-12B does this fine, so it gets its own model setting.
    model = store.get_setting("lm_studio_struct_model", "") or store.get_setting(
        "lm_studio_prose_model", "")
    outfits_by_character = outfits_by_character or {}
    outfit_table = "; ".join(
        f"{cid}: {', '.join(ids)}" for cid, ids in outfits_by_character.items() if ids
    ) or "(none defined)"
    prompt = ctx_mod.render(
        tpl.body if tpl else "",
        prose=prose,
        character_ids=", ".join(character_ids) or "(none)",
        location_ids=", ".join(location_ids) or "(none)",
        outfit_table=outfit_table,
        state=state_block or "(nothing established yet)",
        variables=", ".join(variables or []) or "(none declared)",
        # The stat ladder, so a `+=` is chosen against a known scale rather
        # than guessed -- see context.stat_schema_block.
        stat_schema=stat_schema,
    )
    every_outfit = sorted({oid for ids in outfits_by_character.values() for oid in ids})
    schema = scene_schema(character_ids, location_ids, every_outfit, TIME_OF_DAY,
                          variables or [])
    raw = await lmstudio.chat(
        [{"role": "system", "content": "You segment prose into structured nodes. "
                                       "You never rewrite the text."},
         {"role": "user", "content": prompt}],
        model=model, params=params, json_schema=schema, schema_name="vn_scene",
    )
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise GenerationError(
            f"structuring pass did not return valid JSON: {raw[:400]}"
        ) from exc
    meta = GenMeta(model=model or "(loaded model)", params=params,
                   prompt_hash=_hash(prompt), timestamp=store.now())
    return parsed, meta


# The author-facing "ways this could go" block the prose pass may end on.
# Tolerant of the bullet character and of the options starting on the same
# line, because this is unconstrained output -- but anchored to the line start
# so the word "continue" inside ordinary prose can never trigger it.
_CONTINUE_RE = re.compile(r"^[ \t]*CONTINUE[ \t]*:[ \t]*", re.IGNORECASE | re.MULTILINE)
_BULLET = ("-", "*", "•", "–", "—")


def split_continuations(prose: str) -> tuple[str, list[str]]:
    """Peel the CONTINUE block off the end of a passage.

    These are notes to the author about where the scene could go next -- they
    steer the *next* generation and are never story content. Stripping them
    here rather than relying on the structuring template is what keeps them
    from becoming narration nodes reading "- she follows him out".

    A CHOICE block always wins: it becomes a real node the player sees, so if
    the model emitted both (it was told not to) the choice is kept intact and
    the continuations are ignored rather than swallowing it.
    """
    text = prose or ""
    match = None
    for match in _CONTINUE_RE.finditer(text):
        pass                              # the last one, if several
    if match is None:
        return text.strip(), []

    tail = text[match.end():]
    if "CHOICE:" in tail.upper():
        return text.strip(), []

    options: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(_BULLET):
            options.append(line.lstrip("".join(_BULLET) + " ").strip())
        elif options:
            break                         # prose resumed -- stop collecting
        else:
            options.append(line)          # options began on the CONTINUE: line
    return text[:match.start()].strip(), [o for o in options if o][:4]


# --------------------------------------------------------------------------
# performance notation -- written by the prose pass, applied in code
# --------------------------------------------------------------------------
#
# How a line is played is decided by the writer, on the line, as it is written.
# It used to be a third LLM pass that read the segmented passage and returned
# annotations keyed by node index -- and an index is exactly the thing a model
# loses track of. One skipped beat, one 1-based list, and every expression
# after it landed on the neighbouring line, which is what "the emotions are
# random" looks like from the graph. Notation written inline cannot drift off
# the line it was written on.

# `Aiko (smirking, amused, 0.6): "line"`, plus the two variants a writer model
# actually produces: square brackets, and the tag after the colon. The name is
# bounded and quote-free so an ordinary sentence with a parenthetical in it can
# never match.
_TAG_BEFORE = re.compile(
    r'^[ \t]*(?P<name>[^:"\n()\[\]]{1,40}?)[ \t]*[(\[](?P<tags>[^)\]\n]*)[)\]][ \t]*'
    r':[ \t]*(?P<rest>.*)$'
)
_TAG_AFTER = re.compile(
    r'^[ \t]*(?P<name>[^:"\n()\[\]]{1,40}?)[ \t]*:[ \t]*'
    r'[(\[](?P<tags>[^)\]\n]*)[)\]][ \t]*(?P<rest>.*)$'
)
# Author-facing cue lines. Anchored to the line start, so the word "music"
# inside a sentence is never one.
_CUE = re.compile(r"^[ \t]*(?P<kind>SFX|CG|MUSIC)[ \t]*:[ \t]*(?P<body>.*)$",
                  re.IGNORECASE)

_QUOTES = "\"“”«»‘’'"
_PERF_KEYS = ("expression", "emotion", "intensity", "pose")
_EXPRESSION_SET = set(EXPRESSIONS)
_EMOTION_SET = set(EMOTIONS)
_POSE_SET = set(POSES)
# What `MUSIC:` means when it asks for silence rather than a cue.
_MUSIC_STOP = {"stop", "none", "silence", "off", "cut", "nothing", "stop music"}


def _as_intensity(token: str) -> Optional[float]:
    raw = token.strip()
    try:
        value = float(raw.rstrip("%"))
    except ValueError:
        return None
    if raw.endswith("%"):
        value /= 100.0
    return round(value, 2) if 0.0 <= value <= 1.0 else None


def parse_performance_tags(raw: str) -> dict[str, Any]:
    """`smirking, amused, 0.6, talking over her` -> the fields it names.

    Classified by what each token *is*, not by where it sits: the template asks
    for face first and voice second, but a writer model that swaps them, omits
    one, or adds a note of its own still lands on the right fields. Anything
    the vocabularies do not recognise becomes the delivery note, which is free
    text anyway -- so an invented "half-laughing" costs nothing.
    """
    out: dict[str, Any] = {}
    delivery: list[str] = []
    implied_emotion = ""
    for token in (raw or "").replace(";", ",").split(","):
        token = token.strip().strip(".")
        if not token:
            continue
        key = token.lower().replace(" ", "_").replace("-", "_")
        intensity = _as_intensity(token)
        if intensity is not None and "intensity" not in out:
            out["intensity"] = intensity
            continue
        if key in _EXPRESSION_SET and "expression" not in out:
            out["expression"] = key
            # Several words are in both vocabularies. A line tagged with one of
            # them and nothing else means the voice matches the face.
            if key in _EMOTION_SET:
                implied_emotion = key
            continue
        if key in _EMOTION_SET and "emotion" not in out:
            out["emotion"] = key
            continue
        if key in _POSE_SET and "pose" not in out:
            out["pose"] = key
            continue
        delivery.append(token)
    if "emotion" not in out and implied_emotion:
        out["emotion"] = implied_emotion
    if delivery:
        out["delivery"] = ", ".join(delivery)
    return out


def _split_say(line: str) -> Optional[str]:
    """The spoken words of a `Name: "line"` line, or None if it is narration."""
    head, sep, rest = line.partition(":")
    if not sep or not head.strip() or len(head.strip()) > 40 or '"' in head:
        return None
    rest = rest.strip()
    if rest[:1] not in _QUOTES:
        return None
    return rest.strip(_QUOTES + " ")


def extract_performance(prose: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip the performance notation out of a written passage.

    Returns the clean prose the structuring pass segments, and one record per
    beat in written order. Doing it here rather than in the structuring
    template means a cold segmenter never has to decide what a parenthetical
    is -- it only ever sees `Name: "line"`.
    """
    beats: list[dict[str, Any]] = []
    lines: list[str] = []
    pending: list[tuple[str, str]] = []      # cues waiting for the next beat

    for raw_line in (prose or "").splitlines():
        cue = _CUE.match(raw_line)
        if cue:
            kind, body = cue.group("kind").lower(), cue.group("body").strip()
            if not body:
                continue
            # A CG illustrates the moment just written, so it binds backwards.
            # A sound or a music change sets up what comes next.
            if kind == "cg" and beats:
                beats[-1].setdefault("cg", body)
            else:
                pending.append((kind, body))
            continue

        line = raw_line.strip()
        if not line:
            lines.append("")
            continue

        tags: dict[str, Any] = {}
        match = _TAG_BEFORE.match(line) or _TAG_AFTER.match(line)
        if match:
            found = parse_performance_tags(match.group("tags"))
            rest = match.group("rest").strip()
            # Only rewrite when this really is a tagged line of dialogue:
            # either it is quoted the way the format demands, or the
            # parenthetical resolved to actual performance vocabulary. Anything
            # else is a sentence that happens to contain brackets, and
            # rewriting it would eat the author's text.
            if rest[:1] in _QUOTES or any(k in found for k in _PERF_KEYS):
                line = f"{match.group('name').strip()}: {rest}"
                tags = found

        spoken = _split_say(line)
        if spoken is not None:
            beat: dict[str, Any] = {"kind": "say", "text": spoken, **tags}
        else:
            # A tag on a narration line has no sprite to wear it. The line
            # itself is kept, untagged, rather than being second-guessed.
            beat = {"kind": "narration", "text": line}

        for kind, body in pending:
            beat.setdefault(kind, body)
        pending = []
        beats.append(beat)
        lines.append(line)

    for kind, body in pending:                # cues written after the last beat
        if beats:
            beats[-1].setdefault(kind, body)

    return "\n".join(lines).strip(), beats


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())[:48]


def apply_performance(parsed: dict[str, Any],
                      beats: list[dict[str, Any]]) -> dict[str, Any]:
    """Put each written beat's performance onto the node made from it.

    Aligned by text, not by position. The structuring pass is told to copy
    lines verbatim and usually does, but one merged or dropped line would
    otherwise shift every tag after it onto the wrong speaker. A beat that
    cannot be matched is left untagged, which is always better than wearing
    someone else's face.

    Pure function over the parsed dict, deliberately -- it runs before
    `build_nodes`, so it manipulates plain data rather than Nodes and edges,
    and can be tested without a graph.
    """
    nodes = [dict(n) for n in (parsed.get("nodes") or [])]
    body = [(i, n.get("type")) for i, n in enumerate(nodes)
            if n.get("type") in ("say", "narration")]
    music_before: dict[int, dict[str, Any]] = {}
    cursor = 0
    cg_taken = False

    for pos, (index, kind) in enumerate(body):
        node = nodes[index]
        norm = _norm(node.get("text", ""))
        match: Optional[int] = None
        # A short window, not the whole list: a tag five beats away is not a
        # late match, it is a coincidence.
        for j in range(cursor, min(cursor + 4, len(beats))):
            other = _norm(beats[j].get("text", ""))
            if (beats[j].get("kind") == kind and norm and other
                    and (norm == other or norm.startswith(other)
                         or other.startswith(norm))):
                match = j
                break
        if (match is None and cursor == pos < len(beats) == len(body)
                and beats[pos].get("kind") == kind):
            # No text match, but nothing has slipped either -- same count, same
            # kind, still in step. The segmenter rewrote the punctuation, not
            # the order. Once anything has slipped this never fires again,
            # because then position is exactly what cannot be trusted.
            match = pos
        if match is None:
            continue

        beat = beats[match]
        cursor = match + 1
        if kind == "say":
            for field in ("expression", "pose", "emotion", "delivery"):
                if beat.get(field):
                    node[field] = beat[field]
            if beat.get("intensity") is not None:
                node["intensity"] = beat["intensity"]
        if beat.get("sfx"):
            node["sfx_cue"] = beat["sfx"]
        # One illustration per passage, the first one written. A second `CG:`
        # is the writer over-reaching, and two CGs in six beats is never right.
        if beat.get("cg") and not cg_taken:
            node["cg"] = True
            node["shot_hint"] = beat["cg"]
            cg_taken = True
        if beat.get("music"):
            music = str(beat["music"]).strip()
            if music.lower().strip(".") in _MUSIC_STOP:
                music_before[index] = {"stop_music": True}
            else:
                music_before[index] = {"music_cue": music}

    parsed = dict(parsed)
    if not music_before:
        parsed["nodes"] = nodes
        return parsed

    # Music sets the register for what follows, so it lands *before* the beat
    # that called for it.
    out: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        if i in music_before:
            out.append({"type": "stage", "present": [], "hide": [],
                        **music_before[i]})
        out.append(node)
    parsed["nodes"] = out
    return parsed


# --------------------------------------------------------------------------
# staging reconciliation -- code, not a model
# --------------------------------------------------------------------------

_STAGE_FIELDS = ("location", "time_of_day", "weather", "present", "hide",
                 "outfits", "expressions", "poses", "music_cue", "stop_music")


def _stage_changes_anything(node: dict[str, Any]) -> bool:
    return any(node.get(f) for f in _STAGE_FIELDS)


def reconcile_staging(parsed: dict[str, Any], present: list[str],
                      character_ids: list[str],
                      player_ids: Optional[set[str]] = None,
                      location: Optional[str] = None,
                      time_of_day: Optional[str] = None,
                      weather: Optional[str] = None) -> dict[str, Any]:
    """Make a passage's staging agree with the running present-set.

    Three jobs, all of them facts rather than judgments, which is why none of
    them is asked of a model:

    * put a character on stage before they speak, if nothing else did. A model
      asked to notice that sometimes will not, and that is how a sprite goes
      missing for someone who has been talking for six lines.
    * strip staging that changes nothing -- a character already on stage, a
      `hide` for someone who is not, the location that is already the
      location. The segmenter re-declares what the prose just narrated, which
      is what produces `show A / "A walks in" / show A` in the graph.
    * fold adjacent stage nodes together. Two nodes for one staging moment
      read as a bug whatever put them there.

    The player character is skipped throughout: they have no sprite, so
    staging them produces a node that compiles to nothing.
    """
    nodes = parsed.get("nodes") or []
    players = set(player_ids or ())
    known = set(character_ids)
    live = [c for c in present if c not in players]
    scene = {"location": location, "time_of_day": time_of_day, "weather": weather}
    out: list[dict[str, Any]] = []

    def place(node: dict[str, Any]) -> None:
        """Append a stage node, merging into the one before it if adjacent."""
        prev = out[-1] if out else None
        # Two stage nodes that contradict each other are two staging moments,
        # not one: someone walks out and walks back in, and folding those
        # together would show and hide them in the same breath.
        contradicts = (set(node.get("present") or []) & set(prev.get("hide") or [])
                       or set(node.get("hide") or []) & set(prev.get("present") or [])
                       ) if prev else False
        if not prev or prev.get("type") != "stage" or contradicts:
            out.append(node)
            return
        for key in ("present", "hide"):
            merged = list(prev.get(key) or [])
            merged += [c for c in (node.get(key) or []) if c not in merged]
            prev[key] = merged
        for key in ("location", "time_of_day", "weather", "music_cue"):
            if node.get(key):
                prev[key] = node[key]
        for key in ("outfits", "expressions", "poses"):
            if node.get(key):
                prev[key] = {**(prev.get(key) or {}), **node[key]}
        if node.get("stop_music"):
            prev["stop_music"] = True

    for raw in nodes:
        kind = raw.get("type")
        if kind != "stage":
            if kind == "say":
                speaker = raw.get("speaker")
                if (speaker and speaker in known and speaker not in players
                        and speaker not in live):
                    place({"type": "stage", "present": [speaker], "hide": []})
                    live.append(speaker)
            out.append(raw)
            continue

        node = dict(raw)
        node["present"] = [c for c in (node.get("present") or [])
                           if c in known and c not in players and c not in live]
        node["hide"] = [c for c in (node.get("hide") or [])
                        if c in known and c not in players and c in live]
        for key in ("location", "time_of_day", "weather"):
            if node.get(key) and node[key] == scene.get(key):
                node.pop(key)
        if not _stage_changes_anything(node):
            continue
        live += node["present"]
        for cid in node["hide"]:
            live.remove(cid)
        for key in ("location", "time_of_day", "weather"):
            if node.get(key):
                scene[key] = node[key]
        place(node)

    parsed = dict(parsed)
    parsed["nodes"] = out
    return parsed


# The two screens over the director's output, and over the palette it is
# offered in the first place. They live in `defaults` because both this module
# and `services.tags` apply them and neither may import the other -- see the
# comments there for what each one bans and why.
_BANNED_TAG = defaults_mod.BANNED_TAG
_CG_ALLOWED = defaults_mod.CG_ALLOWED
_CG_ALLOWED_SFW = defaults_mod.CG_ALLOWED_SFW
_ADULT_TAG = defaults_mod.ADULT_TAG


def clean_cg_tags(raw: str, *, nsfw: bool = False,
                  min_count: int = 0) -> str:
    """Split a director field into tags, drop what cannot help, dedupe.

    This is now the whole of the guarantee. The output schema used to be an
    `enum` over the curated palette, so invention was impossible and the adult
    vocabulary was simply absent from an SFW beat's grammar. That cost more
    than it bought (see `schemas.cg_prompt_schema`), and both promises moved
    here -- same promises, enforced by data instead of by the shape of the
    grammar.

    Four filters, in descending order of how sure we are:

    1. The adult screen, when the scene is not marked 18+. This one runs
       *before* the curated exemption, because the curated set includes
       CG_VOCAB_NSFW and the whole point is that an SFW beat cannot reach it.
    2. The identity ban (`_BANNED_TAG`), overridden by the curated vocabulary:
       hair, eyes, body and clothing come from the character record, and a
       director-written one either duplicates it or argues with it.
    3. The booru dictionary, when one has been imported: a tag the checkpoint
       was never trained on conditions on nothing. The director really does
       invent these -- "warm atmosphere", "white knuckles" and "soft focus"
       between them accounted for thirty tags across one real project, none of
       which exist on Danbooru and none of which drew anything.
    4. `min_count`, for tags that exist but barely: below a few hundred posts
       a checkpoint has seen a handful of images, which is indistinguishable
       from noise. Curated tags are exempt -- they are hand-picked rather than
       measured, and carry no count.

    Filters 3 and 4 stay off until a dictionary exists (`tags.has_corpus`).
    With only the curated few hundred in memory "unrecognised" would mean
    almost everything, and a validator that empties every prompt is far worse
    than one that does nothing.
    """
    from . import tags as tag_svc

    flat = (raw or "").replace("\n", ",")
    candidates = [t.strip().strip(".").lower() for t in flat.split(",")]

    validate = tag_svc.has_corpus()
    kept: list[str] = []
    seen: set[str] = set()
    for raw_tag in candidates:
        if not raw_tag:
            continue
        # Every check below is about the *tag*, so the emphasis comes off
        # first and goes back on at the end. Without that, `(hug:1.25)` is an
        # unknown string: the dictionary has never heard of it, `snap` returns
        # None, and the one tag the director marked as the point of the shot
        # is the one tag guaranteed to be deleted.
        tag, weight = _split_weight(raw_tag)
        if not tag:
            continue
        # The SFW palette outranks the adult screen: a curated tag is a
        # judgement already made, and a blunt regex would eat `all fours` and
        # `straddling` off an ordinary shot. Everything else -- unvetted
        # vocabulary, and the whole of CG_VOCAB_NSFW -- is screened.
        if tag not in _CG_ALLOWED_SFW and not nsfw and _ADULT_TAG.search(tag):
            continue
        # The rescue list is register-scoped. It used not to be, and that was
        # safe only while the adult vocabulary held no anatomy: every tag in it
        # was also caught by `_ADULT_TAG` above, so how broad the rescue was
        # made no difference. Adding an anatomy group broke that -- `cleavage`,
        # `pubic hair` and `ass` are not words `_ADULT_TAG` looks for, so being
        # curated-for-adult-scenes was quietly exempting them from the identity
        # ban on ordinary ones too.
        curated = tag in (_CG_ALLOWED if nsfw else _CG_ALLOWED_SFW)
        if not curated and _BANNED_TAG.search(tag):
            continue
        if validate and not curated:
            snapped = tag_svc.snap(tag)
            if snapped is None:
                continue
            if min_count and tag_svc.count_of(snapped) < min_count:
                continue
            tag = snapped
        # Deduped on the tag, not on the weighted form, so a tag that arrives
        # both bare and weighted lands once rather than twice with the second
        # copy quietly re-weighting the first.
        if tag in seen:
            continue
        seen.add(tag)
        kept.append(_with_weight(tag, weight))
    return ", ".join(kept)


# `(hug:1.25)` -- ComfyUI and A1111 emphasis. The weight is the half of a booru
# prompt that says which part is the point, and a tag list without any says
# everything matters equally, which is how the pose ends up being the first
# thing the encoder gives up on.
_WEIGHTED = re.compile(r"^\((?P<tag>.+?):(?P<weight>\d*\.?\d+)\)$")


def _split_weight(text: str) -> tuple[str, Optional[float]]:
    """`(hug:1.25)` -> ("hug", 1.25); `hug` -> ("hug", None)."""
    match = _WEIGHTED.match(text.strip())
    if not match:
        return text.strip(), None
    try:
        return match.group("tag").strip(), float(match.group("weight"))
    except ValueError:
        return text.strip(), None


def _with_weight(tag: str, weight: Optional[float]) -> str:
    """Put the emphasis back, clamped and tidied.

    Clamped at 1.4 because past that SDXL distorts anatomy before it changes
    composition -- which reads as the prompt not working, and invites going
    higher still. A weight of 1.0 is written bare: it is the default, and the
    parentheses would only spend tokens saying so.
    """
    if weight is None:
        return tag
    weight = max(0.5, min(1.4, weight))
    if abs(weight - 1.0) < 0.02:
        return tag
    return f"({tag}:{weight:g})"


# The tags that decide how the picture is composed -- how far away the camera
# is, and where it is. Everything else in the framing group says something
# about the subject (`solo focus`) or the viewer (`pov`) and repeats legitimately
# across a whole conversation, so none of it counts as having framed the shot.
_FRAMING_CORE = {t.lower() for t in (
    list(CG_VOCAB["angle"])
    + ["close-up", "portrait", "upper body", "lower body", "cowboy shot",
       "full body", "wide shot", "profile", "cropped"]
)}


def vary_camera(camera: str, recent: list[str]) -> str:
    """Drop framing/angle tags this scene has just used, if anything is left.

    The prompt has always told the director to vary the camera and has always
    been shown what it last shot; it still returned `close-up, from above, pov`
    for four consecutive beats, because "vary it" is a request and requests
    lose to whatever the beat suggests in isolation.

    This is the enforcement, and it is deliberately timid: a repeat is only
    removed when another framing or angle tag survives it. A shot with no
    framing at all is worse than a repeated one -- it hands the composition
    back to the checkpoint, which will pick the same mid-shot every time.
    """
    used = {t.strip().lower() for t in recent if t.strip()}
    if not used:
        return camera
    parts = [t.strip() for t in camera.split(",") if t.strip()]
    survivors = [t for t in parts if t.lower() not in used]

    # What has to survive is a tag that actually frames the shot -- a distance
    # or an angle. The first version of this asked only for something in the
    # `perspective` category, which `solo focus` satisfies, and that is a
    # subject hint rather than a composition: "close-up, from above, pov,
    # solo focus" was trimmed to "solo focus", handing distance and angle back
    # to the checkpoint on a beat the director had actually framed.
    core = [t for t in survivors if t.lower() in _FRAMING_CORE]
    # Two tags is the floor for the same reason. A camera stripped to one tag
    # is not varied, it is undirected.
    if core and len(survivors) >= 2:
        return ", ".join(survivors)
    return camera


# Tags that already say where the viewer stands. `pov` is the plain one; the
# others are compositions that are about the viewer's position without using
# the word.
_POV_TAGS = {"pov", "pov hands", "first-person view", "over-the-shoulder"}


def enforce_pov(camera: str) -> str:
    """Make sure the camera says the shot is the player's point of view.

    "Nearly every shot is a POV shot" is in the prompt and loses the same way
    "vary the camera" lost: a distance and an angle look like a complete
    answer, so the director stops there and a second-person scene comes back
    as a third-person illustration of itself -- which is also where the player
    starts turning up in frame as a whole separate person.

    This runs *after* `vary_camera` on purpose. `pov` is deliberately not in
    `_FRAMING_CORE` (it says nothing about distance or angle, and it repeats
    legitimately across a whole conversation), which means variety is free to
    strip it as a repeat -- so the default has to be reapplied downstream of
    that rather than instead of it.

    Appended rather than prepended: distance and angle decide what kind of
    picture this is and belong at the front. An empty camera is left empty --
    `assemble_cg_prompt` falls back to the segmenter's `shot_hint` there, and a
    camera of nothing but `pov` would beat it while saying less.
    """
    parts = [t.strip() for t in (camera or "").split(",") if t.strip()]
    if not parts or any(t.lower() in _POV_TAGS for t in parts):
        return camera
    return ", ".join(parts + ["pov"])


async def cg_director_pass(project_id: str, assembled: dict[str, Any]
                           ) -> tuple[dict[str, str], GenMeta]:
    """Distill one node into image tags (`ctx_mod.assemble_cg` builds the input).

    A separate pass from structuring on purpose. Structuring sees a whole
    passage at once and writes `shot_hint` as an aside while it is busy
    segmenting; this sees one beat, the beats around it, who is on stage and
    where, and does nothing else -- which is what it takes to get tags that
    describe *this* moment rather than a generic two-shot.
    """
    tpl = store.active_template(project_id, "system.cg_director")
    params = dict((tpl.params if tpl else {}) or {})
    # Mechanical translation, same as structuring -- a small model does it fine.
    model = store.get_setting("lm_studio_struct_model", "") or store.get_setting(
        "lm_studio_prose_model", "")
    # Constrained to the scene's own register: an SFW beat is not offered the
    # adult vocabulary at all, so it cannot pick from it even by accident.
    nsfw = bool(assembled.get("nsfw"))
    # The floor under a tag the director spends. The corpus is imported at 100
    # posts by default, so this normally changes nothing -- it bites when the
    # config page has fetched a wider dump, where a tag with twelve posts is
    # real, passes `known()`, and still conditions on nothing.
    min_count = int(store.get_setting("cg_tag_min_posts", 100))
    schema = cg_prompt_schema(nsfw=nsfw)
    raw = await lmstudio.chat(
        [{"role": "system", "content": assembled["system"]},
         {"role": "user", "content": assembled["user"]}],
        model=model, params=params, json_schema=schema, schema_name="cg_prompt",
    )
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise GenerationError(
            f"CG director did not return valid JSON: {raw[:400]}"
        ) from exc

    def field(name: str) -> str:
        """The tag fields are arrays now; older stored takes are strings.

        Accepting both keeps a re-run against a project's own template history
        working -- an author who has edited `system.cg_director` sits on their
        own version, and nothing forces that to describe the new shape.
        """
        value = parsed.get(name, "")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        return clean_cg_tags(value, nsfw=nsfw, min_count=min_count)

    camera = enforce_pov(
        vary_camera(field("camera"), assembled.get("recent_camera") or []))
    action = ", ".join(p for p in (field("action"), field("mood")) if p)
    result = {
        # The English read of the beat, written before any tag was chosen.
        "cg_description": (parsed.get("shot_description") or "").strip(),
        "cg_camera": camera,
        # mood rides with action: both belong after the identity block, and one
        # editable field per prompt slot is what the inspector exposes.
        "cg_action": action,
        "cg_negative": clean_cg_tags(parsed.get("negative", ""), nsfw=nsfw),
    }
    meta = GenMeta(model=model or "(loaded model)", params=params,
                   prompt_hash=_hash(assembled["system"]), timestamp=store.now())
    return result, meta


def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# materialization
# --------------------------------------------------------------------------

def _clamp(value: Any, allowed: list[str], fallback: str) -> str:
    return value if value in allowed else fallback


def _clean_assignments(raw: Any, variables: Optional[list[str]]) -> list[dict[str, str]]:
    """Keep only writes to variables the project actually declared."""
    known = set(variables or [])
    out: list[dict[str, str]] = []
    for a in raw or []:
        var = (a or {}).get("var")
        if not var or (known and var not in known):
            continue
        op = a.get("op") if a.get("op") in ("=", "+=", "-=") else "="
        out.append({"var": var, "op": op, "value": str(a.get("value", ""))})
    return out


def _mentions_known_var(expr: str, variables: Optional[list[str]]) -> bool:
    return bool(expr.strip()) and any(v in expr for v in (variables or []))


def build_nodes(parsed: dict[str, Any], gen_id: str, character_ids: list[str],
                anchor: Node, handle: str = "out",
                variables: Optional[list[str]] = None) -> tuple[list[Node], list[tuple[str, str, str]]]:
    """Turn a structuring result into IR nodes plus (src, handle, dst) edges.

    `handle` is the anchor's outgoing socket the chain hangs off. For a plain
    continuation that is "out", but generating down a choice option it is that
    option's id -- an option counts as wired only when an edge leaves the
    choice on a handle matching the option id (services/script.successor), so
    hardcoding "out" here left the branch dangling and the option still
    flagged "goes nowhere".
    """
    nodes: list[Node] = []
    edges: list[tuple[str, str, str]] = []

    prev_id = anchor.id
    prev_handle = handle or "out"

    for item in parsed.get("nodes") or []:
        kind = item.get("type")
        nid = store.new_id("n_")
        if kind == "say":
            speaker = item.get("speaker")
            if speaker not in character_ids:
                speaker = character_ids[0] if character_ids else None
            data = {
                "speaker": speaker,
                "text": item.get("text", ""),
                "expression": _clamp(item.get("expression"), EXPRESSIONS, "neutral"),
                "pose": _clamp(item.get("pose"), POSES, "front"),
                "emotion": _clamp(item.get("emotion"), EMOTIONS, "neutral"),
                "intensity": float(item.get("intensity", 0.5) or 0.5),
                "delivery": item.get("delivery", ""),
                "shot_hint": item.get("shot_hint", ""),
                # Only honoured with something to illustrate -- a CG flag with
                # no shot_hint produces a generic shot of the room.
                "cg": bool(item.get("cg")) and bool(item.get("shot_hint")),
                "sfx_cue": item.get("sfx_cue", ""),
                "_gen": gen_id,
            }
            node = Node(id=nid, type=NodeType.say, data=data,
                        parent_id=anchor.parent_id or (anchor.id if anchor.type == NodeType.scene else None),
                        slug=slugify((data["text"] or "say")[:32], "say"))
        elif kind == "narration":
            data = {"text": item.get("text", ""), "shot_hint": item.get("shot_hint", ""),
                    "cg": bool(item.get("cg")) and bool(item.get("shot_hint")),
                    "sfx_cue": item.get("sfx_cue", ""),
                    "_gen": gen_id}
            node = Node(id=nid, type=NodeType.narration, data=data,
                        parent_id=anchor.parent_id or (anchor.id if anchor.type == NodeType.scene else None),
                        slug=slugify((data["text"] or "narration")[:32], "narration"))
        elif kind == "stage":
            data = {
                "location": item.get("location"),
                "time_of_day": item.get("time_of_day"),
                "weather": item.get("weather") or "",
                "show": [c for c in (item.get("present") or []) if c in character_ids],
                "hide": [c for c in (item.get("hide") or []) if c in character_ids],
                "outfits": {k: v for k, v in (item.get("outfits") or {}).items()
                            if k in character_ids},
                "expressions": {k: _clamp(v, EXPRESSIONS, "neutral")
                                for k, v in (item.get("expressions") or {}).items()
                                if k in character_ids},
                "poses": {k: _clamp(v, POSES, "front")
                          for k, v in (item.get("poses") or {}).items()
                          if k in character_ids},
                "music_cue": item.get("music_cue", ""),
                "stop_music": bool(item.get("stop_music")),
                "_gen": gen_id,
            }
            # A stage node that changes nothing is noise in the graph.
            if not any((data["location"], data["time_of_day"], data["weather"],
                        data["show"], data["hide"], data["outfits"],
                        data["expressions"], data["poses"],
                        data["music_cue"], data["stop_music"])):
                continue
            node = Node(id=nid, type=NodeType.stage, data=data,
                        parent_id=anchor.parent_id or (anchor.id if anchor.type == NodeType.scene else None),
                        slug="stage")
        elif kind == "set":
            assignments = _clean_assignments(item.get("assignments"), variables)
            if not assignments:
                continue
            data = {"assignments": assignments, "_gen": gen_id}
            node = Node(id=nid, type=NodeType.set, data=data,
                        parent_id=anchor.parent_id or (anchor.id if anchor.type == NodeType.scene else None),
                        slug=slugify(assignments[0]["var"], "set"))
        else:
            continue

        nodes.append(node)
        edges.append((prev_id, prev_handle, nid))
        prev_id, prev_handle = nid, "out"

    choice = parsed.get("choice")
    if choice and (choice.get("options") or []):
        cid = store.new_id("n_")
        options = []
        for i, opt in enumerate(choice["options"]):
            options.append({
                "id": f"o{i + 1}",
                "label": opt.get("label", f"Option {i + 1}"),
                # Only kept when it references a declared variable -- a
                # condition on an unknown name hides the option forever.
                "condition": (opt.get("condition", "")
                              if _mentions_known_var(opt.get("condition", ""), variables) else ""),
                "branch_hint": opt.get("branch_hint", ""),
                "effects": _clean_assignments(opt.get("effects"), variables),
            })
        data = {"prompt": choice.get("prompt", ""), "options": options,
                "mode": "jump", "exhaust": False, "menuset": "menuset",
                "loop_back": False, "_gen": gen_id}
        node = Node(id=cid, type=NodeType.choice, data=data,
                    parent_id=anchor.parent_id or (anchor.id if anchor.type == NodeType.scene else None),
                    slug=slugify(data["prompt"] or "choice", "choice"))
        nodes.append(node)
        edges.append((prev_id, prev_handle, cid))

    # Under the port it hangs off: a choice's branches fan out left to right
    # in option order instead of all landing under the middle of the choice.
    placement.stack(anchor, handle or "out", nodes)
    return nodes, edges


def insert_chain(project_id: str, anchor_id: str, handle: str, nodes: list[Node],
                 edges: list[tuple[str, str, str]]) -> dict[str, Any]:
    """Splice a generated chain in after the anchor, keeping what followed."""
    existing = store.list_edges(project_id)
    downstream = next(
        (e for e in existing if e.source == anchor_id and e.source_handle == handle), None
    )
    anchor = store.get_node(anchor_id)
    if anchor is not None:
        placement.make_room(project_id, anchor, handle, nodes,
                            downstream.target if downstream else None)

    for node in nodes:
        store.insert_node(project_id, node)
    for src, src_handle, dst in edges:
        store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=src, target=dst,
                                           source_handle=src_handle))

    reattached = None
    reattached_handles: list[str] = []
    if downstream and nodes:
        tail = nodes[-1]
        if tail.type != NodeType.choice:
            store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=tail.id,
                                               target=downstream.target, source_handle="out"))
            reattached = downstream.target
            reattached_handles = ["out"]
        else:
            # A generated choice ends the chain. Splicing it in front of
            # existing content would strand that content, so every option
            # initially converges on it -- a merge point the compiler already
            # handles -- and the author rewires from there.
            for opt in (tail.data or {}).get("options") or []:
                handle = opt.get("id")
                if not handle:
                    continue
                store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=tail.id,
                                                   target=downstream.target,
                                                   source_handle=handle))
                reattached_handles.append(handle)
            reattached = downstream.target
    return {"inserted": [n.id for n in nodes], "reattached_to": reattached,
            "reattached_handles": reattached_handles}


# --------------------------------------------------------------------------
# regeneration
# --------------------------------------------------------------------------

def orphan_subtree(project_id: str, node_id: str, include_self: bool = False) -> list[str]:
    """Move the downstream subtree to the holding area.

    Never delete -- you will want it back (doc S2 'Regenerate from node').
    """
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    graph = Graph.build(nodes, edges)

    candidates = set(graph.subtree(node_id))
    if include_self:
        candidates.add(node_id)

    # Anything still reachable from elsewhere in the graph stays put -- a merge
    # point fed by a second branch must not be dragged off with this subtree.
    # The node we are regenerating is the exception: the edge that reaches it
    # is the very one being replaced, so it does not protect it.
    keep: set[str] = set()
    frontier: list[str] = []
    for node in nodes:
        # The anchor never counts as an outside referent. With
        # include_self=True it is already in `candidates`; with False -- which
        # is what rewind uses -- it is not, and without this it "reaches" its
        # own subtree and protects every node in it, so parking silently did
        # nothing at all.
        if node.id in candidates or node.id == node_id:
            continue
        for e in graph.out_edges(node.id):
            if e.target in candidates and e.target != node_id:
                keep.add(e.target)
                frontier.append(e.target)
    while frontier:                      # reachability propagates downstream
        current = frontier.pop()
        for e in graph.out_edges(current):
            if e.target in candidates and e.target not in keep and e.target != node_id:
                keep.add(e.target)
                frontier.append(e.target)

    target_ids = sorted(candidates - keep)
    store.set_orphaned(project_id, target_ids, True)
    return target_ids


def restore_orphans(project_id: str, node_ids: list[str]) -> None:
    store.set_orphaned(project_id, node_ids, False)


def generation_head(project_id: str, gen_id: str) -> Optional[str]:
    """The node a given generation starts at, in graph order.

    "Regenerate this block" has to re-run from where the generation began, not
    from whichever of its nodes the cursor is on -- otherwise asking to redo a
    six-node passage because its first line is wrong regenerates only the last
    two. Array order is no use here (nodes come back however the store sorts
    them), so this is the group's topological root: the member no other member
    points at.
    """
    members = {n.id for n in store.list_nodes(project_id, include_orphaned=False)
               if (n.data or {}).get("_gen") == gen_id}
    if not members:
        return None
    edges = store.list_edges(project_id)
    reached_from_inside = {e.target for e in edges
                           if e.source in members and e.target in members}
    heads = sorted(members - reached_from_inside)
    if not heads:
        return sorted(members)[0]        # a cycle: pick deterministically
    # Prefer a head something outside the group actually points at -- that is
    # the one spliced onto the anchor, and the only one with a predecessor to
    # regenerate from.
    attached = {e.target for e in edges
                if e.target in members and e.source not in members}
    return next((h for h in heads if h in attached), heads[0])


def discard_generation(project_id: str, gen_id: str) -> list[str]:
    """Drop every node a given generation created."""
    removed = []
    for node in store.list_nodes(project_id):
        if (node.data or {}).get("_gen") == gen_id:
            store.delete_node(project_id, node.id)
            removed.append(node.id)
    return removed


# --------------------------------------------------------------------------
# generation undo / redo (item 10)
# --------------------------------------------------------------------------

def _generation_node_ids(project_id: str, desc: dict[str, Any]) -> list[str]:
    ids = set(desc.get("inserted") or [])
    gen_id = desc.get("gen_id")
    for node in store.list_nodes(project_id, include_orphaned=True):
        if gen_id and (node.data or {}).get("_gen") == gen_id:
            ids.add(node.id)
    return sorted(ids)


def _boundary_target(desc: dict[str, Any]) -> Optional[str]:
    """The node the anchor pointed at before this generation ran."""
    return desc.get("regen_head") or desc.get("reattached_to")


def rollback_generation(project_id: str, desc: dict[str, Any]) -> dict[str, Any]:
    """Park a generation's nodes and put the graph back the way it was.

    Nothing is deleted -- reapply_generation is the exact inverse, so the
    canvas undo/redo buttons cover LLM output (item 10).
    """
    gen_ids = set(_generation_node_ids(project_id, desc))
    anchor_id = desc["anchor_id"]
    handle = desc.get("handle", "out")

    # Cut every edge between the generated block and the rest of the graph.
    for e in store.list_edges(project_id):
        if (e.source in gen_ids) != (e.target in gen_ids):
            store.delete_edge(project_id, e.id)

    store.set_orphaned(project_id, sorted(gen_ids), True)
    if desc.get("orphaned"):
        store.set_orphaned(project_id, list(desc["orphaned"]), False)

    target = _boundary_target(desc)
    if target and store.get_node(target) is not None:
        store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=anchor_id,
                                           target=target, source_handle=handle))
    return {"parked": sorted(gen_ids), "restored": desc.get("orphaned") or []}


def reapply_generation(project_id: str, desc: dict[str, Any]) -> dict[str, Any]:
    """Inverse of rollback_generation -- re-splice the generated block."""
    gen_ids = _generation_node_ids(project_id, desc)
    anchor_id = desc["anchor_id"]
    handle = desc.get("handle", "out")
    inserted = desc.get("inserted") or gen_ids

    store.set_orphaned(project_id, gen_ids, False)
    if desc.get("orphaned"):
        store.set_orphaned(project_id, list(desc["orphaned"]), True)

    target = _boundary_target(desc)
    for e in store.list_edges(project_id):
        if e.source == anchor_id and e.source_handle == handle and e.target == target:
            store.delete_edge(project_id, e.id)

    if inserted:
        store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=anchor_id,
                                           target=inserted[0], source_handle=handle))
        reattached_to = desc.get("reattached_to")
        if reattached_to and store.get_node(reattached_to) is not None:
            for h in desc.get("reattached_handles") or ["out"]:
                store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=inserted[-1],
                                                   target=reattached_to, source_handle=h))
    return {"reinserted": gen_ids}


def add_variant(project_id: str, node: Node, kind: str, payload: dict[str, Any],
                meta: GenMeta, asset_hash: Optional[str] = None) -> Variant:
    """Keep N takes per node with a flip-between UI (doc S2)."""
    variant = Variant(id=store.new_id("v_"), kind=kind, payload=payload,
                      asset_hash=asset_hash, meta=meta, created_at=store.now())
    node.variants = list(node.variants) + [variant]
    node.active_variant = variant.id
    store.update_node(project_id, node)
    return variant


def apply_variant(project_id: str, node: Node, variant_id: str) -> Node:
    variant = next((v for v in node.variants
                    if (v.id if hasattr(v, "id") else v.get("id")) == variant_id), None)
    if variant is None:
        raise GenerationError(f"node {node.id} has no variant {variant_id}")
    payload = variant.payload if hasattr(variant, "payload") else variant.get("payload", {})
    kind = variant.kind if hasattr(variant, "kind") else variant.get("kind")
    asset_hash = variant.asset_hash if hasattr(variant, "asset_hash") else variant.get("asset_hash")

    data = dict(node.data or {})
    if kind == "text":
        data.update(payload)
    elif kind == "image":
        data["image_hash"] = asset_hash
    elif kind == "audio":
        data["voice_path"] = payload.get("voice_path")
        data["voice_hash"] = asset_hash
    node.data = data
    node.active_variant = variant_id
    return store.update_node(project_id, node)
