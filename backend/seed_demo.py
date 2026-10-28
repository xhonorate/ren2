"""Seed a demo project: a 22-node graph with branches, a merge point,
a conditional, and a hub scene.

This is the doc's Phase 1 check -- hand-build a branching graph and confirm the
compiler emits runnable Ren'Py before any of the AI layers are involved.

    python seed_demo.py            # create it
    python seed_demo.py --export DIR   # also write the .rpy files
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db, store
from app.models import (
    Appearance, Character, Edge, Location, Node, NodeType, Outfit,
    Personality, TaggedField, VoiceCfg,
)
from app.services import build as build_svc


def tf(label: str, tag: str) -> TaggedField:
    return TaggedField(label=label, tag=tag)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", metavar="DIR", help="also export the Ren'Py project here")
    parser.add_argument("--name", default="Rooftop Demo")
    args = parser.parse_args()

    db.init_db()
    project = store.create_project(args.name)
    pid = project.id
    print(f"project {pid}  ({project.name})")

    project.world.setting = "A coastal high school, third year, the week before exams."
    project.world.era = "present day"
    project.world.rating = "PG-13"
    project.world.pov = "second person"
    project.world.tense = "present"
    project.world.style_notes = (
        "Restrained and concrete. Short paragraphs. Let silence do the work. "
        "No melodrama, no purple prose."
    )
    project.world.themes = "distance, the things people do not say"
    store.save_project(project)

    # -- characters --------------------------------------------------------

    aiko = Character(
        id="aiko", display_name="Aiko", age=22, gender="female", color="#c8b4ff",
        appearance=Appearance(
            hair_color=tf("platinum blonde", "platinum blonde hair"),
            hair_style=tf("long, straight, hime cut", "long hair, hime cut, straight hair"),
            eye_color=tf("pale grey", "grey eyes"),
            eye_quality=tf("sharp, guarded", "tsurime, half-closed eyes"),
            face=tf("elegant, refined", "refined features"),
            skin_tone=tf("pale", "pale skin"),
            build=tf("lean, athletic", "slender, toned"),
            height=tf("tall (175cm)", "tall"),
            bust=tf("small breasts", "small breasts"),
            butt=tf("round butt", "round ass, bubble butt"),
            rest_expression=tf("flat, unreadable", "expressionless"),
            posture=tf("arms crossed", "crossed arms"),
            distinguishing=[
                tf("mole under left eye", "mole under eye"),
                tf("shaved vagina", "shaved vagina"),
            ],
        ),
        outfits=[
            Outfit(id="uniform", name="School uniform",
                   tags="school uniform, pleated skirt, black thighhighs, red ribbon"),
            Outfit(id="casual", name="Weekend", tags="oversized hoodie, denim shorts"),
        ],
        default_outfit="uniform",
        personality=Personality(
            summary="Guarded, dry, quietly loyal. Deflects anything that gets close.",
            speech_style="Short sentences. Undercuts sincerity with a joke.",
            speech_register="casual",
            wants="to leave without having to explain herself",
            fears="that being known means being owed something",
            relationship_to_player="Two years of walking home together and nothing said.",
            loves="the sea in winter, being trusted with a secret",
            dislikes="being fussed over, goodbyes, questions about her family",
            hobbies="distance swimming, taping over old cassettes",
            background="Moved to the coast at fourteen after her parents split; "
                       "has been the one who leaves ever since.",
        ),
        voice=VoiceCfg(description="low, dry, slightly hoarse; unhurried", emotion_scale=0.7),
    )
    aiko.assets.trigger = "chr_aiko"
    store.save_character(pid, aiko)

    player = Character(id="pc", display_name="You", age=21, is_player=True, color="#9fd3ff",
                       voice=VoiceCfg(voiced=False))
    store.save_character(pid, player)

    store.save_location(pid, Location(
        id="school_rooftop", name="School rooftop",
        tags="rooftop, chain-link fence, city skyline, water tower"))
    store.save_location(pid, Location(
        id="stairwell", name="Stairwell", tags="stairwell, concrete stairs, fluorescent light"))

    # -- graph -------------------------------------------------------------

    nodes: list[Node] = []
    edges: list[Edge] = []
    y = [0]

    def add(node_type: str, data: dict, slug: str = "", parent: str | None = None,
            x: float = 0) -> Node:
        node = Node(id=store.new_id("n_"), type=NodeType(node_type), data=data,
                    slug=slug or node_type, parent_id=parent, x=x, y=y[0])
        y[0] += 150
        nodes.append(node)
        return node

    def link(src: Node, dst: Node, handle: str = "out") -> None:
        edges.append(Edge(id=store.new_id("e_"), source=src.id, target=dst.id,
                          source_handle=handle))

    scene = add("scene", {
        "title": "Rooftop, after class", "background": "school_rooftop",
        "time_of_day": "dusk", "transition": "dissolve", "music": "quiet_theme.ogg",
        "brief": "Aiko is avoiding the question. Do not resolve it.",
    }, "rooftop")

    stage = add("stage", {"show": ["aiko"], "outfits": {"aiko": "uniform"},
                          "positions": {"aiko": "center"},
                          "expressions": {"aiko": "looking_away"}}, "enter", scene.id)
    n1 = add("narration", {"text": "The door sticks, then gives all at once."},
             "door", scene.id)
    n2 = add("say", {"speaker": "aiko", "text": "You came.", "expression": "looking_away",
                     "emotion": "melancholy", "intensity": 0.6,
                     "delivery": "flat, not looking over",
                     "shot_hint": "upper body, from behind, wind"}, "you_came", scene.id)
    n3 = add("narration", {"text": "She is holding the railing like it might move."},
             "railing", scene.id)
    flags = add("set", {"assignments": [
        {"var": "met_aiko", "op": "=", "value": "true"},
        {"var": "affinity", "op": "+=", "value": "1"},
    ]}, "met", scene.id)

    choice = add("choice", {
        "prompt": "What do you say?",
        "options": [
            {"id": "o1", "label": "Push her on it", "condition": "",
             "branch_hint": "press her about the transfer; she closes up"},
            {"id": "o2", "label": "Let it go", "condition": "",
             "branch_hint": "say nothing; stand next to her"},
            {"id": "o3", "label": "Ask about the letter", "condition": "knows_letter",
             "branch_hint": "she is startled you know"},
        ],
    }, "what_do_you_say", scene.id)

    push = add("say", {"speaker": "aiko", "text": "Don't. Not today.",
                       "expression": "frowning", "emotion": "irritated", "intensity": 0.7},
               "push", scene.id, x=-320)
    push2 = add("set", {"assignments": [{"var": "affinity", "op": "-=", "value": "1"}]},
                "push_cost", scene.id, x=-320)

    let = add("narration", {"text": "You let the silence sit. It is not comfortable, "
                                    "but it is hers."}, "let_go", scene.id, x=0)
    let2 = add("set", {"assignments": [{"var": "affinity", "op": "+=", "value": "2"}]},
               "let_reward", scene.id, x=0)

    letter = add("say", {"speaker": "aiko", "text": "...Who told you about that?",
                         "expression": "surprised", "emotion": "flustered", "intensity": 0.8},
                 "letter", scene.id, x=320)

    merge = add("narration", {"text": "The wind comes up off the water and takes "
                                       "the rest of it."}, "merge", scene.id)

    cond = add("condition", {"branches": [
        {"id": "b1", "expr": "affinity >= 3"},
        {"id": "b2", "expr": "affinity >= 1"},
        {"id": "b3", "expr": ""},
    ], "has_else": True}, "affinity_check", scene.id)

    warm = add("say", {"speaker": "aiko", "text": "Thanks. For not making me say it.",
                       "expression": "soft_smile", "emotion": "affectionate", "intensity": 0.5,
                       "cg": True, "shot_hint": "close-up, three-quarter view, golden hour"},
               "warm", scene.id, x=-320)
    mild = add("say", {"speaker": "aiko", "text": "You should go. It gets cold up here.",
                       "expression": "neutral", "emotion": "tired", "intensity": 0.4},
               "mild", scene.id, x=0)
    cold = add("say", {"speaker": "aiko", "text": "I should go.", "expression": "neutral",
                       "emotion": "cold", "intensity": 0.4}, "cold", scene.id, x=320)

    # A hub scene: menuset auto-hides taken options, each option calls back.
    hub = add("choice", {
        "prompt": "Anything else?",
        "options": [
            {"id": "h1", "label": "Ask about her family", "condition": "", "branch_hint": ""},
            {"id": "h2", "label": "Ask about the club", "condition": "", "branch_hint": ""},
            {"id": "h3", "label": "Say goodnight", "condition": "", "branch_hint": ""},
        ],
        "mode": "call", "exhaust": True, "menuset": "rooftop_topics", "loop_back": True,
    }, "hub", scene.id)

    family = add("say", {"speaker": "aiko", "text": "They're fine. That's the whole answer.",
                         "expression": "smirking", "emotion": "smug", "intensity": 0.4},
                 "topic_family", scene.id, x=-320)
    club = add("say", {"speaker": "aiko", "text": "Quiet. Everyone's studying.",
                       "expression": "neutral", "emotion": "neutral", "intensity": 0.3},
               "topic_club", scene.id, x=0)
    night = add("narration", {"text": "She lifts two fingers off the railing. "
                                       "That is the goodbye."}, "goodnight", scene.id, x=320)

    ending = add("end", {"mode": "ending", "ending_id": "rooftop",
                         "unlock_persistent": ["cg_rooftop_unlocked"]}, "fin", scene.id)

    link(scene, stage)
    link(stage, n1)
    link(n1, n2)
    link(n2, n3)
    link(n3, flags)
    link(flags, choice)

    link(choice, push, "o1")
    link(choice, let, "o2")
    link(choice, letter, "o3")
    link(push, push2)
    link(let, let2)

    # three-way merge
    link(push2, merge)
    link(let2, merge)
    link(letter, merge)

    link(merge, cond)
    link(cond, warm, "b1")
    link(cond, mild, "b2")
    link(cond, cold, "b3")

    # all three converge on the hub
    link(warm, hub)
    link(mild, hub)
    link(cold, hub)

    link(hub, family, "h1")
    link(hub, club, "h2")
    link(hub, night, "h3")
    link(night, ending)

    for node in nodes:
        store.insert_node(pid, node)
    for edge in edges:
        store.insert_edge(pid, edge)

    print(f"  {len(nodes)} nodes, {len(edges)} edges, "
          f"{len(store.list_characters(pid))} characters")

    # -- compile -----------------------------------------------------------

    result = build_svc.compile_ir(pid)
    print(f"  compiled {len(result.files)} files, {len(result.labels)} labels")
    for warning in result.warnings:
        print(f"  ! {warning}")

    report = build_svc.validate(pid)
    print(f"  validation: {report['errors']} errors, {report['warnings']} warnings")
    for problem in report["problems"]:
        print(f"    {problem['severity']}: {problem['message']}")

    if args.export:
        out = build_svc.export(pid, copy_assets=False, dest=args.export)
        print(f"  exported to {out['project_dir']}")
        for name in out["files"]:
            print(f"    {name}")

    print("\nOpen the app and pick this project from the top bar.")


if __name__ == "__main__":
    main()
