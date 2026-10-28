"""Recasting a voice invalidates every line already spoken in the old one.

The text hash cannot see this: swapping a character's reference clip changes no
node, so a line whose words are untouched keeps matching its own hash while the
timbre it was rendered in is one the game no longer uses. Builds its own
project so it stays independent of the other suites.
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
    tmp = tempfile.mkdtemp(prefix="vn_recast_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]


@pytest.fixture()
def project(client):
    """Two voiced characters, one line each, both already rendered."""
    pid = client.post("/api/projects", json={"name": "Recast"}).json()["id"]
    for name in ("Aiko", "Diana"):
        client.post(f"/api/projects/{pid}/characters",
                    json={"display_name": name, "age": 22, "id": name.lower()})

    scene = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "scene", "data": {"title": "Rooftop"}}).json()
    ids = {}
    previous = scene["id"]
    for who in ("aiko", "diana"):
        node = client.post(f"/api/projects/{pid}/nodes", json={
            "type": "say", "data": {"speaker": who, "text": f"{who} says a line."},
            "connect_from": previous, "connect_handle": "out"}).json()
        ids[who] = node["id"]
        previous = node["id"]

    for who in ("aiko", "diana"):
        _upload_clip(client, pid, who, f"first-{who}".encode())
    for who, node_id in ids.items():
        _pretend_voiced(pid, node_id, who)
    return {"id": pid, "ids": ids}


def _audio_status(client, pid, node_id) -> str:
    """What the canvas badge would show -- read the way the canvas reads it."""
    graph = client.get(f"/api/projects/{pid}/graph").json()
    return next(n for n in graph["nodes"] if n["id"] == node_id)["audio_status"]


def _upload_clip(client, pid, char_id, payload: bytes) -> str:
    """Freeze a reference clip; returns the digest now stamped on new takes."""
    r = client.post(f"/api/projects/{pid}/characters/{char_id}/voice-clip",
                    files={"file": ("clip.wav", payload, "audio/wav")})
    assert r.status_code == 200, r.text
    return r.json()["hash"]


def _pretend_voiced(pid, node_id, char_id):
    """Stand in for a tts.line job: record a file and stamp what made it."""
    from app import store
    from app.services import staleness
    node = store.get_node(node_id)
    node.data = {**node.data, "voice_path": f"audio/voice/{node_id}.ogg"}
    character = store.get_character(pid, char_id)
    staleness.stamp(node, "audio", voice=staleness.voice_id(character))
    staleness.evaluate(node, staleness.voice_refs(pid))
    store.update_node(pid, node)
    assert node.audio_status.value == "ready"


# --------------------------------------------------------------------------

def test_swapping_the_reference_clip_marks_that_character_stale(client, project):
    """The line's words never changed, and it is stale anyway."""
    pid, ids = project["id"], project["ids"]

    r = client.post(f"/api/projects/{pid}/characters/aiko/voice-clip",
                    files={"file": ("clip.wav", b"second-aiko", "audio/wav")})
    assert r.status_code == 200
    # The route reports the damage it just did, rather than leaving the count
    # to be discovered on the Build view.
    assert r.json()["lines_stale"] == 1

    assert _audio_status(client, pid, ids["aiko"]) == "stale"
    # Diana was not recast, so her line is untouched -- staleness is per
    # character, not a blanket sweep whenever any voice is touched.
    assert _audio_status(client, pid, ids["diana"]) == "ready"


def test_the_staleness_report_says_which_of_the_two_causes_it_was(client, project):
    """Re-voice one line after an edit; re-voice a whole character after a recast."""
    pid, ids = project["id"], project["ids"]

    _upload_clip(client, pid, "aiko", b"second-aiko")
    client.patch(f"/api/projects/{pid}/nodes/{ids['diana']}",
                 json={"data": {"text": "Diana says something else."}})

    report = client.get(f"/api/projects/{pid}/staleness").json()
    reasons = {e["id"]: e.get("reason") for e in report["audio"]}
    assert reasons[ids["aiko"]] == "voice clip changed"
    assert reasons[ids["diana"]] == "text changed"


def test_re_voicing_against_the_new_clip_clears_it(client, project):
    pid, ids = project["id"], project["ids"]
    _upload_clip(client, pid, "aiko", b"second-aiko")
    assert _audio_status(client, pid, ids["aiko"]) == "stale"

    _pretend_voiced(pid, ids["aiko"], "aiko")
    assert _audio_status(client, pid, ids["aiko"]) == "ready"


def test_batch_scan_and_voice_all_both_pick_a_recast_up(client, project):
    """The two buttons that fill missing audio have to agree it is missing."""
    pid, ids = project["id"], project["ids"]
    _upload_clip(client, pid, "aiko", b"second-aiko")

    scan = client.get(f"/api/projects/{pid}/batch/missing").json()
    recast = [v for v in scan["voice"] if v["node_id"] == ids["aiko"]]
    assert recast and recast[0]["reason"] == "stale"

    queued = client.post(f"/api/projects/{pid}/tts/all?only_missing=true").json()
    assert queued["count"] == 1


def test_a_line_generated_before_this_tracking_is_backfilled_not_condemned(client, project):
    """Every existing project would otherwise read as entirely stale on upgrade."""
    from app import store
    from app.services import staleness
    pid, ids = project["id"], project["ids"]

    # An old line: voiced, text hash stamped, no record of the clip.
    node = store.get_node(ids["aiko"])
    node.data = {k: v for k, v in node.data.items() if k != "_audio_voice_id"}
    store.update_node(pid, node)

    assert staleness.migrate_voice_stamps() == 1
    staleness.refresh_project(pid)
    assert store.get_node(ids["aiko"]).audio_status.value == "ready"

    # ...and the clip it was assumed to have come from is now on record, so the
    # next recast catches it like any other line.
    _upload_clip(client, pid, "aiko", b"second-aiko")
    assert store.get_node(ids["aiko"]).audio_status.value == "stale"


def test_tuning_the_voice_without_changing_the_clip_leaves_lines_alone(client, project):
    """emotion_scale and speed move the output, but they are nudged constantly
    while casting -- flagging the whole game on each nudge is noise, not signal."""
    pid, ids = project["id"], project["ids"]
    aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                if c["id"] == "aiko")
    aiko["voice"]["emotion_scale"] = 0.4
    aiko["voice"]["speed"] = 1.2
    assert client.put(f"/api/projects/{pid}/characters/aiko", json=aiko).status_code == 200

    assert _audio_status(client, pid, ids["aiko"]) == "ready"
