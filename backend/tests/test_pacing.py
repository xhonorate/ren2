"""Route and pacing metrics.

The properties that matter are the ones you would balance a story on: a scene's
size is every word in it including the branch not taken, a route is exactly the
scenes claiming it, and renaming a route cannot leave a hub pointing at one
nobody belongs to.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def client():
    tmp = tempfile.mkdtemp(prefix="vn_pacing_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]


def add(client, pid, ntype, data=None, connect_from=None, handle="out", **kw):
    r = client.post(f"/api/projects/{pid}/nodes", json={
        "type": ntype, "data": data or {}, "connect_from": connect_from,
        "connect_handle": handle, **kw})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def project(client):
    """Two routes: Aiko gets one scene with a branch, Diana gets two short ones."""
    pid = client.post("/api/projects", json={"name": "Pacing"}).json()["id"]
    for name in ("Aiko", "Diana"):
        client.post(f"/api/projects/{pid}/characters",
                    json={"display_name": name, "age": 22, "id": name.lower()})

    ids = {}

    # -- Aiko's route: a choice whose two branches both carry words ----------
    aiko_scene = add(client, pid, "scene", {"title": "Rooftop", "route": "aiko"}, y=100)
    ids["aiko_scene"] = aiko_scene["id"]
    line = add(client, pid, "say",
               {"speaker": "aiko", "text": "One two three four five."},
               connect_from=aiko_scene["id"], parent_id=aiko_scene["id"])
    choice = add(client, pid, "choice", {
        "prompt": "What do you say?",
        "options": [{"id": "o1", "label": "Push"}, {"id": "o2", "label": "Wait"}],
    }, connect_from=line["id"], parent_id=aiko_scene["id"])
    add(client, pid, "say", {"speaker": "aiko", "text": "Taken branch words here."},
        connect_from=choice["id"], handle="o1", parent_id=aiko_scene["id"])
    add(client, pid, "narration", {"text": "The branch nobody walked."},
        connect_from=choice["id"], handle="o2", parent_id=aiko_scene["id"])

    # -- Diana's route: two scenes, one line each ---------------------------
    for n in (1, 2):
        scene = add(client, pid, "scene", {"title": f"Diana {n}", "route": "diana"},
                    y=200 + n * 100)
        ids[f"diana_{n}"] = scene["id"]
        add(client, pid, "say", {"speaker": "diana", "text": "Short."},
            connect_from=scene["id"], parent_id=scene["id"])

    # -- and a common trunk claiming no route -------------------------------
    trunk = add(client, pid, "scene", {"title": "Prologue"}, y=0)
    ids["trunk"] = trunk["id"]
    add(client, pid, "narration", {"text": "Before anything forked."},
        connect_from=trunk["id"], parent_id=trunk["id"])

    return {"id": pid, "ids": ids}


def routes_by_name(report):
    return {r["name"]: r for r in report["routes"]}


# --------------------------------------------------------------------------

def test_a_scene_is_measured_across_every_branch_not_one_path(client, project):
    """The branch nobody walks still had to be written, and still gets voiced."""
    report = client.get(f"/api/projects/{project['id']}/pacing").json()
    rooftop = next(s for s in routes_by_name(report)["aiko"]["scenes_list"]
                   if s["title"] == "Rooftop")

    # 5 + 4 + 4 words: the opening line and *both* sides of the choice.
    assert rooftop["words"] == 13
    assert rooftop["lines"] == 3
    assert rooftop["say_lines"] == 2
    assert rooftop["narration_lines"] == 1
    assert rooftop["choices"] == 1
    assert rooftop["options"] == 2
    assert rooftop["depth"] == 1


def test_scenes_group_under_the_route_they_claim(client, project):
    report = client.get(f"/api/projects/{project['id']}/pacing").json()
    routes = routes_by_name(report)

    assert set(routes) == {"aiko", "diana", ""}
    assert routes["diana"]["scenes"] == 2
    # Down the canvas -- the same order `renpy._routes` emits, so the list you
    # read here is the order a hub actually dispatches them in.
    assert [s["title"] for s in routes["diana"]["scenes_list"]] == ["Diana 1", "Diana 2"]
    # A route needs no registry -- it exists because scenes claim it, so the
    # unrouted trunk is a bucket of its own rather than being dropped.
    assert routes[""]["scenes"] == 1
    assert routes[""]["scenes_list"][0]["title"] == "Prologue"
    # The unnamed bucket sorts last, after every named route.
    assert report["routes"][-1]["name"] == ""


def test_the_imbalance_the_dashboard_exists_to_show(client, project):
    """Aiko's route is longer than Diana's, and the report says so in words."""
    report = client.get(f"/api/projects/{project['id']}/pacing").json()
    routes = routes_by_name(report)
    assert routes["aiko"]["words"] > routes["diana"]["words"]
    assert routes["aiko"]["seconds"] == round(
        routes["aiko"]["words"] / report["words_per_minute"] * 60)
    assert report["totals"]["words"] == sum(r["words"] for r in report["routes"])
    assert report["totals"]["scenes"] == 4


def test_choice_pacing_reads_as_lines_between_choices(client, project):
    report = client.get(f"/api/projects/{project['id']}/pacing").json()
    routes = routes_by_name(report)
    # Three lines, one choice.
    assert routes["aiko"]["lines_per_choice"] == 3.0
    # A route with no choice at all has no ratio rather than a zero, which
    # would sort as the tightest pacing in the story.
    assert routes["diana"]["lines_per_choice"] is None


def test_voice_coverage_counts_only_lines_that_would_ever_be_queued(client, project):
    """Same rule as the tts handler, so the percentage cannot disagree with it."""
    from app import store
    pid = project["id"]

    report = client.get(f"/api/projects/{pid}/pacing").json()
    assert report["totals"]["voiceable"] == 4      # every say node, both casts
    assert report["totals"]["voiced_pct"] == 0

    # Diana stops being voiced: her two lines leave the denominator entirely
    # rather than sitting in it as permanent missing work.
    diana = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                 if c["id"] == "diana")
    diana["voice"]["voiced"] = False
    client.put(f"/api/projects/{pid}/characters/diana", json=diana)
    assert client.get(f"/api/projects/{pid}/pacing").json()["totals"]["voiceable"] == 2

    # And a rendered line lands in the numerator.
    say = next(n for n in store.list_nodes(pid)
               if (n.data or {}).get("text", "").startswith("One two"))
    say.data = {**say.data, "voice_path": "audio/voice/x.ogg"}
    store.update_node(pid, say)
    assert client.get(f"/api/projects/{pid}/pacing").json()["totals"]["voiced_pct"] == 50


def test_the_cast_of_a_route_is_ranked_by_how_much_they_say(client, project):
    report = client.get(f"/api/projects/{project['id']}/pacing").json()
    cast = routes_by_name(report)["aiko"]["cast"]
    assert [c["id"] for c in cast] == ["aiko"]
    assert cast[0]["lines"] == 2
    assert cast[0]["share_pct"] == 100

    people = {c["id"]: c for c in report["characters"]}
    assert people["aiko"]["routes"] == ["aiko"]
    assert people["diana"]["routes"] == ["diana"]
    assert people["aiko"]["words"] > people["diana"]["words"]


def test_renaming_a_route_carries_the_hubs_that_dispatch_over_it(client, project):
    """Renaming only the scenes turns every hub option into a validation error."""
    pid = project["id"]
    hub = add(client, pid, "hub", {
        "prompt": "Where to?",
        "options": [{"id": "o1", "label": "See Diana", "target_route": "diana",
                     "requires": [], "effects": []}],
    })

    assert client.get(f"/api/projects/{pid}/validate").json()["errors"] == 0

    r = client.post(f"/api/projects/{pid}/routes/rename",
                    json={"old": "diana", "new": "diana_true"})
    assert r.status_code == 200
    assert r.json()["updated"] == 3          # two scenes and the hub

    report = client.get(f"/api/projects/{pid}/pacing").json()
    assert "diana_true" in routes_by_name(report)
    assert "diana" not in routes_by_name(report)
    # The hub came with it, so nothing now targets a route nobody belongs to.
    assert client.get(f"/api/projects/{pid}/validate").json()["errors"] == 0
    options = client.get(f"/api/projects/{pid}/graph").json()["nodes"]
    moved = next(n for n in options if n["id"] == hub["id"])
    assert moved["data"]["options"][0]["target_route"] == "diana_true"


def test_a_scene_can_be_created_straight_into_a_route(client, project):
    """The tagging is the whole point -- a scene added by hand and left untagged
    is invisible to every hub that dispatches over the route."""
    pid = project["id"]
    scene = add(client, pid, "scene", {"title": "Diana 3", "route": "diana"}, y=999)
    assert scene["data"]["route"] == "diana"

    report = client.get(f"/api/projects/{pid}/pacing").json()
    titles = [s["title"] for s in routes_by_name(report)["diana"]["scenes_list"]]
    assert titles == ["Diana 1", "Diana 2", "Diana 3"]


def test_depth_survives_a_branch_that_loops_back(client, project):
    """A loop is not infinite depth; it contributes nothing on the way round."""
    pid = project["id"]
    scene = add(client, pid, "scene", {"title": "Loop", "route": "aiko"}, y=999)
    choice = add(client, pid, "choice", {
        "prompt": "Again?",
        "options": [{"id": "o1", "label": "Yes"}, {"id": "o2", "label": "No"}],
    }, connect_from=scene["id"], parent_id=scene["id"])
    back = add(client, pid, "say", {"speaker": "aiko", "text": "Round again."},
               connect_from=choice["id"], handle="o1", parent_id=scene["id"])
    client.post(f"/api/projects/{pid}/edges",
                json={"source": back["id"], "target": choice["id"], "source_handle": "out"})

    report = client.get(f"/api/projects/{pid}/pacing").json()
    loop = next(s for s in routes_by_name(report)["aiko"]["scenes_list"]
                if s["title"] == "Loop")
    assert loop["depth"] == 1
