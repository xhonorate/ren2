"""TTS workflow assembly: the emotion vector reaches the graph, and timbre
reference clips are staged into ComfyUI's input dir before a job runs."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app_modules():
    tmp = tempfile.mkdtemp(prefix="vn_tts_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app import db
    db.init_db()
    from app.clients import comfyui
    from app.defaults import EMOTION_DIMS
    from app.services import assets, tts
    yield {"tts": tts, "assets": assets, "comfyui": comfyui, "dims": EMOTION_DIMS}
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]


# EMOTION_DIMS name -> TTS Audio Suite emotion-vector slider name.
DIM_TO_SLOT = {
    "happy": "Happy", "anger": "Angry", "sad": "Sad", "fear": "Afraid",
    "disgust": "Disgusted", "melancholy": "Melancholic", "surprise": "Surprised",
    "calm": "Calm",
}


def _node(wf, marker):
    """The node carrying `marker` as an input -- the 2.5 graph splits emotion,
    engine settings and synthesis across three nodes whose classes are all
    settings, so the tests find them by what they carry, not by class name."""
    return next(n for n in wf.values() if marker in (n.get("inputs") or {}))


def _node_id(wf, marker):
    return next(nid for nid, n in wf.items() if marker in (n.get("inputs") or {}))


def _emotion_node(wf):
    return _node(wf, "Afraid")


def test_every_emotion_dimension_reaches_the_workflow(app_modules):
    tts, dims = app_modules["tts"], app_modules["dims"]

    resolved = tts.resolve_emotion("angry", 1.0, None)
    assert resolved["vec"]["anger"] > 0            # sanity: the map uses "anger"

    wf = tts.build_workflow("She slammed the door.", "ref.wav", resolved, "px", 7)
    node = _emotion_node(wf)

    # "anger" lands on the wrapper's "Angry" slot -- it used to be dropped.
    assert node["inputs"]["Angry"] == resolved["vec"]["anger"]

    # Every EMOTION_DIMS entry reaches its slot as a real number, never an
    # unresolved "" left behind by a token that matches nothing.
    for dim in dims:
        slot = DIM_TO_SLOT[dim]
        assert slot in node["inputs"], slot
        assert node["inputs"][slot] == resolved["vec"][dim]
        assert isinstance(node["inputs"][slot], (int, float))


def test_slider_values_stay_within_the_wrapper_range(app_modules):
    tts = app_modules["tts"]
    # A hand-tuned map that pushes a single dim past the node's 1.2 ceiling.
    tts.store.set_setting("emotion_map", {"furious": {"vec": {"anger": 1.4}}})
    resolved = tts.resolve_emotion("furious", 1.0, None)
    assert resolved["vec"]["anger"] <= 1.2
    node = _emotion_node(tts.build_workflow("!", "ref.wav", resolved, "px", 1))
    assert all(0.0 <= v <= 1.2 for k, v in node["inputs"].items()
               if k in DIM_TO_SLOT.values())


def test_use_random_defaults_off_and_is_plumbed_into_the_workflow(app_modules):
    """Both line synthesis and auditions want determinism, so nothing passes
    random=True today -- but the flag stays wired for a caller that wants to
    re-roll the same text."""
    tts = app_modules["tts"]
    resolved = tts.resolve_emotion("neutral", 0.5, None)

    line = _node(tts.build_workflow("hi", "ref.wav", resolved, "px", 0), "use_random")
    assert line["inputs"]["use_random"] is False

    rolled = _node(
        tts.build_workflow("hi", "ref.wav", resolved, "px", 42, random=True),
        "use_random")
    assert rolled["inputs"]["use_random"] is True


def test_player_placeholder_is_stripped_before_synthesis(app_modules):
    """`{player}` is an author stand-in for the player's name -- TTS must not
    pronounce the word "player". Drop it and tidy the seam."""
    tts = app_modules["tts"]
    strip = tts.strip_placeholders

    assert strip("Hey {player}, over here.") == "Hey, over here."
    assert strip("I saw {player} yesterday.") == "I saw yesterday."
    assert strip("{player}, wait!") == "wait!"
    assert strip("That's {player}'s bag.") == "That's bag."
    assert strip("{ PLAYER } left.") == "left."
    assert strip("nothing to change here") == "nothing to change here"

    resolved = tts.resolve_emotion("neutral", 0.5, None)
    wf = tts.build_workflow("Careful, {player}.", "ref.wav", resolved, "px", 0)
    node = _node(wf, "text")
    assert "{player}" not in node["inputs"]["text"]
    assert node["inputs"]["text"] == "Careful."


def test_casting_script_covers_distinct_emotions_with_matching_lines(app_modules):
    tts = app_modules["tts"]
    script = tts.casting_script()

    assert len(script) >= 4
    emotions = [take["emotion"] for take in script]
    assert len(set(emotions)) == len(emotions)          # every take a different delivery
    assert len({take["text"] for take in script}) == len(script)
    assert emotions[0] == "neutral"
    # only a neutral read is safe to freeze as a timbre prompt
    assert [t["emotion"] for t in script if t["adoptable"]] == ["neutral"]

    resolved = [tts.resolve_emotion(t["emotion"], t["intensity"], None) for t in script]
    assert len({tuple(sorted(r["vec"].items())) for r in resolved}) == len(script)


def test_casting_script_honours_takes_lines_and_explicit_script(app_modules):
    tts = app_modules["tts"]

    assert len(tts.casting_script(takes=2)) == 2

    lines = tts.casting_script(lines=["one", "two"])
    assert [t["text"] for t in lines] == ["one", "two"]
    assert lines[0]["emotion"] == "neutral"

    explicit = tts.casting_script([{"emotion": "angry", "intensity": 1.0, "text": "out"}])
    assert explicit == [{"emotion": "angry", "intensity": 1.0, "text": "out",
                         "adoptable": False}]


def test_engine_emotion_and_save_nodes_are_wired(app_modules):
    """clip -> synthesis, emotion -> engine -> synthesis -> save. The classes
    are settings, so what is asserted here is the shape, not the names."""
    tts = app_modules["tts"]
    wf = tts.build_workflow("hi", "clip.wav", tts.resolve_emotion("happy", 1.0, None),
                            "px", 0)
    emo_id = _node_id(wf, "Afraid")
    engine_id = _node_id(wf, "use_random")
    text_id = _node_id(wf, "TTS_engine")
    clip_id = next(nid for nid, n in wf.items() if n.get("class_type") == "LoadAudio")

    assert wf[engine_id]["inputs"]["model_path"]        # a real model choice, not ""
    assert wf[engine_id]["inputs"]["emotion_control"][0] == emo_id
    assert wf[text_id]["inputs"]["TTS_engine"][0] == engine_id
    # The frozen clip IS the voice: it reaches synthesis directly rather than
    # through the suite's narrator voice folders.
    assert wf[clip_id]["inputs"]["audio"] == "clip.wav"
    assert wf[text_id]["inputs"]["opt_narrator"][0] == clip_id
    # a whole-token substitution keeps the seed an int, not "0"
    assert wf[text_id]["inputs"]["seed"] == 0
    assert isinstance(wf[text_id]["inputs"]["seed"], int)
    save = next(n for n in wf.values() if n.get("class_type") == "SaveAudio")
    assert save["inputs"]["audio"][0] == text_id


def test_no_placeholder_survives_into_the_workflow(app_modules):
    """A renamed token silently fills with "" (workflows.fill), which reaches
    ComfyUI as an empty class_type or input -- catch it here instead."""
    tts = app_modules["tts"]
    wf = tts.build_workflow("hi", "clip.wav", tts.resolve_emotion("happy", 1.0, None),
                            "px", 3)
    for node in wf.values():
        assert node["class_type"], node
        assert "{{" not in node["class_type"]
        for key, value in node["inputs"].items():
            assert value != "", (node["class_type"], key)
            if isinstance(value, str):
                assert "{{" not in value, (node["class_type"], key)


def test_reference_clip_is_uploaded_to_comfyui_and_the_token_rewritten(app_modules):
    tts, assets, comfyui = (app_modules["tts"], app_modules["assets"],
                            app_modules["comfyui"])

    digest = assets.put(b"RIFF....fake wav bytes", kind="voice_ref", ext="wav")
    reference = f"voice_ref/{digest}.wav"

    seen: dict[str, object] = {}

    async def fake_upload(data: bytes, filename: str, subfolder: str = ""):
        seen["data"] = data
        seen["filename"] = filename
        return {"name": "chr_aiko_ref.wav", "subfolder": "", "type": "input"}

    comfyui.upload_audio = fake_upload
    tts._staged_clips.clear()

    staged = asyncio.run(tts.stage_reference_clip(reference))

    assert staged == "chr_aiko_ref.wav"
    assert seen["data"] == b"RIFF....fake wav bytes"
    # cached: a second call for the same clip does not re-upload
    seen.clear()
    assert asyncio.run(tts.stage_reference_clip(reference)) == "chr_aiko_ref.wav"
    assert seen == {}


def test_staging_falls_back_to_the_raw_reference_when_upload_fails(app_modules):
    tts, assets, comfyui = (app_modules["tts"], app_modules["assets"],
                            app_modules["comfyui"])

    digest = assets.put(b"another fake clip", kind="voice_ref", ext="wav")
    reference = f"voice_ref/{digest}.wav"

    async def boom(*_a, **_k):
        raise RuntimeError("ComfyUI refused the upload")

    comfyui.upload_audio = boom
    tts._staged_clips.clear()

    assert asyncio.run(tts.stage_reference_clip(reference)) == reference


def test_staging_is_a_noop_for_an_empty_reference(app_modules):
    tts = app_modules["tts"]
    assert asyncio.run(tts.stage_reference_clip("")) == ""


def test_discard_audition_deletes_the_takes_but_never_the_frozen_clip(app_modules):
    """Takes are disposable previews; without this they pile up in the gallery,
    one raw clip plus one ogg per emotion on every test run."""
    tts, assets = app_modules["tts"], app_modules["assets"]
    from app.models import AuditionTake, Character

    frozen = assets.put(b"the frozen timbre clip", kind="voice_ref", ext="wav")
    raw = assets.put(b"an angry take", kind="voice_ref", ext="wav")
    ogg = assets.put(b"its speed-corrected preview", kind="audio", ext="ogg")

    character = Character(id="yuki", display_name="Yuki")
    character.voice.reference_clip = f"voice_ref/{frozen}.wav"
    character.voice.audition = [
        AuditionTake(hash=raw, preview_hash=ogg, emotion="angry", line="what?"),
    ]

    assert tts.discard_audition(character) == 2
    assert character.voice.audition == []
    assert assets.path_for(raw) is None and assets.path_for(ogg) is None
    assert assets.path_for(frozen) is not None      # the voice itself survives


def test_discard_audition_keeps_hashes_the_caller_is_about_to_reuse(app_modules):
    """A re-test reuses a hash whenever a take came out byte-identical (the
    store is content-addressed), so the prune must not delete the new set."""
    tts, assets = app_modules["tts"], app_modules["assets"]
    from app.models import AuditionTake, Character

    shared = assets.put(b"identical take", kind="voice_ref", ext="wav")
    stale = assets.put(b"a take that did change", kind="voice_ref", ext="wav")

    character = Character(id="yuki", display_name="Yuki")
    character.voice.audition = [
        AuditionTake(hash=shared, preview_hash="", emotion="neutral"),
        AuditionTake(hash=stale, preview_hash="", emotion="angry"),
    ]

    assert tts.discard_audition(character, keep={shared}) == 1
    assert assets.path_for(shared) is not None
    assert assets.path_for(stale) is None


def test_clip_digest_reads_a_stored_reference(app_modules):
    tts = app_modules["tts"]
    assert tts.clip_digest("voice_ref/abc123.wav") == "abc123"
    assert tts.clip_digest("abc123") == "abc123"
    assert tts.clip_digest("") == ""


# --------------------------------------------------------------------------
# delivery notes, pacing, sampling
# --------------------------------------------------------------------------

def test_delivery_note_edits_only_the_spoken_text(app_modules):
    """Punctuation is the pacing signal IndexTTS reads, so the note becomes
    punctuation -- on the copy sent to the model, never the node."""
    say = app_modules["tts"].speech_text

    assert say("I thought you'd stay.", "trails off") == "I thought you'd stay…"
    assert say("You never listen", "trailing, bitter") == "You never listen…"
    # a question keeps its rise; an existing ellipsis is already trailing
    assert say("You'd stay?", "trails off") == "You'd stay?"
    assert say("I just...", "trails off") == "I just..."

    assert say("Well, I suppose so.", "hesitant") == "Well… I suppose so."
    assert say("I don't know what you mean.", "hesitantly") == "I… don't know what you mean."
    assert say("No.", "hesitant") == "No."                        # nowhere to put it

    assert say("Don't you dare.", "cut off") == "Don't you dare—"
    assert say("Get out of my room.", "shouting") == "Get out of my room!"
    assert say("Why would you?", "shouting") == "Why would you?"

    # no note, or a note no rule knows, leaves the text alone
    assert say("Careful, {player}.", "") == "Careful."
    assert say("Fine.", "half-laughing") == "Fine."


def test_delivery_phrases_match_whole_words_only(app_modules):
    rules = app_modules["tts"].delivery_rules

    def names(note):
        return [r["name"] for r in rules(note)]

    assert names("too quick") == ["rushed"]
    assert names("FLAT") == ["flat"]
    assert names("flatter than usual") == []                     # not "flat"
    assert names("breakfast in hand") == []                      # not "fast"
    assert names("trails off, too quickly") == ["trailing", "rushed"]


def test_delivery_note_scales_speed_and_intensity(app_modules):
    tts = app_modules["tts"]
    plain = tts.resolve_emotion("sad", 0.8, None)
    shaped = tts.resolve_emotion("sad", 0.8, None, "trails off, flat")

    assert "delivery" not in plain
    assert shaped["delivery"] == ["trailing", "flat"]
    # flat halves the intensity before the vector is scaled
    assert shaped["vec"]["sad"] == pytest.approx(plain["vec"]["sad"] * 0.5, abs=1e-3)
    # trailing compounds onto the emotion map's own pace for "sad"
    assert shaped["speed"] == pytest.approx(plain["speed"] * 0.95, abs=1e-3)

    loud = tts.resolve_emotion("angry", 0.9, None, "shouting")
    assert loud["vec"]["anger"] <= 1.2                          # still a legal slider


def test_speed_reaches_the_model_as_duration_factor(app_modules):
    """Pacing happens inside the model, not as an atempo stretch afterwards."""
    tts = app_modules["tts"]
    resolved = tts.resolve_emotion("whisper", 0.5, None)
    assert resolved["speed"] < 1.0

    engine = _node(tts.build_workflow("hush", "ref.wav", resolved, "px", 0), "duration_factor")
    # the inverse: slower speech is a factor above 1.0
    assert engine["inputs"]["duration_factor"] == pytest.approx(1 / resolved["speed"], abs=1e-3)
    assert isinstance(engine["inputs"]["duration_factor"], float)
    assert engine["inputs"]["num_beams"] == 1
    # ...so nothing is left to stretch at encode time
    assert tts.encode_speed(resolved["speed"]) == 1.0

    assert tts.duration_factor(4.0) == 0.5                      # clamped to the node's range
    assert tts.duration_factor(0.1) == 2.0


def test_a_template_that_cannot_pace_falls_back_to_atempo(app_modules):
    """Paste the kaola template back and pacing must not silently vanish."""
    tts = app_modules["tts"]
    from app.services import workflows
    tts.store.set_setting("workflows", {"tts": workflows.TTS_INDEXTTS2})
    assert tts.encode_speed(0.85) == 0.85
