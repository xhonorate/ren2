"""Script view: parsing, emission, round-trip, and linearization.

The round-trip property that matters: parsing text and emitting it again must
not mangle the words. And linearization must stay bound to node ids, because a
new id means a new Ren'Py label means a broken player save.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.graph import Graph
from app.models import Character, Edge, Node, NodeType, Outfit
from app.services.script import emit_script, linearize, parse_script

AIKO = Character(id="aiko", display_name="Aiko", age=22,
                 outfits=[Outfit(id="uniform", name="Uniform", tags="uniform"),
                          Outfit(id="casual", name="Casual", tags="hoodie")],
                 default_outfit="uniform")
PC = Character(id="pc", display_name="You", age=21, is_player=True)
CHARACTERS = {"aiko": AIKO, "pc": PC}


def N(nid, ntype, **data):
    return Node(id=nid, type=NodeType(ntype), data=data, slug=nid)


def E(src, dst, handle="out"):
    return Edge(id=f"{src}>{dst}:{handle}", source=src, target=dst, source_handle=handle)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def test_parses_speakers_narration_and_attributes():
    text = """\
AIKO (looking_away, melancholy 0.6): You came.

The door sticks, then gives all at once.

AIKO: Don't.
"""
    out = parse_script(text, CHARACTERS)
    kinds = [n["type"] for n in out["nodes"]]
    assert kinds == ["say", "narration", "say"]

    first = out["nodes"][0]["data"]
    assert first["speaker"] == "aiko"
    assert first["text"] == "You came."
    assert first["expression"] == "looking_away"
    assert first["emotion"] == "melancholy"
    assert first["intensity"] == 0.6

    # Defaults where no attributes were given.
    assert out["nodes"][2]["data"]["expression"] == "neutral"
    assert out["nodes"][2]["data"]["intensity"] == 0.5


def test_display_name_and_id_both_resolve_a_speaker():
    out = parse_script("Aiko: one\naiko: two\n", CHARACTERS)
    assert [n["data"]["speaker"] for n in out["nodes"]] == ["aiko", "aiko"]


def test_unknown_speaker_stays_narration_rather_than_inventing_a_character():
    out = parse_script("BOB: Who am I?\n", CHARACTERS)
    assert out["nodes"][0]["type"] == "narration"
    assert "BOB" in out["nodes"][0]["data"]["text"]
    assert any("not a known character" in w for w in out["warnings"])


def test_prose_with_a_colon_is_not_treated_as_a_speaker_cue():
    out = parse_script("She had one rule: never look back.\n", CHARACTERS)
    assert out["nodes"][0]["type"] == "narration"
    assert out["nodes"][0]["data"]["text"] == "She had one rule: never look back."


def test_paragraph_lines_join_into_one_narration_node():
    text = "The door sticks,\nthen gives all at once.\n\nShe does not look up.\n"
    out = parse_script(text, CHARACTERS)
    assert len(out["nodes"]) == 2
    assert out["nodes"][0]["data"]["text"] == "The door sticks, then gives all at once."


def test_parses_choices_sets_and_stage_directives():
    text = """\
> scene school_rooftop dusk
> show aiko uniform looking_away center

$ affinity += 1

? What do you say?
- Push her on it
- Ask about the letter [knows_letter]
"""
    out = parse_script(text, CHARACTERS)
    kinds = [n["type"] for n in out["nodes"]]
    assert kinds == ["stage", "set", "choice"]

    stage = out["nodes"][0]["data"]
    assert stage["location"] == "school_rooftop"
    assert stage["time_of_day"] == "dusk"
    assert stage["show"] == ["aiko"]
    assert stage["outfits"]["aiko"] == "uniform"
    assert stage["expressions"]["aiko"] == "looking_away"
    assert stage["positions"]["aiko"] == "center"

    assert out["nodes"][1]["data"]["assignments"][0] == {
        "var": "affinity", "op": "+=", "value": "1"}

    choice = out["nodes"][2]["data"]
    assert choice["prompt"] == "What do you say?"
    assert [o["label"] for o in choice["options"]] == ["Push her on it", "Ask about the letter"]
    assert choice["options"][1]["condition"] == "knows_letter"


def test_raw_blocks_pass_through_verbatim():
    text = '```\n$ renpy.notify("hi")\nshow screen hud\n```\n'
    out = parse_script(text, CHARACTERS)
    assert out["nodes"][0]["type"] == "raw"
    assert out["nodes"][0]["data"]["code"] == '$ renpy.notify("hi")\nshow screen hud'


def test_unknown_attribute_becomes_a_delivery_note_with_a_warning():
    out = parse_script("AIKO (whispering into her sleeve): hm.\n", CHARACTERS)
    data = out["nodes"][0]["data"]
    assert data["speaker"] == "aiko"
    assert "whispering into her sleeve" in data["delivery"]
    assert any("delivery note" in w for w in out["warnings"])


# --------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------

def _rows_from_specs(specs):
    """Shape parser output like linearizer rows, so emit_script can take it."""
    rows = []
    for spec in specs:
        d = spec["data"]
        if spec["type"] == "say":
            rows.append({"kind": "say", "speaker": d["speaker"], "text": d["text"],
                         "expression": d["expression"], "emotion": d["emotion"],
                         "intensity": d["intensity"]})
        elif spec["type"] == "narration":
            rows.append({"kind": "narration", "text": d["text"]})
        elif spec["type"] == "set":
            rows.append({"kind": "set", "text": "; ".join(
                f"{a['var']} {a['op']} {a['value']}" for a in d["assignments"])})
        elif spec["type"] == "choice":
            rows.append({"kind": "choice", "text": d["prompt"], "options": d["options"]})
        elif spec["type"] == "scene":
            rows.append({"kind": "scene", "text": d["title"]})
    return rows


ROUND_TRIP = """\
@ Rooftop, after class

AIKO (looking_away, melancholy 0.6): You came.

The door sticks, then gives all at once.

$ affinity += 1

AIKO: Don't.

? What do you say?
- Push her on it
- Ask about the letter [knows_letter]
"""


def test_parse_emit_parse_is_stable():
    once = parse_script(ROUND_TRIP, CHARACTERS)
    text = emit_script(_rows_from_specs(once["nodes"]))
    twice = parse_script(text, CHARACTERS)

    assert [n["type"] for n in once["nodes"]] == [n["type"] for n in twice["nodes"]]
    for a, b in zip(once["nodes"], twice["nodes"]):
        assert a["data"] == b["data"], a["type"]

    # And emitting the second parse produces byte-identical text.
    assert emit_script(_rows_from_specs(twice["nodes"])) == text


def test_round_trip_preserves_words_exactly():
    once = parse_script(ROUND_TRIP, CHARACTERS)
    text = emit_script(_rows_from_specs(once["nodes"]))
    for phrase in ("You came.", "The door sticks, then gives all at once.",
                   "Don't.", "What do you say?", "Push her on it",
                   "Ask about the letter"):
        assert phrase in text


def test_emitted_attributes_are_omitted_when_default():
    rows = [{"kind": "say", "speaker": "aiko", "text": "flat",
             "expression": "neutral", "emotion": "neutral", "intensity": 0.5}]
    assert emit_script(rows).strip() == "AIKO: flat"


# --------------------------------------------------------------------------
# linearization
# --------------------------------------------------------------------------

def _demo_graph():
    nodes = [
        N("s1", "scene", title="Rooftop", background="school_rooftop"),
        N("a", "say", speaker="aiko", text="You came.", expression="looking_away",
          emotion="melancholy", intensity=0.6),
        N("ch", "choice", prompt="What do you say?", options=[
            {"id": "o1", "label": "Push", "condition": "", "branch_hint": ""},
            {"id": "o2", "label": "Wait", "condition": "knows_letter", "branch_hint": ""},
        ]),
        N("push", "say", speaker="aiko", text="Don't.", expression="frowning"),
        N("wait", "narration", text="You wait."),
        N("merge", "narration", text="Wind."),
    ]
    edges = [E("s1", "a"), E("a", "ch"), E("ch", "push", "o1"), E("ch", "wait", "o2"),
             E("push", "merge"), E("wait", "merge")]
    return Graph.build(nodes, edges)


def test_linearize_follows_one_path_and_keeps_node_ids():
    graph = _demo_graph()
    out = linearize(graph, "s1", CHARACTERS)
    ids = [r["node_id"] for r in out["rows"]]

    # Default path takes the first wired option.
    assert ids == ["s1", "a", "ch", "push", "merge"]
    # Every row is bound to the node it came from -- nothing is re-created.
    assert all(r["node_id"] for r in out["rows"])
    assert out["taken"] == {"ch": "o1"}


def test_linearize_follows_the_chosen_branch():
    graph = _demo_graph()
    out = linearize(graph, "s1", CHARACTERS, choices={"ch": "o2"})
    assert [r["node_id"] for r in out["rows"]] == ["s1", "a", "ch", "wait", "merge"]


def test_choice_row_exposes_every_option_for_the_branch_picker():
    graph = _demo_graph()
    row = next(r for r in linearize(graph, "s1", CHARACTERS)["rows"] if r["kind"] == "choice")
    assert [o["label"] for o in row["options"]] == ["Push", "Wait"]
    assert all(o["wired"] for o in row["options"])
    assert row["options"][1]["condition"] == "knows_letter"
    assert row["selected_option"] == "o1"


def test_merge_points_are_marked_so_the_writer_knows_branches_rejoin():
    graph = _demo_graph()
    rows = linearize(graph, "s1", CHARACTERS)["rows"]
    merge = next(r for r in rows if r["node_id"] == "merge")
    assert merge["is_merge"] is True
    assert not any(r["is_merge"] for r in rows if r["node_id"] == "a")


def test_say_rows_carry_speaker_display_details():
    graph = _demo_graph()
    row = next(r for r in linearize(graph, "s1", CHARACTERS)["rows"] if r["kind"] == "say")
    assert row["speaker"] == "aiko"
    assert row["speaker_name"] == "Aiko"
    assert row["expression"] == "looking_away"
    assert row["intensity"] == 0.6


def test_linearize_stops_on_a_loop_instead_of_spinning():
    nodes = [N("a", "narration", text="ping"), N("b", "narration", text="pong")]
    graph = Graph.build(nodes, [E("a", "b"), E("b", "a")])
    rows = linearize(graph, "a", CHARACTERS)["rows"]
    assert rows[-1]["kind"] == "loop"
    assert len(rows) == 3


def test_non_text_nodes_are_shown_but_not_inline_editable():
    nodes = [
        N("s1", "scene", title="X"),
        N("st", "stage", show=["aiko"], outfits={"aiko": "casual"}),
        N("set", "set", assignments=[{"var": "affinity", "op": "+=", "value": "1"}]),
    ]
    graph = Graph.build(nodes, [E("s1", "st"), E("st", "set")])
    rows = linearize(graph, "s1", CHARACTERS)["rows"]
    stage = next(r for r in rows if r["kind"] == "stage")
    assert stage["editable"] is False
    assert "show aiko casual" in stage["text"]
    assert next(r for r in rows if r["kind"] == "set")["editable"] is False


def test_a_stage_row_carrying_only_music_still_says_something():
    """Music rides on a stage node and is often the only thing on it. Summarised
    as "(no changes)" a working node reads to the author as a bug."""
    nodes = [N("s1", "scene", title="X"),
             N("st", "stage", music_cue="sparse piano, colder")]
    graph = Graph.build(nodes, [E("s1", "st")])
    rows = linearize(graph, "s1", CHARACTERS)["rows"]

    assert next(r for r in rows if r["kind"] == "stage")["text"] == (
        "music sparse piano, colder")
