import shutil
from pathlib import Path

import yaml

from vn.compile_renpy import build
from vn.project import Project, validate

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"


def copy_demo(tmp_path):
    shutil.copytree(DEMO, tmp_path / "p")
    return tmp_path / "p"


def write_scene(root, sid, text):
    (root / "scenes" / f"{sid}.yaml").write_text(text, encoding="utf-8")


def errors(root, *ids):
    return validate(Project.load(root), list(ids) or None)


def test_unknown_vocab_ids_are_rejected(tmp_path):
    root = copy_demo(tmp_path)
    write_scene(root, "c01_s01", """
id: c01_s01
location: campus_gate
time: evening
present: [mira]
steps:
  - say: mira
    expr: ecstatic
    pose: handstand
    delivery: operatic
    text: hi
  - say: ren
    text: not here
  - cg: {id: x, shot: drone, lighting: disco, characters: [{id: mira, pose: flying, expr: smile}]}
next: c01_s02
""")
    errs = "\n".join(errors(root, "c01_s01"))
    for needle in ("expression ecstatic", "pose handstand", "delivery operatic", "ren speaks but is not present",
                   "shot drone", "lighting disco", "cg pose flying", "cg still showing"):
        assert needle in errs, needle


def test_undeclared_flag_and_bad_targets(tmp_path):
    root = copy_demo(tmp_path)
    write_scene(root, "c01_s01", """
id: c01_s01
location: campus_gate
time: evening
present: []
steps:
  - set: {ghost: true}
  - if: "flags.ghost and affection.nobody > 1"
    then: [{narr: x}]
  - choice:
      prompt: q
      options:
        - {text: a, goto: nowhere}
        - {text: b, requires: "flags.ghost"}
next: c01_s02
""")
    errs = "\n".join(errors(root, "c01_s01"))
    for needle in ("flag ghost is not declared", "affection.nobody is not a character", "goto nowhere does not exist"):
        assert needle in errs, needle


def test_linear_projects_cannot_branch(tmp_path):
    root = copy_demo(tmp_path)
    o = yaml.safe_load((root / "outline.yaml").read_text())
    o["branching"] = "linear"
    (root / "outline.yaml").write_text(yaml.safe_dump(o))
    errs = "\n".join(validate(Project.load(root)))
    assert "branches, but the project is linear" in errs
    assert "'requires' is not allowed in a linear project" in errs


def test_unreachable_scene_reported(tmp_path):
    root = copy_demo(tmp_path)
    o = yaml.safe_load((root / "outline.yaml").read_text())
    o["scenes"].append({"id": "orphan", "location": "campus_gate", "time": "morning"})
    (root / "outline.yaml").write_text(yaml.safe_dump(o))
    assert any("orphan: unreachable" in e for e in validate(Project.load(root)))


def test_minor_character_rejected(tmp_path):
    root = copy_demo(tmp_path)
    ap = root / "characters" / "mira" / "appearance.yaml"
    ap.write_text(ap.read_text().replace("age: 24", "age: 17"))
    import pytest
    with pytest.raises(Exception, match="adult"):
        Project.load(root)


def test_explicit_scene_requires_explicit_project(tmp_path):
    root = copy_demo(tmp_path)
    s = yaml.safe_load((root / "scenes" / "c02_mira_s01.yaml").read_text())
    s["rating"] = "explicit"
    (root / "scenes" / "c02_mira_s01.yaml").write_text(yaml.safe_dump(s))
    assert any("explicit scene in a suggestive project" in e for e in validate(Project.load(root)))


def test_nsfw_ref_fill_is_inlined_and_validated(tmp_path):
    root = copy_demo(tmp_path)
    prem = root / "premise.md"
    prem.write_text(prem.read_text().replace("`suggestive`", "`explicit`"))
    s = yaml.safe_load((root / "scenes" / "c02_mira_s01.yaml").read_text())
    s["rating"] = "explicit"
    s["steps"].insert(-2, {"nsfw": {"ref": "c02_mira_s01.1"}})   # before the exit
    (root / "scenes" / "c02_mira_s01.yaml").write_text(yaml.safe_dump(s, sort_keys=False))
    # missing file -> error
    assert any("has no nsfw/c02_mira_s01.1.md" in e for e in validate(Project.load(root)))
    # brief without fill -> allowed, compiles to a placeholder line
    nf = root / "nsfw" / "c02_mira_s01.1.md"
    nf.write_text("# Brief\n\nstuff\n")
    assert validate(Project.load(root)) == []
    build(Project.load(root), tmp_path / "o1")
    assert "is not filled in yet" in (tmp_path / "o1" / "game" / "scenes" / "c02_mira_s01.rpy").read_text()
    # a fill with a bad id is validated like any steps
    nf.write_text("# Brief\n\n## Fill\n\n```yaml\n- say: mira\n  expr: nope\n  text: hello\n```\n")
    assert any("expression nope" in e for e in validate(Project.load(root)))
    nf.write_text("# Brief\n\n## Fill\n\n```yaml\n- say: mira\n  expr: flirty\n  delivery: whisper\n  text: hello there\n```\n")
    assert validate(Project.load(root)) == []
    p = Project.load(root)
    build(p, tmp_path / "o2")
    rpy = (tmp_path / "o2" / "game" / "scenes" / "c02_mira_s01.rpy").read_text()
    assert 'mira "hello there"' in rpy
    assert p.lines(p.load_scene("c02_mira_s01"))[-1]["text"] == "hello there"


def test_choice_branch_voice_numbers_are_unique(tmp_path):
    root = copy_demo(tmp_path)
    p = Project.load(root)
    for sid in p.scene_ids_written():
        files = [l["file"] for l in p.lines(p.load_scene(sid))]
        assert len(files) == len(set(files))


def test_step_key_order_does_not_matter():
    from vn.schema import Say, If, parse_steps
    steps = parse_steps([{"text": "hi", "delivery": "soft", "say": "mira"}, {"then": [{"narr": "x"}], "if": "flags.a"}])
    assert isinstance(steps[0], Say) and steps[0].speaker == "mira"
    assert isinstance(steps[1], If) and steps[1].cond == "flags.a"


def test_line_length_limit_from_style(tmp_path):
    root = copy_demo(tmp_path)
    write_scene(root, "c01_s01", f"""
id: c01_s01
location: campus_gate
time: evening
present: []
steps:
  - narr: "{'x' * 181}"
next: c01_s02
""")
    assert any("181 chars, style.md allows 180" in e for e in errors(root, "c01_s01"))


def test_player_name_entry_and_tts_strip(tmp_path):
    root = copy_demo(tmp_path)
    prem = root / "premise.md"
    prem.write_text(prem.read_text().replace("Kai, 21, a transfer student.", "Name: player-entered, default Kai. A transfer student."))
    write_scene(root, "c01_s01", """
id: c01_s01
location: campus_gate
time: evening
present: [mira]
steps:
  - say: mira
    expr: smile
    text: "Hello, [player_name]. You came."
next: c01_s02
""")
    p = Project.load(root)
    assert validate(p) == []
    build(p, tmp_path / "o")
    assert 'renpy.input("What is your name?", default="Kai"' in (tmp_path / "o" / "game" / "script.rpy").read_text()
    line = p.lines(p.load_scene("c01_s01"))[0]
    assert line["has_player_name"] and line["tts_text"] == "Hello. You came."


def test_call_step_compiles_and_requires_returning_scene(tmp_path):
    root = copy_demo(tmp_path)
    o = yaml.safe_load((root / "outline.yaml").read_text())
    o["flags"]["mira_step"] = 0
    o["scenes"].append({"id": "mira_spoke", "route": "mira", "location": "mira_stall", "time": "night", "present": ["mira"]})
    (root / "outline.yaml").write_text(yaml.safe_dump(o, sort_keys=False))
    write_scene(root, "mira_spoke", """
id: mira_spoke
location: mira_stall
time: night
present: [mira]
steps:
  - say: mira
    expr: smile
    text: "Back again."
  - set: {mira_step: 1}
next: null
""")
    write_scene(root, "c01_s01", """
id: c01_s01
location: campus_gate
time: evening
present: []
steps:
  - choice:
      prompt: "Tonight."
      options:
        - text: "The stall."
          steps:
            - if: "flags.mira_step == 0"
              then: [{call: mira_spoke}]
        - text: "Home."
next: c01_s02
""")
    assert errors(root) == []
    build(Project.load(root), tmp_path / "o")
    rpy = (tmp_path / "o" / "game" / "scenes" / "c01_s01.rpy").read_text()
    assert "call scene_mira_spoke" in rpy
    # a called scene with next: is an error
    write_scene(root, "mira_spoke", (root / "scenes" / "mira_spoke.yaml").read_text().replace("next: null", "next: c01_s02"))
    assert any("must end with next: null" in e for e in errors(root))


def test_ledger_follows_outline_order_and_route():
    p = Project.load(DEMO)
    # mira's route scene sees the common scenes, not ren's
    ids = [e["scene"] for e in p.ledger_for("c02_mira_s01")]
    assert ids == ["c01_s01"]


def test_intimacy_level_vs_rating(tmp_path):
    root = copy_demo(tmp_path)
    o = yaml.safe_load((root / "outline.yaml").read_text())
    for sc in o["scenes"]:
        if sc["id"] == "c02_mira_s01":
            sc["intimacy"] = 3                      # suggestive scene cannot be level 3
        if sc["id"] == "c01_s01":
            sc["intimacy"] = 2                      # sfw scene cannot be level 2
    (root / "outline.yaml").write_text(yaml.safe_dump(o, sort_keys=False))
    errs = "\n".join(validate(Project.load(root)))
    assert "intimacy 3 needs rating explicit" in errs
    assert "intimacy 2 needs rating suggestive or explicit" in errs
    # scene file must agree with the outline
    assert "differs from the outline's 3" in errs
    p = Project.load(root)
    assert "## Intimacy level: 3" in p.context_pack("c02_mira_s01")


def test_include_step_expands(tmp_path):
    root = copy_demo(tmp_path)
    (root / "includes").mkdir()
    (root / "includes" / "tail.yaml").write_text("- narr: \"The lanterns hum.\"\n- say: mira\n  expr: smile\n  text: \"Included.\"\n")
    write_scene(root, "c01_s02", (root / "scenes" / "c01_s02.yaml").read_text().replace("next: c01_s03", "  - include: tail\nnext: c01_s03"))
    assert errors(root) == []
    p = Project.load(root)
    build(p, tmp_path / "o")
    rpy = (tmp_path / "o" / "game" / "scenes" / "c01_s02.rpy").read_text()
    assert 'mira "Included."' in rpy and "The lanterns hum." in rpy
    assert p.lines(p.load_scene("c01_s02"))[-1]["text"] == "Included."
    (root / "includes" / "tail.yaml").unlink()
    assert any("include tail has no includes/tail.yaml" in e for e in errors(root))


def test_ledger_dir_and_act_rule(tmp_path):
    root = copy_demo(tmp_path)
    (root / "ledger").mkdir()
    (root / "ledger" / "mira.yaml").write_text("- scene: c02_mira_s01\n  summary: 'jars'\n")
    (root / "ledger" / "common.yaml").write_text("- scene: c01_s02\n  summary: 'street'\n")
    p = Project.load(root)
    ids = {e["scene"] for e in p.ledger}
    assert {"c01_s01", "c01_s02", "c02_mira_s01"} <= ids
    # mira's route scene (act 2) sees act-1 common scenes, not ren's
    assert [e["scene"] for e in p.ledger_for("c02_mira_s01")] == ["c01_s01", "c01_s02"]
