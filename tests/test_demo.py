from pathlib import Path

import pytest

from vn.compile_renpy import build
from vn.project import Project, validate

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo"


def test_demo_validates():
    assert validate(Project.load(DEMO)) == []


def test_demo_builds(tmp_path):
    p = Project.load(DEMO)
    written = build(p, tmp_path)
    game = tmp_path / "game"
    assert len(written) == 5
    assert (game / "script.rpy").exists()
    chars = (game / "characters.rpy").read_text()
    assert 'define mira = Character("Mira", voice_tag="mira"' in chars
    assert "image bg lantern_street_night = Placeholder" in chars
    assert 'image mira stall hands_clasped smile = Placeholder("girl"' in chars
    s2 = (game / "scenes" / "c01_s02.rpy").read_text()
    assert "scene cg c01_s02_cg1 with dissolve" in s2
    assert '"Hold the ladder." if affection["ren"] >= 1:' in (game / "scenes" / "c01_s03.rpy").read_text()
    assert 'if flags["asked_about_street"]:' in s2


def test_voice_numbering_matches_lines_export(tmp_path):
    p = Project.load(DEMO)
    sc = p.load_scene("c01_s02")
    lines = p.lines(sc)
    files = [l["file"] for l in lines]
    assert len(files) == len(set(files)), "voice file numbers must be unique"
    # create the voice files the export names and check the compiler picks them up in that order
    for l in lines:
        f = tmp_path / "proj" / l["file"]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
    import shutil
    shutil.copytree(DEMO, tmp_path / "proj", dirs_exist_ok=True)
    p2 = Project.load(tmp_path / "proj")
    build(p2, tmp_path / "out")
    rpy = (tmp_path / "out" / "game" / "scenes" / "c01_s02.rpy").read_text()
    order = [ln.strip() for ln in rpy.splitlines() if ln.strip().startswith("voice ")]
    assert order == [f'voice "{f}"' for f in files]
    # the say that follows each voice statement is the exported line
    rl = rpy.splitlines()
    for i, ln in enumerate(rl):
        if ln.strip().startswith("voice "):
            n = int(ln.strip().split("/")[-1][:3])
            assert lines[n - 1]["text"].split()[0] in rl[i + 1]


def test_pack_contains_everything_the_writer_needs():
    p = Project.load(DEMO)
    pack = p.context_pack("c01_s02")
    for needle in ("## Style", "### Mira (`mira`)", "### Ren (`ren`)", "Sample lines", "## Continuity ledger",
                   "why can only Kai see the lit street", "expressions:", "flags declared: asked_about_street",
                   "Marks: small scar on through the left eyebrow"):
        assert needle in pack, needle
    # route arc section is selected by route
    assert "Act 1: Kai finds the street" in pack
    assert "Lock at `c01_s03`" in p.context_pack("c02_mira_s01")


def test_cg_export():
    p = Project.load(DEMO)
    cgs = p.cgs(p.load_scene("c01_s02"))
    assert cgs[0]["id"] == "c01_s02_cg1" and cgs[0]["characters"][0]["pose"] == "standing_close"
    assert cgs[0]["location"] == "mira_stall"  # stage change before the cg is tracked
