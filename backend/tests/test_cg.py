"""Event CG: the director pass, prompt assembly, LoRA slots, face pass.

Nothing here calls a model or ComfyUI. What it guards is the wiring: which
tags reach which slot of the prompt, which LoRA (if any) a CG loads, and
whether the workflow ComfyUI would be handed is actually a valid graph.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def app_modules():
    tmp = tempfile.mkdtemp(prefix="vn_cg_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app import db
    db.init_db()
    from app import defaults, store
    from app.services import context, generation, images, workflows
    for key, value in defaults.DEFAULT_SETTINGS.items():
        if store.get_setting(key, None) is None:
            store.set_setting(key, value)
    return {"defaults": defaults, "store": store, "context": context,
            "generation": generation, "images": images, "workflows": workflows}


@pytest.fixture(scope="module")
def scene(app_modules):
    """A project with one on-stage character, a player character, a location."""
    from app.models import Character, Location, Outfit
    store = app_modules["store"]
    pid = store.create_project("cg").id
    store.save_character(pid, Character(
        id="aiko", display_name="Aiko", age=22, gender="female",
        outfits=[Outfit(id="uniform", name="Uniform", tags="school uniform")],
        default_outfit="uniform",
    ))
    store.save_character(pid, Character(
        id="pc", display_name="You", age=21, gender="female", is_player=True))
    store.save_location(pid, Location(id="roof", name="Rooftop", tags="rooftop, fence"))
    return pid


def _state(location="roof", present=("aiko", "pc")):
    from app.graph import PathState
    return PathState(location=location, present=list(present),
                     outfits={"aiko": "uniform"}, time_of_day="dusk")


# --------------------------------------------------------------------------
# the director
# --------------------------------------------------------------------------

def test_director_template_renders_with_the_variables_assemble_cg_supplies(app_modules):
    """StrictUndefined: a variable the template wants and assemble_cg does not
    pass is a hard failure at generation time, not a blank."""
    ctx = app_modules["context"]
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.cg_director"]["body"]

    rendered = ctx.render(
        body,
        vocabulary=app_modules["defaults"].cg_vocab_block(),
        world="a coastal school", state="Location: Rooftop",
        cards="- Aiko (id: aiko).", subjects="Aiko", player_name="You",
        recent='Aiko: "You came."', moment='Aiko: "I waited."',
        next_beat="She looks away.", shot_hint="close, wind",
        instruction="",
    )

    assert "Aiko" in rendered and "You" in rendered
    # The two rules the whole pass depends on. The player's *face* is the ban,
    # not the player: "never put them in the shot" is unachievable on a hug and
    # the director improvised by writing a second character into the frame.
    assert "Their head and face may not be" in rendered
    assert "faceless male" in rendered
    assert "Never describe identity" in rendered
    # The vocabulary has to actually arrive, or the model invents tags.
    assert "imminent kiss" in rendered and "from below" in rendered


def test_the_director_is_told_what_makes_a_cg_worth_looking_at(app_modules):
    """A CG is the frame the player stops on. Accurate tags alone render flat:
    the template has to ask for a committed camera, directed light, and one
    physical detail, or every shot comes back an eye-level mid-shot."""
    defaults = app_modules["defaults"]
    body = defaults.DEFAULT_TEMPLATES["system.cg_director"]["body"]

    assert "Commit to a camera" in body
    assert "Give the light a direction" in body
    assert "physical detail" in body
    # ...and restraint, so "striking" doesn't turn every quiet beat loud.
    assert "does\nnot mean \"loud\"" in body or 'not mean "loud"' in body
    # The tags to do it with have to be in the vocabulary the director is given.
    vocab = defaults.cg_vocab_block()
    assert "sweat" in vocab and "white knuckles" not in vocab   # real tags only
    assert "backlighting" in vocab and "depth of field" in vocab

    # This section used to illustrate "a visceral detail" with phrases that are
    # not tags -- "white knuckles", "breath in cold air", "a hand pressed hard
    # enough to blanch" -- and the director copied the habit: 47% of the tags
    # it emitted across one real project did not exist. Every example here has
    # to be something the renderer has actually been trained on.
    examples = body.split("Name one physical detail")[1].split("##")[0]
    for phrase in ("white knuckles", "breath in cold air", "blanch"):
        assert phrase not in examples, f"{phrase!r} is not a tag"
    assert "`wet hair`" in examples and "`clenched hand`" in examples


def test_the_director_cannot_write_identity_or_outfit_tags(app_modules):
    """Identity comes from the character record every time, unchanged. A
    director-written hair tag either duplicates it or contradicts it."""
    clean = app_modules["generation"].clean_cg_tags

    out = clean("close-up, blonde hair, blue eyes, school uniform, "
                "leaning forward, 2girls, thighhighs")

    assert out == "close-up, leaning forward"


def test_vocabulary_tags_survive_the_identity_filter(app_modules):
    """Several real camera tags contain a banned word. Dropping them would
    delete exactly the gaze direction the director was asked for."""
    clean = app_modules["generation"].clean_cg_tags

    out = clean("closed eyes, averting eyes, floating hair, eye contact")

    assert out == "closed eyes, averting eyes, floating hair, eye contact"


def test_clean_cg_tags_dedupes_and_normalises(app_modules):
    clean = app_modules["generation"].clean_cg_tags
    assert clean("Close-Up,  close-up , from below.") == "close-up, from below"
    assert clean("") == ""


def test_assemble_cg_leaves_the_player_out_and_leads_with_the_speaker(app_modules, scene):
    """The PC is the camera in a first/second-person VN; rendering them is the
    single most common way a CG comes out wrong."""
    ctx, store = app_modules["context"], app_modules["store"]
    characters = {c.id: c for c in store.list_characters(scene)}

    subjects = ctx.cg_subjects(characters, _state(), speaker_id="aiko")

    assert [c.id for c in subjects] == ["aiko"]


def test_cg_subjects_are_capped_so_faces_do_not_blend(app_modules, scene):
    from app.models import Character
    ctx, store = app_modules["context"], app_modules["store"]
    for cid in ("b", "c"):
        store.save_character(scene, Character(id=cid, display_name=cid.upper(), age=20))
    characters = {c.id: c for c in store.list_characters(scene)}

    subjects = ctx.cg_subjects(characters, _state(present=("aiko", "b", "c", "pc")),
                               speaker_id="aiko")

    assert len(subjects) == 2


# --------------------------------------------------------------------------
# prompt assembly
# --------------------------------------------------------------------------

@pytest.fixture()
def corpus(app_modules):
    """A handful of real Danbooru rows on disk, so the cleaner's dictionary
    filters are live. Written rather than fetched: the import is a network
    call, and what these tests check is the filtering, not the download."""
    from app import db
    from app.services import tags as tag_svc

    path = tag_svc.corpus_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"name": "close-up", "count": 63509},
        {"name": "window", "count": 180133},
        {"name": "wet clothes", "count": 61763},
        {"name": "school desk", "count": 25233},
        {"name": "hug", "count": 173862},
        {"name": "backlighting", "count": 41000},
        {"name": "obscure thing", "count": 12},
    ]
    path.write_text(json.dumps({"tags": rows}), encoding="utf-8")
    tag_svc.load()
    try:
        yield rows
    finally:
        path.unlink(missing_ok=True)
        tag_svc.load()


def _bare(prompt: str) -> str:
    """A positive prompt with the emphasis syntax stripped back to plain tags.

    Weights are assigned by category at assembly time (`images.weigh`), so the
    tests that care about *ordering* -- which slot a tag lands in -- read the
    prompt through this rather than hard-coding `(close-up:1.1)` and breaking
    every time a weight is retuned.
    """
    import re
    return re.sub(r"\(([^():]+):[0-9.]+\)", r"\1", prompt)


def test_the_prompt_is_ordered_the_way_the_checkpoint_was_captioned(app_modules, scene):
    """Illustrious was captioned subject-count first and quality last, and the
    wrapper used to be the reverse of that: it opened with `masterpiece, best
    quality, newest, absurdres, highres` plus the style block, spending the
    region the encoder attends to most on words that describe no content, and
    left the count buried in the middle.

    The one ordering rule that predates this and still holds: body tags go
    *after* the identity block, because placing them before it measurably cost
    face fidelity.
    """
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")
    locations = {l.id: l for l in store.list_locations(scene)}

    prompt = images.assemble_cg_prompt(
        project, [aiko], _state(), locations,
        camera="close-up, from below", action="leaning forward, eye contact",
        expression="blushing")
    positive = _bare(prompt["positive"])

    assert "close-up, from below" in positive
    assert "leaning forward, eye contact" in positive
    assert positive.startswith("1girl"), "the subject count leads the prompt"
    assert positive.index("1girl") < positive.index("close-up")
    assert positive.index("1girl") < positive.index("leaning forward")
    # Quality and era tags trail, where they cost the least attention.
    assert positive.index("close-up") < positive.index("masterpiece")
    assert positive.rstrip().endswith("highres")
    # Identity, outfit and location still come from the records, not the LLM.
    assert "school uniform" in positive and "rooftop" in positive


def test_the_inert_lora_text_is_gone_from_the_prompt(app_modules, scene):
    """`<lora:name:0.55>` is A1111 syntax. ComfyUI's CLIPTextEncode does not
    parse it -- the LoraLoader node applies the LoRA -- so it was ten tokens of
    literal characters encoded directly in front of the identity block."""
    images, store = app_modules["images"], app_modules["store"]
    from app.models import Character, CharAssets

    store.save_character(scene, Character(
        id="lora_girl", display_name="Mika", age=22, gender="female",
        assets=CharAssets(lora="chr_mika.safetensors", trigger="chr_mika")))
    try:
        prompt = images.assemble_cg_prompt(
            store.get_project(scene), [store.get_character(scene, "lora_girl")],
            _state(present=("lora_girl",)),
            {l.id: l for l in store.list_locations(scene)}, camera="close-up")
        assert "<lora:" not in prompt["positive"]
        # The trigger word is a real token and stays.
        assert "chr_mika" in prompt["positive"]
    finally:
        store.delete_character(scene, "lora_girl")


def test_the_directors_sentence_rides_along_as_natural_language(app_modules, scene):
    """Illustrious takes natural language as support alongside tags, and the
    sentence is written before any tag is picked -- so it says the thing the
    tags approximate. It was generated, stored, shown to the author, and never
    sent to the renderer."""
    images, store = app_modules["images"], app_modules["store"]
    args = (store.get_project(scene), [store.get_character(scene, "aiko")],
            _state(), {l.id: l for l in store.list_locations(scene)})

    prompt = images.assemble_cg_prompt(
        *args, camera="close-up",
        description="She leans over the bed, both arms around his shoulders.")
    positive = prompt["positive"]
    assert "both arms around his shoulders" in positive
    # Support, not subject: it sits after the tags, before the quality block.
    assert positive.index("close-up") < positive.index("She leans")
    assert positive.index("She leans") < positive.index("masterpiece")

    store.set_setting("cg_prompt_description", False)
    try:
        off = images.assemble_cg_prompt(
            *args, camera="close-up", description="She leans over the bed.")
        assert "She leans" not in off["positive"]
    finally:
        store.set_setting("cg_prompt_description", True)


def test_a_sentence_cannot_reweight_the_prompt_it_sits_in(app_modules):
    """Parentheses are attention syntax. An unescaped one in a sentence would
    silently reweight whatever it encloses, and the director writes prose that
    contains them."""
    images = app_modules["images"]

    assert images.prompt_sentence("Close on her (from above), eyes shut.") == (
        r"Close on her \(from above\), eyes shut.")
    # One line, whatever the model returned.
    assert images.prompt_sentence("two\nlines   here") == "two lines here"
    assert images.prompt_sentence("") == ""

    # Capped: natural language is support, and the prompt already runs to
    # three CLIP chunks. Prefers a sentence boundary over a word break.
    long = ("She stands at the window with her back to him. " * 8)
    out = images.prompt_sentence(long)
    assert len(out) <= 240 and out.endswith(".")


def test_the_sprite_pose_enum_never_reaches_a_cg_prompt(app_modules, scene):
    """`front` / `arms_crossed` are sprite-slot keys, not Danbooru tags. The
    handler used to pass them through verbatim, so most CGs carried a literal
    underscore token that only diluted the conditioning."""
    images = app_modules["images"]

    assert images.POSE_CG_TAGS["front"] == ""
    assert images.POSE_CG_TAGS["arms_crossed"] == "crossed arms"
    assert "_" not in "".join(images.POSE_CG_TAGS.values())


def test_a_characters_resting_posture_never_reaches_an_image_prompt(app_modules, scene):
    """Posture is a pose, not an identity trait. Every generator already fills
    a pose slot of its own -- the director's action, the sheet's angle,
    POSE_TAGS on a sprite -- so a stance baked into the identity block fought
    all three, and a sheet batch taught the LoRA the stance as identity."""
    from app.models import TaggedField
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    # A local copy -- `scene` is module-scoped, and every test after this one
    # would otherwise inherit the stance.
    aiko = store.get_character(scene, "aiko").model_copy(deep=True)
    aiko.appearance.posture = TaggedField(label="arms crossed", tag="crossed arms")
    aiko.appearance.rest_expression = TaggedField(label="guarded", tag="serious")
    aiko.appearance.hair_color = TaggedField(label="blonde", tag="blonde hair")

    identity = images.identity_tags(aiko, aiko.outfits[0])
    assert "blonde hair" in identity          # real identity still locked in
    assert "crossed arms" not in identity
    assert "serious" not in identity

    sprite = images.assemble_sprite_prompt(project, aiko, "uniform", "happy")
    assert "crossed arms" not in sprite["positive"]
    sheet = images.assemble_cg_set_prompt(project, aiko, "uniform", "happy",
                                          angle="from side, profile")
    assert "crossed arms" not in sheet["positive"]

    # ...but the writer model still gets both, as characterisation.
    line = app_modules["context"].compose_appearance(aiko)
    assert "arms crossed" in line and "guarded" in line


def test_a_custom_appearance_field_falls_back_to_its_booru_tag(app_modules, scene):
    """"custom..." on the Appearance dropdown leaves no preset label, so the
    typed tag is all there is. It used to be dropped from the writer's
    appearance line entirely; now it stands in for the missing label."""
    from app.models import TaggedField
    images, store = app_modules["images"], app_modules["store"]
    aiko = store.get_character(scene, "aiko").model_copy(deep=True)
    aiko.appearance.face = TaggedField(label="", tag="sharp jawline")

    assert "sharp jawline" in app_modules["context"].compose_appearance(aiko)
    assert "sharp jawline" in images.identity_tags(aiko, aiko.outfits[0])


def test_shot_hint_is_the_fallback_when_nothing_is_distilled(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    locations = {l.id: l for l in store.list_locations(scene)}

    prompt = images.assemble_cg_prompt(
        project, [], _state(), locations, shot_hint="wide shot, empty roof")

    assert "wide shot, empty roof" in _bare(prompt["positive"])


def test_a_node_negative_reaches_the_negative_prompt(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")
    locations = {l.id: l for l in store.list_locations(scene)}

    prompt = images.assemble_cg_prompt(project, [aiko], _state(), locations,
                                       extra_negative="smile, multiple views")

    assert "smile" in prompt["negative"]


# --------------------------------------------------------------------------
# LoRA slots
# --------------------------------------------------------------------------

def test_a_cg_uses_the_lora_by_default_when_the_character_has_one(app_modules, scene):
    """A CG should get the same consistency a LoRA gives sprites -- it should
    just never be *blocked* by a character that has not been trained yet."""
    images, store = app_modules["images"], app_modules["store"]
    aiko = store.get_character(scene, "aiko")
    aiko.assets.lora = "chr_aiko_v1.safetensors"
    aiko.assets.lora_weight = 0.85
    store.save_character(scene, aiko)

    name, weight = images.cg_lora_for(aiko)

    assert name == "chr_aiko_v1.safetensors"
    # ...but at the CG weight, not the sprite weight -- low enough that the
    # pose stays free of the sheet's front-facing prior; the face pass covers
    # the rest of that gap.
    assert weight == aiko.assets.cg_lora_weight
    assert weight < aiko.assets.lora_weight


def test_explicit_none_still_skips_the_lora_for_one_shot(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    aiko = store.get_character(scene, "aiko")

    assert images.cg_lora_for(aiko, "none") == ("", 0.0)


def test_a_character_with_no_lora_trained_yet_just_renders_without_one(app_modules, scene):
    """The point of the fallback: an untrained character must not block CG
    generation, default mode or not."""
    images, store = app_modules["images"], app_modules["store"]
    pc = store.get_character(scene, "pc")

    assert pc.assets.lora == "" or pc.assets.lora is None
    assert images.cg_lora_for(pc) == ("", 0.0)


def test_auto_prefers_the_cg_lora_over_the_sprite_lora_when_both_are_trained(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    aiko = store.get_character(scene, "aiko")
    aiko.assets.lora_cg = "chr_aiko_cg.safetensors"
    store.save_character(scene, aiko)

    assert images.cg_lora_for(aiko)[0] == "chr_aiko_cg.safetensors"


def test_explicit_cg_never_falls_back_to_the_sprite_lora(app_modules, scene):
    """A CG that quietly used the flat LoRA after you asked for the varied one
    is exactly the confusion the explicit slot exists to remove -- only `auto`
    falls back, and only from the CG slot to the Sprite slot."""
    images, store = app_modules["images"], app_modules["store"]
    aiko = store.get_character(scene, "aiko")
    aiko.assets.lora_cg = ""
    store.save_character(scene, aiko)

    assert images.cg_lora_for(aiko, "cg") == ("", 0.0)


def test_the_project_setting_is_the_default_and_the_node_overrides_it(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    aiko = store.get_character(scene, "aiko")
    store.set_setting("cg_lora_mode", "sprite")
    try:
        assert images.cg_lora_for(aiko)[0] == "chr_aiko_v1.safetensors"
        assert images.cg_lora_for(aiko, "none") == ("", 0.0)
    finally:
        store.set_setting("cg_lora_mode", "auto")


def test_sprites_keep_using_the_sprite_lora_whatever_the_cg_setting_says(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")

    prompt = images.assemble_sprite_prompt(project, aiko, "uniform", "neutral")

    assert "<lora:chr_aiko_v1:0.85>" in prompt["positive"]


# --------------------------------------------------------------------------
# the face-detail pass
# --------------------------------------------------------------------------

def test_the_face_prompt_is_identity_only(app_modules, scene):
    """The pass re-renders a face crop, so framing, location and body tags are
    noise there -- and this is where the LoRA runs at full strength."""
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")

    prompt = images.assemble_face_prompt(project, aiko, _state(), "smiling")

    assert "face focus" in prompt["positive"]
    assert "smile" in prompt["positive"]
    assert "rooftop" not in prompt["positive"]
    assert f":{aiko.assets.face_lora_weight}>" in prompt["positive"]


def test_the_face_pass_replaces_the_flat_save_and_keeps_a_valid_graph(app_modules):
    images, workflows = app_modules["images"], app_modules["workflows"]

    wf = images.build_workflow(
        {"positive": "p", "negative": "n"}, width=1344, height=768, seed=7,
        prefix="cg_x", face_prompt={"positive": "fp", "negative": "fn"},
        face_lora="chr_aiko_v1.safetensors", face_lora_weight=0.9)

    assert "34" in wf and "35" in wf        # FaceDetailer + its save
    assert "8" not in wf                    # the flat save is gone
    assert wf["30"]["inputs"]["lora_name"] == "chr_aiko_v1.safetensors"
    assert wf["34"]["inputs"]["image"] == ["7", 0]
    # Nothing dangling: every link points at a node that is still in the graph.
    assert set(workflows.prune_disabled(wf)) == set(wf)


def test_with_no_lora_the_face_pass_hangs_off_the_checkpoint(app_modules):
    """A LoraLoader with an empty lora_name is a hard ComfyUI error, and no
    LoRA is now the common case."""
    images, workflows = app_modules["images"], app_modules["workflows"]

    wf = images.build_workflow(
        {"positive": "p", "negative": "n"}, width=1344, height=768, seed=7,
        prefix="cg_x", face_prompt={"positive": "fp", "negative": "fn"})

    assert "30" not in wf
    assert wf["34"]["inputs"]["model"] == ["1", 0]
    assert wf["31"]["inputs"]["clip"] == ["1", 1]
    assert set(workflows.prune_disabled(wf)) == set(wf)


def test_without_a_face_prompt_the_cg_is_the_plain_graph(app_modules):
    images = app_modules["images"]

    wf = images.build_workflow({"positive": "p", "negative": "n"},
                               width=1344, height=768, seed=7, prefix="cg_x")

    assert "8" in wf and "34" not in wf


# --------------------------------------------------------------------------
# the profile headshot (image.profile / character overview)
# --------------------------------------------------------------------------

def _portrait_character(app_modules, scene):
    from app.models import Character, Outfit
    store = app_modules["store"]
    store.save_character(scene, Character(
        id="mira", display_name="Mira", age=24, gender="female",
        appearance={"hair_color": {"label": "black", "tag": "black hair"},
                    "eye_color": {"label": "green", "tag": "green eyes"}},
        outfits=[Outfit(id="dress", name="Dress", tags="red dress")],
        default_outfit="dress",
    ))
    return store.get_character(scene, "mira")


def test_the_portrait_prompt_is_identity_and_expression_only(app_modules, scene):
    """The overview headshot is a portrait crop -- identity tags plus one
    expression, on a plain background. No scene, no location, no director."""
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    mira = _portrait_character(app_modules, scene)

    prompt = images.assemble_portrait_prompt(project, mira, "soft_smile")

    assert "portrait" in prompt["positive"]
    assert "black hair" in prompt["positive"] and "green eyes" in prompt["positive"]
    # EXPRESSION_TAGS["soft_smile"], not the raw enum key.
    assert "light smile, gentle expression" in prompt["positive"]
    assert "red dress" in prompt["positive"]        # the default outfit
    assert "1girl" in prompt["positive"]            # from the identity block
    # None of the CG/scene machinery leaks in.
    assert "rooftop" not in prompt["positive"]


def test_the_portrait_prefers_the_cg_lora_then_the_sprite_lora_then_none(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    _portrait_character(app_modules, scene)

    mira = store.get_character(scene, "mira")
    mira.assets.lora = "chr_mira_sprite.safetensors"
    mira.assets.lora_cg = "chr_mira_cg.safetensors"
    store.save_character(scene, mira)
    name, weight = images.portrait_lora(store.get_character(scene, "mira"))
    assert name == "chr_mira_cg.safetensors"
    # A headshot is all face, so it runs at the (higher) face-pass weight.
    assert weight == mira.assets.face_lora_weight
    assert "<lora:chr_mira_cg:" in images.assemble_portrait_prompt(
        project, store.get_character(scene, "mira"), "neutral")["positive"]

    mira = store.get_character(scene, "mira")
    mira.assets.lora_cg = ""
    store.save_character(scene, mira)
    assert images.portrait_lora(store.get_character(scene, "mira"))[0] == \
        "chr_mira_sprite.safetensors"

    mira = store.get_character(scene, "mira")
    mira.assets.lora = ""
    store.save_character(scene, mira)
    assert images.portrait_lora(store.get_character(scene, "mira")) == ("", 0.0)


# --------------------------------------------------------------------------
# the LoRA dataset
# --------------------------------------------------------------------------

def test_the_sheet_keeps_the_sprite_look(app_modules, scene):
    """The Sprite LoRA's job is drawing sprites, so its dataset matches what it
    will be asked for: solo, full body, white background. Variety belongs to
    the CG LoRA's own set, not here -- one dataset trying to be both was worse
    at both."""
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")

    sheet = images.assemble_sprite_prompt(
        project, aiko, "uniform", "neutral", wrapper="image.sheet_wrapper")

    assert "solo" in sheet["positive"]
    assert "full body" in sheet["positive"]
    assert "white background" in sheet["positive"]


def test_the_sheet_plan_samples_the_grid_instead_of_multiplying_it(app_modules):
    """outfits x angles x expressions reaches three figures on a character with
    a few outfits; the LoRA wants 15-30 images, not every cell."""
    images = app_modules["images"]
    angles = ["front view", "from side", "from behind"]
    expressions = ["neutral", "smiling", "angry", "crying", "surprised"]
    outfits = ["uniform", "casual", "swimsuit"]

    plan = images.sheet_plan(angles, expressions, outfits, count=20, seed=7)

    assert len(plan) == 20                       # the count, not the 45-cell grid
    assert len({(i["outfit_id"], i["angle"], i["expression"]) for i in plan}) == 20
    assert len({i["outfit_id"] for i in plan}) == 3
    assert images.sheet_plan(angles, expressions, outfits, count=20, seed=7) == plan


def test_a_count_past_the_grid_covers_every_combination_before_repeating(app_modules):
    images = app_modules["images"]

    plan = images.sheet_plan(["front view"], ["neutral", "smiling"], ["a"],
                             count=3, seed=1)

    assert len(plan) == 3
    assert len({(i["angle"], i["expression"]) for i in plan}) == 2   # both, then a repeat


def test_the_cg_set_samples_action_and_company_the_sheet_cannot_teach(app_modules):
    """A CG prompts on context and action from the player's point of view --
    neither of which a solo studio sheet contains."""
    images = app_modules["images"]

    plan = images.cg_set_plan(40, angles=[], expressions=[], outfit_ids=["uniform"],
                              actions=[], company=[], framings=[], seed=3)

    assert len(plan) == 40
    assert len({i["action"] for i in plan}) > 1
    assert len({i["framing"] for i in plan}) > 1
    # Both halves of the company axis have to actually show up: solo shots and
    # shots with someone else in frame (POV included).
    assert any(i["company"] == "solo" for i in plan)
    assert any(i["company"] != "solo" for i in plan)
    assert any("pov" in i["company"] for i in plan)
    assert any("faceless" in i["company"] for i in plan)


def test_a_pinned_cg_recipe_reproduces_itself(app_modules):
    """What "regenerate this one" relies on: single-element lists pin every
    axis, so only the seed varies."""
    images = app_modules["images"]

    plan = images.cg_set_plan(3, angles=["from above"], expressions=["crying"],
                              outfit_ids=["uniform"], actions=["kneeling"],
                              company=["solo"], framings=["close-up"], seed=5)

    assert all(i == {"outfit_id": "uniform", "angle": "from above",
                     "expression": "crying", "action": "kneeling",
                     "company": "solo", "framing": "close-up"} for i in plan)


def test_a_company_shot_declares_one_count_and_drops_the_solo_negatives(app_modules, scene):
    """The company tags carry the count, so the identity block must not emit a
    second one -- and the global negative's solo terms have to come back out or
    they fight every non-solo image in the set."""
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")

    solo = images.assemble_cg_set_prompt(project, aiko, "uniform", "neutral",
                                         company="solo", action="sitting")
    duo = images.assemble_cg_set_prompt(project, aiko, "uniform", "neutral",
                                        company="1girl, 1other, faceless male",
                                        action="sitting")

    assert solo["positive"].count("1girl") == 1
    assert duo["positive"].count("1girl") == 1
    assert "faceless male" in duo["positive"]
    assert "2girls" in solo["negative"] and "2girls" not in duo["negative"]
    # The action reaches the prompt in both -- that is the point of the set.
    assert "sitting" in solo["positive"] and "sitting" in duo["positive"]


def test_a_company_count_is_computed_from_the_pair_not_written_by_hand(app_modules, scene):
    """The company entries used to be expected to carry their own count, and a
    hardcoded one cannot be right: `1girl, 1boy` is wrong for a male subject,
    and a female subject beside a faceless *female* partner is `2girls` --
    which no arrangement of per-body `1girl` tags adds up to, because they
    dedupe back into one and the renderer draws one person from two bodies."""
    images, store = app_modules["images"], app_modules["store"]
    count_tag = images.count_tag

    assert count_tag(["female"]) == "1girl"
    assert count_tag(["female", "male"]) == "1girl, 1boy"
    assert count_tag(["female", "female"]) == "2girls"   # the impossible one
    assert count_tag(["male", "male"]) == "2boys"

    # Read off the company entry rather than declared by it.
    aiko = store.get_character(scene, "aiko")           # female
    assert images.company_count(aiko, "pov, faceless male, hug") == "1girl, 1boy"
    assert images.company_count(aiko, "pov, faceless female, hug") == "2girls"
    assert images.company_count(aiko, "pov") == "1girl"

    # And it reaches the prompt, once, in front of the company tags.
    duo = images.assemble_cg_set_prompt(store.get_project(scene), aiko, "uniform",
                                        "neutral", company="pov, faceless female, hug")
    assert "2girls" in duo["positive"]
    assert duo["positive"].count("1girl") == 0


def test_the_cg_set_teaches_a_faceless_second_body(app_modules):
    """A CG LoRA trained only on solo studio shots fights `faceless male` at
    render time -- it puts a face on the body, or builds the body out of the
    subject's own tags. The director now asks for that tag by name on any shot
    that brings the player's head into frame, so the set has to contain it.

    Faceless, and only faceless: a second real face in the dataset is a second
    identity for the LoRA to learn and then leak into its subject.
    """
    company = app_modules["defaults"].CG_SET_COMPANY

    faceless = [c for c in company if "faceless" in c]
    assert faceless, "the set has to contain a second body"
    assert any("faceless male" in c for c in faceless)
    assert any("faceless female" in c for c in faceless)
    # Solo still dominates: the partner is a much smaller job than the face.
    assert len([c for c in company if "solo" in c]) > len(faceless)
    # No counts, and no camera angles -- `cg_set_plan` samples the angle axis
    # independently, so one here would contradict whatever it drew.
    for entry in company:
        assert "1girl" not in entry and "1boy" not in entry, entry
        for angle in ("from behind", "from above", "from below", "from side"):
            assert angle not in entry, entry


def test_the_cg_set_wrapper_does_not_bake_in_the_studio_look(app_modules, scene):
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    aiko = store.get_character(scene, "aiko")

    cg = images.assemble_cg_set_prompt(
        project, aiko, "uniform", "neutral", framing="close-up",
        scene="indoors, dramatic lighting", action="leaning forward")

    assert "white background" not in cg["positive"]
    assert "full body" not in cg["positive"]
    assert "close-up" in cg["positive"]
    assert "indoors, dramatic lighting" in cg["positive"]


# --------------------------------------------------------------------------
# the setting, and who is in the shot
# --------------------------------------------------------------------------

def test_an_untagged_location_still_names_the_setting(app_modules):
    """A CG in a location nobody tagged came out with time-of-day and weather
    but no setting at all -- correctly lit, in nowhere. The background path had
    the name fallback inline all along, which is why one untagged location gave
    a described background and a location-less CG."""
    from app.models import Location
    subject = app_modules["context"].location_subject

    assert subject(Location(id="h", name="Hospital Room", tags="")) == "hospital room"
    # Explicit tags still win over the name.
    assert subject(Location(id="h", name="Hospital Room",
                            tags="hospital, bed")) == "hospital, bed"
    # Lowercased and stripped: the rest of the prompt is booru tags, and a
    # hand-typed trailing space would otherwise survive into it.
    assert subject(Location(id="h", name="Home ", tags="")) == "home"
    assert subject(None) == ""


def test_the_cg_prompt_carries_the_setting_not_just_the_lighting(app_modules, scene):
    """The regression this fixes: the lighting arrived, the room did not."""
    from app.models import Location
    images, store = app_modules["images"], app_modules["store"]
    characters = {c.id: c for c in store.list_characters(scene)}
    project = store.get_project(scene)

    untagged = Location(id="roof", name="Hospital Room", tags="")
    prompt = images.assemble_cg_prompt(
        project, [characters["aiko"]], _state(), {"roof": untagged})

    assert "hospital room" in prompt["positive"], prompt["positive"]
    # And the lighting that always worked still does.
    assert "dusk" in prompt["positive"] or "sunset" in prompt["positive"]


def test_the_background_and_the_cg_agree_on_an_untagged_location(app_modules, scene):
    """One location must not describe itself two different ways depending on
    which pipeline asked -- that divergence is exactly what hid this bug."""
    from app.models import Location
    images, store = app_modules["images"], app_modules["store"]
    project = store.get_project(scene)
    characters = {c.id: c for c in store.list_characters(scene)}

    untagged = Location(id="roof", name="Hospital Room", tags="")
    bg = images.assemble_background_prompt(project, untagged, "dusk", "")
    cg = images.assemble_cg_prompt(project, [characters["aiko"]], _state(),
                                   {"roof": untagged})
    assert "hospital room" in bg["positive"]
    assert "hospital room" in cg["positive"]


def test_a_hand_picked_cast_overrides_who_is_on_stage(app_modules, scene):
    """The automatic answer is a guess from who happens to be on stage; a shot
    of one person in a crowded room is a normal thing to want."""
    ctx, store = app_modules["context"], app_modules["store"]
    chars = {c.id: c for c in store.list_characters(scene)}
    state = _state()

    # No override: the speaker leads and the player is never a subject.
    assert [c.id for c in ctx.cg_subjects(chars, state, "aiko")] == ["aiko"]
    assert [c.id for c in ctx.cg_subjects(chars, state, "pc")] == ["aiko"]

    # An override keeps its order -- the first is the focal one whose LoRA and
    # face pass get used.
    assert [c.id for c in ctx.cg_subjects(chars, state, "aiko",
                                          override=["pc", "aiko"])] == ["pc", "aiko"]
    # The player is included when explicitly picked: every filter in the
    # automatic path exists to fix a guess, not to overrule an instruction.
    assert [c.id for c in ctx.cg_subjects(chars, state, "aiko",
                                          override=["pc"])] == ["pc"]
    # The two-subject cap is advice about face blending, not a rule the author
    # cannot overrule, so an explicit cast is not truncated.
    assert len(ctx.cg_subjects(chars, state, "aiko", limit=2,
                               override=["aiko", "pc", "aiko"])) == 3
    # An unknown id is dropped rather than exploding.
    assert [c.id for c in ctx.cg_subjects(chars, state, "aiko",
                                          override=["ghost", "aiko"])] == ["aiko"]
    # And an empty list means "work it out", not "nobody".
    assert [c.id for c in ctx.cg_subjects(chars, state, "aiko", override=[])] == ["aiko"]


def test_group_count_tags_scale_past_two(app_modules):
    """An explicit cast can exceed the automatic cap, so the count tag has to
    keep up -- 3girls, not a silently dropped third character."""
    from app.models import Character
    tag = app_modules["images"].group_count_tag

    def girl(cid):
        return Character(id=cid, display_name=cid.title(), age=22, gender="female")

    assert tag([girl("a")]) == "1girl"
    assert tag([girl("a"), girl("b")]) == "2girls"
    assert tag([girl("a"), girl("b"), girl("c")]) == "3girls"


# --------------------------------------------------------------------------
# the CG vocabulary
# --------------------------------------------------------------------------

def test_the_vocabulary_never_describes_what_a_body_looks_like(app_modules):
    """`_CG_ALLOWED` lets this vocabulary override the identity ban, so a size
    or shape word in it is a hole straight through that ban -- the director
    could then emit "large breasts" for a character whose record says
    otherwise, which is the two-people-in-one-body failure the ban exists to
    stop. Parts are fine (the camera can point at a collarbone); adjectives
    about parts are not.
    """
    defaults = app_modules["defaults"]

    # A size word is only a problem when it is sizing a *body*: "wide shot" is
    # a camera, "wide hips" is Natalie's appearance record. So the test is the
    # pair, not either word alone.
    sizes = {
        "large", "small", "huge", "gigantic", "big", "tiny", "medium",
        "wide", "narrow", "thick", "slim", "long", "short", "flat",
        "curvy", "petite", "busty", "muscular", "chubby", "skinny",
        "perky", "round", "bubble",
    }
    body = {
        "hips", "breasts", "breast", "ass", "butt", "waist", "thighs", "thigh",
        "legs", "leg", "hair", "eyes", "eye", "build", "chest", "bust",
        "body", "frame", "figure", "lips", "nose", "face", "skin", "areolae",
    }
    # Colour words are identity wherever they appear -- there is no
    # compositional reading of "blonde".
    colours = {"blonde", "blond", "brunette", "redhead", "pale", "tan",
               "tanned", "dark-skinned"}

    offenders = []
    for name, vocab in (("CG_VOCAB", defaults.CG_VOCAB),
                        ("CG_VOCAB_NSFW", defaults.CG_VOCAB_NSFW)):
        for group, tags in vocab.items():
            for tag in tags:
                words = set(tag.lower().replace("-", " ").split())
                if words & colours or (words & sizes and words & body):
                    offenders.append(f"{name}/{group}: {tag!r}")
    assert not offenders, (
        "these read as identity description, which the character record owns: "
        + ", ".join(offenders))


def test_every_ban_override_is_deliberate(app_modules):
    """A tag containing a banned word survives `clean_cg_tags` only because it
    is in the vocabulary. That rescue is necessary -- "closed eyes" is a gaze
    direction, not an eye colour -- but it has to stay a short, reviewed list
    rather than something a careless addition slips into."""
    defaults = app_modules["defaults"]
    from app.services.generation import _BANNED_TAG

    rescued = sorted(
        tag
        for vocab in (defaults.CG_VOCAB, defaults.CG_VOCAB_NSFW)
        for tags in vocab.values()
        for tag in tags
        if _BANNED_TAG.search(tag)
    )
    # Every one of these is a gaze direction, a transient state of the hair or
    # skin, a body part named as *composition*, or an action -- never a
    # permanent trait. The list grew when the ban was widened to cover shape
    # and proportion words ("hips", "ass", "bust", "build"), which is what
    # brings `ass focus`, `waist hug` and `thighs` in here: the part is the
    # subject of the shot, and only an adjective about it would be identity.
    #
    # The expression words are the other reason it grew: the node's own
    # `expression` field owns the face, so the ban covers `smile`, `frown`,
    # `worried` and the rest. `blush`, `tears`, `flushed` and `open mouth`
    # come back through here because they *add* to a face rather than
    # deciding it -- unlike `embarrassed`, which is a value of that enum and
    # was removed from the vocabulary instead of rescued.
    #
    # The adult vocabulary's own `body` group is the newest reason this list
    # grew, and it obeys the same rule: each of those is a part or a visible
    # state, something a frame either shows or does not, and not one is a size
    # or a shape -- `nipples` yes, `large breasts` never, because that is what
    # the character record owns. They are reachable only in an 18+ scene: the
    # rescue list is register-scoped, so an ordinary scene still bans them.
    expected = {
        "adjusting hair", "areolae", "ass", "ass focus", "ass grab",
        "averting eyes", "blood on face", "blush", "breast grab", "butt crack",
        "chin grab", "cleavage", "closed eyes", "covering breasts",
        "dirty face", "downcast eyes", "face focus", "face-to-face",
        "floating hair", "flushed", "hair between eyes", "hair grab",
        "half-closed eyes", "hand on another's chest", "hand on own chest",
        "hand over eyes", "head on chest", "jawline", "knees to chest",
        "messy hair", "naked shirt", "nipple slip", "nipple stimulation",
        "nipples", "open mouth", "penis", "pubic hair", "pussy",
        "rolling eyes", "scar", "shadow over eyes", "solo focus",
        "stroking own chin", "sunlight through hair", "tears", "testicles",
        # `thigh grab` joins the list because the ban now covers the singular
        # (for `thigh gap`). It is an action, so the rescue is correct.
        "thigh grab", "thighs", "waist hug", "wet hair", "wet skin",
    }
    assert set(rescued) == expected, (
        "the set of tags overriding the identity ban changed -- confirm each "
        "new one is compositional, not descriptive, then update this list.\n"
        f"added: {sorted(set(rescued) - expected)}\n"
        f"gone:  {sorted(expected - set(rescued))}")


def test_the_identity_ban_still_bites(app_modules):
    """The vocabulary grew a lot; the ban must not have been loosened by it."""
    from app.services.generation import clean_cg_tags

    for tag in ("long hair", "blue eyes", "large breasts", "school uniform",
                "blonde", "freckles", "twintails", "pantyhose", "1girl",
                "solo", "tattoo", "glasses", "dark skin"):
        assert clean_cg_tags(tag) == "", f"{tag!r} reached the prompt"


def test_the_ban_covers_shape_and_the_cut_names_that_never_say_hair(app_modules):
    """The list was written by enumerating words, so it missed the ones nobody
    thought of: `wide hips`, `round ass` and `bob cut` all reached the prompt,
    and the first two are on a real character's own record -- exactly the
    duplicate-or-contradict failure the ban exists to prevent."""
    from app.services.generation import clean_cg_tags

    for tag in ("wide hips", "round ass", "bubble butt", "narrow waist",
                "flat chest", "huge ass", "massive butt", "thick thighs",
                "cleavage", "large areolae", "curvy", "slender", "petite",
                "muscular", "plump", "average build",
                "bob cut", "pixie cut", "wolf cut", "undercut", "afro",
                "dark-skinned female", "stretch marks", "tanlines",
                "baby face", "square jaw", "tareme", "milf",
                "earrings", "necklace", "choker", "garter straps"):
        assert clean_cg_tags(tag) == "", f"{tag!r} reached the prompt"


def test_the_ban_leaves_the_director_its_own_job(app_modules):
    """Posture is what the director decides, and the appearance record's
    "resting posture" presets are written in the same words -- so these must
    NOT be banned, or the pass loses the half of its output that matters."""
    from app.services.generation import clean_cg_tags

    for tag in ("standing", "slouching", "crossed arms", "leaning forward",
                "arched back", "sitting", "all fours", "bent over"):
        assert clean_cg_tags(tag) == tag, f"{tag!r} was wrongly dropped"


def test_only_a_reviewed_handful_of_identity_presets_survive(app_modules):
    """A backstop on the ban's coverage, measured against the actual presets
    the appearance fields can emit rather than against a guess."""
    defaults = app_modules["defaults"]
    from app.services.generation import _BANNED_TAG

    survivors = {
        (entry.get("tag") or "").strip()
        for entries in defaults.TAG_VOCAB.values()
        for entry in entries
        if (entry.get("tag") or "").strip()
        and not _BANNED_TAG.search((entry.get("tag") or "").strip())
    }
    # Posture, which is the director's job and is written in the same words as
    # CG_VOCAB's own posture group, plus "bandaged arm" -- whose `bandaged` is
    # a legitimate transient detail. Everything else the appearance record can
    # say about a character is now covered, expression included.
    assert survivors == {
        "standing straight", "slouching", "crossed arms", "hand in pocket",
        "fidgeting", "bandaged arm",
    }, sorted(survivors)


def test_the_new_vocabulary_survives_the_cleaner(app_modules):
    """Every tag the director is offered has to be one it can actually emit --
    a vocabulary entry that the cleaner eats is worse than no entry, because
    the director spends attention on it and gets nothing."""
    defaults = app_modules["defaults"]
    from app.services.generation import clean_cg_tags

    eaten = []
    # The adult half is only offered to a scene that asked for it, so that is
    # the register it has to survive -- on an SFW scene the cleaner dropping it
    # is the gate working, not the vocabulary being eaten.
    for name, vocab, nsfw in (("CG_VOCAB", defaults.CG_VOCAB, False),
                              ("CG_VOCAB_NSFW", defaults.CG_VOCAB_NSFW, True)):
        for group, tags in vocab.items():
            for tag in tags:
                if clean_cg_tags(tag, nsfw=nsfw) != tag.lower():
                    eaten.append(f"{name}/{group}: {tag!r}")
    assert not eaten, f"vocabulary the cleaner would delete: {eaten}"


def test_the_adult_vocabulary_is_gated_on_the_scene(app_modules):
    """An explicit tag offered on every shot is one that turns up in a scene
    that never asked for it, and "ignore this unless" is not an instruction a
    7-12B holds. A project with no NSFW scene never sees these at all."""
    defaults = app_modules["defaults"]

    sfw = defaults.cg_vocab_block()
    adult = defaults.cg_vocab_block(nsfw=True)

    for tag in ("missionary", "fellatio", "doggystyle", "ahegao", "nude"):
        assert tag not in sfw, f"{tag!r} leaked into the SFW vocabulary"
        assert tag in adult, f"{tag!r} missing from the adult vocabulary"

    # The SFW half is still all there, and still first.
    for tag in ("close-up", "from below", "hug from behind", "collarbone"):
        assert tag in sfw and tag in adult
    assert adult.startswith(sfw)
    # And it says why the extra groups are there, so they do not read as just
    # more camera vocabulary.
    assert "adult (18+)" in adult


def test_the_director_gets_the_scene_register(app_modules, scene):
    """The gate is only worth anything if the scene's own flag reaches it."""
    from app.models import Node, NodeType
    store, ctx = app_modules["store"], app_modules["context"]
    from app.graph import Graph

    from app.models import Edge
    made = []
    for nsfw in (False, True):
        s = Node(id=f"sc_{nsfw}", type=NodeType.scene,
                 data={"title": f"scene {nsfw}", "nsfw": nsfw, "background": "roof"})
        n = Node(id=f"say_{nsfw}", type=NodeType.say,
                 data={"speaker": "aiko", "text": "Come here.", "cg": True})
        store.insert_node(scene, s)
        store.insert_node(scene, n)
        store.insert_edge(scene, Edge(id=f"e_{nsfw}", source=s.id, target=n.id))
        made.append((nsfw, n.id))

    graph = Graph.build(store.list_nodes(scene), store.list_edges(scene))
    for nsfw, node_id in made:
        assembled = ctx.assemble_cg(scene, graph, node_id)
        assert assembled["nsfw"] is nsfw, node_id
        has_adult = "missionary" in assembled["system"]
        assert has_adult is nsfw, (
            f"adult vocabulary {'leaked into' if has_adult else 'missing from'} "
            f"a scene with nsfw={nsfw}")


def test_the_vocabulary_covers_what_the_director_is_asked_for(app_modules):
    """The template asks for a committed camera, a body doing something, and
    one visceral detail on every shot. Each of those has to have enough
    vocabulary behind it to be answerable."""
    defaults = app_modules["defaults"]
    v = defaults.CG_VOCAB

    assert len(v["framing"]) >= 10 and len(v["angle"]) >= 8
    assert len(v["posture"]) >= 30, "postures are the body doing something"
    assert len(v["contact"]) >= 30, "two-character contact is most of a VN CG"
    assert len(v["detail"]) >= 30, "the visceral half the template insists on"
    assert "body" in v, "body parts as composition"

    # No duplicates inside a group, and the block renders one line per group.
    for group, tags in v.items():
        assert len(tags) == len(set(tags)), f"{group} repeats a tag"
    assert len(defaults.cg_vocab_block().splitlines()) == len(v)


# --------------------------------------------------------------------------
# batching candidates
# --------------------------------------------------------------------------

def test_a_batch_sets_the_latent_batch_size(app_modules):
    images, workflows = app_modules["images"], app_modules["workflows"]

    wf = images.build_workflow({"positive": "p", "negative": "n"},
                               width=1344, height=768, seed=7, prefix="cg_x", batch=4)

    assert wf["5"]["inputs"]["batch_size"] == 4
    # An int, not the string "4" -- ComfyUI validates the type and rejects the
    # prompt outright otherwise.
    assert isinstance(wf["5"]["inputs"]["batch_size"], int)
    assert set(workflows.prune_disabled(wf)) == set(wf)


def test_no_batch_asked_for_renders_exactly_one(app_modules):
    """The default has to stay 1: the overnight fill and the stale sweep both
    go through this path, and four times the images is four times the hours."""
    images = app_modules["images"]

    wf = images.build_workflow({"positive": "p", "negative": "n"},
                               width=1344, height=768, seed=7, prefix="cg_x")

    assert wf["5"]["inputs"]["batch_size"] == 1


def test_a_customised_workflow_still_gets_the_batch(app_modules):
    """Workflows are templates the author can replace from the config page, and
    one saved before batching existed has a literal 1 where the token now is.
    The caller's batch is stamped on regardless, or the picker would silently
    do nothing for exactly the people who customised the most."""
    images, store = app_modules["images"], app_modules["store"]
    workflows = app_modules["workflows"]

    edited = {k: dict(v) for k, v in workflows.TXT2IMG_NO_LORA.items()}
    edited["5"] = {"class_type": "EmptyLatentImage",
                   "inputs": {"width": "{{width}}", "height": "{{height}}",
                              "batch_size": 1}}
    saved = store.get_setting("workflows", {}) or {}
    store.set_setting("workflows", {**saved, "txt2img_no_lora": edited})
    try:
        # The stored template says 1, and it is still 1 with no batch asked for.
        assert images.build_workflow(
            {"positive": "p", "negative": "n"}, width=1344, height=768, seed=7,
            prefix="cg_x")["5"]["inputs"]["batch_size"] == 1
        wf = images.build_workflow({"positive": "p", "negative": "n"},
                                   width=1344, height=768, seed=7, prefix="cg_x", batch=3)
        assert wf["5"]["inputs"]["batch_size"] == 3
    finally:
        store.set_setting("workflows", saved)


# --------------------------------------------------------------------------
# the director cannot invent
# --------------------------------------------------------------------------

def test_the_schema_leaves_the_tags_free_and_asks_for_the_reasoning_first(app_modules):
    """The enum is gone on purpose, and what replaced it is two things.

    The palette was 276 tags against a six-figure dictionary, so the director
    could not say `window` or `wet clothes` at all; and an enum with
    `minItems` forces N members whether or not the model has N things to say,
    which is where "it adds random tags that don't make sense" came from.
    Those are one knob, and it cannot be set to satisfy both.

    `shot_description` is first because llama.cpp builds its grammar in
    property order: the model writes its read of the beat before it picks a
    single tag, and every tag is then conditioned on it.
    """
    from app.services.schemas import cg_prompt_schema

    schema = cg_prompt_schema()
    assert list(schema["properties"])[0] == "shot_description"
    assert "shot_description" in schema["required"]
    for field in ("camera", "action", "mood"):
        spec = schema["properties"][field]
        assert spec["type"] == "array", field
        assert "enum" not in spec["items"], f"{field} is no longer caged"
        # No floor at all: a model that is not forced to fill a slot stops
        # inventing filler for it.
        assert "minItems" not in spec, f"{field} must have no floor"
        assert spec["maxItems"] >= 8


def test_without_a_dictionary_the_validator_stays_out_of_the_way(app_modules):
    """A corpus is optional, and with only the curated few hundred in memory
    "unrecognised" would mean almost everything. A validator that empties
    every prompt is far worse than one that does nothing."""
    from app.services.generation import clean_cg_tags
    from app.services import tags as tag_svc

    assert not tag_svc.has_corpus(), "this fixture has no import"
    assert "warm atmosphere" in clean_cg_tags("close-up, warm atmosphere")


def test_invention_is_caught_by_the_dictionary_instead(app_modules, corpus):
    """What the enum used to guarantee, now enforced after the fact -- and the
    real vocabulary the enum was blocking now gets through."""
    from app.services.generation import clean_cg_tags

    kept = clean_cg_tags(
        "close-up, warm atmosphere, window, gentle embrace, wet clothes, "
        "white knuckles, school desk, soft focus")
    assert "close-up" in kept
    # Real tags the curated palette never had room for.
    assert "window" in kept and "wet clothes" in kept and "school desk" in kept
    for invented in ("warm atmosphere", "gentle embrace", "white knuckles"):
        assert invented not in kept, invented


def test_an_adult_scene_can_name_a_body_part(app_modules):
    """An 18+ scene came back describing what was happening but never what was
    visible, and two mechanisms conspired to do it.

    The adult palette held positions, acts and states and no anatomy at all, so
    the director was never *offered* a body-part tag for an adult beat. And any
    it reached for anyway was eaten on the way back: the identity ban screens
    anatomy in every register, because in an ordinary scene that comes from the
    character record, and its only exemption was the curated vocabulary.
    """
    from app.defaults import CG_VOCAB_NSFW
    from app.services.generation import clean_cg_tags

    # Named `body`, not `anatomy`: a group name becomes a tag category, and a
    # category nothing else knows has no weight and no bucket in the UI.
    assert "body" in CG_VOCAB_NSFW
    for tag in ("nipples", "cleavage", "pubic hair", "ass"):
        assert not clean_cg_tags(tag, nsfw=False), f"{tag} must not reach an SFW scene"
        assert clean_cg_tags(tag, nsfw=True) == tag, f"{tag} was dropped from an 18+ scene"


def test_the_adult_anatomy_list_cannot_argue_with_a_character_record(app_modules):
    """This list is what overrides the identity ban, so what goes in it decides
    what the director can contradict. A part is something a frame either shows
    or does not; a *size* is a fact about the character her record already
    supplies -- and `large breasts` on a character recorded flat is two
    instructions about one body."""
    from app.defaults import CG_VOCAB_NSFW
    from app.services.generation import clean_cg_tags

    sizes = ("breasts", "large breasts", "small breasts", "flat chest",
             "huge ass", "wide hips", "thigh gap")
    for tag in sizes:
        assert tag not in CG_VOCAB_NSFW["body"], tag
        assert not clean_cg_tags(tag, nsfw=True), f"{tag} reached an 18+ prompt"


def test_the_adult_palette_does_not_rescue_tags_on_an_ordinary_scene(app_modules):
    """The rescue list used to be both halves in every register. That was safe
    only while the adult vocabulary held no anatomy -- every tag in it was also
    caught by the adult screen, so the breadth made no difference. Anatomy
    broke it: `cleavage` is not a word that screen looks for, so being curated
    for adult scenes quietly exempted it from the identity ban on all of them.
    """
    from app.services.generation import clean_cg_tags

    # Adult-vocabulary tags stay out of an ordinary scene...
    for tag in ("hair grab", "cleavage", "underboob", "crotch"):
        assert not clean_cg_tags(tag, nsfw=False), tag
        assert clean_cg_tags(tag, nsfw=True) == tag, tag
    # ...while the curated SFW set is untouched in both registers. `straddling`
    # and `all fours` are the ones a blunt adult regex would eat.
    for tag in ("hug", "straddling", "all fours", "bare legs", "from behind"):
        assert clean_cg_tags(tag, nsfw=False) == tag, tag
        assert clean_cg_tags(tag, nsfw=True) == tag, tag


def test_every_adult_vocabulary_tag_is_a_real_one(app_modules, corpus):
    """The file's own rule for these lists, and the reason it matters: an
    invented tag does not fail, it conditions on nothing. Checked against the
    curated exemption rather than the corpus, because the corpus fixture is
    seven rows -- what this guards is that nobody adds a plausible-looking
    phrase to the palette."""
    from app.defaults import CG_ALLOWED, CG_VOCAB_NSFW

    for group, tags in CG_VOCAB_NSFW.items():
        for tag in tags:
            assert tag == tag.lower().strip(), f"{group}/{tag} is not normalised"
            assert tag in CG_ALLOWED, f"{group}/{tag} missing from the rescue list"


def test_record_tags_can_be_screened_the_way_director_output_is(app_modules, corpus):
    """Only the director's output was ever checked. A tag typed into a
    character or location record reaches ComfyUI verbatim, and a misspelled one
    does not fail -- it conditions on nothing and the render comes back subtly
    wrong with no error anywhere. One real project shipped `public hair` on a
    character and `cou` on a location."""
    from app.services import tags as tag_svc

    found = {u["tag"]: u["suggestion"] for u in tag_svc.check(
        ["hug", "close up", "public hair", "cou", "window"])}

    # Real tags are not reported at all -- an empty list means "clean".
    assert "hug" not in found and "window" not in found
    assert "public hair" in found and "cou" in found
    # A near miss carries its fix; a typo with no near miss is still reported.
    assert found["close up"] == "close-up"
    assert found["public hair"] == ""

    # Deduped and blank-tolerant, because it is fed a raw field.
    assert len(tag_svc.check(["cou", "cou", "", "  "])) == 1


def test_nothing_is_screened_before_a_dictionary_exists(app_modules):
    """Same rule the cleaner follows: with nothing to check against,
    "unrecognised" would mean everything and the warning would be worthless."""
    from app.services import tags as tag_svc

    assert not tag_svc.has_corpus(), "this fixture has no import"
    assert tag_svc.check(["public hair", "cou"]) == []


def test_a_tag_the_checkpoint_barely_saw_is_dropped(app_modules, corpus):
    """Real, in the dictionary, and still worthless: below a few hundred posts
    a checkpoint has seen a handful of images, which is indistinguishable from
    noise. The import floors at 100 by default, so this bites when the config
    page has fetched a wider dump."""
    from app.services.generation import clean_cg_tags

    assert "obscure thing" in clean_cg_tags("obscure thing, hug")
    assert "obscure thing" not in clean_cg_tags("obscure thing, hug", min_count=500)
    assert "hug" in clean_cg_tags("obscure thing, hug", min_count=500)


def test_the_adult_vocabulary_is_only_admitted_for_an_adult_scene(app_modules):
    """The gate used to be the grammar: an SFW beat was handed a schema built
    from the SFW groups only. Free-text fields give that up, so the same
    promise is kept by the validator instead."""
    from app.services.generation import clean_cg_tags

    assert "missionary" not in clean_cg_tags("hug, missionary", nsfw=False)
    assert "missionary" in clean_cg_tags("hug, missionary", nsfw=True)
    # Real dictionary tags nobody curated are screened the same way.
    assert "vaginal" not in clean_cg_tags("vaginal, standing", nsfw=False)
    # ...but a curated SFW posture is a judgement already made, and outranks
    # the screen even though it reads like adult vocabulary to a regex.
    for safe in ("all fours", "spread legs", "straddling"):
        assert safe in clean_cg_tags(f"{safe}, standing", nsfw=False), safe


def test_the_director_is_told_the_palette_is_a_floor_not_a_ceiling(app_modules):
    """Two wrong versions of this instruction, in opposite directions.

    The first said "only invent one when nothing in the list covers what is
    happening" -- a licence, and a recipe for `warm atmosphere`. The second
    said the format only accepted the listed values, which was true while the
    schema held an enum and is a lie now that it does not: it left the
    director spending its effort inside 276 tags when the checkpoint knows six
    figures of them.

    What is true now is the third thing: reach for any real tag, and an
    unreal one is dropped before the render."""
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.cg_director"]["body"]

    assert "only invent one when nothing in the list covers" not in body
    assert "the output format only accepts those values" not in body
    assert "Any real\nDanbooru tag is allowed" in body
    # And it names tags outside the palette, so "allowed" is concrete.
    for outside in ("window", "school desk", "wet clothes"):
        assert outside in body, f"{outside!r} shows the palette is not the limit"
    # It names the real failures so they are recognisable rather than abstract.
    for phrase in ("warm atmosphere", "gentle embrace", "white knuckles"):
        assert phrase in body, f"{phrase!r} should be shown as a counter-example"
    # And the word that produced two dozen of them is gone from the field's
    # description: asking for "atmosphere" got `warm atmosphere`.
    mood_section = body.split("- `mood`")[1].split("- `negative`")[0]
    assert "atmosphere" not in mood_section


def test_the_director_is_shown_its_own_output(app_modules):
    """There were no worked examples at all, which is the same gap the style
    examples had: a format described but never demonstrated."""
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.cg_director"]["body"]

    assert "worked example" in body.lower()
    assert body.count('"camera":') >= 2, "at least two demonstrated outputs"
    # The examples have to be valid under the schema, or they teach the wrong
    # shape -- lists, not comma-joined strings.
    assert '"camera": ["close-up"' in body or '"camera": ["upper body"' in body


def test_the_director_is_told_which_cameras_the_scene_already_used(app_modules):
    """The prompt asked it not to shoot every CG the same way and gave it
    nothing to compare against, so it returned `close-up, from above, pov`
    for four consecutive beats -- each defensible alone."""
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.cg_director"]["body"]
    assert "{{ recent_shots }}" in body


def test_assemble_cg_supplies_the_cameras_already_used(app_modules, scene):
    """And the variable has to actually arrive: under StrictUndefined a name
    the template wants and assemble_cg does not pass is a failed generation."""
    from app.graph import Graph
    from app.models import Edge, Node, NodeType
    store, ctx = app_modules["store"], app_modules["context"]

    s = Node(id="sc_shots", type=NodeType.scene,
             data={"title": "shots", "background": "roof"})
    first = Node(id="cg_one", type=NodeType.say,
                 data={"speaker": "aiko", "text": "One.", "cg": True,
                       "cg_camera": "close-up, from below"})
    second = Node(id="cg_two", type=NodeType.say,
                  data={"speaker": "aiko", "text": "Two.", "cg": True})
    for node in (s, first, second):
        store.insert_node(scene, node)
    store.insert_edge(scene, Edge(id="e_s1", source=s.id, target=first.id))
    store.insert_edge(scene, Edge(id="e_12", source=first.id, target=second.id))

    graph = Graph.build(store.list_nodes(scene), store.list_edges(scene))
    assembled = ctx.assemble_cg(scene, graph, second.id)
    assert "close-up, from below" in assembled["system"], (
        "the earlier beat's camera should reach the prompt")


# --------------------------------------------------------------------------
# per-shot CG tag switches
# --------------------------------------------------------------------------

def _cg(app_modules, scene, subjects, hidden=None, state=None, **kw):
    images, store = app_modules["images"], app_modules["store"]
    forced = kw.pop("forced", None)
    return images.assemble_cg_prompt(
        store.get_project(scene),
        [store.get_character(scene, cid) for cid in subjects],
        state or _state(),
        {l.id: l for l in store.list_locations(scene)},
        hidden=images.hidden_set(hidden),
        forced=images.hidden_set(forced), **kw)


def test_a_hidden_tag_is_dropped_from_this_shot(app_modules, scene):
    """A tag in a prompt is a request to find room for the thing, so shoes on a
    frame that ends at the shoulders come back as shoes, not as nothing."""
    store = app_modules["store"]

    character = store.get_character(scene, "aiko")
    character.outfits[0].tags = "school uniform, pleated skirt, brown loafers"
    store.save_character(scene, character)
    try:
        assert "brown loafers" in _cg(app_modules, scene, ["aiko"])["positive"]

        hidden = _cg(app_modules, scene, ["aiko"], hidden=["brown loafers"])
        assert "brown loafers" not in hidden["positive"]
        # Only that tag: the rest of the outfit is still on her.
        assert "pleated skirt" in hidden["positive"]
    finally:
        character = store.get_character(scene, "aiko")
        character.outfits[0].tags = "school uniform"
        store.save_character(scene, character)


def test_a_framing_cut_can_be_overruled_for_one_shot(app_modules, scene):
    """FRAMING_CUTOFF is a fact about the words and right nearly always, but
    "nearly" is the point: a close-up composed over a shoulder really can show
    a skirt, and a lookup table cannot know that. So the automatic cut is an
    opinion the author overrules per shot rather than a verdict."""
    store = app_modules["store"]

    character = store.get_character(scene, "aiko")
    character.outfits[0].tags = "school uniform, pleated skirt, brown loafers"
    store.save_character(scene, character)
    try:
        tight = _cg(app_modules, scene, ["aiko"], camera="close-up")
        assert "pleated skirt" not in tight["positive"]
        assert "pleated skirt" in tight["cast"][0]["out_of_frame"]

        back = _cg(app_modules, scene, ["aiko"], camera="close-up",
                   forced=["pleated skirt"])
        assert "pleated skirt" in back["positive"]
        # And it stops being reported as missing, because it is not.
        assert "pleated skirt" not in back["cast"][0]["out_of_frame"]
        # Only that one: the rest of the cut still holds.
        assert "brown loafers" not in back["positive"]
        assert "brown loafers" in back["cast"][0]["out_of_frame"]
    finally:
        character = store.get_character(scene, "aiko")
        character.outfits[0].tags = "school uniform"
        store.save_character(scene, character)


def test_switching_a_tag_off_still_wins_over_forcing_it_in(app_modules, scene):
    """The UI keeps the two lists exclusive, but the renderer must not depend on
    the UI for that -- a stale node from before could carry both, and "hidden"
    is the more specific instruction."""
    store = app_modules["store"]

    character = store.get_character(scene, "aiko")
    character.outfits[0].tags = "school uniform, pleated skirt"
    store.save_character(scene, character)
    try:
        both = _cg(app_modules, scene, ["aiko"], camera="close-up",
                   hidden=["pleated skirt"], forced=["pleated skirt"])
        assert "pleated skirt" not in both["positive"]
    finally:
        character = store.get_character(scene, "aiko")
        character.outfits[0].tags = "school uniform"
        store.save_character(scene, character)


def test_hiding_is_per_shot_and_leaves_every_other_render_alone(app_modules, scene):
    """The switch is a property of the frame, not of the character: the same
    character two beats later in a full-body shot still wants her shoes, and
    the sheet the LoRA trains on has to keep her whole."""
    images, store = app_modules["images"], app_modules["store"]

    character = store.get_character(scene, "aiko")
    character.outfits[0].tags = "school uniform, brown loafers"
    store.save_character(scene, character)
    try:
        character = store.get_character(scene, "aiko")
        project = store.get_project(scene)
        # Hidden on one shot...
        assert "brown loafers" not in _cg(
            app_modules, scene, ["aiko"], hidden=["brown loafers"])["positive"]
        # ...and on nothing else: the next CG, and the sprite, are untouched.
        assert "brown loafers" in _cg(app_modules, scene, ["aiko"])["positive"]
        sprite = images.assemble_sprite_prompt(
            project, character, character.default_outfit, "neutral", "front")
        assert "brown loafers" in sprite["positive"]
    finally:
        character = store.get_character(scene, "aiko")
        character.outfits[0].tags = "school uniform"
        store.save_character(scene, character)


def test_a_hidden_tag_applies_to_everyone_in_the_frame(app_modules, scene):
    """"This shot does not show feet" is a fact about the shot, so a second
    character standing in it does not get shoes either."""
    store = app_modules["store"]
    from app.models import Character, Outfit

    store.save_character(scene, Character(
        id="rei", display_name="Rei", age=20, gender="female",
        appearance={"hair_color": {"label": "black", "tag": "black hair"}},
        outfits=[Outfit(id="uniform", name="Uniform",
                        tags="school uniform, brown loafers")],
        default_outfit="uniform"))
    aiko = store.get_character(scene, "aiko")
    aiko.outfits[0].tags = "school uniform, brown loafers"
    store.save_character(scene, aiko)
    try:
        state = _state(present=("aiko", "rei", "pc"))
        state.outfits["rei"] = "uniform"
        prompt = _cg(app_modules, scene, ["aiko", "rei"],
                     hidden=["brown loafers"], state=state)
        assert "brown loafers" not in prompt["positive"]
        # Both are still in the shot, wearing everything else.
        assert prompt["positive"].count("school uniform") >= 1
        assert "black hair" in prompt["positive"]
    finally:
        aiko = store.get_character(scene, "aiko")
        aiko.outfits[0].tags = "school uniform"
        store.save_character(scene, aiko)
        store.delete_character(scene, "rei")


def test_the_prompt_reports_what_each_subject_contributed(app_modules, scene):
    """The inspector's switchboard is a render of this, so it has to come from
    the assembly itself -- a list rebuilt beside the renderer would drift, and
    a switch for a tag the shot never emits is worse than no switch."""
    store = app_modules["store"]

    character = store.get_character(scene, "aiko")
    character.outfits[0].tags = "school uniform, brown loafers"
    store.save_character(scene, character)
    try:
        cast = _cg(app_modules, scene, ["aiko"], hidden=["brown loafers"])["cast"]
        assert [c["character_id"] for c in cast] == ["aiko"]
        # Reported before the switches are applied: the badge for a disabled
        # tag still has to be there to click back on.
        assert "brown loafers" in cast[0]["outfit"]
        assert cast[0]["name"] == "Aiko"
    finally:
        character = store.get_character(scene, "aiko")
        character.outfits[0].tags = "school uniform"
        store.save_character(scene, character)


def test_the_face_pass_hides_the_same_tags(app_modules, scene):
    """The crop is part of the same picture; a tag suppressed in one half and
    not the other has the detailer arguing with the render underneath it."""
    images, store = app_modules["images"], app_modules["store"]
    from app.models import TaggedField

    character = store.get_character(scene, "aiko")
    mark = "hair ribbon"
    character.appearance.accessories = [TaggedField(label="ribbon", tag=mark)]
    store.save_character(scene, character)
    try:
        character = store.get_character(scene, "aiko")
        face = images.assemble_face_prompt(
            store.get_project(scene), character, _state(), "neutral",
            hidden=images.hidden_set([mark]))
        assert mark not in face["positive"]
    finally:
        character = store.get_character(scene, "aiko")
        character.appearance.accessories = []
        store.save_character(scene, character)


def test_the_prompt_asks_for_density_without_the_grammar_forcing_it(app_modules):
    """Floors are gone from the schema, so the instruction has to carry it --
    and it has to be an instruction, because the failure the floors produced
    (filler to satisfy a minimum) is worse than the one they fixed."""
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.cg_director"]["body"]

    assert "minItems" not in body
    # It still names how many, as guidance rather than as arithmetic.
    assert "3-6" in body and "4-10" in body


def test_a_close_up_does_not_ask_for_shoes(app_modules, scene):
    """Framing gates the body. A tag for something outside the frame is not
    ignored by the renderer -- it is a request to find room for it, which is
    how a shot composed at the shoulders comes back with a pair of feet in the
    corner."""
    images, store = app_modules["images"], app_modules["store"]

    character = store.get_character(scene, "aiko")
    character.outfits[0].tags = "school uniform, pleated skirt, brown loafers"
    store.save_character(scene, character)
    try:
        project = store.get_project(scene)
        locations = {l.id: l for l in store.list_locations(scene)}
        args = (project, [store.get_character(scene, "aiko")], _state(), locations)

        tight = _bare(images.assemble_cg_prompt(*args, camera="close-up")["positive"])
        assert "brown loafers" not in tight
        assert "pleated skirt" not in tight
        assert "school uniform" in tight

        wide = _bare(images.assemble_cg_prompt(*args, camera="full body")["positive"])
        assert "brown loafers" in wide and "pleated skirt" in wide

        # An unframed shot has not said the feet are out, so nothing is cut.
        assert "brown loafers" in _bare(images.assemble_cg_prompt(*args)["positive"])
    finally:
        character = store.get_character(scene, "aiko")
        character.outfits[0].tags = "school uniform"
        store.save_character(scene, character)


def test_an_ambiguous_framing_keeps_the_wider_reading(app_modules):
    """Directors write `close-up, full body` more often than they should, and
    cropping on the tighter reading is the destructive way to resolve it."""
    images = app_modules["images"]

    assert images.framing_cutoff("close-up, full body") == images.CUT_FEET
    assert images.framing_cutoff("close-up") == images.CUT_HEAD
    assert images.framing_cutoff("") == images.CUT_FEET


def test_emphasis_comes_from_what_a_tag_is(app_modules):
    """Weights are assigned by category in code, never written by the model:
    an LLM has no calibration for what 1.3 does to a sampler, and a free
    decimal field is one more thing to invent."""
    images = app_modules["images"]

    weighted = images.weigh("hug, close-up, indoors, sunlight")
    assert "(hug:1.15)" in weighted          # the subject of the shot
    assert "(close-up:1.1)" in weighted      # framing
    assert "(indoors:0.9)" in weighted       # backdrop, stepped back
    # Identity is never routed through this at all -- see assemble_cg_prompt.
    assert images.weigh("hug", enabled=False) == "hug"


def test_a_refinement_starts_from_the_image_not_from_noise(app_modules):
    """The case this exists for: a CG whose composition, lighting and
    expression are right and whose left hand is wrong. Re-rolling throws away
    the four good things to fix the one bad one."""
    images = app_modules["images"]
    prompt = {"positive": "1girl, hug", "negative": "worst quality"}

    wf = images.build_img2img_workflow(
        prompt, source_image="src.png", seed=7, prefix="r", denoise=0.4)

    # The latent comes from the source, so there is no EmptyLatentImage and no
    # width/height -- a refinement lands at the original's dimensions whatever
    # the CG settings currently say.
    classes = {n["class_type"] for n in wf.values()}
    assert "EmptyLatentImage" not in classes
    assert {"LoadImage", "VAEEncode", "KSampler"} <= classes
    assert wf["9"]["inputs"]["image"] == "src.png"
    assert wf["6"]["inputs"]["denoise"] == 0.4


def test_an_unmasked_refinement_drops_the_mask_loader_entirely(app_modules):
    """`LoadImage` fails the whole graph on a file it cannot find, so leaving
    the mask nodes in with an empty filename would error rather than refine."""
    images = app_modules["images"]

    wf = images.build_img2img_workflow(
        {"positive": "1girl", "negative": ""},
        source_image="src.png", seed=7, prefix="r", denoise=0.4)

    assert not {"11", "12", "13"} & set(wf)
    assert "SetLatentNoiseMask" not in {n["class_type"] for n in wf.values()}
    # And the sampler is repointed, or it would reference a node that is gone.
    assert wf["6"]["inputs"]["latent_image"] == ["14", 0]


def test_a_masked_refinement_confines_the_sampler_to_the_region(app_modules):
    """The difference between fixing a hand and re-rolling the picture hoping
    the hand improves: everything outside the mask is carried through the
    sampler untouched."""
    images = app_modules["images"]

    wf = images.build_img2img_workflow(
        {"positive": "1girl", "negative": ""}, source_image="src.png",
        mask_image="m.png", seed=7, prefix="r", denoise=0.4,
        lora="chr.safetensors")

    assert wf["11"]["inputs"]["image"] == "m.png"
    assert wf["13"]["class_type"] == "SetLatentNoiseMask"
    assert wf["6"]["inputs"]["latent_image"] == ["13", 0]
    # Masked *and* batched: the mask applies after the repeat, so every
    # candidate is confined to the same region.
    assert wf["13"]["inputs"]["samples"] == ["14", 0]
    # A LoRA'd refinement runs the sampler off the LoraLoader, not the ckpt.
    assert wf["6"]["inputs"]["model"] == ["2", 0]


def test_a_refinement_batch_repeats_the_source_latent(app_modules):
    images = app_modules["images"]
    wf = images.build_img2img_workflow(
        {"positive": "x", "negative": ""}, source_image="s.png", seed=1,
        prefix="r", denoise=0.5, batch=4)
    assert wf["14"]["class_type"] == "RepeatLatentBatch"
    assert wf["14"]["inputs"]["amount"] == 4
    assert wf["14"]["inputs"]["samples"] == ["10", 0]


def test_denoise_is_held_inside_the_range_where_it_means_something(app_modules):
    """At 1.0 the sampler starts from pure noise and the source may as well not
    be there -- a confusing thing for a slider labelled "adherence" to be able
    to do. At 0 it returns the source and looks like a broken render."""
    images = app_modules["images"]

    assert images.clamp_denoise(0.45) == 0.45
    assert images.clamp_denoise(0) == 0.05
    assert images.clamp_denoise(1.0) == images.MAX_REFINE_DENOISE
    assert images.clamp_denoise(99) == images.MAX_REFINE_DENOISE
    # A missing or unparseable value is a middling refinement, not a crash.
    assert images.clamp_denoise(None) == 0.45
    assert images.clamp_denoise("nonsense") == 0.45


def test_the_painted_region_arrives_as_png_bytes(app_modules):
    """It is drawn on a canvas in the browser and `toDataURL` hands back a data
    URL, so that is what crosses the wire."""
    import base64

    images = app_modules["images"]
    png = b"\x89PNG\r\n\x1a\n fake"
    encoded = base64.b64encode(png).decode()

    assert images.decode_mask(f"data:image/png;base64,{encoded}") == png
    assert images.decode_mask(encoded) == png
    # Nothing painted is not an empty mask -- it is "the whole frame", and the
    # builder drops the mask branch on the strength of this being falsy.
    assert images.decode_mask("") == b""
    assert images.decode_mask("   ") == b""

    with pytest.raises(RuntimeError):
        images.decode_mask("data:image/png;base64,not base64 at all!!")


def test_the_directors_own_emphasis_survives_the_category_pass(app_modules):
    """It did not. `for_prompt` escaped the parentheses of a tag that still
    carried its weight, and the category pass wrapped the result, so
    `(hug:1.25)` reached ComfyUI as `(\\(hug:1.25\\):1.15)` -- the literal
    characters as a prompt token, and the emphasis gone.

    That is the whole "say which part is the point" half of the director
    prompt: it is told to weight the one or two tags the shot fails without,
    and every one of them was being mangled.
    """
    images = app_modules["images"]

    weighted = images.weigh("(hug:1.25), leaning forward")
    assert "(hug:1.25)" in weighted
    assert "\\(" not in weighted, "the tag was escaped as literal text"
    # The shot's own decision wins over the category rule rather than
    # multiplying with it.
    assert "1.15" not in weighted
    assert "(leaning forward:1.1)" in weighted

    # Clamped at the documented ceiling: past 1.4 SDXL distorts anatomy before
    # it changes composition, which reads as the prompt not working.
    assert "(hug:1.4)" in images.weigh("(hug:1.9)")
    # And a weight of 1.0 is written bare rather than spending tokens saying so.
    assert images.weigh("(hug:1.0)") == "hug"


def test_a_weighted_tag_is_still_read_as_its_bare_tag(app_modules):
    """Everything that matches a tag against a set has to see through the
    emphasis, or the one tag the director marked as the point of the shot is
    the one tag every check misses."""
    images = app_modules["images"]

    assert images.split_weight("(hug:1.25)") == ("hug", 1.25)
    assert images.split_weight("hug") == ("hug", None)
    assert images.shot_tag_set("(hug:1.3), standing") == {"hug", "standing"}


def test_the_camera_does_not_repeat_the_last_few_shots(app_modules):
    """`recent_shots` has always been in the prompt and the director shot
    `close-up, from above, pov` four beats running anyway. Requests lose to
    whatever the beat suggests in isolation.

    What "unframed" means here is the whole point, and it is narrower than it
    looks. `pov` and `solo focus` are in the framing group but neither says
    how far away the camera is or where it stands, so a trim that leaves only
    those has not varied the shot -- it has deleted the composition and handed
    it back to the sampler. Observed live: the director answered
    `close-up, from above, pov, solo focus` and the shot reaching ComfyUI was
    `solo focus`.
    """
    from app.services.generation import vary_camera

    # A repeat goes when a real distance or angle survives it...
    varied = vary_camera("close-up, from below, pov, dutch angle",
                         ["close-up", "pov"])
    assert "close-up" not in varied
    assert "from below" in varied and "dutch angle" in varied

    # ...and stays when trimming would strip the framing out of the shot.
    assert vary_camera("close-up", ["close-up"]) == "close-up"
    assert vary_camera("close-up, from above, pov", ["close-up", "from above"]) \
        == "close-up, from above, pov"
    assert vary_camera("close-up, from above, pov, solo focus",
                       ["close-up", "from above", "pov"]) \
        == "close-up, from above, pov, solo focus"


# --------------------------------------------------------------------------
# the player's own body
# --------------------------------------------------------------------------

def test_the_camera_defaults_to_the_players_point_of_view(app_modules):
    """A second-person scene shot in third person is where the player starts
    appearing as a whole separate person. The prompt has always asked for POV;
    a distance and an angle look like a finished answer without it."""
    from app.services.generation import enforce_pov

    assert enforce_pov("close-up, from above") == "close-up, from above, pov"
    # Already said, in any of the ways that say it.
    assert enforce_pov("close-up, pov") == "close-up, pov"
    assert enforce_pov("upper body, pov hands") == "upper body, pov hands"
    # Runs after vary_camera, which is allowed to strip `pov` as a repeat.
    from app.services.generation import vary_camera
    assert "pov" in enforce_pov(vary_camera("close-up, from below, pov, dutch angle",
                                            ["close-up", "pov"]))
    # An empty camera falls back to the segmenter's shot_hint downstream; a
    # camera of nothing but `pov` would beat it while saying less.
    assert enforce_pov("") == ""


def test_contact_puts_the_player_in_frame_only_when_they_are_the_other_body(app_modules):
    """`hug` with one subject is a hug with the camera. With two it is a hug
    between them, and dragging the viewer in would invent a third person."""
    images = app_modules["images"]

    assert images.viewer_in_frame("pov", "hug, standing", 1)
    assert not images.viewer_in_frame("pov", "hug, standing", 2)
    # The viewer-named tags mean the viewer however big the cast is.
    assert images.viewer_in_frame("pov, pov hands", "standing", 2)
    assert images.viewer_in_frame("pov", "hand on viewer", 2)
    # An ordinary shot of one person is still a shot of one person.
    assert not images.viewer_in_frame("close-up, pov", "standing, looking at viewer", 1)
    # The weighted form is the *common* case, not the exception: the prompt
    # tells the director to weight the contact tag specifically, because a
    # contact tag is the first thing the encoder gives up on.
    assert images.viewer_in_frame("pov", "(hug:1.3), standing", 1)
    assert images.shot_tag_set("(hug:1.3), standing") == {"hug", "standing"}


def test_a_pov_embrace_counts_the_player_and_refuses_them_a_face(app_modules, scene):
    """The reported bug. `hug` asks for a second body; with `1girl` and her
    `school uniform` the only identity in the prompt, the renderer had one
    wardrobe to dress it in and the player came back wearing her clothes --
    with a face, in a shot taken from behind the player's eyes.

    Both halves are the fix: the count admits the body exists, and `faceless
    female` is the trained Danbooru idiom for a body with no face.
    """
    images, store = app_modules["images"], app_modules["store"]

    prompt = images.assemble_cg_prompt(
        store.get_project(scene), [store.get_character(scene, "aiko")],
        _state(), {l.id: l for l in store.list_locations(scene)},
        camera="upper body, pov", action="hug, standing",
        viewer=store.get_character(scene, "pc"))
    positive = _bare(prompt["positive"])

    assert "2girls" in positive, "the body the hug needs has to be counted"
    assert "faceless female" in positive
    # The count tag in the positive and `2girls` in the global negative would
    # fight, and one of the embrace or the anatomy loses.
    assert "2girls" not in prompt["negative"]
    # Still no identity for them: hair, eyes and face are what the camera
    # cannot see, and a LoRA is never loaded for the viewer.
    assert "solo" not in positive


def test_the_player_stays_out_of_frame_when_nothing_puts_them_in_it(app_modules, scene):
    """The default is unchanged: the player is the camera, and a shot of her
    standing on a roof contains one person."""
    images, store = app_modules["images"], app_modules["store"]

    prompt = images.assemble_cg_prompt(
        store.get_project(scene), [store.get_character(scene, "aiko")],
        _state(), {l.id: l for l in store.list_locations(scene)},
        camera="close-up, pov", action="standing, looking at viewer",
        viewer=store.get_character(scene, "pc"))
    positive = _bare(prompt["positive"])

    assert "1girl" in positive and "2girls" not in positive
    assert "faceless" not in positive


def test_a_shot_that_already_solved_the_face_is_left_alone(app_modules, scene):
    """`from behind` and `head out of frame` are the other two answers the
    prompt offers. Adding `faceless female` on top is a second instruction
    about one head, which is the same mistake as two expression tags."""
    images, store = app_modules["images"], app_modules["store"]
    args = (store.get_project(scene), [store.get_character(scene, "aiko")],
            _state(), {l.id: l for l in store.list_locations(scene)})
    pc = store.get_character(scene, "pc")

    for camera in ("upper body, pov, from behind", "pov, head out of frame"):
        positive = _bare(images.assemble_cg_prompt(
            *args, camera=camera, action="hug, standing", viewer=pc)["positive"])
        assert "faceless" not in positive, camera
        # The body is still counted -- that half is about anatomy, not faces.
        assert "2girls" in positive, camera


def test_the_players_visible_body_wears_its_own_clothes(app_modules, scene):
    """The actual mechanism of the bug: an arm reaching into frame is wearing a
    sleeve, and if the prompt does not say whose, the only sleeve on offer is
    hers."""
    images, store = app_modules["images"], app_modules["store"]
    from app.models import Outfit

    pc = store.get_character(scene, "pc")
    pc.outfits = [Outfit(id="hoodie", name="Hoodie", tags="grey hoodie, jeans")]
    pc.default_outfit = "hoodie"
    store.save_character(scene, pc)
    try:
        args = (store.get_project(scene), [store.get_character(scene, "aiko")],
                _state(), {l.id: l for l in store.list_locations(scene)})
        worn = _bare(images.assemble_cg_prompt(
            *args, camera="upper body, pov", action="hug, standing",
            viewer=store.get_character(scene, "pc"))["positive"])
        assert "grey hoodie" in worn and "school uniform" in worn

        # Out of frame, out of the prompt: their clothes arrive only with their
        # body, or every CG in the game carries a second wardrobe for nothing.
        alone = _bare(images.assemble_cg_prompt(
            *args, camera="close-up, pov", action="standing",
            viewer=store.get_character(scene, "pc"))["positive"])
        assert "grey hoodie" not in alone
    finally:
        pc.outfits = []
        pc.default_outfit = ""
        store.save_character(scene, pc)


def test_a_player_cast_by_hand_is_a_subject_not_the_camera(app_modules, scene):
    """`cg_subjects` takes an author's override literally, player included --
    every filter there exists to fix a guess. So a player the author put in the
    cast gets their real identity, and the faceless machinery stays out of it."""
    images, store = app_modules["images"], app_modules["store"]
    pc = store.get_character(scene, "pc")

    prompt = images.assemble_cg_prompt(
        store.get_project(scene), [store.get_character(scene, "aiko"), pc],
        _state(), {l.id: l for l in store.list_locations(scene)},
        camera="upper body", action="hug, standing", viewer=pc)
    positive = _bare(prompt["positive"])

    assert "2girls" in positive
    assert "faceless" not in positive


def test_the_director_is_told_how_to_keep_the_players_face_out(app_modules):
    """The prompt used to say only "NEVER put the player in the shot", which is
    unachievable on a hug and left the director to improvise -- usually by
    writing a second character into the frame."""
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.cg_director"]["body"]
    vocab = app_modules["defaults"].CG_VOCAB["pov"]

    assert "faceless male" in body and "faceless female" in body
    assert "head out of frame" in body
    # Offered in the palette as well as described, and curated so that
    # `clean_cg_tags` can never drop it on the way back.
    assert "faceless male" in vocab and "faceless female" in vocab
    from app.services.generation import clean_cg_tags
    assert clean_cg_tags("hug, faceless male") == "hug, faceless male"
