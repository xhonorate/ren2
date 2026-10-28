"""Compiler tests.

The design doc calls out exactly where compiler bugs live (S9): merge points
and label naming. These cover two branches converging, a three-way merge, a
branch that jumps backward into an earlier label, and nested conditionals
inside menu options.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.compiler.renpy import compile_project
from app.graph import Graph, resolve_state
# All from one import, deliberately: other test modules clear `app.*` out of
# sys.modules to re-init with their own VN_DATA_DIR, so a class imported inside
# a test function is a *different* class object than the module-level
# ProjectMeta it gets handed to, and pydantic rejects it.
from app.models import (Character, Edge, Location, Node, NodeType, Outfit,
                        ProjectMeta, StatDef, VariableDef)


def N(nid: str, ntype: str, **data) -> Node:
    slug = data.pop("slug", nid)
    pinned = data.pop("pinned_label", False)
    parent = data.pop("parent_id", None)
    return Node(id=nid, type=NodeType(ntype), data=data, slug=slug,
                pinned_label=pinned, parent_id=parent)


def E(src: str, dst: str, handle: str = "out") -> Edge:
    return Edge(id=f"{src}->{dst}:{handle}", source=src, target=dst, source_handle=handle)


PROJECT = ProjectMeta(id="p1", name="Test Project")

AIKO = Character(
    id="aiko",
    display_name="Aiko",
    age=22,
    outfits=[Outfit(id="uniform", name="School uniform", tags="school uniform"),
             Outfit(id="casual", name="Weekend", tags="hoodie")],
    default_outfit="uniform",
)
PC = Character(id="pc", display_name="You", age=21, is_player=True)
SCHOOL = Location(id="school_rooftop", name="Rooftop", tags="rooftop, school",
                  variants={"day": "h1", "dusk": "h2"})


def build(nodes, edges, characters=None, locations=None):
    return compile_project(
        PROJECT, nodes, edges,
        characters if characters is not None else [AIKO, PC],
        locations if locations is not None else [SCHOOL],
    )


def script(result) -> str:
    return "\n".join(
        body for path, body in sorted(result.files.items())
        if "/scenes/" in path
    )


def labels_in(text: str) -> list[str]:
    return re.findall(r"^label (\S+):", text, re.M)


# --------------------------------------------------------------------------
# label emission rules (doc S1)
# --------------------------------------------------------------------------

def test_straight_line_emits_one_label():
    nodes = [
        N("s1", "scene", title="Opening", background="school_rooftop"),
        N("a", "narration", text="The bell rings."),
        N("b", "say", speaker="aiko", text="You came.", expression="smiling"),
        N("c", "end", mode="return"),
    ]
    edges = [E("s1", "a"), E("a", "b"), E("b", "c")]
    out = build(nodes, edges)
    text = script(out)

    # One label for the scene container -- not one per node.
    assert len(labels_in(text)) == 1
    assert "The bell rings." in text
    assert 'aiko "You came."' in text
    assert text.strip().endswith("return")


def test_two_branches_converging_creates_a_merge_label():
    nodes = [
        N("s1", "scene", title="Choice", background="school_rooftop"),
        N("ch", "choice", prompt="What do you say?", options=[
            {"id": "o1", "label": "Push her on it"},
            {"id": "o2", "label": "Let it go"},
        ]),
        N("pa", "narration", text="You press."),
        N("pb", "narration", text="You let it sit."),
        N("merge", "narration", text="Either way, the wind picks up.", slug="merge"),
        N("done", "end", mode="return"),
    ]
    edges = [
        E("s1", "ch"),
        E("ch", "pa", "o1"), E("ch", "pb", "o2"),
        E("pa", "merge"), E("pb", "merge"),
        E("merge", "done"),
    ]
    out = build(nodes, edges)
    text = script(out)

    merge_label = out.labels["merge"]
    assert merge_label is not None
    # Both branches must converge by jumping to the same label.
    assert text.count(f"jump {merge_label}") == 2
    # The merged body is emitted exactly once.
    assert text.count("Either way, the wind picks up.") == 1


def test_three_way_merge():
    nodes = [
        N("s1", "scene", title="Three", background="school_rooftop"),
        N("ch", "choice", options=[
            {"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}, {"id": "o3", "label": "C"},
        ]),
        N("a", "narration", text="A path."),
        N("b", "narration", text="B path."),
        N("c", "narration", text="C path."),
        N("m", "narration", text="All roads.", slug="allroads"),
        N("e", "end", mode="return"),
    ]
    edges = [
        E("s1", "ch"),
        E("ch", "a", "o1"), E("ch", "b", "o2"), E("ch", "c", "o3"),
        E("a", "m"), E("b", "m"), E("c", "m"), E("m", "e"),
    ]
    out = build(nodes, edges)
    text = script(out)

    assert text.count(f"jump {out.labels['m']}") == 3
    assert text.count("All roads.") == 1


def test_backward_jump_into_an_earlier_label():
    """A back-edge target has in-degree > 1, so it is already a label."""
    nodes = [
        N("s1", "scene", title="Loop", background="school_rooftop"),
        N("hub", "narration", text="You are back in the hallway.", slug="hallway"),
        N("ch", "choice", options=[
            {"id": "o1", "label": "Look around"},
            {"id": "o2", "label": "Leave"},
        ]),
        N("look", "narration", text="Nothing new."),
        N("leave", "end", mode="return"),
    ]
    edges = [
        E("s1", "hub"), E("hub", "ch"),
        E("ch", "look", "o1"), E("ch", "leave", "o2"),
        E("look", "hub"),          # backward jump
    ]
    out = build(nodes, edges)
    text = script(out)

    hub_label = out.labels["hub"]
    assert hub_label in labels_in(text)
    assert f"jump {hub_label}" in text
    # The hub body is emitted once, not inlined into the loop as well.
    assert text.count("You are back in the hallway.") == 1


def test_nested_conditionals_inside_menu_options():
    nodes = [
        N("s1", "scene", title="Nested", background="school_rooftop"),
        N("ch", "choice", options=[
            {"id": "o1", "label": "Ask about the letter", "condition": "knows_letter"},
            {"id": "o2", "label": "Leave"},
        ]),
        N("cond", "condition", branches=[
            {"id": "b1", "expr": "affinity > 5"},
            {"id": "b2", "expr": "affinity > 2"},
            {"id": "b3", "expr": ""},
        ], has_else=True),
        N("hi", "say", speaker="aiko", text="I kept it.", expression="looking_away"),
        N("mid", "say", speaker="aiko", text="...Why?", expression="frowning"),
        N("lo", "say", speaker="aiko", text="Forget it.", expression="angry"),
        N("out", "end", mode="return", slug="fin"),
        N("bye", "end", mode="return", slug="bye"),
    ]
    edges = [
        E("s1", "ch"),
        E("ch", "cond", "o1"), E("ch", "bye", "o2"),
        E("cond", "hi", "b1"), E("cond", "mid", "b2"), E("cond", "lo", "b3"),
        E("hi", "out"), E("mid", "out"), E("lo", "out"),
    ]
    out = build(nodes, edges)
    text = script(out)

    assert '"Ask about the letter" if knows_letter:' in text
    # A choice-branch target is always a label (doc S1 rule 2), so the option
    # body ends in a jump and the conditional lives in that label's block.
    cond_label = out.labels["cond"]
    assert f"jump {cond_label}" in text
    block = text.split(f"label {cond_label}:", 1)[1].split("\nlabel ", 1)[0]
    assert "    if affinity > 5:" in block
    assert "    elif affinity > 2:" in block
    assert "    else:" in block
    assert '        aiko "I kept it."' in block
    assert '        aiko "Forget it."' in block


def test_conditions_nest_inside_conditions():
    nodes = [
        N("s1", "scene", title="Deep", background="school_rooftop"),
        N("outer", "condition", branches=[
            {"id": "a", "expr": "knows_letter"}, {"id": "b", "expr": ""},
        ]),
        N("inner", "condition", branches=[
            {"id": "c", "expr": "affinity > 5"}, {"id": "d", "expr": ""},
        ]),
        N("deep", "narration", text="Deep branch."),
        N("shallow", "narration", text="Shallow branch."),
        N("other", "narration", text="Other branch."),
    ]
    edges = [
        E("s1", "outer"),
        E("outer", "inner", "a"), E("outer", "other", "b"),
        E("inner", "deep", "c"), E("inner", "shallow", "d"),
    ]
    text = script(build(nodes, edges))
    assert "    if knows_letter:" in text
    assert "        if affinity > 5:" in text
    assert '            "Deep branch."' in text
    assert "        else:" in text
    assert '            "Shallow branch."' in text


# --------------------------------------------------------------------------
# stable naming (doc S1: "non-negotiable")
# --------------------------------------------------------------------------

def test_labels_are_stable_across_recompiles_and_reordering():
    nodes = [
        N("s1", "scene", title="Opening", background="school_rooftop"),
        N("a", "narration", text="One."),
        N("m", "narration", text="Merge.", slug="merge"),
        N("b", "narration", text="Two."),
        N("e", "end", mode="return"),
    ]
    edges = [E("s1", "a"), E("a", "m"), E("s1", "b"), E("b", "m"), E("m", "e")]

    first = build(nodes, edges).labels
    # Same graph, different insertion order and node positions.
    shuffled = list(reversed(nodes))
    for i, n in enumerate(shuffled):
        n.x, n.y = i * 10, i * 20
    second = build(shuffled, list(reversed(edges))).labels

    assert first == second
    assert all(re.fullmatch(r"(sc|lb)_[a-z0-9_]+_[0-9a-f]{8}", v) for v in first.values())


def test_label_name_ignores_position_but_follows_the_uuid():
    a = N("uuid-one", "scene", title="Opening")
    b = N("uuid-two", "scene", title="Opening")
    out_a = build([a], [])
    out_b = build([b], [])
    assert out_a.labels["uuid-one"] != out_b.labels["uuid-two"]


# --------------------------------------------------------------------------
# hub scenes / menuset (doc S1)
# --------------------------------------------------------------------------

def test_hub_scene_uses_menuset_and_calls_back():
    nodes = [
        N("s1", "scene", title="Hub", background="school_rooftop"),
        N("hub", "choice", slug="hub", exhaust=True, mode="call", loop_back=True, options=[
            {"id": "o1", "label": "Ask about her family"},
            {"id": "o2", "label": "Ask about the club"},
        ]),
        N("fam", "say", speaker="aiko", text="They're fine.", slug="topic_family"),
        N("club", "say", speaker="aiko", text="It's quiet.", slug="topic_club"),
    ]
    edges = [E("s1", "hub"), E("hub", "fam", "o1"), E("hub", "club", "o2")]
    out = build(nodes, edges)
    text = script(out)

    assert "menu (set=menuset):" in text
    assert f"call {out.labels['fam']}" in text
    assert f"jump {out.labels['hub']}" in text
    assert "default menuset = set()" in out.files["game/generated/flags.rpy"]


# --------------------------------------------------------------------------
# statements
# --------------------------------------------------------------------------

def test_scene_container_resets_to_neutral():
    nodes = [N("s1", "scene", title="Rooftop", background="school_rooftop",
               time_of_day="dusk", transition="fade", music="theme.ogg")]
    text = script(build(nodes, []))
    assert "scene bg_school_rooftop_dusk with fade" in text
    assert 'play music "audio/music/theme.ogg" fadein 1.0' in text


def test_say_emits_show_then_voice_then_dialogue():
    """`voice` sits immediately before the line (doc S5). Nothing between them
    may be an interaction: a CG dissolve is one, and a voice queued ahead of it
    would start on the dissolve instead of on the line."""
    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("st", "stage", show=["aiko"], outfits={"aiko": "casual"}, positions={"aiko": "center"}),
        N("a", "say", speaker="aiko", text="I said I was fine.",
          expression="angry", voice_path="audio/voice/say_a3f9c2.ogg"),
    ]
    edges = [E("s1", "st"), E("st", "a")]
    text = script(build(nodes, edges))
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    i = lines.index('voice "audio/voice/say_a3f9c2.ogg"')
    assert lines[i - 1] == "show aiko casual front angry at center"
    assert lines[i + 1] == 'aiko "I said I was fine."'


def test_player_character_gets_no_voice_line():
    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("a", "say", speaker="pc", text="Sure.", voice_path="audio/voice/x.ogg"),
    ]
    text = script(build(nodes, [E("s1", "a")]))
    assert "voice " not in text
    assert 'pc "Sure."' in text


def test_player_is_never_staged_as_a_sprite():
    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("st", "stage", show=["pc", "aiko"], expressions={"pc": "smiling"},
          outfits={"pc": "casual"}, poses={"pc": "front"}),
        N("st2", "stage", hide=["pc"]),
        N("a", "say", speaker="pc", text="Hey."),
    ]
    edges = [E("s1", "st"), E("st", "st2"), E("st2", "a")]
    out = build(nodes, edges)
    text = script(out)
    images = out.files["game/generated/images.rpy"]

    # No sprite is shown or hidden for the player, and no image is declared.
    assert "show pc" not in text
    assert "hide pc" not in text
    assert "image pc" not in images
    # Non-player staging on the same node is unaffected.
    assert "show aiko" in text


def test_player_with_outfits_still_gets_no_images():
    pc_with_outfits = Character(
        id="pc", display_name="You", age=21, is_player=True,
        outfits=[Outfit(id="casual", name="Casual", tags="hoodie")],
        default_outfit="casual",
    )
    out = build([N("s1", "scene", title="X")], [], characters=[AIKO, pc_with_outfits])
    assert "image pc" not in out.files["game/generated/images.rpy"]


def test_character_definitions_carry_voice_tags():
    out = build([N("s1", "scene", title="X")], [])
    chars = out.files["game/generated/characters.rpy"]
    assert 'define aiko = Character("Aiko", color="#c8c8ff", image="aiko", voice_tag="aiko")' in chars
    # The player character gets no image and no voice_tag.
    assert 'define pc = Character("You", color="#c8c8ff")' in chars


def test_set_node_and_default_emission():
    nodes = [
        N("s1", "scene", title="Flags", background="school_rooftop"),
        N("set", "set", assignments=[
            {"var": "knows_letter", "op": "=", "value": "true"},
            {"var": "affinity", "op": "+=", "value": "2"},
        ]),
    ]
    out = build(nodes, [E("s1", "set")])
    text = script(out)
    flags = out.files["game/generated/flags.rpy"]
    assert "$ knows_letter = True" in text
    assert "$ affinity += 2" in text
    assert "default knows_letter = False" in flags
    assert "default affinity = 0" in flags


def test_choice_option_effects_fire_when_the_option_is_picked():
    nodes = [
        N("s1", "scene", title="Choice", background="school_rooftop"),
        N("ch", "choice", prompt="What do you say?", options=[
            {"id": "o1", "label": "Push her on it",
             "effects": [{"var": "affinity", "op": "-=", "value": "1"},
                         {"var": "pushed", "op": "=", "value": "true"}]},
            {"id": "o2", "label": "Let it go",
             "effects": [{"var": "affinity", "op": "+=", "value": "1"}]},
        ]),
        N("pa", "narration", text="You press.", slug="pa"),
        N("pb", "narration", text="You let it sit.", slug="pb"),
    ]
    edges = [E("s1", "ch"), E("ch", "pa", "o1"), E("ch", "pb", "o2")]
    out = build(nodes, edges)
    text = script(out)
    flags = out.files["game/generated/flags.rpy"]

    # The assignment lands inside the menu option body, before its branch.
    push = text.index('"Push her on it":')
    assert text.index("$ affinity -= 1", push) < text.index("You press.", push)
    assert "$ pushed = True" in text
    # And choice-driven variables get a default like any Set var.
    assert "default affinity = 0" in flags
    assert "default pushed = False" in flags


def test_project_variable_defaults_win_over_inference():
    project = ProjectMeta(id="pv", name="Vars", variables=[
        {"name": "affinity", "type": "int", "default": "3"},
        {"name": "route", "type": "str", "default": "none"},
    ])
    nodes = [
        N("s1", "scene", title="X"),
        N("set", "set", assignments=[{"var": "affinity", "op": "+=", "value": "1"}]),
    ]
    out = compile_project(project, nodes, [E("s1", "set")], [AIKO, PC], [SCHOOL])
    flags = out.files["game/generated/flags.rpy"]
    assert "default affinity = 3" in flags
    assert 'default route = "none"' in flags


def test_strict_subtree_stops_at_a_live_merge():
    nodes = [
        N("s1", "scene", title="X"),
        N("ch", "choice", prompt="?", options=[
            {"id": "o1", "label": "a"}, {"id": "o2", "label": "b"}]),
        N("a", "narration", text="a"),
        N("b", "narration", text="b"),
        N("merge", "narration", text="m", slug="merge"),
    ]
    edges = [E("s1", "ch"), E("ch", "a", "o1"), E("ch", "b", "o2"),
             E("a", "merge"), E("b", "merge")]
    graph = Graph.build(nodes, edges)
    # Deleting branch "a" must not drag the merge along -- "b" still reaches it.
    assert graph.strict_subtree("a") == []
    # Deleting the choice takes everything below it.
    assert set(graph.strict_subtree("ch")) == {"a", "b", "merge"}


def test_sprite_images_are_declared_per_attribute_combination():
    """Sprites are whole images per outfit x pose x expression, so they are
    declared as attribute-tagged images. The old layeredimage composited an
    outfit layer with a separate face layer and looked up sprite_sheet keys
    (`outfit/<id>`, `face/<expr>`) that the generator never wrote, so no
    sprite ever reached a build."""
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "say", speaker="aiko", text="hi", expression="smiling"),
    ]
    images = build(nodes, [E("s1", "a")]).files["game/generated/images.rpy"]

    assert "layeredimage" not in images
    # The combination this script shows, declared so the show statement resolves.
    assert "image aiko uniform front smiling" in images
    # Nothing generated yet, so it stands in a placeholder rather than pointing
    # at a file that will not exist in the build.
    assert 'image aiko uniform front smiling = Placeholder("girl")' in images
    assert 'image aiko = Placeholder("girl")' in images
    assert ('image bg_school_rooftop_day = '
            'At("images/bg/bg_school_rooftop_day.png", vn_bg)') in images


def test_generated_sprites_reach_the_build():
    """The regression the layeredimage hid: a real sprite hash has to produce
    both an image statement and a copied asset."""
    aiko = AIKO.model_copy(deep=True)
    aiko.assets.sprite_sheet = {"uniform/front/smiling": "abc123"}
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "say", speaker="aiko", text="hi", expression="smiling"),
    ]
    result = build(nodes, [E("s1", "a")], characters=[aiko, PC])
    images = result.files["game/generated/images.rpy"]

    assert ('image aiko uniform front smiling = '
            'At("images/chars/aiko/uniform_front_smiling.png", vn_sprite)') in images
    assert {"hash": "abc123",
            "dest": "game/images/chars/aiko/uniform_front_smiling.png"} in result.assets


def test_a_pose_with_no_art_falls_back_to_the_nearest_sprite():
    """Sprite sets are always partial mid-project. A show naming attributes
    with no matching image is a hard Ren'Py error, so the declaration stands
    in the closest sprite that does exist -- outfit held, pose given up."""
    aiko = AIKO.model_copy(deep=True)
    aiko.assets.sprite_sheet = {"uniform/front/smiling": "abc123"}
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "say", speaker="aiko", text="hi", expression="smiling", pose="sitting"),
    ]
    result = build(nodes, [E("s1", "a")], characters=[aiko, PC])
    images = result.files["game/generated/images.rpy"]

    # Shown as sitting, drawn with the front sprite that exists.
    assert "show aiko uniform sitting smiling" in script(result)
    assert ('image aiko uniform sitting smiling = '
            'At("images/chars/aiko/uniform_front_smiling.png", vn_sprite)') in images


def test_raw_node_passes_through_verbatim():
    nodes = [
        N("s1", "scene", title="Raw"),
        N("r", "raw", code='$ renpy.notify("hi")\nshow screen custom_hud'),
    ]
    text = script(build(nodes, [E("s1", "r")]))
    assert '$ renpy.notify("hi")' in text
    assert "show screen custom_hud" in text


def test_text_is_escaped_for_renpy():
    nodes = [
        N("s1", "scene", title="Esc"),
        N("a", "narration", text='She said "no" [really] {b}'),
    ]
    text = script(build(nodes, [E("s1", "a")]))
    # Ren'Py escapes only the OPENING bracket: [[ for a literal [, {{ for {.
    assert r'"She said \"no\" [[really] {{b}"' in text


def test_persistent_unlock_on_ending():
    nodes = [
        N("s1", "scene", title="End"),
        N("e", "end", mode="ending", ending_id="beach", unlock_persistent=["cg_beach_unlocked"]),
    ]
    out = build(nodes, [E("s1", "e")])
    assert "$ persistent.cg_beach_unlocked = True" in script(out)
    assert "jump ending_beach" in script(out)
    assert "persistent.cg_beach_unlocked = False" in out.files["game/generated/flags.rpy"]


# --------------------------------------------------------------------------
# path state (doc S4)
# --------------------------------------------------------------------------

def test_path_state_resolves_outfit_and_location():
    nodes = [
        N("s1", "scene", title="X", background="school_rooftop", time_of_day="dusk"),
        N("st", "stage", show=["aiko"], outfits={"aiko": "uniform"}),
        N("st2", "stage", outfits={"aiko": "casual"}),
        N("a", "say", speaker="aiko", text="hey", expression="smiling"),
    ]
    edges = [E("s1", "st"), E("st", "st2"), E("st2", "a")]
    g = Graph.build(nodes, edges)
    state = resolve_state(g.path_to("a"))

    assert state.location == "school_rooftop"
    assert state.time_of_day == "dusk"
    assert state.outfits["aiko"] == "casual"
    assert state.expressions["aiko"] == "smiling"
    assert state.present == ["aiko"]


def test_branch_state_does_not_leak_across_branches():
    nodes = [
        N("s1", "scene", title="X", background="school_rooftop"),
        N("ch", "choice", options=[{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}]),
        N("a", "set", assignments=[{"var": "took_a", "op": "=", "value": "true"}]),
        N("b", "set", assignments=[{"var": "took_b", "op": "=", "value": "true"}]),
        N("ea", "narration", text="A end"),
        N("eb", "narration", text="B end"),
    ]
    edges = [E("s1", "ch"), E("ch", "a", "o1"), E("ch", "b", "o2"), E("a", "ea"), E("b", "eb")]
    g = Graph.build(nodes, edges)

    assert g_flags(g, "ea") == {"took_a": True}
    assert g_flags(g, "eb") == {"took_b": True}


def g_flags(g: Graph, node_id: str) -> dict:
    return resolve_state(g.path_to(node_id)).flags


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

def test_every_node_is_emitted_exactly_once():
    nodes = [
        N("s1", "scene", title="Cover", background="school_rooftop"),
        N("n1", "narration", text="alpha"),
        N("ch", "choice", options=[{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}]),
        N("n2", "narration", text="beta"),
        N("n3", "narration", text="gamma"),
        N("n4", "narration", text="delta"),
        N("e", "end", mode="return"),
    ]
    edges = [
        E("s1", "n1"), E("n1", "ch"),
        E("ch", "n2", "o1"), E("ch", "n3", "o2"),
        E("n2", "n4"), E("n3", "n4"), E("n4", "e"),
    ]
    text = script(build(nodes, edges))
    for word in ("alpha", "beta", "gamma", "delta"):
        assert text.count(word) == 1, word


def test_isolated_cycle_is_still_emitted():
    nodes = [
        N("a", "narration", text="ping"),
        N("b", "narration", text="pong"),
    ]
    edges = [E("a", "b"), E("b", "a")]
    out = build(nodes, edges)
    text = script(out)
    assert "ping" in text and "pong" in text
    assert out.warnings  # promoting a cycle entry point is worth reporting


def test_orphaned_nodes_are_excluded():
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "narration", text="kept"),
        N("b", "narration", text="orphaned"),
    ]
    nodes[2].orphaned = True
    text = script(build(nodes, [E("s1", "a"), E("a", "b")]))
    assert "kept" in text
    assert "orphaned" not in text


def test_choice_option_without_an_edge_warns_and_still_compiles():
    nodes = [
        N("s1", "scene", title="X"),
        N("ch", "choice", options=[{"id": "o1", "label": "A"}, {"id": "o2", "label": "Dead end"}]),
        N("a", "narration", text="ok"),
    ]
    out = build(nodes, [E("s1", "ch"), E("ch", "a", "o1")])
    assert any("Dead end" in w for w in out.warnings)
    assert "pass" in script(out)


def test_age_floor_is_enforced_at_the_schema():
    with pytest.raises(Exception):
        Character(id="x", display_name="X", age=17)


def test_event_cgs_are_shown_not_just_declared():
    """The image was declared and its file copied, but nothing ever emitted a
    show, so a CG could never appear in a build."""
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "say", speaker="aiko", text="Look.", cg=True, image_hash="cg111"),
        N("b", "narration", text="The sky opens.", cg=True, image_hash="cg222"),
        N("c", "say", speaker="aiko", text="Plain line."),
    ]
    result = build(nodes, [E("s1", "a"), E("a", "b"), E("b", "c")])
    images = result.files["game/generated/images.rpy"]
    body = script(result)

    assert "image cg_a = At(\"images/cg/cg_a.png\", vn_bg)" in images
    assert {"hash": "cg111", "dest": "game/images/cg/cg_a.png"} in result.assets

    # Shown under a shared tag so consecutive CGs replace rather than stack,
    # and taken down again so the sprite scene comes back.
    assert "show cg_a as cg" in body
    assert "show cg_b as cg" in body          # narration nodes carry CGs too
    assert body.count("hide cg") == 2
    assert body.index("show cg_a as cg") < body.index("Look.")
    assert body.index("Look.") < body.index("hide cg")
    # A node with no CG gets no CG statements.
    assert "cg_c" not in body


def _lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip()]


def test_a_held_cg_stays_up_until_the_next_cg_replaces_it():
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "say", speaker="aiko", text="Look.", cg=True, cg_hold=True, image_hash="cg111"),
        N("b", "narration", text="Neither of us moves."),
        N("c", "say", speaker="aiko", text="Closer.", cg=True, image_hash="cg222"),
        N("d", "narration", text="After."),
    ]
    lines = _lines(script(build(nodes, [E("s1", "a"), E("a", "b"), E("b", "c"), E("c", "d")])))

    show_a = lines.index("show cg_a as cg zorder 10")
    # Nothing takes it down before the next CG, which cross-fades over it:
    # the two are usually versions of one picture.
    show_c = lines.index("show cg_c as cg zorder 10 with dissolve")
    assert show_a < lines.index('"Neither of us moves."') < show_c
    assert "hide cg" not in lines[show_a:show_c]
    # The replacing CG does not hold, so it comes down after its own line.
    assert lines.index('aiko "Closer."') < lines.index("hide cg") < lines.index('"After."')
    assert lines.count("hide cg") == 1


def test_clearing_a_held_cg_dissolves_onto_the_sprite_before_the_voice():
    nodes = [
        N("s1", "scene", title="X", background="school_rooftop"),
        N("st", "stage", show=["aiko"]),
        N("a", "narration", text="The sky opens.", cg=True, cg_hold=True, image_hash="cg111"),
        N("b", "say", speaker="aiko", text="Back to it.", expression="smiling",
          cg_clear=True, voice_path="audio/voice/b.ogg"),
    ]
    lines = _lines(script(build(nodes, [E("s1", "st"), E("st", "a"), E("a", "b")])))

    hide = lines.index("hide cg with dissolve")
    # The sprite changes first, so the dissolve reveals the face this line
    # has; the voice comes after, so it starts on the line, not the dissolve.
    assert lines[hide - 1].startswith("show aiko")
    assert lines[hide + 1] == 'voice "audio/voice/b.ogg"'
    assert lines[hide + 2] == 'aiko "Back to it."'


def test_an_unrendered_cg_still_ends_a_held_run():
    """Rendering a CG later must not move where the run ends, so a flagged line
    with no image yet takes the slot all the same: the held picture stands in
    for it, then comes down."""
    nodes = [
        N("s1", "scene", title="X"),
        N("a", "narration", text="One.", cg=True, cg_hold=True, image_hash="cg111"),
        N("b", "narration", text="Two.", cg=True),
        N("c", "narration", text="Three."),
    ]
    edges = [E("s1", "a"), E("a", "b"), E("b", "c")]
    lines = _lines(script(build(nodes, edges)))
    assert not any("cg_b" in line for line in lines)
    assert lines.index('"Two."') < lines.index("hide cg") < lines.index('"Three."')

    graph = Graph.build(nodes, edges)
    assert resolve_state(graph.path_to("b"), include_last=False).cg_node == "a"
    assert resolve_state(graph.path_to("b")).cg_node is None


def test_path_state_ends_a_held_cg_wherever_the_build_clears_the_stage():
    held = dict(cg=True, cg_hold=True, image_hash="cg111")
    nodes = [
        N("s1", "scene", title="X", background="school_rooftop"),
        N("a", "narration", text="Held.", **held),
        N("same", "stage", location="school_rooftop", show=["aiko"]),
        N("b", "narration", text="Still held."),
        N("moved", "stage", location="classroom"),
        N("c", "narration", text="Gone.", **{**held, "image_hash": "cg222"}),
        N("clear", "narration", text="Cleared.", cg_clear=True),
        N("d", "narration", text="Again.", **{**held, "image_hash": "cg333"}),
        N("s2", "scene", title="Next"),
    ]
    order = ["s1", "a", "same", "b", "moved", "c", "clear", "d", "s2"]
    edges = [E(x, y) for x, y in zip(order, order[1:])]
    graph = Graph.build(nodes, edges)
    after = {nid: resolve_state(graph.path_to(nid)) for nid in order}

    assert (after["a"].cg_node, after["a"].cg_image) == ("a", "cg111")
    # A stage change that stays put emits no `scene`, so nothing is cleared.
    assert after["same"].cg_node == "a" and after["b"].cg_node == "a"
    assert after["moved"].cg_node is None
    assert after["c"].cg_node == "c"
    assert after["clear"].cg_node is None
    assert after["d"].cg_node == "d"
    assert after["s2"].cg_node is None and after["s2"].cg_image is None

    lines = _lines(script(build(nodes, edges, locations=[
        SCHOOL, Location(id="classroom", name="Classroom", tags="classroom")])))
    assert sum(1 for l in lines if l.startswith("scene ")) == 3


def test_preview_plays_a_held_cg_the_way_the_build_does():
    from app.compiler.preview import compile_preview

    nodes = [
        N("s1", "scene", title="X", background="school_rooftop"),
        N("a", "say", speaker="aiko", text="Look.", cg=True, cg_hold=True, image_hash="cg111"),
        N("b", "narration", text="Held."),
        N("c", "narration", text="Replaced.", cg=True, image_hash="cg222"),
        N("d", "narration", text="Clear.", cg_clear=True),
    ]
    edges = [E("s1", "a"), E("a", "b"), E("b", "c"), E("c", "d")]
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])
    ops = [(op["op"], op.get("node"), op.get("image"), op.get("transition"))
           for block in program["blocks"].values() for op in block
           if op["op"] in ("cg", "say")]
    assert ops == [
        ("cg", "a", "cg111", "none"), ("say", "a", "cg111", None),
        ("say", "b", None, None),
        ("cg", "c", "cg222", "dissolve"), ("say", "c", "cg222", None),
        ("cg", "c", None, "none"),
        ("cg", "d", None, "dissolve"), ("say", "d", None, None),
    ]

    # Playing from inside the run puts the held picture back, credited to
    # the node that owns it.
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC], [SCHOOL], start_node_id="b")
    restored = [op for op in program["blocks"][program["entry"]] if op.get("restored")]
    assert restored[-1] == {"op": "cg", "node": "b", "image": "cg111",
                            "transition": "none", "restored": True, "source": "a"}


def test_hold_and_clear_survive_the_undo_of_a_first_toggle():
    """The inspector's undo writes a missing key back as null, and nodes saved
    before these switches existed have no key for them."""
    from app.models import NarrationData, SayData

    for model in (SayData, NarrationData):
        data = model.model_validate({"cg_hold": None, "cg_clear": None})
        assert data.cg_hold is False and data.cg_clear is False


def test_generated_art_is_scaled_to_the_projects_resolution():
    """Sprites are anchored bottom-centre by config.default_transform, so an
    unscaled 1216px sprite loses its head off the top, not its feet."""
    result = build([N("s1", "scene", title="X")], [])
    images = result.files["game/generated/images.rpy"]

    assert "transform vn_sprite:" in images
    assert "transform vn_bg:" in images
    # Relative to the running resolution, never a baked-in zoom.
    assert "config.screen_height" in images
    assert "define vn_sprite_source_height = 1216" in images

    from app.compiler.renpy import compile_project
    tall = compile_project(PROJECT, [N("s1", "scene", title="X")], [], [], [],
                           sprite_size=(1024, 1536), bg_size=(1920, 1080))
    assert "define vn_sprite_source_height = 1536" in tall.files["game/generated/images.rpy"]
    assert "define vn_bg_source_size = (1920, 1080)" in tall.files["game/generated/images.rpy"]


# --------------------------------------------------------------------------
# preview compiler: the player is never a sprite there either
# --------------------------------------------------------------------------

def test_preview_never_emits_show_ops_for_the_player():
    from app.compiler.preview import compile_preview

    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("st", "stage", show=["pc", "aiko"], expressions={"pc": "smiling"},
          outfits={"pc": "casual"}),
        N("a", "say", speaker="pc", text="Hey."),
    ]
    edges = [E("s1", "st"), E("st", "a")]
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])

    ops = [op for block in program["blocks"].values() for op in block]
    shows = [op for op in ops if op["op"] == "show"]
    assert all(op["character"] != "pc" for op in shows)
    assert any(op["character"] == "aiko" for op in shows)


def test_preview_voices_exactly_the_lines_the_build_voices():
    """The player plays `voice` as an asset URL, so it must be the hash -- and
    only where the Ren'Py compiler would emit a `voice` statement."""
    from app.compiler.preview import compile_preview

    mute = Character(id="mio", display_name="Mio", age=20,
                     voice={"voiced": False}, outfits=AIKO.outfits,
                     default_outfit="uniform")
    voice = {"voice_path": "audio/voice/say_abc.ogg", "voice_hash": "a" * 64}
    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("a", "say", speaker="aiko", text="Voiced.", **voice),
        N("b", "say", speaker="pc", text="Player.", **voice),
        N("c", "say", speaker="mio", text="Unvoiced character.", **voice),
        N("d", "say", speaker="aiko", text="No clip yet."),
    ]
    edges = [E("s1", "a"), E("a", "b"), E("b", "c"), E("c", "d")]
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC, mute], [SCHOOL])

    says = {op["text"]: op.get("voice") for block in program["blocks"].values()
            for op in block if op["op"] == "say"}
    assert says == {"Voiced.": "a" * 64, "Player.": None,
                    "Unvoiced character.": None, "No clip yet.": None}
    built = script(build(nodes, edges, characters=[AIKO, PC, mute]))
    assert built.count("voice ") == 1


def test_preview_stage_restore_skips_the_player():
    from app.compiler.preview import compile_preview

    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("st", "stage", show=["pc", "aiko"]),
        N("a", "say", speaker="aiko", text="One."),
        N("b", "say", speaker="aiko", text="Two."),
    ]
    edges = [E("s1", "st"), E("st", "a"), E("a", "b")]
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC], [SCHOOL],
                              start_node_id="b")
    restored = program["blocks"][program["entry"]]
    shows = [op for op in restored if op["op"] == "show"]
    assert shows and all(op["character"] == "aiko" for op in shows)


def test_preview_stage_restore_names_the_nodes_that_set_it():
    """The player opens these when the backdrop or a sprite is clicked, so they
    have to be the upstream nodes, not the node playback started from."""
    from app.compiler.preview import compile_preview

    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("st", "stage", show=["aiko"]),
        N("a", "say", speaker="aiko", text="One.", expression="smiling"),
        N("n", "narration", text="Wind."),
        N("b", "say", speaker="aiko", text="Two.", expression="smiling"),
    ]
    edges = [E("s1", "st"), E("st", "a"), E("a", "n"), E("n", "b")]
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC], [SCHOOL],
                              start_node_id="b")
    block = program["blocks"][program["entry"]]
    restored = [op for op in block if op.get("restored")]
    assert [op["op"] for op in restored] == ["scene", "show"]
    assert restored[0]["source"] == "s1"
    assert restored[1]["source"] == "a"
    # Everything after the setup is the node's own, and none of it is tagged.
    assert block.index(restored[-1]) == len(restored) - 1
    assert any(op["op"] == "say" and op["node"] == "b" for op in block)


def test_preview_shows_a_pose_change_the_build_shows():
    from app.compiler.preview import compile_preview

    nodes = [
        N("s1", "scene", title="V", background="school_rooftop"),
        N("a", "say", speaker="aiko", text="One."),
        N("b", "say", speaker="aiko", text="Two.", pose="arms_crossed"),
    ]
    edges = [E("s1", "a"), E("a", "b")]
    program = compile_preview(PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])
    shows = [op for block in program["blocks"].values() for op in block
             if op["op"] == "show" and op["node"] == "b"]
    assert [op["pose"] for op in shows] == ["arms_crossed"]
    assert "arms_crossed" in script(build(nodes, edges))


# --------------------------------------------------------------------------
# character stats
# --------------------------------------------------------------------------

def test_stats_compile_to_one_default_per_character():
    """Every {stat}_{character} pair needs a `default`, or the first choice
    effect that touches one crashes Ren'Py with an undefined name."""
    project = ProjectMeta(id="p1", name="Stats", character_stats=[
        StatDef(id="affection", default="0"),
        StatDef(id="progress", type="int", default="0"),
    ])
    aiko = AIKO.model_copy(update={"stat_overrides": {"affection": "30"}})
    result = compile_project(project, [N("s1", "scene", title="S")], [],
                             [aiko, PC], [SCHOOL])
    flags = result.files["game/generated/flags.rpy"]

    assert "default affection_aiko = 30" in flags     # the override
    assert "default progress_aiko = 0" in flags
    # The player gets them too: nothing says a stat cannot be about the PC.
    assert "default affection_pc = 0" in flags


def test_a_hand_declared_variable_is_not_reset_by_the_stat_schema():
    project = ProjectMeta(
        id="p1", name="Stats",
        character_stats=[StatDef(id="affection", default="0")],
        variables=[VariableDef(name="affection_aiko", type="int", default="7")],
    )
    result = compile_project(project, [N("s1", "scene", title="S")], [],
                             [AIKO], [SCHOOL])
    assert "default affection_aiko = 7" in result.files["game/generated/flags.rpy"]


# --------------------------------------------------------------------------
# sound cues
# --------------------------------------------------------------------------

def _scene_text(result) -> str:
    return "\n".join(v for k, v in result.files.items() if k.startswith("game/generated/scenes/"))


def test_an_unresolved_sound_cue_compiles_to_a_comment():
    """The writer marks a sound long before there is a file for it. A
    `play sound "knock at the door"` would break the build on a missing asset,
    so an un-sourced cue has to stay inert."""
    nodes = [
        N("s1", "scene", title="S", background="school_rooftop"),
        N("n1", "narration", text="The door.", sfx_cue="knock at the door"),
    ]
    body = _scene_text(build(nodes, [E("s1", "n1")]))

    assert "# TODO sfx: knock at the door" in body
    assert "play sound" not in body


def test_a_sourced_sound_cue_plays_before_the_line():
    nodes = [
        N("s1", "scene", title="S", background="school_rooftop"),
        N("a", "say", speaker="aiko", text="Come in.",
          sfx_cue="knock at the door", sfx_file="knock.ogg"),
    ]
    result = build(nodes, [E("s1", "a")])
    body = _scene_text(result)

    play = body.index('play sound "audio/sfx/knock.ogg"')
    assert play < body.index("Come in.")
    assert result.sound_lines == 1


def test_music_on_a_stage_node_does_not_drag_a_transition_with_it():
    """A stage node emits `with <transition>` only when something visual
    changed. A music cue alone is not that, and pairing one with a dissolve
    would fade the screen for no reason."""
    nodes = [
        N("s1", "scene", title="S", background="school_rooftop"),
        N("st", "stage", music_cue="sparse piano, colder", transition="dissolve"),
        N("a", "say", speaker="aiko", text="Sit down."),
    ]
    body = _scene_text(build(nodes, [E("s1", "st"), E("st", "a")]))

    assert "# TODO music: sparse piano, colder" in body
    assert "with dissolve" not in body.split("# TODO music")[1].split("aiko")[0]


def test_stopping_the_music_needs_no_file():
    nodes = [
        N("s1", "scene", title="S", background="school_rooftop"),
        N("st", "stage", stop_music=True),
    ]
    body = _scene_text(build(nodes, [E("s1", "st")]))
    assert "stop music fadeout 1.0" in body


# --------------------------------------------------------------------------
# hub nodes
# --------------------------------------------------------------------------

HUB_PROJECT = ProjectMeta(id="p1", name="Hub", character_stats=[
    StatDef(id="affection", default="0"),
])


def _hub_block(program, node_id):
    label = next(lbl for lbl, nid in program["labels"].items() if nid == node_id)
    return program["blocks"][label]


def test_hub_gates_options_on_declared_stats():
    """A hub's conditions are structured, so the compiler builds the `if`
    clause from `{stat, op, value}` against the same `{stat}_{character}`
    variable the flags file declares -- there is no author-typed expression
    that could name a variable which does not exist."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("hub", "hub", slug="topics", prompt="What do you ask about?", options=[
            {"id": "o1", "label": "The club"},
            {"id": "o2", "label": "Her sister", "locked": "grey",
             "locked_label": "Her sister (she barely knows you)",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "20"}]},
        ]),
        N("club", "say", speaker="aiko", text="It is quiet.", slug="topic_club"),
        N("sis", "say", speaker="aiko", text="She left.", slug="topic_sister"),
    ]
    edges = [E("s1", "hub"), E("hub", "club", "o1"), E("hub", "sis", "o2")]
    out = compile_project(HUB_PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])
    text = script(out)

    assert "menu (set=hub_topics):" in text
    assert '"The club":' in text                      # no gate, no `if` clause
    assert '"Her sister" if affection_aiko >= 20:' in text
    assert f"call {out.labels['sis']}" in text        # hubs default to `call`
    assert f"jump {out.labels['hub']}" in text        # ...and loop back
    assert "default hub_topics = set()" in out.files["game/generated/flags.rpy"]


def test_a_greyed_hub_option_is_shown_as_an_inert_twin():
    """Ren'Py's menu statement has no disabled item, so `grey` compiles to a
    second entry under the negated guard whose body goes straight back to the
    hub -- and un-remembers itself, or the menuset would hide the locked line
    for the rest of the game the first time someone clicked it."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("hub", "hub", slug="topics", options=[
            {"id": "o1", "label": "Her sister", "locked": "grey",
             "locked_label": "Her sister - needs affection 20",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "20"}]},
        ]),
        N("sis", "say", speaker="aiko", text="She left.", slug="topic_sister"),
    ]
    out = compile_project(HUB_PROJECT, nodes, [E("s1", "hub"), E("hub", "sis", "o1")],
                          [AIKO, PC], [SCHOOL])
    text = script(out)
    hub_label = out.labels["hub"]

    assert '"Her sister - needs affection 20" if not (affection_aiko >= 20):' in text
    assert '$ hub_topics.discard("Her sister - needs affection 20")' in text
    locked_at = text.index("Her sister - needs affection 20")
    assert text.index(f"jump {hub_label}", locked_at) > locked_at


def test_a_hidden_hub_option_gets_no_greyed_twin():
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("hub", "hub", slug="topics", options=[
            {"id": "o1", "label": "Her sister",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "20"}]},
        ]),
        N("sis", "say", speaker="aiko", text="She left.", slug="topic_sister"),
    ]
    text = script(compile_project(HUB_PROJECT, nodes,
                                  [E("s1", "hub"), E("hub", "sis", "o1")],
                                  [AIKO, PC], [SCHOOL]))
    assert '"Her sister" if affection_aiko >= 20:' in text
    assert "if not (" not in text


def test_a_hub_condition_can_name_a_plain_variable():
    """No character means the stat field is already a whole variable name, so
    a hub can gate on story state that is not about anybody."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("hub", "hub", slug="topics", options=[
            {"id": "o1", "label": "The letter",
             "requires": [{"stat": "knows_letter", "op": "==", "value": "true"}]},
        ]),
        N("l", "say", speaker="aiko", text="You read it.", slug="topic_letter"),
    ]
    text = script(build(nodes, [E("s1", "hub"), E("hub", "l", "o1")]))
    assert '"The letter" if knows_letter == True:' in text


def test_hub_option_can_target_the_next_unplayed_scene_in_a_route():
    """The point of a route destination: the hub does not have to be rewired
    every time a scene is added to the route."""
    nodes = [
        N("s0", "scene", title="Hub scene", background="school_rooftop"),
        N("hub", "hub", slug="routes", mode="jump", loop_back=False, exhaust=False,
          options=[{"id": "o1", "label": "See Aiko", "target_route": "aiko"}]),
        N("d1", "scene", title="Aiko 1", route="aiko", background="school_rooftop"),
        N("d2", "scene", title="Aiko 2", route="aiko", background="school_rooftop"),
    ]
    out = build(nodes, [E("s0", "hub")])
    flags = out.files["game/generated/flags.rpy"]
    text = script(out)

    assert "default vn_visited = set()" in flags
    assert f'"aiko": ["{out.labels["d1"]}", "{out.labels["d2"]}"],' in flags
    assert "def vn_next_in_route(route):" in flags

    # Resolved when the option is picked, not at compile time.
    assert '"See Aiko" if vn_next_in_route("aiko") is not None:' in text
    assert 'jump expression vn_next_in_route("aiko")' in text
    # ...which only works because each route scene records that it played.
    assert f'$ vn_visited.add("{out.labels["d1"]}")' in text
    assert f'$ vn_visited.add("{out.labels["d2"]}")' in text


def test_a_jump_node_can_target_the_next_unplayed_scene_in_a_route():
    """The same dispatch a hub option gets, on a node the author can drop
    anywhere -- "carry on down this route" without a menu wrapped round it."""
    nodes = [
        N("s0", "scene", title="Opening", background="school_rooftop"),
        N("j", "jump", slug="next_aiko", target_route="aiko"),
        N("d1", "scene", title="Aiko 1", route="aiko", background="school_rooftop"),
        N("d2", "scene", title="Aiko 2", route="aiko", background="school_rooftop"),
    ]
    out = build(nodes, [E("s0", "j")])
    text = script(out)

    assert '$ vn_next = vn_next_in_route("aiko")' in text
    assert "jump expression vn_next" in text
    # Nothing is wired past the jump, so an exhausted route ends the block
    # rather than falling into whatever happens to be next in the file.
    assert "return" in text
    assert "default vn_next = None" in out.files["game/generated/flags.rpy"]


def test_an_exhausted_route_falls_through_to_the_jumps_wired_target():
    """The wired edge stops being the destination and becomes the fallback,
    which is what makes "play the route, then back to the hub" one node."""
    nodes = [
        N("s0", "scene", title="Opening", background="school_rooftop"),
        N("j", "jump", slug="next_aiko", target_route="aiko"),
        N("d1", "scene", title="Aiko 1", route="aiko", background="school_rooftop"),
        N("back", "scene", title="Back at the hub", background="school_rooftop"),
    ]
    out = build(nodes, [E("s0", "j"), E("j", "back")])
    text = script(out)

    assert "jump expression vn_next" in text
    assert f'jump {out.labels["back"]}' in text


def test_a_jump_to_a_route_nobody_claims_warns_and_uses_the_wired_target():
    """`vn_next_in_route` is only defined when a route table exists, so
    dispatching over a route no scene belongs to would be a NameError at
    run time. It degrades to the plain jump instead."""
    nodes = [
        N("s0", "scene", title="Opening", background="school_rooftop"),
        N("j", "jump", slug="nowhere", target_route="ghost"),
        N("back", "scene", title="Elsewhere", background="school_rooftop"),
    ]
    out = build(nodes, [E("s0", "j"), E("j", "back")])
    text = script(out)

    assert any("route 'ghost'" in w for w in out.warnings)
    assert "vn_next_in_route" not in text
    assert f'jump {out.labels["back"]}' in text


def test_a_route_with_no_scenes_warns_rather_than_compiling_a_dead_option():
    nodes = [
        N("s0", "scene", title="Hub scene", background="school_rooftop"),
        N("hub", "hub", slug="routes", options=[
            {"id": "o1", "label": "See Aiko", "target_route": "aiko"}]),
    ]
    out = build(nodes, [E("s0", "hub")])
    assert any("route 'aiko'" in w for w in out.warnings)


def test_scenes_outside_a_route_do_not_record_visits():
    out = build([N("s1", "scene", title="Plain", background="school_rooftop")], [])
    assert "vn_visited" not in script(out)
    assert "vn_routes" not in out.files["game/generated/flags.rpy"]


def test_the_preview_gets_the_hub_conditions_structured():
    """The player cannot eval a Python expression -- `True` is not `true` --
    so a hub option's gates cross as `{var, op, value}` triples instead."""
    from app.compiler.preview import compile_preview

    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("hub", "hub", slug="topics", options=[
            {"id": "o1", "label": "Her sister", "locked": "grey",
             "locked_label": "Her sister (locked)",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "20"}]},
        ]),
        N("sis", "say", speaker="aiko", text="She left.", slug="topic_sister"),
        N("d1", "scene", title="Aiko 1", route="aiko", background="school_rooftop"),
    ]
    program = compile_preview(HUB_PROJECT, nodes,
                              [E("s1", "hub"), E("hub", "sis", "o1")],
                              [AIKO, PC], [SCHOOL])
    menu = next(op for op in _hub_block(program, "hub") if op["op"] == "menu")

    assert menu["hub"] is True
    option = menu["options"][0]
    assert option["requires"] == [{"var": "affection_aiko", "op": ">=", "value": "20"}]
    assert option["locked"] == "grey"
    assert option["locked_label"] == "Her sister (locked)"

    d1_label = next(lbl for lbl, nid in program["labels"].items() if nid == "d1")
    assert program["routes"]["aiko"] == [d1_label]


def test_a_repeatable_hub_topic_forgets_itself_so_it_stays_on_the_menu():
    """An exhausting hub that loops runs out of topics, and Ren'Py raises on a
    menu with nothing in it -- so the way out has to survive being taken."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("hub", "hub", slug="topics", options=[
            {"id": "o1", "label": "Chat"},
            {"id": "o2", "label": "Leave", "repeatable": True},
        ]),
        N("chat", "say", speaker="aiko", text="Hey.", slug="t_chat"),
        N("out", "end", slug="t_out", mode="return"),
    ]
    out = build(nodes, [E("s1", "hub"), E("hub", "chat", "o1"), E("hub", "out", "o2")])
    text = script(out)

    assert '$ hub_topics.discard("Leave")' in text
    assert '$ hub_topics.discard("Chat")' not in text


def test_a_line_wired_into_another_scene_compiles_to_a_jump():
    """What "go to the next scene" is, underneath: an edge into the next Scene
    container. Every scene is a label, so no Jump node is needed in between --
    which is what the Script and Improv "Go to scene..." pickers write."""
    nodes = [
        N("s1", "scene", title="Opening", background="school_rooftop"),
        N("a", "say", speaker="aiko", text="Goodnight.", slug="goodnight"),
        N("s2", "scene", title="Meeting", background="school_rooftop"),
        N("b", "say", speaker="aiko", text="Sit down.", slug="sit_down"),
    ]
    out = build(nodes, [E("s1", "a"), E("a", "s2"), E("s2", "b")])
    text = script(out)

    # The first scene ends by jumping into the second, which keeps its own
    # label -- so the transition costs one statement and no extra node.
    assert f"jump {out.labels['s2']}" in text
    assert f"label {out.labels['s2']}:" in text
    assert not out.warnings


# --------------------------------------------------------------------------
# structured stat gates on choices and conditions
# --------------------------------------------------------------------------

def test_choice_option_gates_on_a_picked_stat():
    """A choice option gates the same way a hub topic does. The point is that
    the author picks `[Aiko] affection >= 20` instead of typing a variable name
    that only exists because the stat schema implies it."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("ch", "choice", slug="ask", prompt="What do you say?", options=[
            {"id": "o1", "label": "Small talk"},
            {"id": "o2", "label": "Ask her out",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "20"}]},
        ]),
        N("a", "say", speaker="aiko", text="Sure.", slug="small"),
        N("b", "say", speaker="aiko", text="Okay.", slug="askout"),
    ]
    edges = [E("s1", "ch"), E("ch", "a", "o1"), E("ch", "b", "o2")]
    out = compile_project(HUB_PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])
    text = script(out)

    assert '"Small talk":' in text                    # no gate, no `if` clause
    assert '"Ask her out" if affection_aiko >= 20:' in text
    assert "default affection_aiko = 0" in out.files["game/generated/flags.rpy"]


def test_a_choice_gate_and_a_typed_expression_are_anded():
    """The typed expression is parenthesised: `a or b` ANDed with a stat gate
    means something else without the brackets."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("ch", "choice", slug="ask", options=[
            {"id": "o1", "label": "Ask her out", "condition": "knows_her or bold",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "20"}]},
            {"id": "o2", "label": "Say nothing"},
        ]),
        N("a", "say", speaker="aiko", text="Okay.", slug="askout"),
        N("b", "say", speaker="aiko", text="...", slug="silence"),
    ]
    edges = [E("s1", "ch"), E("ch", "a", "o1"), E("ch", "b", "o2")]
    out = compile_project(HUB_PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])

    assert ('"Ask her out" if affection_aiko >= 20 and (knows_her or bold):'
            in script(out))


def test_condition_branch_gates_on_a_picked_stat():
    """A branch with picked gates and no expression is a real branch, not the
    `else` -- the else is the one with neither."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("cond", "condition", slug="check", branches=[
            {"id": "b1", "expr": "",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "50"}]},
        ], has_else=True),
        N("warm", "say", speaker="aiko", text="You came.", slug="warm"),
        N("cold", "say", speaker="aiko", text="Oh. You.", slug="cold"),
    ]
    edges = [E("s1", "cond"), E("cond", "warm", "b1"), E("cond", "cold", "else")]
    out = compile_project(HUB_PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])
    text = script(out)

    assert "if affection_aiko >= 50:" in text
    assert "else:" in text


def test_condition_branch_effects_fire_on_the_way_through():
    """A branch can adjust a stat itself, so "she notices, +5 affection" is one
    node rather than a Set node hanging off every arm. The variable still gets
    a `default`, or Ren'Py raises the first time it is read."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("cond", "condition", slug="check", branches=[
            {"id": "b1", "expr": "",
             "requires": [{"stat": "affection", "character": "aiko",
                           "op": ">=", "value": "50"}],
             "effects": [{"var": "affection_aiko", "op": "+=", "value": "5"},
                         {"var": "noticed", "op": "=", "value": "true"}]},
        ], has_else=True),
        N("warm", "say", speaker="aiko", text="You came.", slug="warm"),
        N("cold", "say", speaker="aiko", text="Oh. You.", slug="cold"),
    ]
    edges = [E("s1", "cond"), E("cond", "warm", "b1"), E("cond", "cold", "else")]
    out = compile_project(HUB_PROJECT, nodes, edges, [AIKO, PC], [SCHOOL])
    text = script(out)
    flags = out.files["game/generated/flags.rpy"]

    assert "if affection_aiko >= 50:" in text
    # The adjustment lands inside the branch, ahead of where it goes next.
    at = text.index("if affection_aiko >= 50:")
    assert text.index("$ affection_aiko += 5", at) > at
    assert text.index("$ noticed = True", at) > at
    # A `default` is the value before anything sets it, so a flag this branch
    # switches on starts off -- and the stat keeps the schema's starting value
    # rather than the one it is bumped to.
    assert "default noticed = False" in flags
    assert "default affection_aiko = 0" in flags


def test_a_branch_with_only_effects_needs_no_pass():
    """`pass` is for an empty body. Effects are statements, so emitting both
    would be dead code inside every gated branch that goes nowhere yet."""
    nodes = [
        N("s1", "scene", title="Dorm", background="school_rooftop"),
        N("cond", "condition", slug="check", branches=[
            {"id": "b1", "expr": "noticed",
             "effects": [{"var": "affection_aiko", "op": "+=", "value": "5"}]},
        ], has_else=False),
    ]
    out = compile_project(HUB_PROJECT, nodes, [E("s1", "cond")], [AIKO, PC], [SCHOOL])
    text = script(out)

    assert "$ affection_aiko += 5" in text
    at = text.index("if noticed:")
    assert "pass" not in text[at:at + 120]
