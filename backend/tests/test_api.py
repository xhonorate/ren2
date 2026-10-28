"""End-to-end API tests over a real (temporary) database."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def client():
    tmp = tempfile.mkdtemp(prefix="vn_test_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def project(client):
    return client.post("/api/projects", json={"name": "Rooftop"}).json()


def add_node(client, pid, ntype, data=None, connect_from=None, handle="out", **kw):
    body = {"type": ntype, "data": data or {}, "connect_from": connect_from,
            "connect_handle": handle, **kw}
    r = client.post(f"/api/projects/{pid}/nodes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------

def test_project_lifecycle(client, project):
    pid = project["id"]
    assert project["name"] == "Rooftop"

    r = client.patch(f"/api/projects/{pid}", json={
        "world": {"setting": "A coastal high school", "era": "present day",
                  "rating": "PG-13", "pov": "second person", "tense": "present",
                  "style_notes": "Restrained. Concrete detail.", "themes": "distance"},
        "style_tags": "anime style, soft lighting",
    })
    assert r.status_code == 200
    assert r.json()["world"]["setting"] == "A coastal high school"

    # The edit persists across a fresh fetch, and a later PATCH that names only
    # one world field must merge -- not reset every other field to its default.
    assert client.get(f"/api/projects/{pid}").json()["world"]["era"] == "present day"
    client.patch(f"/api/projects/{pid}", json={"world": {"setting": "A rooftop"}})
    world = client.get(f"/api/projects/{pid}").json()["world"]
    assert world["setting"] == "A rooftop"
    assert world["era"] == "present day"
    assert world["style_notes"] == "Restrained. Concrete detail."


def test_character_wizard_enforces_the_age_floor(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/characters",
                    json={"display_name": "Underage", "age": 16})
    assert r.status_code == 422
    assert "18" in r.text

    r = client.post(f"/api/projects/{pid}/characters",
                    json={"display_name": "Aiko", "age": 22})
    assert r.status_code == 200
    aiko = r.json()
    assert aiko["id"] == "aiko"
    assert aiko["assets"]["trigger"] == "chr_aiko"

    aiko["appearance"]["hair_color"] = {"label": "platinum blonde",
                                        "tag": "platinum blonde hair"}
    aiko["appearance"]["eye_color"] = {"label": "pale grey", "tag": "grey eyes"}
    aiko["appearance"]["distinguishing"] = [{"label": "mole under left eye",
                                             "tag": "mole under eye"}]
    aiko["outfits"] = [
        {"id": "uniform", "name": "School uniform",
         "tags": "school uniform, pleated skirt, red ribbon", "negative": ""},
        {"id": "casual", "name": "Weekend", "tags": "oversized hoodie, denim shorts",
         "negative": ""},
    ]
    aiko["default_outfit"] = "uniform"
    aiko["personality"]["summary"] = "Guarded, dry, quietly loyal."
    aiko["personality"]["speech_style"] = "Short sentences. Deflects with jokes."
    r = client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)
    assert r.status_code == 200

    client.post(f"/api/projects/{pid}/characters",
                json={"display_name": "You", "age": 21, "is_player": True, "id": "pc"})


def test_image_prompt_keeps_identity_tags_off_the_llm(client, project):
    pid = project["id"]
    r = client.get(f"/api/projects/{pid}/characters/aiko/prompt-preview",
                   params={"outfit_id": "casual", "expression": "angry"})
    assert r.status_code == 200
    body = r.json()
    # Identity comes from the record, every time, unchanged (doc S4).
    assert "platinum blonde hair" in body["identity_tags"]
    assert "mole under eye" in body["identity_tags"]
    assert "oversized hoodie" in body["outfit_tags"]
    assert "angry" in body["positive"]
    # Personality prose must never reach an image prompt.
    assert "loyal" not in body["positive"].lower()
    assert body["gated"] is False


def test_locations(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/locations",
                    json={"name": "School rooftop", "tags": "rooftop, fence, city skyline"})
    assert r.status_code == 200
    assert r.json()["id"] == "school_rooftop"


def test_build_a_branching_graph(client, project):
    """A 20-node graph with branches, a merge point and a conditional."""
    pid = project["id"]
    scene = add_node(client, pid, "scene", {
        "title": "Rooftop, after class", "background": "school_rooftop",
        "time_of_day": "dusk", "transition": "dissolve",
        "brief": "Aiko is avoiding the question.",
    })
    stage = add_node(client, pid, "stage",
                     {"show": ["aiko"], "outfits": {"aiko": "uniform"},
                      "positions": {"aiko": "center"}},
                     connect_from=scene["id"])
    n1 = add_node(client, pid, "narration", {"text": "The door sticks, then gives."},
                  connect_from=stage["id"])
    n2 = add_node(client, pid, "say",
                  {"speaker": "aiko", "text": "You came.", "expression": "looking_away",
                   "emotion": "melancholy", "intensity": 0.6},
                  connect_from=n1["id"])
    setflag = add_node(client, pid, "set",
                       {"assignments": [{"var": "met_aiko", "op": "=", "value": "true"},
                                        {"var": "affinity", "op": "+=", "value": "1"}]},
                       connect_from=n2["id"])
    choice = add_node(client, pid, "choice", {
        "prompt": "What do you say?",
        "options": [
            {"id": "o1", "label": "Push her on it", "condition": "", "branch_hint": "press"},
            {"id": "o2", "label": "Let it go", "condition": "", "branch_hint": "retreat"},
        ],
    }, connect_from=setflag["id"])

    push = add_node(client, pid, "say",
                    {"speaker": "aiko", "text": "Don't.", "expression": "frowning",
                     "emotion": "irritated", "intensity": 0.7},
                    connect_from=choice["id"], handle="o1")
    push2 = add_node(client, pid, "set",
                     {"assignments": [{"var": "affinity", "op": "-=", "value": "1"}]},
                     connect_from=push["id"])
    let = add_node(client, pid, "narration", {"text": "You let the silence sit."},
                   connect_from=choice["id"], handle="o2")
    let2 = add_node(client, pid, "set",
                    {"assignments": [{"var": "affinity", "op": "+=", "value": "2"}]},
                    connect_from=let["id"])

    merge = add_node(client, pid, "narration",
                     {"text": "The wind picks up off the water."}, slug="merge")
    for src in (push2["id"], let2["id"]):
        r = client.post(f"/api/projects/{pid}/edges",
                        json={"source": src, "target": merge["id"]})
        assert r.status_code == 200

    cond = add_node(client, pid, "condition", {
        "branches": [{"id": "b1", "expr": "affinity >= 3"},
                     {"id": "b2", "expr": ""}],
        "has_else": True,
    }, connect_from=merge["id"])
    warm = add_node(client, pid, "say",
                    {"speaker": "aiko", "text": "Thanks. For not pushing.",
                     "expression": "soft_smile", "emotion": "affectionate",
                     "intensity": 0.5},
                    connect_from=cond["id"], handle="b1")
    cold = add_node(client, pid, "say",
                    {"speaker": "aiko", "text": "I should go.", "expression": "neutral",
                     "emotion": "cold", "intensity": 0.4},
                    connect_from=cond["id"], handle="b2")
    end = add_node(client, pid, "end",
                   {"mode": "ending", "ending_id": "rooftop",
                    "unlock_persistent": ["cg_rooftop_unlocked"]}, slug="fin")
    for src in (warm["id"], cold["id"]):
        client.post(f"/api/projects/{pid}/edges", json={"source": src, "target": end["id"]})

    graph = client.get(f"/api/projects/{pid}/graph").json()
    assert len(graph["nodes"]) == 15
    assert graph["scenes"][0]["title"] == "Rooftop, after class"

    # Stash ids for the later tests.
    project["ids"] = {"scene": scene["id"], "choice": choice["id"], "merge": merge["id"],
                      "say": n2["id"], "end": end["id"], "cond": cond["id"]}


def test_path_state_resolves_along_one_branch(client, project):
    pid = project["id"]
    ids = project["ids"]
    r = client.get(f"/api/projects/{pid}/nodes/{ids['merge']}/state")
    assert r.status_code == 200
    state = r.json()["state"]
    assert state["location"] == "school_rooftop"
    assert state["time_of_day"] == "dusk"
    assert state["outfits"]["aiko"] == "uniform"
    assert state["flags"]["met_aiko"] is True
    # The merge point takes the first deterministic predecessor, so affinity
    # reflects that branch alone -- not a blend of both.
    assert state["flags"]["affinity"] in (0, 3)


def test_compile_produces_runnable_renpy(client, project):
    pid = project["id"]
    out = client.get(f"/api/projects/{pid}/compile").json()
    script = "\n".join(v for k, v in out["files"].items() if "/scenes/" in k)

    assert "scene bg_school_rooftop with dissolve" in script
    assert "show aiko uniform front looking_away at center" in script
    assert 'aiko "You came."' in script
    assert "menu:" in script
    assert '"Push her on it":' in script
    assert "$ affinity += 1" in script
    assert "if affinity >= 3:" in script
    assert "else:" in script
    assert "$ persistent.cg_rooftop_unlocked = True" in script
    assert "jump ending_rooftop" in script

    chars = out["files"]["game/generated/characters.rpy"]
    assert 'voice_tag="aiko"' in chars
    assert 'define pc = Character("You"' in chars
    assert "voice_tag" not in chars.split("define pc")[1]

    flags = out["files"]["game/generated/flags.rpy"]
    assert "default met_aiko = False" in flags
    assert "default affinity = 0" in flags

    images = out["files"]["game/generated/images.rpy"]
    # Whole-image sprites, declared per outfit/pose/expression combination the
    # script shows -- not a layeredimage compositing separate outfit and face
    # layers, which looked up sprite_sheet keys nothing ever wrote.
    assert "layeredimage" not in images
    assert "image aiko uniform front looking_away" in images

    # Both branches converge on one label.
    merge_label = out["labels"][project["ids"]["merge"]]
    assert script.count(f"jump {merge_label}") == 2
    assert not out["warnings"]


def test_preview_program_compiles_from_the_same_ir(client, project):
    pid = project["id"]
    program = client.get(f"/api/projects/{pid}/preview").json()
    assert program["entry"]
    assert program["entry"] in program["blocks"]
    assert program["characters"]["aiko"]["name"] == "Aiko"
    assert program["flags"]["affinity"] == 0

    ops = program["blocks"][program["entry"]]
    kinds = [op["op"] for op in ops]
    assert kinds[0] == "scene"
    assert "show" in kinds and "say" in kinds and "menu" in kinds

    menu = next(op for op in ops if op["op"] == "menu")
    assert len(menu["options"]) == 2
    assert all(o["target"] for o in menu["options"])


def test_validation_flags_real_problems(client, project):
    pid = project["id"]
    report = client.get(f"/api/projects/{pid}/validate").json()
    assert report["errors"] == 0, report["problems"]

    dangling = add_node(client, pid, "choice", {
        "prompt": "Nowhere", "options": [{"id": "o1", "label": "A", "condition": "",
                                          "branch_hint": ""},
                                         {"id": "o2", "label": "B", "condition": "",
                                          "branch_hint": ""}],
    })
    report = client.get(f"/api/projects/{pid}/validate").json()
    assert report["errors"] >= 2
    assert any("goes nowhere" in p["message"] for p in report["problems"])
    client.delete(f"/api/projects/{pid}/nodes/{dangling['id']}")


def test_search_finds_nodes(client, project):
    pid = project["id"]
    hits = client.get(f"/api/projects/{pid}/search", params={"q": "wind picks up"}).json()
    assert len(hits) == 1
    assert hits[0]["id"] == project["ids"]["merge"]


def test_editing_text_marks_assets_stale(client, project):
    """Without this you ship a build where the voice does not match the text."""
    pid = project["id"]
    say_id = project["ids"]["say"]

    # Pretend an image and a voice line were generated for this node.
    from app import store
    from app.services import staleness
    node = store.get_node(say_id)
    node.data = {**node.data, "image_hash": "deadbeef", "voice_path": "audio/voice/x.ogg"}
    staleness.stamp(node, "image")
    staleness.stamp(node, "audio")
    staleness.evaluate(node)
    store.update_node(pid, node)
    assert node.image_status.value == "ready"
    assert node.audio_status.value == "ready"

    r = client.patch(f"/api/projects/{pid}/nodes/{say_id}",
                     json={"data": {"text": "You actually came."}})
    assert r.status_code == 200
    assert r.json()["image_status"] == "stale"
    assert r.json()["audio_status"] == "stale"

    report = client.get(f"/api/projects/{pid}/staleness").json()
    assert any(e["id"] == say_id for e in report["images"])
    assert any(e["id"] == say_id for e in report["audio"])


def test_two_pass_generation_materializes_nodes(client, project, monkeypatch):
    """Prose unconstrained and carrying its own performance notation,
    structuring schema-constrained and segmentation only, then written to the
    graph."""
    pid = project["id"]
    from app.clients import lmstudio
    from app.services import generation

    calls: list[dict] = []

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        system = messages[0]["content"]
        calls.append({"schema": json_schema is not None, "params": params or {},
                      "system": system})
        if json_schema is None:
            # The writer tags the line as it writes it; the tag comes off in
            # code before the segmenter ever sees the passage.
            return ('Aiko does not look up.\n\n'
                    'AIKO (looking_away, melancholy, 0.6, trails off): '
                    '"You always take the long way."\n'
                    'CG: sitting with her back to the fence, not looking up')
        return json.dumps({
            "scene_title": "Rooftop, later",
            "nodes": [
                {"type": "narration", "text": "Aiko does not look up."},
                {"type": "say", "speaker": "aiko",
                 "text": "You always take the long way."},
            ],
            "choice": {
                "prompt": "What do you say?",
                "options": [
                    {"label": "Sit down beside her", "branch_hint": "close the distance"},
                    {"label": "Stay standing", "branch_hint": "keep the distance"},
                ],
            },
        })

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    before = len(client.get(f"/api/projects/{pid}/graph").json()["nodes"])
    r = client.post(f"/api/projects/{pid}/generate/scene",
                    json={"anchor_id": project["ids"]["say"], "brief": "She deflects."})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    job = _await_job(client, job_id)
    assert job["status"] == "done", job.get("error")
    result = job["result"]
    assert result["node_count"] == 2

    # Prose has no schema and creative sampling; structuring has a schema and
    # runs cold. Two model calls, not three -- performance is notation the
    # writer already wrote, so nothing reads the passage back afterwards.
    assert calls[0]["schema"] is False
    assert calls[0]["params"]["temperature"] >= 0.85
    assert calls[0]["params"]["top_k"] == 0
    assert len(calls) == 2
    # structure_pass sends the template as the *user* message under a fixed
    # system line, so it is identified by that line rather than by the body.
    struct = next(c for c in calls if "segment prose into structured nodes" in c["system"])
    assert struct["schema"] is True and struct["params"]["temperature"] <= 0.3

    graph = client.get(f"/api/projects/{pid}/graph").json()
    assert len(graph["nodes"]) == before + 3     # narration + say + choice
    new_say = next(n for n in graph["nodes"]
                   if n["data"].get("text") == "You always take the long way.")
    assert new_say["data"]["emotion"] == "melancholy"
    assert new_say["data"]["expression"] == "looking_away"
    assert new_say["data"]["intensity"] == 0.6
    assert new_say["data"]["delivery"] == "trails off"
    # The `CG:` line binds backwards, onto the beat it illustrates.
    assert new_say["data"]["cg"] is True
    assert new_say["variants"], "the take should be kept as a variant"

    # The chain was spliced in ahead of what previously followed the anchor.
    # It ends in a choice, so every option converges on the old content rather
    # than stranding it.
    project["gen_id"] = result["gen_id"]
    assert result["reattached_to"]
    assert sorted(result["reattached_handles"]) == ["o1", "o2"]


def test_regenerate_from_node_parks_the_subtree(client, project, monkeypatch):
    pid = project["id"]
    graph = client.get(f"/api/projects/{pid}/graph").json()
    target = next(n for n in graph["nodes"]
                  if n["data"].get("text") == "You always take the long way.")

    from app.clients import lmstudio
    from app.services import generation

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        if json_schema is None:
            return 'AIKO: "Take the stairs next time."'
        return json.dumps({"scene_title": "Redo", "nodes": [
            {"type": "say", "speaker": "aiko", "text": "Take the stairs next time.",
             "expression": "smirking", "emotion": "amused", "intensity": 0.4,
             "delivery": "", "shot_hint": ""}]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    r = client.post(f"/api/projects/{pid}/generate/regenerate-from",
                    json={"node_id": target["id"]})
    job = _await_job(client, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    assert target["id"] in job["result"]["orphaned"]

    # Parked, not deleted -- you will want it back.
    orphans = client.get(f"/api/projects/{pid}/orphans").json()
    assert any(o["id"] == target["id"] for o in orphans)
    live = client.get(f"/api/projects/{pid}/graph").json()["nodes"]
    assert all(n["id"] != target["id"] for n in live)
    assert any(n["data"].get("text") == "Take the stairs next time." for n in live)

    client.post(f"/api/projects/{pid}/orphans/restore",
                json={"node_ids": [target["id"]]})
    assert any(n["id"] == target["id"]
               for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"])


def test_prompt_templates_are_versioned_and_diffable(client, project):
    pid = project["id"]
    original = client.get(f"/api/projects/{pid}/templates/system.prose_gen").json()
    assert original["active"]["version"] == 1

    r = client.post(f"/api/projects/{pid}/templates/system.prose_gen",
                    json={"body": original["active"]["body"] + "\n- Never use semicolons.",
                          "params": {**original["active"]["params"], "temperature": 0.97}})
    assert r.status_code == 200
    assert r.json()["template"]["version"] == 2
    assert any("Never use semicolons" in line for line in r.json()["diff"])

    versions = client.get(f"/api/projects/{pid}/templates/system.prose_gen").json()
    assert len(versions["versions"]) == 2
    assert versions["active"]["version"] == 2

    # The previous version is still there to go back to.
    v1 = next(v for v in versions["versions"] if v["version"] == 1)
    client.post(f"/api/projects/{pid}/templates/system.prose_gen/activate/{v1['id']}")
    assert client.get(
        f"/api/projects/{pid}/templates/system.prose_gen").json()["active"]["version"] == 1


def test_emotion_vector_respects_the_wrapper_constraints(client, project):
    pid = project["id"]
    r = client.get(f"/api/projects/{pid}/characters/aiko/emotion-preview",
                   params={"emotion": "crying", "intensity": 1.0})
    assert r.status_code == 200
    resolved = r.json()
    assert sum(resolved["vec"].values()) <= 1.5 + 1e-6
    assert all(0 <= v <= 1.4 for v in resolved["vec"].values())
    assert resolved["emo_alpha"] <= 0.6
    # Per-character cap keeps timbre leakage down.
    assert resolved["vec"]["sad"] <= 0.8 * 0.7 + 1e-6

    whisper = client.get(f"/api/projects/{pid}/characters/aiko/emotion-preview",
                         params={"emotion": "whisper", "intensity": 1.0}).json()
    assert whisper["speed"] < 1.0
    assert whisper["gain_db"] < 0


def test_export_writes_only_into_generated(client, project, tmp_path_factory):
    """One folder per project inside the SDK, named after the project -- the
    directory the Ren'Py launcher scans, so it appears in the launcher list."""
    pid = project["id"]
    from app import store
    from app.services import build as build_svc

    sdk = tmp_path_factory.mktemp("renpy_sdk")
    store.set_setting("renpy_sdk_path", str(sdk))
    target = build_svc.project_dir(pid)
    assert target == sdk / "rooftop", target      # project is named "Rooftop"

    r = client.post(f"/api/projects/{pid}/export", json={"copy_assets": False})
    job = _await_job(client, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")

    assert (target / "game" / "script.rpy").exists()
    assert (target / "game" / "generated" / "characters.rpy").exists()
    assert (target / "game" / "generated" / "images.rpy").exists()
    assert list((target / "game" / "generated" / "scenes").glob("*.rpy"))

    # Hand-owned files are never overwritten on a re-export.
    hand_written = target / "game" / "script.rpy"
    hand_written.write_text("# my own entry point\nlabel start:\n    return\n", encoding="utf-8")
    custom = target / "game" / "custom.rpy"
    custom.write_text("# mine\n", encoding="utf-8")

    r = client.post(f"/api/projects/{pid}/export", json={"copy_assets": False})
    _await_job(client, r.json()["job_id"])
    assert "my own entry point" in hand_written.read_text(encoding="utf-8")
    assert custom.read_text(encoding="utf-8") == "# mine\n"

    store.set_setting("renpy_sdk_path", "")


def test_jobs_are_listed_and_cancellable(client, project):
    pid = project["id"]
    jobs = client.get(f"/api/projects/{pid}/jobs").json()
    assert jobs
    assert {"id", "kind", "status", "progress"} <= set(jobs[0])
    assert client.post(f"/api/jobs/{jobs[0]['id']}/cancel").status_code == 200


def _await_job(client, job_id, timeout=30.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_generated_asset_hashes_survive_a_later_text_edit(client, project):
    """Export copies assets by hash, so an edit must not drop the hash."""
    pid = project["id"]
    say_id = project["ids"]["say"]

    client.patch(f"/api/projects/{pid}/nodes/{say_id}", json={"data": {
        "voice_path": "audio/voice/say_abc.ogg",
        "voice_hash": "a" * 64,
        "image_hash": "b" * 64,
    }})
    # A later, unrelated edit to the text must keep both hashes.
    node = client.patch(f"/api/projects/{pid}/nodes/{say_id}",
                        json={"data": {"text": "Edited again."}}).json()
    assert node["data"]["voice_hash"] == "a" * 64
    assert node["data"]["image_hash"] == "b" * 64
    assert node["data"]["voice_path"] == "audio/voice/say_abc.ogg"
    # ...and the edit still marks them stale.
    assert node["audio_status"] == "stale"
    assert node["image_status"] == "stale"
