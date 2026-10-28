"""The LLM pipeline's contract: templates render, the schema constrains, and
the structuring result materialises into the nodes it claims to.

Nothing here calls a model. What it guards is the wiring around the model,
which is where the silent failures live: a template variable that no call site
supplies blows up only at generation time, and a field the schema allows but
build_nodes ignores is generated and thrown away with no error anywhere.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def app_modules():
    tmp = tempfile.mkdtemp(prefix="vn_gen_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app import db
    db.init_db()
    from app import defaults, store
    from app.services import context, generation, schemas
    for key, value in defaults.DEFAULT_SETTINGS.items():
        if store.get_setting(key, None) is None:
            store.set_setting(key, value)
    return {"defaults": defaults, "store": store, "context": context,
            "generation": generation, "schemas": schemas}


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------

def test_prose_template_renders_with_the_variables_assemble_supplies(app_modules):
    """StrictUndefined means a variable the template wants and assemble() does
    not pass is a hard failure at generation time, not a blank."""
    ctx = app_modules["context"]
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.prose_gen"]["body"]

    def render(**overrides):
        return ctx.render(body, **{
            "style_notes": "restrained", "pov": "second person", "tense": "present",
            "rating": "general audiences", "explicit": False,
            "world": "a coastal school", "character_cards": "### Aiko",
            "roster": "- Claire. Aiko's sister.", "examples": "--- example 1 ---",
            "state": "Location: rooftop", "summary": "(none)",
            "recent": "(nothing yet)", "brief": "she almost says it",
            "task": "Write the opening of this scene now.",
            "scene_length": "medium", "must_hit": "the letter comes up",
            "ends_with": "she leaves without answering", "tone": "restrained",
            "beat_target": "6-12 beats", "pc_policy": "The player speaks rarely.",
            "expressions": "neutral, smirking", "emotions": "neutral, amused",
            "poses": "front, sitting", "nsfw": False,
            **overrides,
        })

    rendered = render()

    assert "second person" in rendered
    assert "The player speaks rarely." in rendered
    # The format contract the structuring pass depends on.
    assert 'Name (face, feeling): "Spoken line."' in rendered
    assert "CHOICE:" in rendered
    assert "SFX:" in rendered
    # The performance vocabularies, which nothing else assigns any more.
    assert "neutral, smirking" in rendered and "neutral, amused" in rendered
    assert "front, sitting" in rendered
    assert "- Claire. Aiko's sister." in rendered
    assert "--- example 1 ---" in rendered
    # Scene-wide targets must arrive labelled as such, or the writer treats
    # them as instructions for this stretch and rushes the whole arc.
    assert "AS A WHOLE" in rendered
    assert "she leaves without answering" in rendered
    # The register is a scene property now, so an ordinary scene must not carry
    # the adult direction at all -- it used to hang off a project-wide rating.
    assert "This scene is explicit." not in rendered

    adult = render(nsfw=True)
    assert "## This scene is explicit" in adult
    assert "fade to black" in adult


def test_the_prose_template_specifies_the_notation_the_code_parses(app_modules):
    """The writer's notation and `extract_performance` are one contract with
    two halves. If the template stops asking for the bracket, nothing else
    assigns an expression and every line in the project comes out `neutral`."""
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.prose_gen"]["body"]

    assert 'Aiko (smirking, amused): "You\'re late again."' in body
    assert "MUSIC:" in body and "CG:" in body
    # The instruction the third pass used to carry, now carried here.
    assert "flat readout" in body


def test_structuring_template_renders_with_the_variables_structure_pass_supplies(app_modules):
    ctx = app_modules["context"]
    body = app_modules["defaults"].DEFAULT_TEMPLATES["system.structuring"]["body"]

    rendered = ctx.render(
        body,
        prose='Aiko: "You came."',
        character_ids="aiko, pc",
        location_ids="rooftop",
        outfit_table="aiko: uniform, casual",
        state="Location: rooftop\nPresent: Aiko in uniform",
        variables="affinity, met_aiko",
    )

    assert "aiko: uniform, casual" in rendered
    assert "Present: Aiko in uniform" in rendered
    assert "affinity, met_aiko" in rendered
    # The pass can only stage changes if it is told what is already true.
    assert "State at the start of this passage" in rendered


def test_character_card_omits_empty_fields(app_modules):
    """A blank "Wants:  | Fears:" is not neutral filler, and telling the model
    to match an empty SPEECH line is worse than saying nothing."""
    from app.models import Character, Personality
    store, ctx = app_modules["store"], app_modules["context"]
    pid = store.create_project("cards").id

    bare = Character(id="pc", display_name="You", age=21, is_player=True)
    card = ctx.character_card(pid, bare)
    assert "SPEECH" not in card
    assert "Wants:" not in card
    assert "Toward the player:" not in card          # they *are* the player
    assert "[PLAYER CHARACTER" in card

    filled = Character(id="aiko", display_name="Aiko", age=22, personality=Personality(
        speech_style="Short sentences.", wants="to be trusted",
        relationship_to_player="wary"))
    card = ctx.character_card(pid, filled)
    assert "Short sentences." in card
    assert "Wants: to be trusted." in card
    assert "Toward the player: wary" in card


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def test_schema_constrains_outfits_to_ids_that_exist(app_modules):
    schema = app_modules["schemas"].scene_schema(
        ["aiko"], ["rooftop"], ["uniform", "casual"], ["day", "dusk"])
    stage = schema["properties"]["nodes"]["items"]["anyOf"][0]["properties"]

    assert stage["outfits"]["additionalProperties"]["enum"] == ["uniform", "casual"]
    assert stage["time_of_day"]["enum"] == ["day", "dusk"]
    # Staging needs all of these to track state, not just who is on screen.
    assert {"hide", "weather", "location"} <= set(stage)


def test_structuring_schema_carries_no_performance_fields(app_modules):
    """Segmentation only. Expression, emotion and shot direction moved to the
    writer, who tags each line as it writes it -- leaving them here is what
    made a cold model answer `neutral` to every line it had ever seen."""
    schema = app_modules["schemas"].scene_schema(["aiko"], ["rooftop"])
    kinds = schema["properties"]["nodes"]["items"]["anyOf"]
    stage, narration, say = (k["properties"] for k in kinds[:3])

    assert set(say) == {"type", "speaker", "text"}
    assert set(narration) == {"type", "text"}
    assert "expressions" not in stage and "poses" not in stage


def test_schema_survives_a_project_with_no_outfits(app_modules):
    """New projects have no outfits; an empty enum would make the grammar
    unsatisfiable, so the field falls back to a free string."""
    schema = app_modules["schemas"].scene_schema(["aiko"], ["rooftop"], [])
    stage = schema["properties"]["nodes"]["items"]["anyOf"][0]["properties"]
    assert stage["outfits"]["additionalProperties"] == {"type": "string"}


# --------------------------------------------------------------------------
# performance notation, written by the writer and applied in code
# --------------------------------------------------------------------------

def test_a_tagged_line_gives_up_its_performance_and_its_clean_text(app_modules):
    gen = app_modules["generation"]
    clean, beats = gen.extract_performance(
        'Aiko (looking_away, melancholy, 0.7, trails off): "I waited."')

    assert clean == 'Aiko: "I waited."'          # what the segmenter sees
    assert beats == [{"kind": "say", "text": "I waited.",
                      "expression": "looking_away", "emotion": "melancholy",
                      "intensity": 0.7, "delivery": "trails off"}]


def test_the_tag_survives_the_shapes_a_writer_model_actually_produces(app_modules):
    """Square brackets and the tag after the colon are both banned by the
    template and both turn up anyway."""
    gen = app_modules["generation"]
    _, beats = gen.extract_performance(
        'Aiko [smirking, amused]: "You came."\n'
        'Aiko: (crying) "Don\'t."')

    assert beats[0]["expression"] == "smirking" and beats[0]["emotion"] == "amused"
    # A word in both vocabularies and nothing else: the voice matches the face.
    assert beats[1]["expression"] == "crying" and beats[1]["emotion"] == "crying"


def test_vocabulary_it_invented_becomes_the_delivery_note(app_modules):
    """`delivery` is free text, so an unrecognised word costs nothing -- and
    dropping it would throw away the one part of the tag the model meant."""
    gen = app_modules["generation"]
    _, beats = gen.extract_performance('Aiko (half-laughing, amused): "Sure."')

    assert beats[0]["emotion"] == "amused"
    assert beats[0]["delivery"] == "half-laughing"
    assert "expression" not in beats[0]


def test_a_narration_line_with_a_parenthetical_is_left_alone(app_modules):
    """The notation is a bracket in one specific place. A sentence that merely
    contains brackets is prose, and rewriting it would eat the author's text."""
    gen = app_modules["generation"]
    clean, beats = gen.extract_performance(
        "The door (the one nobody uses): still propped open.")

    assert clean == "The door (the one nobody uses): still propped open."
    assert beats[0]["kind"] == "narration"


def test_cues_bind_to_the_beat_they_belong_to(app_modules):
    """A sound sets up what follows; an illustration is of what just happened.
    Getting these backwards puts the knock after the door opens."""
    gen = app_modules["generation"]
    clean, beats = gen.extract_performance(
        "SFX: a knock at the door\n"
        "The door opens.\n"
        'Aiko (surprised, surprised): "You\'re early."\n'
        "CG: standing in the doorway, still holding her coat")

    assert "SFX:" not in clean and "CG:" not in clean
    assert beats[0]["sfx"] == "a knock at the door"
    assert beats[1]["cg"] == "standing in the doorway, still holding her coat"


def _passage():
    return {"scene_title": "Roof", "nodes": [
        {"type": "narration", "text": "Her eyes narrowed."},
        {"type": "say", "speaker": "aiko", "text": "You came."},
        {"type": "narration", "text": "She pushes the door open and steps through."},
        {"type": "say", "speaker": "aiko", "text": "Don't follow me."},
    ]}


def test_performance_lands_on_the_node_made_from_that_line(app_modules):
    gen = app_modules["generation"]
    out = gen.apply_performance(_passage(), [
        {"kind": "narration", "text": "Her eyes narrowed."},
        {"kind": "say", "text": "You came.", "expression": "glaring",
         "emotion": "irritated", "intensity": 0.7, "delivery": "flat",
         "pose": "arms_crossed"},
        {"kind": "narration", "text": "She pushes the door open and steps through."},
        {"kind": "say", "text": "Don't follow me.", "expression": "looking_away"},
    ])

    say = out["nodes"][1]
    assert say["expression"] == "glaring"
    assert say["emotion"] == "irritated"
    assert say["intensity"] == 0.7
    assert say["pose"] == "arms_crossed"
    assert out["nodes"][3]["expression"] == "looking_away"


def test_a_line_the_segmenter_lost_does_not_shift_every_tag_after_it(app_modules):
    """The regression this whole design exists for. Aligned by position, a
    dropped line puts every remaining expression on its neighbour -- which is
    what "the emotions are random" looked like from the graph."""
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "say", "speaker": "aiko", "text": "You came."},
        {"type": "say", "speaker": "aiko", "text": "Don't follow me."},
    ]}
    out = gen.apply_performance(passage, [
        {"kind": "say", "text": "You came.", "expression": "smiling"},
        {"kind": "say", "text": "I mean it.", "expression": "crying"},
        {"kind": "say", "text": "Don't follow me.", "expression": "glaring"},
    ])

    assert out["nodes"][0]["expression"] == "smiling"
    assert out["nodes"][1]["expression"] == "glaring"


def test_a_beat_that_matches_nothing_leaves_the_line_untagged(app_modules):
    """Better a `neutral` line than one wearing someone else's face."""
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "say", "speaker": "aiko", "text": "You came."},
        {"type": "say", "speaker": "aiko", "text": "Don't follow me."},
        {"type": "say", "speaker": "aiko", "text": "Ever."},
    ]}
    out = gen.apply_performance(passage, [
        {"kind": "say", "text": "Something else entirely.", "expression": "crying"},
    ])
    assert all("expression" not in n for n in out["nodes"])


def test_punctuation_the_segmenter_normalised_still_matches(app_modules):
    gen = app_modules["generation"]
    passage = {"nodes": [{"type": "say", "speaker": "aiko", "text": "I waited..."}]}
    out = gen.apply_performance(passage, [
        {"kind": "say", "text": "I waited\u2026", "expression": "looking_away"},
    ])
    assert out["nodes"][0]["expression"] == "looking_away"


def test_sound_lands_on_narration_as_well_as_dialogue(app_modules):
    gen = app_modules["generation"]
    out = gen.apply_performance(_passage(), [
        {"kind": "narration", "text": "Her eyes narrowed."},
        {"kind": "say", "text": "You came."},
        {"kind": "narration", "text": "She pushes the door open and steps through.",
         "sfx": "a door swinging wide"},
        {"kind": "say", "text": "Don't follow me."},
    ])
    assert out["nodes"][2]["sfx_cue"] == "a door swinging wide"


def test_only_the_first_cg_in_a_passage_is_honoured(app_modules):
    """Two illustrations in six beats is never right, and the second one is the
    writer over-reaching rather than a second real moment."""
    gen = app_modules["generation"]
    out = gen.apply_performance(_passage(), [
        {"kind": "narration", "text": "Her eyes narrowed."},
        {"kind": "say", "text": "You came.", "cg": "reaching for the handle"},
        {"kind": "narration", "text": "She pushes the door open and steps through.",
         "cg": "walking away down the stairwell"},
        {"kind": "say", "text": "Don't follow me."},
    ])
    assert out["nodes"][1]["cg"] is True
    assert out["nodes"][1]["shot_hint"] == "reaching for the handle"
    assert not out["nodes"][2].get("cg")


def test_music_lands_before_the_beat_that_called_for_it(app_modules):
    gen = app_modules["generation"]
    out = gen.apply_performance(_passage(), [
        {"kind": "narration", "text": "Her eyes narrowed."},
        {"kind": "say", "text": "You came."},
        {"kind": "narration", "text": "She pushes the door open and steps through."},
        {"kind": "say", "text": "Don't follow me.", "music": "sparse piano, colder"},
    ])

    assert out["nodes"][3]["type"] == "stage"
    assert out["nodes"][3]["music_cue"] == "sparse piano, colder"
    assert out["nodes"][4]["text"] == "Don't follow me."


def test_music_stop_cuts_it_instead_of_naming_a_cue(app_modules):
    gen = app_modules["generation"]
    out = gen.apply_performance(
        {"nodes": [{"type": "say", "speaker": "aiko", "text": "You came."}]},
        [{"kind": "say", "text": "You came.", "music": "stop"}])

    assert out["nodes"][0] == {"type": "stage", "present": [], "hide": [],
                               "stop_music": True}


# --------------------------------------------------------------------------
# staging reconciliation -- code, not a model
# --------------------------------------------------------------------------

def test_a_speaker_who_is_not_on_stage_gets_staged_first(app_modules):
    gen = app_modules["generation"]
    out = gen.reconcile_staging(_passage(), present=[], character_ids=["aiko"])

    assert out["nodes"][1] == {"type": "stage", "present": ["aiko"], "hide": []}
    # Only once -- she is on stage for every line after that.
    assert sum(1 for n in out["nodes"] if n["type"] == "stage") == 1


def test_nobody_is_staged_twice_when_the_passage_already_did_it(app_modules):
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "stage", "present": ["aiko"], "hide": []},
        {"type": "say", "speaker": "aiko", "text": "You came."},
    ]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["aiko"])
    assert len(out["nodes"]) == 2


def test_the_segmenter_restaging_what_it_already_staged_is_dropped(app_modules):
    """`show A / "A walks in" / show A` -- the doubled staging an author sees
    in the graph. The second node changes nothing, so it is not a node."""
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "stage", "present": ["aiko"], "hide": []},
        {"type": "narration", "text": "Aiko walks in."},
        {"type": "stage", "present": ["aiko"], "hide": []},
        {"type": "say", "speaker": "aiko", "text": "You came."},
    ]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["aiko"])

    assert [n["type"] for n in out["nodes"]] == ["stage", "narration", "say"]


def test_staging_that_only_restates_the_current_scene_is_dropped(app_modules):
    """A stage node re-declaring the location it is already in survives
    `build_nodes` (it has a location, so it looks like a change) and renders as
    a node that does nothing."""
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "stage", "present": [], "hide": [], "location": "rooftop",
         "time_of_day": "dusk"},
        {"type": "narration", "text": "The wind picks up."},
    ]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["aiko"],
                                location="rooftop", time_of_day="dusk")

    assert [n["type"] for n in out["nodes"]] == ["narration"]


def test_a_move_to_a_new_location_is_kept(app_modules):
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "stage", "present": [], "hide": [], "location": "classroom"},
    ]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["aiko"],
                                location="rooftop")
    assert out["nodes"][0]["location"] == "classroom"


def test_hiding_someone_who_is_not_on_stage_changes_nothing(app_modules):
    gen = app_modules["generation"]
    passage = {"nodes": [{"type": "stage", "present": [], "hide": ["aiko"]}]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["aiko"])
    assert out["nodes"] == []


def test_two_staging_nodes_in_a_row_become_one(app_modules):
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "stage", "present": [], "hide": [], "music_cue": "sparse piano"},
        {"type": "stage", "present": ["aiko"], "hide": []},
        {"type": "say", "speaker": "aiko", "text": "You came."},
    ]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["aiko"])

    assert len(out["nodes"]) == 2
    assert out["nodes"][0]["music_cue"] == "sparse piano"
    assert out["nodes"][0]["present"] == ["aiko"]


def test_someone_who_left_is_staged_again_when_they_speak(app_modules):
    """And the two moments stay two nodes: folding them together would hide
    and show her in the same breath."""
    gen = app_modules["generation"]
    passage = {"nodes": [
        {"type": "stage", "present": [], "hide": ["aiko"]},
        {"type": "say", "speaker": "aiko", "text": "One more thing."},
    ]}
    out = gen.reconcile_staging(passage, present=["aiko"], character_ids=["aiko"])
    assert out["nodes"][0]["hide"] == ["aiko"]
    assert out["nodes"][1]["present"] == ["aiko"]


def test_the_player_is_never_staged(app_modules):
    """They have no sprite, so the node would compile to nothing."""
    gen = app_modules["generation"]
    passage = {"nodes": [{"type": "say", "speaker": "pc", "text": "I did."}]}
    out = gen.reconcile_staging(passage, present=[], character_ids=["pc", "aiko"],
                                player_ids={"pc"})
    assert len(out["nodes"]) == 1


# --------------------------------------------------------------------------
# character stats
# --------------------------------------------------------------------------

def test_stats_expand_to_one_variable_per_character(app_modules):
    from app.graph import stat_defaults
    from app.models import Character, ProjectMeta, StatDef

    project = ProjectMeta(id="p", name="p", character_stats=[
        StatDef(id="affection", default="0"),
        StatDef(id="trust", default="5"),
    ])
    people = [Character(id="aiko", display_name="Aiko", age=22,
                        stat_overrides={"affection": "30"}),
              Character(id="claire", display_name="Claire", age=24)]

    assert stat_defaults(project, people) == {
        "affection_aiko": 30, "trust_aiko": 5,      # override wins
        "affection_claire": 0, "trust_claire": 5,
    }


def test_a_declared_variable_beats_the_stat_schema(app_modules):
    """A project that hand-declared `affection_aiko` before stats existed keeps
    its own default rather than being silently reset."""
    from app.graph import merged_flag_defaults
    from app.models import Character, ProjectMeta, StatDef, VariableDef

    project = ProjectMeta(id="p", name="p",
                          character_stats=[StatDef(id="affection", default="0")],
                          variables=[VariableDef(name="affection_aiko", type="int",
                                                 default="12")])
    people = [Character(id="aiko", display_name="Aiko", age=22)]
    assert merged_flag_defaults(project, [], people)["affection_aiko"] == 12


def test_the_card_reports_the_stat_as_it_stands_now(app_modules):
    """The point of stats on the card: the writer gets the current
    relationship, not the one written at character creation."""
    ctx = app_modules["context"]
    from app.models import Character, ProjectMeta, StatBand, StatDef

    project = ProjectMeta(id="p", name="p", character_stats=[
        StatDef(id="affection", name="Affection", bands=[
            StatBand(min=0, label="cold"),
            StatBand(min=21, label="warming", note="she initiates now"),
            StatBand(min=51, label="close"),
        ]),
        StatDef(id="progress", name="Route", in_card=False),
    ])
    aiko = Character(id="aiko", display_name="Aiko", age=22)

    line = ctx.stat_line(project, aiko, {"affection_aiko": 34, "progress_aiko": 2})
    assert line == "Affection 34 (warming) -- she initiates now"
    # in_card=False keeps a content pointer out of the writer's way.
    assert "Route" not in line


def test_the_player_card_carries_no_stats(app_modules):
    ctx = app_modules["context"]
    from app.models import Character, ProjectMeta, StatDef

    project = ProjectMeta(id="p", name="p",
                          character_stats=[StatDef(id="affection")])
    pc = Character(id="pc", display_name="You", age=21, is_player=True)
    assert ctx.stat_line(project, pc, {"affection_pc": 40}) == ""


def test_the_segmenter_is_told_what_a_point_of_affection_is_worth(app_modules):
    """It used to see a bare list of variable names, so `affection_diana += 1`
    was writable but unjudgeable -- nothing said the range was 0-100 or that a
    band is twenty points away. At +1 a warm choice that is twenty choices per
    band, i.e. a relationship that never visibly moves."""
    ctx = app_modules["context"]
    from app.models import Character, ProjectMeta, StatBand, StatDef

    project = ProjectMeta(id="p", name="p", character_stats=[
        StatDef(id="affection", name="Affection", min=0, max=100, bands=[
            StatBand(min=0, label="distant"), StatBand(min=20, label="warming"),
            StatBand(min=40, label="close"),
        ]),
    ])
    people = [Character(id="aiko", display_name="Aiko", age=22),
              Character(id="pc", display_name="You", age=21, is_player=True)]

    block = ctx.stat_schema_block(project, people)
    assert "0 to 100" in block
    assert "0 distant, 20 warming, 40 close" in block
    assert "A band is 20 points wide" in block
    # The player is not a relationship the writer tracks toward the player.
    assert "aiko" in block and "pc" not in block


def test_the_suggested_step_follows_the_bands_it_is_derived_from(app_modules):
    """Written down, the advice would go stale the moment the ladder is retuned
    in Config and quietly keep recommending the old scale."""
    ctx = app_modules["context"]
    from app.models import Character, ProjectMeta, StatBand, StatDef

    def block_for(width: int) -> str:
        project = ProjectMeta(id="p", name="p", character_stats=[
            StatDef(id="bond", name="Bond", min=0, max=width * 2, bands=[
                StatBand(min=0, label="low"), StatBand(min=width, label="high"),
            ]),
        ])
        return ctx.stat_schema_block(
            project, [Character(id="aiko", display_name="Aiko", age=22)])

    assert "warm beat is +2" in block_for(20)      # 20/10
    assert "turning point +7" in block_for(20)     # 20/3
    assert "warm beat is +1" in block_for(10)
    assert "turning point +3" in block_for(10)


def test_a_project_with_no_stats_says_nothing_about_them(app_modules):
    """An empty heading is not neutral -- it spends context telling a small
    model there is a mechanism here that it cannot use."""
    ctx = app_modules["context"]
    from app.models import Character, ProjectMeta

    people = [Character(id="aiko", display_name="Aiko", age=22)]
    assert ctx.stat_schema_block(ProjectMeta(id="p", name="p"), people) == ""


def test_a_new_project_starts_with_a_relationship_stat(app_modules):
    """The machinery was complete but unseeded, so nothing rendered: with no
    stat declared, every card reported the day-one `relationship_to_player`
    for the whole game however far the story had moved."""
    store = app_modules["store"]

    project = store.create_project("seeded")
    stats = {s.id: s for s in project.character_stats}

    assert "affection" in stats
    affection = stats["affection"]
    assert affection.type == "int" and affection.in_card
    # The bands are the working half -- a bare number is not a register.
    labels = [b.label for b in sorted(affection.bands, key=lambda b: b.min)]
    assert labels == ["distant", "warming", "close", "attached", "devoted"]
    assert all(b.note for b in affection.bands)
    # Zero means "nothing tracked yet", not "dislikes you" -- a hostile floor
    # would contradict the relationship line directly above it on the card.
    assert affection.band_for(0).label == "distant"


def test_the_seeded_stat_covers_characters_added_later(app_modules):
    """Nothing is enabled per character. The stat is declared once and
    `stat_defaults` instantiates it across the whole cast on read, so a
    character created after the project already has one."""
    store = app_modules["store"]
    from app.graph import stat_defaults
    from app.models import Character

    project = store.create_project("late arrivals")
    store.save_character(project.id, Character(
        id="yuki", display_name="Yuki", age=20, gender="female"))
    store.save_character(project.id, Character(
        id="you", display_name="You", age=21, gender="male", is_player=True))

    flags = stat_defaults(project, store.list_characters(project.id))
    assert flags["affection_yuki"] == 0

    # And it renders as behaviour once the story has moved it.
    ctx = app_modules["context"]
    yuki = store.get_character(project.id, "yuki")
    assert ctx.stat_line(project, yuki, {"affection_yuki": 64}) == (
        "Affection 64 (attached) -- assumes the player is on their side "
        "-- teasing, unguarded")


# --------------------------------------------------------------------------
# materialisation
# --------------------------------------------------------------------------

def _anchor(app_modules):
    from app.models import Node, NodeType
    return Node(id="s", type=NodeType.scene, x=0, y=0, data={"title": "Rooftop"})


def test_build_nodes_carries_staging_through(app_modules):
    gen = app_modules["generation"]
    parsed = {"scene_title": "x", "nodes": [{
        "type": "stage", "present": ["aiko"], "hide": ["pc"], "location": "rooftop",
        "time_of_day": "dusk", "weather": "wind",
        "outfits": {"aiko": "uniform"}, "expressions": {"aiko": "looking_away"},
    }]}
    nodes, _ = gen.build_nodes(parsed, "g1", ["aiko", "pc"], _anchor(app_modules))

    data = nodes[0].data
    assert data["show"] == ["aiko"] and data["hide"] == ["pc"]
    assert data["location"] == "rooftop" and data["time_of_day"] == "dusk"
    assert data["outfits"] == {"aiko": "uniform"}
    assert data["expressions"] == {"aiko": "looking_away"}


def test_build_nodes_drops_a_stage_node_that_changes_nothing(app_modules):
    gen = app_modules["generation"]
    parsed = {"scene_title": "x", "nodes": [
        {"type": "stage", "present": []},
        {"type": "narration", "text": "The door sticks."},
    ]}
    nodes, _ = gen.build_nodes(parsed, "g1", ["aiko"], _anchor(app_modules))
    assert [n.type.value for n in nodes] == ["narration"]


def test_cg_needs_something_to_illustrate(app_modules):
    """A cg flag with no shot_hint renders a generic shot of the room, so it
    is not honoured."""
    gen = app_modules["generation"]
    parsed = {"scene_title": "x", "nodes": [
        {"type": "narration", "text": "She turns.", "shot_hint": "turning away", "cg": True},
        {"type": "narration", "text": "Wind.", "cg": True},
        {"type": "say", "speaker": "aiko", "text": "Sit.", "expression": "serious",
         "emotion": "cold", "intensity": 0.4, "cg": True},
    ]}
    nodes, _ = gen.build_nodes(parsed, "g1", ["aiko"], _anchor(app_modules))

    assert nodes[0].data["cg"] is True          # has a shot_hint
    assert nodes[1].data["cg"] is False         # no shot_hint
    assert nodes[2].data["cg"] is False         # no shot_hint


def test_pose_is_carried_and_clamped(app_modules):
    gen = app_modules["generation"]
    parsed = {"scene_title": "x", "nodes": [
        {"type": "say", "speaker": "aiko", "text": "Sit.", "expression": "serious",
         "pose": "arms_crossed", "emotion": "cold", "intensity": 0.4},
        {"type": "say", "speaker": "aiko", "text": "Now.", "expression": "serious",
         "pose": "levitating", "emotion": "cold", "intensity": 0.4},
        {"type": "stage", "present": ["aiko"], "poses": {"aiko": "sitting"}},
    ]}
    nodes, _ = gen.build_nodes(parsed, "g1", ["aiko"], _anchor(app_modules))

    assert nodes[0].data["pose"] == "arms_crossed"
    assert nodes[1].data["pose"] == "front"        # not a real pose -> default
    assert nodes[2].data["poses"] == {"aiko": "sitting"}


def test_flag_writes_are_limited_to_declared_variables(app_modules):
    """An invented variable compiles to a `default` nothing ever reads, which
    is worse than no flag at all."""
    gen = app_modules["generation"]
    parsed = {"scene_title": "x", "nodes": [
        {"type": "set", "assignments": [
            {"var": "affinity", "op": "+=", "value": "1"},
            {"var": "hallucinated", "op": "=", "value": "true"},
        ]},
    ], "choice": {"prompt": "?", "options": [
        {"label": "Warm", "branch_hint": "closer",
         "effects": [{"var": "affinity", "op": "+=", "value": "2"},
                     {"var": "nonsense", "op": "=", "value": "1"}],
         "condition": "affinity >= 1"},
        {"label": "Cold", "branch_hint": "distance",
         "effects": [{"var": "made_up", "op": "=", "value": "9"}],
         "condition": "made_up >= 4"},
    ]}}
    nodes, _ = gen.build_nodes(parsed, "g1", ["aiko"], _anchor(app_modules),
                               variables=["affinity", "met_aiko"])

    set_node = nodes[0]
    assert set_node.type.value == "set"
    assert set_node.data["assignments"] == [{"var": "affinity", "op": "+=", "value": "1"}]

    options = nodes[-1].data["options"]
    assert options[0]["effects"] == [{"var": "affinity", "op": "+=", "value": "2"}]
    assert options[0]["condition"] == "affinity >= 1"
    # Every effect referenced an unknown name, and so did the condition.
    assert options[1]["effects"] == []
    assert options[1]["condition"] == ""


def test_a_set_node_with_nothing_left_is_dropped(app_modules):
    gen = app_modules["generation"]
    parsed = {"scene_title": "x", "nodes": [
        {"type": "set", "assignments": [{"var": "nope", "op": "=", "value": "1"}]},
        {"type": "narration", "text": "Wind."},
    ]}
    nodes, _ = gen.build_nodes(parsed, "g1", ["aiko"], _anchor(app_modules),
                               variables=["affinity"])
    assert [n.type.value for n in nodes] == ["narration"]


def test_flag_machinery_is_absent_when_no_variables_are_declared(app_modules):
    """An empty enum makes the grammar unsatisfiable, so the whole feature is
    withheld rather than offered with nothing to point at."""
    schema = app_modules["schemas"].scene_schema(["aiko"], ["rooftop"], [], None, [])
    kinds = [k["properties"]["type"].get("const") for k in
             schema["properties"]["nodes"]["items"]["anyOf"]]
    assert "set" not in kinds
    option = schema["properties"]["choice"]["properties"]["options"]["items"]["properties"]
    assert "effects" not in option

    schema = app_modules["schemas"].scene_schema(["aiko"], ["rooftop"], [], None, ["affinity"])
    kinds = [k["properties"]["type"].get("const") for k in
             schema["properties"]["nodes"]["items"]["anyOf"]]
    assert "set" in kinds
    option = schema["properties"]["choice"]["properties"]["options"]["items"]["properties"]
    assert option["effects"]["items"]["properties"]["var"]["enum"] == ["affinity"]


def test_missing_default_outfit_is_repaired_without_picking_the_nude_one(app_modules):
    """An empty default is not neutral -- generation falls back to outfits[0],
    which is the nude entry often enough to matter."""
    from app.models import Character, Outfit
    store = app_modules["store"]
    pid = store.create_project("defaults").id

    store.save_character(pid, Character(id="diana", display_name="Diana", age=45,
                                        outfits=[Outfit(id="none", name="None"),
                                                 Outfit(id="office", name="Office")],
                                        default_outfit=""))
    store.save_character(pid, Character(id="nude_only", display_name="Nia", age=30,
                                        outfits=[Outfit(id="none", name="None")],
                                        default_outfit=""))
    store.save_character(pid, Character(id="keep", display_name="Keeper", age=30,
                                        outfits=[Outfit(id="a", name="A"),
                                                 Outfit(id="b", name="B")],
                                        default_outfit="b"))

    store.migrate_missing_default_outfit()

    assert store.get_character(pid, "diana").default_outfit == "office"
    # Nothing else to choose, so the nude entry is still better than empty.
    assert store.get_character(pid, "nude_only").default_outfit == "none"
    # An existing choice is never overridden.
    assert store.get_character(pid, "keep").default_outfit == "b"
    assert store.migrate_missing_default_outfit() == 0


# --------------------------------------------------------------------------
# context range
# --------------------------------------------------------------------------

def _linear_project(app_modules, scenes: list[tuple[str, int]], big_cards: bool = False):
    """Build scenes as (title, line_count) and return (pid, graph, last_node)."""
    from app.models import Character, Node, NodeType, Edge, Personality
    from app.graph import Graph
    store = app_modules["store"]
    pid = store.create_project("ctx").id
    persona = Personality(summary="x" * 400, speech_style="y" * 400,
                          background="b" * 400) if big_cards else Personality(summary="dry")
    for name in ("Aiko", "Bella"):
        store.save_character(pid, Character(id=name.lower(), display_name=name, age=22,
                                            personality=persona))

    # Node ids are unique per project, and this module shares one database
    # across tests, so they have to be namespaced by project.
    tag = pid[-8:]
    nodes, edges, prev = [], [], None
    for si, (title, count) in enumerate(scenes):
        sid = f"{tag}s{si}"
        nodes.append(Node(id=sid, type=NodeType.scene, data={"title": title}, x=0, y=0))
        if prev:
            edges.append(Edge(id=f"e{sid}", source=prev, target=sid, source_handle="out"))
        prev = sid
        for li in range(count):
            nid = f"{sid}n{li}"
            nodes.append(Node(id=nid, type=NodeType.say, x=0, y=0, data={
                "speaker": "aiko" if li % 2 else "bella",
                "text": f"{title} line {li}. " + "padding " * 12}))
            edges.append(Edge(id=f"e{nid}", source=prev, target=nid, source_handle="out"))
            prev = nid
    for n in nodes:
        store.insert_node(pid, n)
    for e in edges:
        store.insert_edge(pid, e)
    return pid, Graph.build(store.list_nodes(pid), store.list_edges(pid)), prev


def test_the_scene_so_far_stops_at_the_scene_boundary(app_modules):
    """The verbatim tail is handed to the writer as "the scene so far", so
    pulling in the end of the *previous* scene told it finished material was
    what it is continuing."""
    import asyncio
    ctx = app_modules["context"]
    pid, graph, last = _linear_project(app_modules, [("Old scene", 25), ("Current scene", 3)])

    asm = asyncio.run(ctx.assemble(pid, graph, last, summarize=False))

    assert "Current scene" in asm.recent
    assert "Old scene" not in asm.recent


def test_the_adult_register_is_gated_on_the_scene_nsfw_flag(app_modules):
    """It used to hang off a world-bible rating, which described a whole story
    with one number -- so a story with one explicit scene in it was written as
    though every scene were. Then it was one value of a tone enum, which made
    the register and what the scene *is* the same axis."""
    import asyncio
    ctx, store = app_modules["context"], app_modules["store"]
    pid, graph, last = _linear_project(app_modules, [("Quiet scene", 3)])
    scene = next(n for n in store.list_nodes(pid) if n.type.value == "scene")

    plain = asyncio.run(ctx.assemble(pid, graph, last, summarize=False))
    assert "This scene is explicit" not in plain.system

    scene.data = {**(scene.data or {}), "nsfw": True}
    store.update_node(pid, scene)
    graph2 = __import__("app.graph", fromlist=["Graph"]).Graph.build(
        store.list_nodes(pid), store.list_edges(pid))
    adult = asyncio.run(ctx.assemble(pid, graph2, last, summarize=False))
    assert "## This scene is explicit" in adult.system


def test_a_scene_that_was_tagged_explicit_stays_explicit(app_modules):
    """The one direction the tone -> nsfw split must not fail in."""
    store = app_modules["store"]
    from app.models import Node, NodeType

    pid = store.create_project("tone-migration").id
    store.insert_node(pid, Node(id="n_tone", type=NodeType.scene,
                                data={"title": "S", "tone": "explicit"}))
    store.insert_node(pid, Node(id="n_tender", type=NodeType.scene,
                                data={"title": "T", "tone": "tender"}))

    assert store.migrate_scene_tone_to_nsfw() == 2

    assert store.get_node("n_tone").data["nsfw"] is True
    assert store.get_node("n_tender").data["nsfw"] is False
    # The rest of the enum is dropped: nothing reads it, and the prompt line
    # that did is gone from the template.
    assert "tone" not in store.get_node("n_tender").data


def test_a_characters_nsfw_notes_reach_only_an_nsfw_scene(app_modules):
    """Absent from the prompt entirely, rather than present under an
    instruction to ignore it -- a distinction a 24B does not reliably hold."""
    from app.models import Character, Personality
    store, ctx = app_modules["store"], app_modules["context"]
    pid = store.create_project("nsfw-card").id

    aiko = Character(
        id="aiko", display_name="Aiko", age=22, gender="female",
        personality=Personality(summary="Guarded, dry",
                                speech_style="Short sentences.",
                                nsfw="Goes quiet, and hates being asked why."))
    store.save_character(pid, aiko)

    plain = ctx.character_card(pid, aiko)
    assert "Guarded, dry" in plain
    assert "hates being asked why" not in plain

    adult = ctx.character_card(pid, aiko, nsfw=True)
    assert "Guarded, dry" in adult
    assert "hates being asked why" in adult


def test_the_card_is_unchanged_for_a_character_with_no_nsfw_notes(app_modules):
    """The subsection is one more thing that must not leave a dangling heading
    on a card that has nothing to put under it."""
    from app.models import Character, Personality
    store, ctx = app_modules["store"], app_modules["context"]
    pid = store.create_project("nsfw-card-empty").id
    aiko = Character(id="aiko", display_name="Aiko", age=22, gender="female",
                     personality=Personality(summary="Guarded, dry"))
    store.save_character(pid, aiko)

    assert ctx.character_card(pid, aiko, nsfw=True) == ctx.character_card(pid, aiko)
    assert "ADULT REGISTER" not in ctx.character_card(pid, aiko, nsfw=True)


def test_a_template_naming_something_no_longer_passed_still_renders(app_modules):
    """The forgiving fallback built its environment with an undefined
    *instance* where jinja2 wants the class, so it raised TypeError every time
    and `render` handed back the unrendered template. A prompt full of literal
    `{% if %}` tags is not a degraded generation, it is a nonsense one -- and it
    only ever happened on the path that exists to prevent exactly that."""
    ctx = app_modules["context"]

    out = ctx.render("Voice: {{ speech }}. {% if gone %}never{% endif %}"
                     "{{ personality.nsfw }}|", speech="dry")

    assert out == "Voice: dry. |"


def test_the_world_bible_no_longer_carries_a_rating(app_modules):
    from app.models import WorldBible
    assert not hasattr(WorldBible(), "rating")


def test_the_unsummarized_path_is_token_bounded(app_modules):
    """It used to take a flat chain[-40:], which on long narration could blow
    straight past the window."""
    import asyncio
    ctx, store = app_modules["context"], app_modules["store"]
    pid, graph, last = _linear_project(app_modules, [("Only scene", 80)])
    store.set_setting("verbatim_token_budget", 300)

    asm = asyncio.run(ctx.assemble(pid, graph, last, summarize=False))

    assert len(asm.recent.splitlines()) < 40
    store.set_setting("verbatim_token_budget", 2000)


def test_context_budget_is_enforced_and_reports_what_it_could_not_cut(app_modules):
    """context_token_budget was declared, shown in the config UI, and never
    read by anything."""
    import asyncio
    ctx, store = app_modules["context"], app_modules["store"]
    pid, graph, last = _linear_project(app_modules, [("Scene", 60)], big_cards=True)

    store.set_setting("context_token_budget", 100000)
    roomy = asyncio.run(ctx.assemble(pid, graph, last, summarize=False))

    store.set_setting("context_token_budget", 1500)
    tight = asyncio.run(ctx.assemble(pid, graph, last, summarize=False))

    assert tight.token_estimate < roomy.token_estimate
    # Two characters are on stage and their cards are the floor, so the budget
    # cannot actually be met -- that gets reported rather than hidden.
    assert tight.over_budget > 0

    store.set_setting("context_token_budget", 6000)


def test_generated_branch_attaches_on_the_option_handle(app_modules):
    """An option counts as wired only when an edge leaves the choice on a
    handle matching the option id."""
    gen = app_modules["generation"]
    from app.models import Node, NodeType
    choice = Node(id="q", type=NodeType.choice, x=0, y=0, data={
        "prompt": "?", "options": [{"id": "o1", "label": "Push"},
                                   {"id": "o2", "label": "Wait"}]})
    parsed = {"scene_title": "x", "nodes": [{"type": "narration", "text": "Silence."}]}

    _, edges = gen.build_nodes(parsed, "g1", ["aiko"], choice, "o2")
    assert edges[0][0] == "q" and edges[0][1] == "o2"

    _, plain = gen.build_nodes(parsed, "g1", ["aiko"], choice)
    assert plain[0][1] == "out"


def _choice(cid: str, options: int, parent_id=None):
    from app.models import Node, NodeType
    return Node(id=cid, type=NodeType.choice, x=0, y=0, parent_id=parent_id, data={
        "prompt": "?",
        "options": [{"id": f"o{i + 1}", "label": f"Option {i + 1}"} for i in range(options)]})


def test_generated_branches_fan_out_under_their_options(app_modules):
    """Each option's branch lands under its own side of the choice -- first
    option leftmost -- a card and a gap apart, instead of every branch piling
    up under the middle of the choice."""
    gen = app_modules["generation"]
    from app.services import placement
    choice = _choice("fan_q", 3)
    parsed = {"scene_title": "x", "nodes": [{"type": "narration", "text": "One."},
                                            {"type": "narration", "text": "Two."}]}

    chains = [gen.build_nodes(parsed, "g1", ["aiko"], choice, f"o{i + 1}")[0] for i in range(3)]
    heads = [chain[0] for chain in chains]
    centres = [n.x + placement.NODE_W / 2 for n in heads]

    assert centres[0] < centres[1] < centres[2]
    assert centres[1] == choice.x + placement.width_for(choice) / 2
    for left, right in zip(heads, heads[1:]):
        assert right.x - left.x >= placement.NODE_W + placement.COL_GAP
    assert heads[0].y >= choice.y + placement.height_for(choice)
    # The rest of a chain runs straight down its own column.
    assert chains[0][1].x == heads[0].x and chains[0][1].y > heads[0].y


def test_a_new_branch_slides_off_a_card_already_in_its_column(app_modules):
    """A sub-choice deep in one branch can have its outer option land where a
    neighbouring branch already runs. It moves outward -- keeping its side --
    rather than landing on top of it."""
    store, gen = app_modules["store"], app_modules["generation"]
    from app.models import Edge, Node, NodeType
    from app.services import placement
    pid = store.create_project("fan slide").id
    scene = Node(id="slide_s", type=NodeType.scene, x=0, y=-400, data={"title": "s"})
    choice = _choice("slide_q", 2, parent_id="slide_s")
    centre, top = placement.column(choice, "o1")
    blocker = Node(id="slide_b", type=NodeType.say, parent_id="slide_s",
                   x=centre - 120, y=top + 60, data={"text": "Already here."})
    for node in (scene, choice, blocker):
        store.insert_node(pid, node)
    store.insert_edge(pid, Edge(id="slide_e", source="slide_s", target="slide_q"))

    parsed = {"scene_title": "x", "nodes": [{"type": "narration", "text": "Left."}]}
    nodes, edges = gen.build_nodes(parsed, "g1", ["aiko"], choice, "o1")
    gen.insert_chain(pid, "slide_q", "o1", nodes, edges)

    head = store.get_node(nodes[0].id)
    assert head.x + placement.NODE_W <= blocker.x


def test_a_spliced_branch_pushes_what_follows_down(app_modules):
    """Options start out wired to the scene's exit. The branch written for the
    middle one stays in the middle and moves the exit down out of its way."""
    store, gen = app_modules["store"], app_modules["generation"]
    from app.models import Edge, Node, NodeType
    from app.services import placement
    pid = store.create_project("fan push").id
    scene = Node(id="push_s", type=NodeType.scene, x=0, y=-400, data={"title": "s"})
    choice = _choice("push_q", 3, parent_id="push_s")
    centre, top = placement.column(choice, "o2")
    exit_node = Node(id="push_x", type=NodeType.narration, parent_id="push_s",
                     x=centre - 120, y=top, data={"text": "The end."})
    for node in (scene, choice, exit_node):
        store.insert_node(pid, node)
    store.insert_edge(pid, Edge(id="push_e0", source="push_s", target="push_q"))
    for i in (1, 2, 3):
        store.insert_edge(pid, Edge(id=f"push_e{i}", source="push_q", target="push_x",
                                    source_handle=f"o{i}"))

    parsed = {"scene_title": "x", "nodes": [{"type": "narration", "text": "Middle."},
                                            {"type": "narration", "text": "Still."}]}
    nodes, edges = gen.build_nodes(parsed, "g1", ["aiko"], choice, "o2")
    gen.insert_chain(pid, "push_q", "o2", nodes, edges)

    assert store.get_node(nodes[0].id).x == centre - 120
    tail = store.get_node(nodes[-1].id)
    assert store.get_node("push_x").y >= tail.y + placement.height_for(tail)


# --------------------------------------------------------------------------
# continuation suggestions
# --------------------------------------------------------------------------

def test_the_continue_block_is_peeled_off_the_prose(app_modules):
    """They are notes to the author about where the scene could go. Left in,
    the structuring pass turns them into narration reading "- she follows"."""
    gen = app_modules["generation"]
    prose, options = gen.split_continuations(
        'Aiko does not look up.\n\nAIKO: "You always take the long way."\n\n'
        "CONTINUE:\n"
        "- She finally asks why you came\n"
        "- You let the silence run and she breaks it\n"
    )

    assert prose.endswith('"You always take the long way."')
    assert "CONTINUE" not in prose
    assert options == ["She finally asks why you came",
                       "You let the silence run and she breaks it"]


def test_a_passage_with_no_continue_block_is_untouched(app_modules):
    gen = app_modules["generation"]
    prose, options = gen.split_continuations('AIKO: "You came."')
    assert prose == 'AIKO: "You came."'
    assert options == []


def test_a_choice_block_always_wins_over_continuations(app_modules):
    """The model is told to write one or the other. If it writes both, the
    choice is what the player sees, so it must survive intact."""
    gen = app_modules["generation"]
    raw = ('AIKO: "Well?"\n\nCONTINUE:\n- she waits\n\n'
           "CHOICE: What do you say?\n- Sit down => close the distance\n"
           "- Stay standing => keep it\n")
    prose, options = gen.split_continuations(raw)

    assert options == []
    assert "CHOICE: What do you say?" in prose
    assert "Sit down => close the distance" in prose


def test_continuations_stop_at_the_bullets(app_modules):
    """A model that keeps writing after the block must not have its prose
    swallowed into the option list."""
    gen = app_modules["generation"]
    _, options = gen.split_continuations(
        "CONTINUE:\n- one\n- two\n\nI hope that helps!\n- not an option\n")
    assert options == ["one", "two"]


def test_at_most_four_continuations_are_kept(app_modules):
    gen = app_modules["generation"]
    _, options = gen.split_continuations(
        "CONTINUE:\n" + "".join(f"- option {i}\n" for i in range(9)))
    assert len(options) == 4


# --------------------------------------------------------------------------
# regenerate a whole generation
# --------------------------------------------------------------------------

def test_the_head_of_a_generation_is_where_it_started(app_modules):
    """Regenerating a block from whichever node you clicked would redo only
    its tail, so the head is found by graph order, not array order."""
    store, gen = app_modules["store"], app_modules["generation"]
    from app.models import Edge, Node, NodeType

    pid = store.create_project("heads").id
    anchor = Node(id="n_anchor", type=NodeType.scene, data={"title": "S"})
    store.insert_node(pid, anchor)
    # Inserted out of order on purpose: the store must not decide the answer.
    for nid, text in (("n_c", "third"), ("n_a", "first"), ("n_b", "second")):
        store.insert_node(pid, Node(id=nid, type=NodeType.narration,
                                    data={"text": text, "_gen": "job1"}))
    for src, dst in (("n_anchor", "n_a"), ("n_a", "n_b"), ("n_b", "n_c")):
        store.insert_edge(pid, Edge(id=f"e_{src}_{dst}", source=src, target=dst))

    assert gen.generation_head(pid, "job1") == "n_a"
    assert gen.generation_head(pid, "nope") is None


def test_migrate_superseded_templates_fixes_defaults_but_not_edits(app_modules):
    """`active_template` only adopts an improved default for a seeded v1 with
    no version 2, so a project that has been through one revision sits on a
    prompt the default already fixed. This closes that gap -- and must still
    leave an authored prompt alone."""
    import hashlib
    from app import db
    defaults, store = app_modules["defaults"], app_modules["store"]

    stale_hash = defaults.SUPERSEDED_TEMPLATES["system.structuring"][-1]
    stale_body = None
    # Reconstruct the outgoing default -- the shipped body from just before the
    # stat ladder was added to the Flags section -- and verify it by hash, so
    # this fails loudly if the recorded hash and the text ever drift apart.
    #
    # Always the *most recently* recorded hash, reversing only the newest edit.
    # Whoever next corrects this prompt records their outgoing hash and swaps
    # this reversal for theirs; chaining every historical edit back to the
    # first would grow a second copy of the template in the test.
    shipped = defaults.DEFAULT_TEMPLATES["system.structuring"]["body"]
    candidate = shipped.replace(
        "Declared variables: {{ variables }}\n"
        "{% if stat_schema %}\n{{ stat_schema }}\n{% endif %}\n"
        "These are the only names",
        "Declared variables: {{ variables }}\n\nThese are the only names")
    assert "{% if stat_schema %}" not in candidate, "the reversal no longer matches"
    if hashlib.sha256(candidate.encode()).hexdigest() == stale_hash:
        stale_body = candidate
    assert stale_body is not None, "recorded superseded hash no longer matches the old text"

    stale_pid = store.create_project("stale").id
    kept_pid = store.create_project("authored").id
    store.active_template(stale_pid, "system.structuring")
    store.active_template(kept_pid, "system.structuring")
    # Both are on v2, so active_template's in-place adopt can never fire.
    for pid, body in ((stale_pid, stale_body), (kept_pid, "my own prompt: {{ prose }}")):
        db.execute("UPDATE templates SET version = 2, body = ? WHERE project_id = ?"
                   " AND key = 'system.structuring'", (body, pid))

    changed = store.migrate_superseded_templates()

    assert f"{stale_pid}:system.structuring" in changed
    assert f"{kept_pid}:system.structuring" not in changed
    assert store.active_template(stale_pid, "system.structuring").body == shipped
    # Params ride along, or the fixed prompt keeps the old token cap.
    assert (store.active_template(stale_pid, "system.structuring").params
            == defaults.STRUCTURING_SAMPLING)
    assert store.active_template(kept_pid, "system.structuring").body == "my own prompt: {{ prose }}"


def test_structuring_cap_clears_the_worst_legitimate_passage(app_modules):
    """A truncated response is invalid JSON, and that discards the whole
    passage -- so the cap has to sit above the largest JSON the prose pass can
    legitimately produce (worst observed amplification ~1.8x its own cap)."""
    defaults = app_modules["defaults"]
    prose_cap = defaults.PROSE_SAMPLING["max_tokens"]
    assert defaults.STRUCTURING_SAMPLING["max_tokens"] >= prose_cap * 1.8
    # Verbatim copying: llama.cpp applies repeat_penalty across the prompt too,
    # so anything above 1.0 pushes the pass off the words it must reproduce.
    assert defaults.STRUCTURING_SAMPLING["repetition_penalty"] == 1.0


# --------------------------------------------------------------------------
# the style examples
# --------------------------------------------------------------------------

def test_the_style_examples_demonstrate_the_performance_notation(app_modules):
    """The prose prompt says dialogue is *always* `Name (face, feeling):
    "line"`. These examples are the same prompt's demonstration of the house
    style, and every dialogue line in them used to be bare -- so they were
    teaching the model to drop the tagging the instructions ask for, which
    few-shot examples generally win.
    """
    defaults = app_modules["defaults"]
    from app.services.generation import extract_performance

    body = defaults.DEFAULT_TEMPLATES["system.style_examples"]["body"]
    _, beats = extract_performance(body)
    says = [b for b in beats if b["kind"] == "say"]
    assert len(says) >= 8, "the examples should still be mostly dialogue"

    tagged = [b for b in says if b.get("expression")]
    assert len(tagged) >= 6, (
        f"only {len(tagged)} of {len(says)} example lines carry a performance "
        "bracket -- the examples have to show the notation, not just the prose")

    # Enough variety to demonstrate a shape rather than one repeated reading.
    assert len({b["expression"] for b in tagged}) >= 5
    assert len({b["emotion"] for b in tagged if b.get("emotion")}) >= 5

    # The optional halves have to appear somewhere, or nothing teaches when
    # they are worth using.
    assert any(b.get("intensity") is not None for b in tagged), "no example intensity"
    assert any(b.get("delivery") for b in tagged), "no example delivery note"

    # And at least one line where the face and the feeling disagree -- the
    # prose prompt calls that out and no example used to show it.
    assert any(b.get("emotion") and b["expression"] != b["emotion"] for b in tagged)


def test_the_style_examples_only_use_real_vocabulary(app_modules):
    """The tags here are hardcoded -- this template is rendered with only
    nsfw/explicit/rating, so it cannot interpolate `{{ expressions }}` the way
    the prose prompt does. Without this test they would rot silently the first
    time an enum changed, and the examples would teach tags that parse into
    the delivery note instead."""
    defaults = app_modules["defaults"]
    from app.models import EMOTIONS, EXPRESSIONS, POSES
    from app.services.generation import extract_performance

    body = defaults.DEFAULT_TEMPLATES["system.style_examples"]["body"]
    _, beats = extract_performance(body)
    for beat in beats:
        if beat["kind"] != "say":
            continue
        if beat.get("expression"):
            assert beat["expression"] in EXPRESSIONS, beat["expression"]
        if beat.get("emotion"):
            assert beat["emotion"] in EMOTIONS, beat["emotion"]
        if beat.get("pose"):
            assert beat["pose"] in POSES, beat["pose"]


def test_no_example_wraps_a_line_of_dialogue(app_modules):
    """Segmentation splits on line boundaries, so a dialogue line wrapped
    across two physical lines becomes a say node plus a narration node holding
    the tail -- with the closing quote mark still in it. The shipped examples
    did exactly that on Kade's last line, which is a habit the writer model
    can learn from them."""
    defaults = app_modules["defaults"]
    from app.services.generation import extract_performance

    body = defaults.DEFAULT_TEMPLATES["system.style_examples"]["body"]
    _, beats = extract_performance(body)
    orphans = [b["text"] for b in beats
               if b["kind"] == "narration" and '"' in b["text"]]
    assert not orphans, (
        f"a wrapped dialogue line left its tail as narration: {orphans}")


def test_player_lines_in_the_examples_stay_untagged(app_modules):
    """The player character has no sprite and no voice, so a performance tag
    on one of their lines has nowhere to go. The examples should not teach
    writing tags that cannot be used."""
    defaults = app_modules["defaults"]
    from app.services.generation import extract_performance

    body = defaults.DEFAULT_TEMPLATES["system.style_examples"]["body"]
    clean, beats = extract_performance(body)
    # "You:" is the player in both examples.
    player_lines = [l for l in clean.splitlines() if l.startswith("You:")]
    assert player_lines, "the examples should still show the player speaking"
    assert all("(" not in l for l in player_lines), player_lines


def test_the_bare_style_examples_are_superseded(app_modules):
    """A project that never edited its examples has to pick this up: the old
    body is not a matter of taste, it actively contradicts the prose prompt
    shipped beside it."""
    import hashlib
    defaults = app_modules["defaults"]

    bare = defaults.SUPERSEDED_TEMPLATES.get("system.style_examples") or []
    assert bare, "the outgoing default's hash must be recorded"

    current = hashlib.sha256(
        defaults.DEFAULT_TEMPLATES["system.style_examples"]["body"].encode()
    ).hexdigest()
    assert current not in bare, "the current default must not supersede itself"


def test_a_project_on_the_bare_examples_is_migrated(app_modules):
    """End to end through the real migration, so the hash is proved to match
    rather than assumed to."""
    store, defaults = app_modules["store"], app_modules["defaults"]
    pid = store.create_project("style migration").id
    store.seed_templates(pid)

    bare = '''Two short passages showing the house style. Match their rhythm and their
proportion of dialogue to narration. Do not reuse their content.

--- example 1 ---
SFX: knock at the door, three times, unhurried
Mira lets herself in before you answer.

Mira: "You didn't eat."
You: "I ate."
Mira: "The bowl's in the sink and it's clean."
Mira: "So either you washed it in the four minutes since I texted, or."

She puts the bag down on the counter and starts unpacking it anyway.

You: "Or."
Mira: "Yeah."
--- end example 1 ---

--- example 2 ---
Kade doesn't get up when you come in. He keeps his eyes on the board.

Kade: "You're going to ask me about Sunday."
You: "I was."
Kade: "Ask something else."
You: "Why?"

He moves a piece, then moves it back, then leaves it where it started.

Kade: "Because I don't have a good answer and I've been working on one all
week and it's still not good."
--- end example 2 ---'''

    # Saved as a *new version*, which is what a project that has been through
    # one template revision looks like. That is the case the migration exists
    # for: `active_template` only adopts an improved default for a seeded v1
    # with no v2, so a project on v2+ would otherwise sit on the bare examples
    # forever.
    store.save_template_version(pid, "system.style_examples", bare, {})
    assert store.active_template(pid, "system.style_examples").version == 2

    changed = store.migrate_superseded_templates()
    assert f"{pid}:system.style_examples" in changed, changed

    now = store.active_template(pid, "system.style_examples").body
    assert "(smirking, smug, 0.2)" in now
    assert now == defaults.DEFAULT_TEMPLATES["system.style_examples"]["body"]


def test_an_edited_examples_template_is_left_alone(app_modules):
    """The migration exists to fix a default nobody chose, never to overwrite
    a prompt the author wrote."""
    store = app_modules["store"]
    pid = store.create_project("style authored").id
    store.seed_templates(pid)

    # Through the real edit path: an author's save is a new version, never an
    # overwrite. Writing the body straight into v1 instead would look exactly
    # like an untouched seed, which `active_template` is entitled to replace.
    mine = 'My own examples.\n\nAda: "Mine."\n'
    store.save_template_version(pid, "system.style_examples", mine, {})

    store.migrate_superseded_templates()
    assert store.active_template(pid, "system.style_examples").body == mine
