"""Text-to-speech via IndexTTS-2.5 (through ComfyUI).

Timbre comes from a fixed reference clip; emotion is supplied separately, so
the character's voice does not drift when they get angry (doc S5). That
separation is the whole reason for this model choice.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .. import db, store
from ..clients import comfyui
from ..defaults import (
    DELIVERY_RULES,
    EMOTION_DIMS,
    EMOTION_MAP,
    EMOTION_SLIDER_CAP,
    EMOTION_VECTOR_SUM_CAP,
)
from ..models import Character
from . import assets, images, workflows


def emotion_map() -> dict[str, Any]:
    stored = store.get_setting("emotion_map", None)
    return stored if isinstance(stored, dict) and stored else EMOTION_MAP


def resolve_emotion(emotion: str, intensity: float, character: Optional[Character],
                    delivery: str = "") -> dict[str, Any]:
    """enum + intensity + delivery note + per-character cap -> a legal IndexTTS-2.5 vector.

    Constraints: 8 sliders, each 0.0-`EMOTION_SLIDER_CAP` (1.2 for TTS Audio
    Suite's emotion node), and the sum must stay <= 1.5. Strong emotion
    causes slight timbre leakage, which is what the per-character
    `emotion_scale` cap is for.

    The delivery note's rules (defaults.DELIVERY_RULES) scale the intensity
    before anything else -- "flat" halves it, "shouting" pushes it -- and
    compound onto the speed. The names of the rules that fired ride along as
    `delivery`, so a take records why it was paced the way it was.
    """
    rules = delivery_rules(delivery)
    intensity = float(intensity)
    pace = 1.0
    for rule in rules:
        intensity *= float(rule.get("intensity", 1.0))
        pace *= float(rule.get("speed", 1.0))

    entry = emotion_map().get(emotion) or emotion_map().get("neutral") or {"vec": {}}
    scale = float(character.voice.emotion_scale) if character else 1.0
    strength = max(0.0, min(1.0, intensity)) * max(0.0, min(1.4, scale))

    vec = {dim: 0.0 for dim in EMOTION_DIMS}
    for dim, value in (entry.get("vec") or {}).items():
        if dim in vec:
            vec[dim] = min(EMOTION_SLIDER_CAP, float(value) * strength)

    total = sum(vec.values())
    if total > EMOTION_VECTOR_SUM_CAP and total > 0:
        factor = EMOTION_VECTOR_SUM_CAP / total
        vec = {k: round(v * factor, 4) for k, v in vec.items()}

    speed = float(entry.get("speed", 1.0)) * pace
    if character:
        speed *= float(character.voice.speed or 1.0)
    resolved = {
        "vec": {k: round(v, 4) for k, v in vec.items()},
        "speed": round(speed, 3),
        "gain_db": float(entry.get("gain_db", 0.0)),
        "emo_alpha": min(0.6, float(store.get_setting("tts_emo_alpha", 0.6))),
    }
    if rules:
        resolved["delivery"] = [rule["name"] for rule in rules]
    return resolved


# --------------------------------------------------------------------------
# delivery notes (defaults.DELIVERY_RULES)
# --------------------------------------------------------------------------

_phrase_patterns: dict[str, re.Pattern[str]] = {}


def _phrase(phrase: str) -> re.Pattern[str]:
    """Whole words only, hyphens included: "rapid" must not fire on "rapid-fire"."""
    pattern = _phrase_patterns.get(phrase)
    if pattern is None:
        pattern = re.compile(r"(?<![\w-])" + re.escape(phrase.lower()) + r"(?![\w-])")
        _phrase_patterns[phrase] = pattern
    return pattern


def delivery_rules(delivery: str) -> list[dict[str, Any]]:
    """The rules a free-text delivery note triggers, in table order."""
    note = (delivery or "").lower()
    if not note.strip():
        return []
    return [rule for rule in DELIVERY_RULES
            if any(_phrase(p).search(note) for p in rule.get("match", ()))]


# Closing punctuation a delivery ending replaces. "..." is three of these.
_CLOSING = re.compile(r"[\s.!?…,;:—–-]+$")


def _hesitate(text: str) -> str:
    """A catch near the start: the first comma becomes "…", or, with no comma,
    one goes after the first word. A line already carrying an ellipsis was
    written hesitant, and a line under four words has nowhere to put one."""
    if "…" in text or "..." in text:
        return text
    comma = text.find(",")
    if 0 < comma < len(text.rstrip()) - 1:
        return text[:comma] + "…" + text[comma + 1:]
    words = text.split(" ")
    if len(words) < 4:
        return text
    words[0] = words[0].rstrip(",;:") + "…"
    return " ".join(words)


def _exclaim(text: str) -> str:
    stripped = text.rstrip()
    if stripped.endswith(("…", "...", "?", "!", "—", "-")):
        return text
    if stripped.endswith("."):
        return stripped[:-1] + "!"
    return stripped + "!"


def _trail(text: str) -> str:
    stripped = text.rstrip()
    # A question keeps its rise; one already trailing needs nothing.
    if stripped.endswith(("?", "…", "...")):
        return text
    return _CLOSING.sub("", stripped) + "…"


def _cut(text: str) -> str:
    return _CLOSING.sub("", text.rstrip()) + "—"


_ENDINGS = (("exclaim", _exclaim), ("trail", _trail), ("cut", _cut))


# Clips already pushed into ComfyUI's input/ dir this process run, keyed by the
# content hash -> the name the workflow should reference. Content-addressed, so a
# hash always maps to the same bytes; the file persists across ComfyUI restarts
# (only temp/ is cleared), which makes the cache safe.
_staged_clips: dict[str, str] = {}


async def stage_reference_clip(reference_clip: str) -> str:
    """Make a timbre clip readable by the TTS workflow's audio loader.

    Our clips live in the content-addressed store; ComfyUI's LoadAudio only
    reads from its own input/ dir, and nothing else copies them across. Upload
    the clip there and return the name to put in `{{reference_clip}}`. On any
    failure -- clip missing locally, ComfyUI upload refused, a custom loader node
    that wants a raw path -- fall back to the stored string unchanged.
    """
    if not reference_clip:
        return ""
    digest = reference_clip.split("/")[-1].split(".")[0]
    if digest in _staged_clips:
        return _staged_clips[digest]
    path = assets.path_for(digest)
    if path is None:
        return reference_clip
    try:
        uploaded = await comfyui.upload_audio(path.read_bytes(), path.name)
    except Exception:  # noqa: BLE001 - any failure: leave the reference untouched
        return reference_clip
    name = uploaded.get("name") or path.name
    subfolder = uploaded.get("subfolder") or ""
    staged = f"{subfolder}/{name}" if subfolder else name
    _staged_clips[digest] = staged
    return staged


# `{player}` is the author's stand-in for the player character's name -- there
# is no name to speak, and IndexTTS-2 would otherwise pronounce the word
# "player". Drop every occurrence (plus a trailing possessive), eat the space
# it sat behind, and close the seam. This only touches what reaches the model;
# the say node keeps its text exactly as authored.
_PLACEHOLDER = re.compile(r"\s*\{\s*player\s*\}(['’]s)?", re.IGNORECASE)


def strip_placeholders(text: str) -> str:
    if not text or "{" not in text:
        return text
    cleaned = _PLACEHOLDER.sub("", text)
    if cleaned == text:
        return text
    cleaned = re.sub(r",\s*([,.!?;:])", r"\1", cleaned)  # comma stranded by a removed name ("...,.")
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)   # stranded space before punctuation
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)         # doubled space where a word was
    cleaned = re.sub(r"^[\s,;:]+", "", cleaned)          # stray leading comma from "{player}, ..."
    return cleaned.strip()


def speech_text(text: str, delivery: str = "") -> str:
    """What the model is asked to say: placeholders out, delivery edits in.

    Punctuation is the one pacing signal IndexTTS-2.5 reads from the text, so
    "trails off" becomes a closing "…" and "cut off" a closing dash. Only the
    spoken copy changes -- the say node, and the line on screen, keep the text
    exactly as written.
    """
    spoken = strip_placeholders(text)
    edits = {rule.get("text") for rule in delivery_rules(delivery)} - {None}
    if not spoken or not edits:
        return spoken
    if "hesitate" in edits:
        spoken = _hesitate(spoken)
    for name, edit in _ENDINGS:
        if name in edits:
            spoken = edit(spoken)
    return spoken


def duration_factor(speed: float) -> float:
    """Emotion-map speed -> IndexTTS-2.5 `duration_factor`: its inverse, in the
    0.5-2.0 the engine node accepts (>1.0 is slower there)."""
    speed = float(speed or 1.0)
    if speed <= 0:
        return 1.0
    return round(max(0.5, min(2.0, 1.0 / speed)), 3)


def encode_speed(speed: float) -> float:
    """The speed still owed at encode time.

    None when the `tts` template paces inside the model through
    `{{duration_factor}}` -- applying it again as atempo would double it. All
    of it when the template has no such token (the kaola `tts_indextts2`
    template, or an older edited copy), so pacing is never silently lost.
    """
    template = json.dumps(images.get_workflow("tts"))
    return 1.0 if "{{duration_factor}}" in template else float(speed)


def build_workflow(text: str, reference_clip: str, resolved: dict[str, Any],
                   prefix: str, seed: int, *, random: bool = False,
                   delivery: str = "") -> dict[str, Any]:
    """Fill the `tts` template.

    `random` -> the engine's `use_random` flag, off everywhere today. `seed`
    reaches the TTS Text node, so a fixed nonzero seed is reproducible, and a
    re-take gets its variety from fresh seeds rather than from this flag.
    `speed` becomes `duration_factor` (its inverse) for the shipped template;
    `speed` and `emo_alpha` also ride along raw for templates that want them.
    `delivery` edits the spoken text (see `speech_text`).
    """
    template = images.get_workflow("tts")
    classes = dict(workflows.DEFAULT_NODE_CLASSES)
    classes.update(store.get_setting("node_classes", {}) or {})
    values = {
        **classes,
        "text": speech_text(text, delivery),
        "reference_clip": reference_clip,
        "prefix": prefix,
        "seed": seed,
        "tts_use_random": bool(random),
        "speed": resolved["speed"],
        "duration_factor": duration_factor(resolved["speed"]),
        "emo_alpha": resolved["emo_alpha"],
        **{f"emo_{dim}": resolved["vec"].get(dim, 0.0) for dim in EMOTION_DIMS},
    }
    return workflows.fill(template, values)


async def synthesize(text: str, character: Character, emotion: str, intensity: float,
                     seed: int = 0, on_progress=None, delivery: str = "") -> dict[str, Any]:
    if not character.voice.reference_clip:
        raise RuntimeError(
            f"{character.display_name} has no reference clip. Cast the voice first -- "
            "a fixed clip IS the character's voice identity."
        )
    resolved = resolve_emotion(emotion, intensity, character, delivery)
    key = (text, emotion, round(intensity, 2), delivery, seed)
    prefix = f"{character.id}_{abs(hash(key)) % 10**8:08d}"
    staged_clip = await stage_reference_clip(character.voice.reference_clip)
    workflow = build_workflow(text, staged_clip, resolved, prefix, seed, delivery=delivery)

    entry = await comfyui.run_workflow(workflow, on_progress=on_progress)
    clips = comfyui.collect_audio(entry)
    if not clips:
        raise RuntimeError("ComfyUI finished but produced no audio -- check the TTS workflow's "
                           "SaveAudio node and the tts_text_class / tts_engine_class settings")

    clip = clips[0]
    data = await comfyui.view(clip["filename"], clip["subfolder"], clip["type"])
    ext = Path(clip["filename"]).suffix.lstrip(".") or "wav"
    raw_hash = assets.put(data, kind="audio", ext=ext,
                          meta={"character": character.id, "emotion": emotion,
                                "intensity": intensity, "resolved": resolved})

    ogg_hash, encoded = await to_ogg(raw_hash, speed=encode_speed(resolved["speed"]))
    return {
        "hash": ogg_hash,
        "raw_hash": raw_hash,
        "encoded": encoded,
        "resolved": resolved,
        "seed": seed,
        "voice_path": f"audio/voice/say_{ogg_hash[:12]}.ogg" if encoded
                      else f"audio/voice/say_{raw_hash[:12]}.{ext}",
    }


# --------------------------------------------------------------------------
# ogg encoding -- dramatically smaller than WAV for a fully voiced build
# --------------------------------------------------------------------------

def ffmpeg_path() -> Optional[str]:
    configured = store.get_setting("ffmpeg_path", "")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("ffmpeg")


async def to_ogg(asset_hash: str, quality: int = 5, speed: float = 1.0) -> tuple[str, bool]:
    """Transcode to Ogg Vorbis, applying any `speed` still owed.

    The shipped template paces inside the model (`duration_factor`), so callers
    pass `encode_speed(...)`, which is 1.0 then and nothing is stretched here.
    A template without that token gets its pacing as an `atempo` filter
    instead: pitch-preserving, but audibly smeared at whisper/crying pace.
    Needs ffmpeg; without it the raw clip is kept at its natural speed.
    """
    src = assets.path_for(asset_hash)
    ffmpeg = ffmpeg_path()
    if src is None or ffmpeg is None:
        return asset_hash, False
    if src.suffix.lower() == ".ogg":
        return asset_hash, True

    dest = db.ROOT / "tmp" / f"{asset_hash}.ogg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    filters = []
    if abs(float(speed) - 1.0) > 0.01:
        filters = ["-filter:a", f"atempo={max(0.5, min(2.0, float(speed))):.3f}"]

    def _run() -> bool:
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", str(src), *filters,
                 "-c:a", "libvorbis", "-q:a", str(quality), str(dest)],
                capture_output=True, timeout=180,
            )
            return result.returncode == 0 and dest.exists()
        except (OSError, subprocess.SubprocessError):
            return False

    ok = await asyncio.get_event_loop().run_in_executor(None, _run)
    if not ok:
        return asset_hash, False
    ogg_hash = assets.put(dest.read_bytes(), kind="audio", ext="ogg",
                          meta={"source": asset_hash, "speed": round(float(speed), 3)})
    dest.unlink(missing_ok=True)
    return ogg_hash, True


# --------------------------------------------------------------------------
# casting (doc S5 'Casting trick')
# --------------------------------------------------------------------------
# IndexTTS-2.5 has no described-voice mode -- it clones a reference clip -- so
# every take of an audition necessarily has the same timbre. Re-rolling the
# same neutral line at N sampler seeds therefore produced N near-identical
# clips: nothing about the voice can change, only micro-prosody. (The 2.5
# template does pass a real seed, unlike the old one, but with `use_random` off
# it is there for reproducibility, not variety.)
#
# What actually varies -- and what you need to hear before you freeze a clip --
# is how the timbre holds up under emotion: IndexTTS leaks timbre at high
# emotion strength, which is exactly what `voice.emotion_scale` caps. So an
# audition is one line per emotion, with text that fits the emotion, rendered
# deterministically (`use_random` off) so re-auditioning after a scale/speed
# change is a clean A/B.
#
# `voice.description` remains only a note for whoever sources the seed clip --
# it reaches neither this model nor the writer model. Its intended use is as a
# prompt you paste into a described-voice model (Chatterbox,
# github.com/resemble-ai/chatterbox) to synthesise a candidate clip. Cloning a
# real person needs their explicit consent; a frozen clip becomes every line the
# character speaks.

# `adoptable` marks the one take that would not be self-defeating to freeze --
# the neutral read. Nothing in the UI offers that any more (a take is a re-read
# of the clip you already have), but the flag stays in the job result so an API
# caller auditioning a clip it has NOT frozen knows which take is the safe one.
CASTING_TAKES: list[dict[str, Any]] = [
    {"emotion": "neutral", "intensity": 0.5, "adoptable": True,
     "text": "It's this way. Come on -- we're already late."},
    {"emotion": "happy", "intensity": 0.8,
     "text": "You actually came. I really didn't think you would!"},
    {"emotion": "angry", "intensity": 0.9,
     "text": "Do you have any idea what you just did?"},
    {"emotion": "melancholy", "intensity": 0.75,
     "text": "I told you already. I'm not going back there."},
    {"emotion": "afraid", "intensity": 0.8,
     "text": "Something moved. Right there -- did you not hear that?"},
]

# Kept for the /tts/casting-lines endpoint and older payloads that pass plain
# strings.
CASTING_LINES = [take["text"] for take in CASTING_TAKES]


def casting_script(script: Optional[list[dict[str, Any]]] = None,
                   lines: Optional[list[str]] = None,
                   takes: int = 0) -> list[dict[str, Any]]:
    """Normalise a cast payload into a list of {emotion, intensity, text, adoptable}.

    `script` wins; a bare `lines` list is read as text overrides on the default
    takes (so old callers still get one clip per line); otherwise the first
    `takes` defaults, or all of them.
    """
    if script:
        out = []
        for i, raw in enumerate(script):
            base = CASTING_TAKES[i % len(CASTING_TAKES)]
            emotion = str(raw.get("emotion") or base["emotion"])
            out.append({
                "emotion": emotion,
                "intensity": float(raw.get("intensity", base["intensity"])),
                "text": str(raw.get("text") or base["text"]),
                "adoptable": bool(raw.get("adoptable", emotion == "neutral")),
            })
        return out
    if lines:
        return [{**CASTING_TAKES[i % len(CASTING_TAKES)], "text": line,
                 "adoptable": CASTING_TAKES[i % len(CASTING_TAKES)].get("adoptable", False)}
                for i, line in enumerate(lines)]
    chosen = CASTING_TAKES[:takes] if takes > 0 else list(CASTING_TAKES)
    return [{**take, "adoptable": take.get("adoptable", False)} for take in chosen]


def clip_digest(reference_clip: str) -> str:
    """`voice_ref/<hash>.<ext>` -> `<hash>`; tolerant of a bare hash or ''."""
    if not reference_clip:
        return ""
    return reference_clip.split("/")[-1].split(".")[0]


def discard_audition(character, keep: Optional[set[str]] = None) -> int:
    """Delete a character's stored audition clips and clear the list.

    Auditions are disposable previews, so nothing references them once they are
    off the character -- without this they accumulate in the gallery, one raw
    clip plus one ogg per emotion, every time you press Test emotion range.
    """
    from . import assets as assets_svc

    protected = set(keep or ())
    protected.add(clip_digest(character.voice.reference_clip or ""))
    removed = 0
    for take in character.voice.audition:
        for digest in (take.hash, take.preview_hash):
            if digest and digest not in protected:
                if assets_svc.delete(digest, force=True).get("deleted"):
                    removed += 1
    character.voice.audition = []
    return removed


def save_reference_clip(character_id: str, data: bytes, ext: str = "wav") -> dict[str, str]:
    """Freeze the chosen clip. Changing it mid-project re-voices the whole game."""
    digest = assets.put(data, kind="voice_ref", ext=ext, meta={"character": character_id})
    path = assets.path_for(digest)
    return {"hash": digest, "path": str(path) if path else "",
            "reference_clip": f"voice_ref/{digest}.{ext}"}
