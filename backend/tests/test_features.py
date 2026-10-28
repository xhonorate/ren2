"""API tests for the script view, batch fill, gallery and playtest-from-node.

Shares the module fixtures' shape with test_api.py but builds its own project,
so the two files stay independent.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def client():
    tmp = tempfile.mkdtemp(prefix="vn_feat_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def project(client):
    """A small branching scene with a character and a location."""
    pid = client.post("/api/projects", json={"name": "Features"}).json()["id"]

    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})
    aiko = client.get(f"/api/projects/{pid}/characters").json()[0]
    aiko["outfits"] = [{"id": "uniform", "name": "Uniform", "tags": "uniform", "negative": ""}]
    aiko["default_outfit"] = "uniform"
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)
    client.post(f"/api/projects/{pid}/characters",
                json={"display_name": "You", "age": 21, "is_player": True, "id": "pc"})
    client.post(f"/api/projects/{pid}/locations",
                json={"name": "School rooftop", "tags": "rooftop"})

    def add(ntype, data, connect_from=None, handle="out"):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": connect_from,
            "connect_handle": handle}).json()

    scene = add("scene", {"title": "Rooftop", "background": "school_rooftop",
                          "time_of_day": "dusk"})
    say = add("say", {"speaker": "aiko", "text": "You came.",
                      "expression": "looking_away", "emotion": "melancholy",
                      "intensity": 0.6}, scene["id"])
    narr = add("narration", {"text": "The door sticks."}, say["id"])
    choice = add("choice", {"prompt": "What do you say?", "options": [
        {"id": "o1", "label": "Push", "condition": "", "branch_hint": "press"},
        {"id": "o2", "label": "Wait", "condition": "", "branch_hint": "retreat"},
    ]}, narr["id"])
    push = add("say", {"speaker": "aiko", "text": "Don't.", "expression": "frowning",
                       "emotion": "irritated", "intensity": 0.7}, choice["id"], "o1")
    wait = add("narration", {"text": "You wait."}, choice["id"], "o2")
    merge = add("narration", {"text": "Wind."})
    for src in (push["id"], wait["id"]):
        client.post(f"/api/projects/{pid}/edges", json={"source": src, "target": merge["id"]})

    return {"id": pid, "scene": scene["id"], "say": say["id"], "choice": choice["id"],
            "merge": merge["id"]}


def _await_job(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def _node(client, pid, node_id):
    """No single-node GET exists, so read it back out of the graph."""
    graph = client.get(f"/api/projects/{pid}/graph").json()
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def _rows(client, pid, choices=None):
    return client.post(f"/api/projects/{pid}/script",
                       json={"choices": choices or {}}).json()["rows"]


# --------------------------------------------------------------------------
# script view
# --------------------------------------------------------------------------

def test_script_view_linearizes_the_scene(client, project):
    pid = project["id"]
    out = client.post(f"/api/projects/{pid}/script", json={}).json()
    assert out["rows"][0]["kind"] == "scene"
    assert [r["kind"] for r in out["rows"]][:4] == ["scene", "say", "narration", "choice"]
    assert all(r["node_id"] for r in out["rows"])
    assert out["scenes"][0]["title"] == "Rooftop"

    say = out["rows"][1]
    assert say["speaker_name"] == "Aiko"
    assert say["expression"] == "looking_away"
    assert say["intensity"] == 0.6


def test_script_view_follows_a_chosen_branch(client, project):
    pid = project["id"]
    default_path = _rows(client, pid)
    assert any(r["text"] == "Don't." for r in default_path)

    other = _rows(client, pid, {project["choice"]: "o2"})
    assert any(r["text"] == "You wait." for r in other)
    assert not any(r["text"] == "Don't." for r in other)
    # Both paths rejoin, and the merge is flagged as such.
    merge = next(r for r in other if r["node_id"] == project["merge"])
    assert merge["is_merge"] is True


def test_editing_a_line_patches_in_place_and_never_churns_labels(client, project):
    pid = project["id"]
    labels_before = client.get(f"/api/projects/{pid}/compile").json()["labels"]

    client.patch(f"/api/projects/{pid}/nodes/{project['say']}",
                 json={"data": {"text": "You actually came."}})

    rows = _rows(client, pid)
    assert any(r["text"] == "You actually came." for r in rows)
    labels_after = client.get(f"/api/projects/{pid}/compile").json()["labels"]
    assert labels_after == labels_before, "editing text must not move any label"


def test_insert_after_splices_without_stranding_the_rest(client, project):
    pid = project["id"]
    before = [r["node_id"] for r in _rows(client, pid)]
    anchor, following = before[1], before[2]

    out = client.post(f"/api/projects/{pid}/nodes/insert-after", json={
        "anchor_id": anchor, "type": "narration",
        "data": {"text": "Inserted line."}}).json()
    assert out["reattached_to"] == following

    after = [r["node_id"] for r in _rows(client, pid)]
    assert after[1] == anchor
    assert after[2] == out["node"]["id"]
    assert after[3] == following


def test_deleting_a_line_heals_the_path(client, project):
    pid = project["id"]
    rows = _rows(client, pid)
    victim = next(r for r in rows if r["text"] == "Inserted line.")
    before = [r["node_id"] for r in rows]

    result = client.delete(f"/api/projects/{pid}/nodes/{victim['node_id']}?heal=true").json()
    assert result["healed"]

    after = [r["node_id"] for r in _rows(client, pid)]
    assert after == [n for n in before if n != victim["node_id"]]


def test_deleting_without_heal_leaves_the_hole(client, project):
    pid = project["id"]
    out = client.post(f"/api/projects/{pid}/nodes/insert-after", json={
        "anchor_id": project["say"], "type": "narration",
        "data": {"text": "Temporary."}}).json()
    client.delete(f"/api/projects/{pid}/nodes/{out['node']['id']}")
    rows = _rows(client, pid)
    # The path now stops at the anchor rather than silently rejoining.
    assert not any(r["text"] == "The door sticks." for r in rows)
    # Put it back for the remaining tests.
    client.post(f"/api/projects/{pid}/edges", json={
        "source": project["say"], "target": _find_narration(client, pid)})


def _find_narration(client, pid):
    nodes = client.get(f"/api/projects/{pid}/graph").json()["nodes"]
    return next(n["id"] for n in nodes if n["data"].get("text") == "The door sticks.")


def test_script_import_creates_nodes_and_keeps_existing_ids(client, project):
    pid = project["id"]
    rows = _rows(client, pid)
    ids_before = {r["node_id"] for r in rows}
    labels_before = client.get(f"/api/projects/{pid}/compile").json()["labels"]

    out = client.post(f"/api/projects/{pid}/script/import", json={
        "anchor_id": project["say"],
        "text": "AIKO (smirking, amused 0.4): Took you long enough.\n\n"
                "She finally turns around.\n",
    }).json()
    assert out["count"] == 2

    rows = _rows(client, pid)
    texts = [r["text"] for r in rows]
    assert "Took you long enough." in texts
    assert "She finally turns around." in texts

    imported = next(r for r in rows if r["text"] == "Took you long enough.")
    assert imported["emotion"] == "amused"
    assert imported["expression"] == "smirking"

    # Nothing existing was re-created, so no label moved.
    assert ids_before <= {r["node_id"] for r in rows}
    labels_after = client.get(f"/api/projects/{pid}/compile").json()["labels"]
    for node_id, label in labels_before.items():
        assert labels_after.get(node_id) == label, f"label churned for {node_id}"


def test_script_parse_is_a_dry_run(client, project):
    pid = project["id"]
    before = len(client.get(f"/api/projects/{pid}/graph").json()["nodes"])
    out = client.post(f"/api/projects/{pid}/script/parse",
                      json={"text": "AIKO: hello\n\nsome narration\n"}).json()
    assert out["count"] == 2
    assert len(client.get(f"/api/projects/{pid}/graph").json()["nodes"]) == before


def test_script_export_round_trips_back_through_the_parser(client, project):
    pid = project["id"]
    text = client.post(f"/api/projects/{pid}/script/export", json={}).json()["text"]
    assert "AIKO" in text
    parsed = client.post(f"/api/projects/{pid}/script/parse", json={"text": text}).json()
    assert parsed["count"] > 0
    assert not [w for w in parsed["warnings"] if "not a known character" in w]


# --------------------------------------------------------------------------
# playtest from a node
# --------------------------------------------------------------------------

def test_preview_can_start_from_any_node(client, project):
    pid = project["id"]
    program = client.get(f"/api/projects/{pid}/preview?from_node={project['say']}").json()
    assert program["entry"] in program["blocks"]
    first_say = next(op for op in program["blocks"][program["entry"]] if op["op"] == "say")
    assert first_say["node"] == project["say"]


def test_preview_from_a_node_restores_the_stage_first(client, project):
    """Starting mid-scene must not play against a blank backdrop.

    The background and everyone on screen were established upstream, so the
    synthetic entry block replays them from resolved path state before the
    first line.
    """
    pid = project["id"]
    program = client.get(f"/api/projects/{pid}/preview?from_node={project['merge']}").json()
    ops = program["blocks"][program["entry"]]

    assert ops[0]["op"] == "scene"
    assert ops[0]["restored"] is True
    assert ops[0]["background"] == "school_rooftop"
    assert ops[0]["time_of_day"] == "dusk"
    # Aiko was on screen before this point, so she is put back.
    assert any(op["op"] == "show" and op["character"] == "aiko" for op in ops)

    # After the restoration, it plays the same content as the node's own label.
    labels = client.get(f"/api/projects/{pid}/compile").json()["labels"]
    own_block = program["blocks"][labels[project["merge"]]]
    tail = ops[len(ops) - len(own_block):]
    assert [o["op"] for o in tail] == [o["op"] for o in own_block]


def test_preview_without_from_node_still_starts_at_the_scene(client, project):
    pid = project["id"]
    program = client.get(f"/api/projects/{pid}/preview").json()
    labels = client.get(f"/api/projects/{pid}/compile").json()["labels"]
    assert program["entry"] == labels[project["scene"]]


# --------------------------------------------------------------------------
# scene collapse support
# --------------------------------------------------------------------------

def test_graph_reports_scene_membership(client, project):
    pid = project["id"]
    graph = client.get(f"/api/projects/{pid}/graph").json()
    assert graph["scene_of"][project["scene"]] == project["scene"]
    assert graph["scene_of"][project["say"]] == project["scene"]
    members = [n for n, s in graph["scene_of"].items() if s == project["scene"]]
    assert len(members) >= 6


# --------------------------------------------------------------------------
# batch fill
# --------------------------------------------------------------------------

def test_missing_report_is_scoped_to_what_the_script_uses(client, project):
    pid = project["id"]
    report = client.get(f"/api/projects/{pid}/batch/missing").json()

    slots = {s["slot"] for s in report["sprites"]}
    assert slots, "sprites in use should be reported as missing"
    assert all(s["character_id"] == "aiko" for s in report["sprites"])
    assert "uniform/front/looking_away" in slots
    # Never the full cross product: only expressions the script actually uses.
    assert "uniform/crying" not in slots

    assert {b["variant"] for b in report["backgrounds"]} == {"dusk"}
    assert all(b["location_id"] == "school_rooftop" for b in report["backgrounds"])

    # Aiko has no reference clip, so voice work is blocked, not queued to fail.
    assert report["counts"]["voice"] == 0
    assert any("reference clip" in b for b in report["blocked"])

    counts = report["counts"]
    assert counts["total"] == (counts["voice"] + counts["cgs"]
                               + counts["backgrounds"] + counts["sprites"])
    assert report["estimate_seconds"] == counts["voice"] * 5 + (
        counts["cgs"] + counts["backgrounds"] + counts["sprites"]) * 15


def test_missing_report_includes_flagged_cgs(client, project):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}", json={"data": {"cg": True}})
    report = client.get(f"/api/projects/{pid}/batch/missing").json()
    assert any(c["node_id"] == project["say"] for c in report["cgs"])
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}", json={"data": {"cg": False}})


def test_fill_missing_queues_only_the_requested_kinds(client, project):
    pid = project["id"]
    report = client.get(f"/api/projects/{pid}/batch/missing").json()

    r = client.post(f"/api/projects/{pid}/batch/fill-missing",
                    json={"kinds": ["backgrounds", "sprites"]})
    job = _await_job(client, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")

    expected = report["counts"]["backgrounds"] + report["counts"]["sprites"]
    assert job["result"]["queued"] == expected
    kinds = {client.get(f"/api/jobs/{jid}").json()["kind"]
             for jid in job["result"]["job_ids"]}
    assert kinds <= {"image.background", "image.sprite"}

    for jid in job["result"]["job_ids"]:
        client.post(f"/api/jobs/{jid}/cancel")


def test_fill_missing_respects_a_limit(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/batch/fill-missing",
                    json={"kinds": ["sprites"], "limit": 1})
    job = _await_job(client, r.json()["job_id"])
    assert job["result"]["queued"] == 1
    assert job["result"]["truncated_by_limit"] is True
    for jid in job["result"]["job_ids"]:
        client.post(f"/api/jobs/{jid}/cancel")


def test_fill_missing_asks_a_batched_cg_to_distill_its_own_prompt(client, project):
    """An overnight batch that rendered every CG from the segmenter's rough
    shot hint is the problem the director exists to fix. One job rather than
    two, so `limit` can't split the pair."""
    pid = project["id"]
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}", json={"data": {"cg": True}})
    try:
        r = client.post(f"/api/projects/{pid}/batch/fill-missing", json={"kinds": ["cgs"]})
        job = _await_job(client, r.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        queued = [client.get(f"/api/jobs/{jid}").json() for jid in job["result"]["job_ids"]]
        assert queued and all(j["kind"] == "image.generate" for j in queued)
        assert all(j["payload"]["distill_if_missing"] for j in queued)
        for j in queued:
            client.post(f"/api/jobs/{j['id']}/cancel")
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{project['say']}",
                     json={"data": {"cg": False}})


def test_missing_report_flags_cgs_with_nothing_to_shoot_from(client, project):
    """A pose of "front" is not direction: POSE_CG_TAGS maps it to the empty
    string, so a node carrying only the default is as undirected as one with
    no pose at all."""
    pid = project["id"]
    say = project["say"]
    client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {"cg": True}})
    try:
        report = client.get(f"/api/projects/{pid}/batch/missing").json()
        item = next(c for c in report["cgs"] if c["node_id"] == say)
        assert item["needs_direction"] is True
        assert [c["node_id"] for c in report["cg_prompts"]] == [say]
        assert report["counts"]["cg_prompts"] == 1
        # A subset of `cgs`, so it must not inflate the "N missing" headline.
        assert report["counts"]["total"] == (
            report["counts"]["voice"] + report["counts"]["cgs"]
            + report["counts"]["backgrounds"] + report["counts"]["sprites"])

        # Anything authored -- a shot hint, or the director's own tags -- takes
        # the node back out of the list.
        for field in ("shot_hint", "cg_camera", "cg_action"):
            client.patch(f"/api/projects/{pid}/nodes/{say}",
                         json={"data": {field: "close-up"}})
            report = client.get(f"/api/projects/{pid}/batch/missing").json()
            assert report["cg_prompts"] == [], field
            assert next(c for c in report["cgs"]
                        if c["node_id"] == say)["needs_direction"] is False
            client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {field: ""}})
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {"cg": False}})


def test_fill_missing_directs_undirected_cgs_before_it_renders_them(client, project):
    """The director jobs have to be queued ahead of the renders that read
    them, and have to be safe to run in either order -- the worker takes four
    jobs at a time, so queue position is a strong hint, not a guarantee."""
    pid = project["id"]
    say = project["say"]
    client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {"cg": True}})
    try:
        r = client.post(f"/api/projects/{pid}/batch/fill-missing",
                        json={"kinds": ["cg_prompts", "cgs"]})
        job = _await_job(client, r.json()["job_id"])
        assert job["status"] == "done", job.get("error")

        queued = [client.get(f"/api/jobs/{jid}").json() for jid in job["result"]["job_ids"]]
        kinds = [j["kind"] for j in queued]
        assert kinds.index("image.cg_prompt") < kinds.index("image.generate")

        director = next(j for j in queued if j["kind"] == "image.cg_prompt")
        assert director["node_id"] == say
        assert director["payload"]["skip_if_set"] is True
        # The render keeps its own fallback: neither end assumes it won first.
        assert all(j["payload"]["distill_if_missing"]
                   for j in queued if j["kind"] == "image.generate")

        for j in queued:
            client.post(f"/api/jobs/{j['id']}/cancel")
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {"cg": False}})


def test_fill_missing_queues_no_direction_for_a_cg_that_has_a_shot(client, project):
    pid = project["id"]
    say = project["say"]
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg": True, "cg_camera": "close-up, from below"}})
    try:
        r = client.post(f"/api/projects/{pid}/batch/fill-missing",
                        json={"kinds": ["cg_prompts"]})
        job = _await_job(client, r.json()["job_id"])
        assert job["result"]["queued"] == 0
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"cg": False, "cg_camera": ""}})


def test_a_directed_node_short_circuits_its_own_director_job(client, project):
    """`skip_if_set` is what makes the batch pair safe in either order: if the
    render distilled the node first, the director job must not spend a second
    LLM call overwriting it."""
    pid = project["id"]
    say = project["say"]
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg_camera": "wide shot", "cg_action": "turning away"}})
    try:
        r = client.post(f"/api/projects/{pid}/images/cg/prompt",
                        json={"node_id": say, "skip_if_set": True})
        job = _await_job(client, r.json()["job_id"])
        # No LM Studio in the test environment: reaching the director at all
        # would error, so 'done' is itself the assertion that it was skipped.
        assert job["status"] == "done", job.get("error")
        assert job["result"]["skipped"]
        assert job["result"]["cg_camera"] == "wide shot"
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"cg_camera": "", "cg_action": ""}})


def test_cg_prompt_preview_shows_what_would_be_rendered(client, project):
    """Assembled by the same code the render job uses -- a preview built by a
    parallel path drifts and is then worse than none."""
    pid = project["id"]
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}",
                 json={"data": {"cg_camera": "close-up, from below",
                                "cg_action": "leaning forward, eye contact",
                                "cg_negative": "smile"}})

    r = client.get(f"/api/projects/{pid}/images/cg/preview?node_id={project['say']}")
    assert r.status_code == 200
    body = r.json()

    # Read through the emphasis syntax: weights are assigned by category at
    # assembly time (images.weigh), and what this test is about is which slot
    # a tag lands in, not what it is currently weighted at.
    bare = re.sub(r"\(([^():]+):[0-9.]+\)", r"\1", body["positive"])
    assert "close-up, from below" in bare
    assert "leaning forward, eye contact" in bare
    assert "smile" in body["negative"]
    # The player character is the camera, never a subject.
    assert body["subjects"] == ["aiko"]
    # No LoRA on a CG unless asked for; the face pass still gets a prompt.
    assert body["lora"] == ""
    assert "face focus" in body["face_positive"]


def test_the_node_cast_reaches_the_rendered_prompt(client, project):
    """Set in the inspector, honoured by the render -- and by the preview,
    which is deliberately the same code path."""
    pid, say = project["id"], project["say"]
    before = client.get(f"/api/projects/{pid}/images/cg/preview?node_id={say}").json()
    assert before["subjects"] == ["aiko"]

    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg_subjects": ["pc"]}})
    try:
        after = client.get(f"/api/projects/{pid}/images/cg/preview?node_id={say}").json()
        assert after["subjects"] == ["pc"]
        assert after["positive"] != before["positive"]
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"cg_subjects": []}})

    # Cleared, it goes back to working it out from who is on stage.
    restored = client.get(f"/api/projects/{pid}/images/cg/preview?node_id={say}").json()
    assert restored["subjects"] == ["aiko"]


def test_the_director_sees_the_same_cast_the_render_will_use(client, project):
    """Directing a shot for two people and then rendering one of them is worse
    than either on its own."""
    from app import store as store_mod
    from app.graph import Graph
    from app.services import context as ctx_mod

    pid, say = project["id"], project["say"]
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg_subjects": ["pc"]}})
    try:
        graph = Graph.build(store_mod.list_nodes(pid), store_mod.list_edges(pid))
        assert ctx_mod.assemble_cg(pid, graph, say)["subjects"] == ["pc"]
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"cg_subjects": []}})


def test_cg_prompt_preview_404s_on_an_unknown_node(client, project):
    r = client.get(f"/api/projects/{project['id']}/images/cg/preview?node_id=nope")
    assert r.status_code == 404


def test_a_cg_training_set_refuses_to_run_before_the_sprite_lora_exists(client, project):
    """The CG training set is shot *with* the Sprite LoRA -- without it there
    is nothing to shoot with, and the job would just repeat the base
    checkpoint."""
    r = client.post(f"/api/projects/{project['id']}/images/cg-set",
                    json={"character_id": "aiko", "count": 4})
    assert r.status_code == 422
    assert "Sprite LoRA" in r.json()["detail"]


def test_training_the_cg_lora_leaves_the_sprite_lora_alone(client, project):
    """Sprites are keyed to the Sprite LoRA slot; training the CG LoRA must
    never cost you the LoRA they already load."""
    pid = project["id"]
    characters = client.get(f"/api/projects/{pid}/characters").json()
    aiko = next(c for c in characters if c["id"] == "aiko")
    aiko["assets"]["lora"] = "chr_aiko_sprite.safetensors"
    aiko["assets"]["lora_cg"] = "chr_aiko_cg.safetensors"
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)

    saved = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                 if c["id"] == "aiko")
    assert saved["assets"]["lora"] == "chr_aiko_sprite.safetensors"
    assert saved["assets"]["lora_cg"] == "chr_aiko_cg.safetensors"
    assert saved["assets"]["cg_lora_weight"] < saved["assets"]["lora_weight"]


def test_fill_missing_reports_blocked_work_rather_than_failing_silently(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/batch/fill-missing", json={"kinds": ["voice"]})
    job = _await_job(client, r.json()["job_id"])
    assert job["result"]["queued"] == 0
    assert any("reference clip" in b for b in job["result"]["blocked"])


# --------------------------------------------------------------------------
# gallery
# --------------------------------------------------------------------------


def test_deleting_an_asset_unsets_every_node_that_pointed_at_it(client, project):
    """A deleted file has to leave the graph saying "no image" -- not "image
    ready" with nothing behind the hash. Otherwise the node keeps its badge,
    the build copies a file that is not there, and the missing-assets scan
    walks straight past the gap."""
    from app.services import assets as asset_svc

    pid = project["id"]
    say = project["say"]
    digest = asset_svc.put(b"a deleted cg", kind="img", ext="png")
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg": True, "image_hash": digest,
                                "cg_camera": "close-up"}})
    try:
        node = _node(client, pid, say)
        assert node["data"]["image_hash"] == digest

        r = client.delete(f"/api/projects/{pid}/gallery/{digest}?force=true")
        assert r.status_code == 200, r.text
        assert r.json()["cleared_count"] >= 1
        assert any(c["kind"] == "node" and c["id"] == say and c["role"] == "image"
                   for c in r.json()["cleared"])

        node = _node(client, pid, say)
        assert not node["data"].get("image_hash")
        # Unset, not "ready but broken" -- and the staleness stamp goes with
        # it, or the next render would look current the moment it landed.
        assert node["image_status"] == "none"
        assert "_image_text_hash" not in node["data"]

        # And it is missing work again, which is the point.
        report = client.get(f"/api/projects/{pid}/batch/missing").json()
        assert any(c["node_id"] == say for c in report["cgs"])
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"cg": False, "cg_camera": ""}})


def test_deleting_a_voice_line_puts_it_back_in_the_missing_report(client, project):
    from app.services import assets as asset_svc

    pid = project["id"]
    say = project["say"]
    # Cast Aiko so voice work is queueable rather than blocked.
    characters = client.get(f"/api/projects/{pid}/characters").json()
    aiko = next(c for c in characters if c["id"] == "aiko")
    clip = asset_svc.put(b"reference clip", kind="voice_ref", ext="wav")
    aiko["voice"]["reference_clip"] = f"assets/voice_ref/{clip}.wav"
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)

    digest = asset_svc.put(b"a deleted voice line", kind="audio", ext="ogg")
    client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {
        "voice_hash": digest, "voice_path": "audio/voice/say_deadbeef.ogg"}})
    try:
        # It has audio, so the scan can only ever call it stale, never missing.
        assert not any(v["node_id"] == say and v["reason"] == "missing" for v in
                       client.get(f"/api/projects/{pid}/batch/missing").json()["voice"])

        r = client.delete(f"/api/projects/{pid}/gallery/{digest}?force=true")
        assert r.status_code == 200, r.text

        node = _node(client, pid, say)
        assert not node["data"].get("voice_hash")
        # The compiled path goes too: it only ever meant anything while the
        # hash behind it resolved, and build.py copies from the hash.
        assert not node["data"].get("voice_path")
        assert node["audio_status"] == "none"

        report = client.get(f"/api/projects/{pid}/batch/missing").json()
        assert any(v["node_id"] == say and v["reason"] == "missing"
                   for v in report["voice"])
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"voice_hash": None, "voice_path": None}})
        aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                    if c["id"] == "aiko")
        aiko["voice"]["reference_clip"] = None
        client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)


def test_deleting_a_background_unsets_the_location_variant(client, project):
    from app.services import assets as asset_svc

    pid = project["id"]
    digest = asset_svc.put(b"a deleted background", kind="img", ext="png")
    location = client.get(f"/api/projects/{pid}/locations").json()[0]
    location["variants"] = {**(location["variants"] or {}), "dusk": digest}
    client.put(f"/api/projects/{pid}/locations/{location['id']}", json=location)

    assert not any(b["variant"] == "dusk" for b in
                   client.get(f"/api/projects/{pid}/batch/missing").json()["backgrounds"])

    r = client.delete(f"/api/projects/{pid}/gallery/{digest}?force=true")
    assert r.status_code == 200, r.text
    assert any(c["kind"] == "location" and c["role"] == "bg dusk"
               for c in r.json()["cleared"])

    saved = next(l for l in client.get(f"/api/projects/{pid}/locations").json()
                 if l["id"] == location["id"])
    assert "dusk" not in (saved["variants"] or {})
    assert any(b["variant"] == "dusk" for b in
               client.get(f"/api/projects/{pid}/batch/missing").json()["backgrounds"])


def test_deleting_a_sprite_unsets_the_slot_and_the_scan_finds_it_again(client, project):
    from app.services import assets as asset_svc

    pid = project["id"]
    slot = "uniform/front/looking_away"
    digest = asset_svc.put(b"a deleted sprite", kind="img", ext="png")
    aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                if c["id"] == "aiko")
    aiko["assets"]["sprite_sheet"] = {**(aiko["assets"]["sprite_sheet"] or {}),
                                      slot: digest}
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)

    assert not any(s["slot"] == slot for s in
                   client.get(f"/api/projects/{pid}/batch/missing").json()["sprites"])

    r = client.delete(f"/api/projects/{pid}/gallery/{digest}?force=true")
    assert r.status_code == 200, r.text

    saved = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                 if c["id"] == "aiko")
    assert slot not in (saved["assets"]["sprite_sheet"] or {})
    assert any(s["slot"] == slot for s in
               client.get(f"/api/projects/{pid}/batch/missing").json()["sprites"])


def test_deleting_a_voice_reference_uncasts_the_character(client, project):
    """The clip is the voice: IndexTTS-2.5 clones it and has nothing else to go
    on, so losing it has to read as uncast rather than as a character who
    still claims a reference that is not there."""
    from app.services import assets as asset_svc

    pid = project["id"]
    digest = asset_svc.put(b"a deleted reference clip", kind="voice_ref", ext="wav")
    aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                if c["id"] == "aiko")
    aiko["voice"]["reference_clip"] = f"assets/voice_ref/{digest}.wav"
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)

    r = client.delete(f"/api/projects/{pid}/gallery/{digest}?force=true")
    assert r.status_code == 200, r.text
    assert any(c["role"] == "voice reference" for c in r.json()["cleared"])

    saved = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                 if c["id"] == "aiko")
    assert not saved["voice"]["reference_clip"]
    # And the scan says so, rather than queueing lines that would fail one by one.
    assert any("reference clip" in b for b in
               client.get(f"/api/projects/{pid}/batch/missing").json()["blocked"])


def test_deleting_an_unreferenced_asset_still_reports_nothing_cleared(client, project):
    from app.services import assets as asset_svc

    pid = project["id"]
    digest = asset_svc.put(b"nothing points at this", kind="img", ext="png")
    r = client.delete(f"/api/projects/{pid}/gallery/{digest}")
    assert r.status_code == 200, r.text
    assert r.json()["cleared_count"] == 0


def test_deleting_a_referenced_asset_without_a_force_changes_nothing(client, project):
    from app.services import assets as asset_svc

    pid = project["id"]
    say = project["say"]
    digest = asset_svc.put(b"still in use", kind="img", ext="png")
    client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {"image_hash": digest}})
    try:
        r = client.delete(f"/api/projects/{pid}/gallery/{digest}")
        assert r.status_code == 409
        node = _node(client, pid, say)
        assert node["data"]["image_hash"] == digest
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}", json={"data": {"image_hash": None}})


def test_gallery_lists_assets_with_their_usages(client, project):
    pid = project["id"]
    from app.services import assets as asset_svc

    used = asset_svc.put(b"used-image-bytes", kind="img", ext="png")
    unused = asset_svc.put(b"unused-image-bytes", kind="img", ext="png")
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}",
                 json={"data": {"image_hash": used}})

    gallery = client.get(f"/api/projects/{pid}/gallery").json()
    by_hash = {a["hash"]: a for a in gallery["assets"]}
    assert by_hash[used]["use_count"] >= 1
    assert by_hash[used]["used_by"][0]["kind"] == "node"
    assert by_hash[unused]["use_count"] == 0
    assert gallery["bytes"] > 0
    assert gallery["unused"] >= 1

    only_unused = {a["hash"] for a in
                   client.get(f"/api/projects/{pid}/gallery?only_unused=true").json()["assets"]}
    assert unused in only_unused and used not in only_unused


def test_gallery_tracks_sprite_and_location_usage(client, project):
    pid = project["id"]
    from app.services import assets as asset_svc

    sprite = asset_svc.put(b"sprite-bytes", kind="img", ext="png")
    background = asset_svc.put(b"bg-bytes", kind="img", ext="png")

    character = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                     if c["id"] == "aiko")
    character["assets"]["sprite_sheet"] = {"uniform/neutral": sprite}
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)

    location = client.get(f"/api/projects/{pid}/locations").json()[0]
    location["variants"] = {"dusk": background}
    client.put(f"/api/projects/{pid}/locations/{location['id']}", json=location)

    by_hash = {a["hash"]: a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert by_hash[sprite]["used_by"][0]["kind"] == "character"
    assert "sprite" in by_hash[sprite]["used_by"][0]["role"]
    assert by_hash[background]["used_by"][0]["kind"] == "location"


def test_gallery_tracks_sheet_pool_and_frees_it_once_culled(client, project):
    """A LoRA-bootstrap pool image is still "in use" while it sits in
    sheet_pool -- the character/Sprites cull UI, not the Gallery, is where it
    gets deleted. Deleting it there (removing it from sheet_pool first) is
    what lets the Gallery-side delete actually succeed afterward."""
    pid = project["id"]
    from app.services import assets as asset_svc

    pool_img = asset_svc.put(b"pool-bytes", kind="img", ext="png")

    character = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                     if c["id"] == "aiko")
    character["assets"]["sheet_pool"] = [
        {"hash": pool_img, "outfit_id": "uniform", "angle": "front view", "expression": "neutral"}
    ]
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)

    gallery = {a["hash"]: a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert gallery[pool_img]["use_count"] == 1
    assert gallery[pool_img]["used_by"][0]["kind"] == "character"
    assert "pool" in gallery[pool_img]["used_by"][0]["role"]
    assert client.delete(f"/api/projects/{pid}/gallery/{pool_img}").status_code == 409

    character["assets"]["sheet_pool"] = []
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)
    assert client.delete(f"/api/projects/{pid}/gallery/{pool_img}").status_code == 200


def test_gallery_tracks_the_cg_pool_separately_from_the_sheet_pool(client, project):
    """The CG LoRA's training set is its own pool -- an image in it is "in
    use" the same way a sheet_pool image is, and it must not be confused with
    (or freed by clearing) the sprite pool."""
    pid = project["id"]
    from app.services import assets as asset_svc

    pool_img = asset_svc.put(b"cg-pool-bytes", kind="img", ext="png")

    character = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                     if c["id"] == "aiko")
    character["assets"]["cg_pool"] = [
        {"hash": pool_img, "outfit_id": "uniform", "angle": "front view", "expression": "neutral"}
    ]
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)

    gallery = {a["hash"]: a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert gallery[pool_img]["use_count"] == 1
    assert "CG pool" in gallery[pool_img]["used_by"][0]["role"]
    assert client.delete(f"/api/projects/{pid}/gallery/{pool_img}").status_code == 409

    # Clearing the *other* pool must not free this one.
    character["assets"]["sheet_pool"] = []
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)
    assert client.delete(f"/api/projects/{pid}/gallery/{pool_img}").status_code == 409

    character["assets"]["cg_pool"] = []
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)
    assert client.delete(f"/api/projects/{pid}/gallery/{pool_img}").status_code == 200


def test_a_profile_headshot_enqueues_a_render_job(client, project):
    """The overview headshot: one image.profile job, expression carried on the
    payload. The result lands on character.assets.profile_image."""
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/images/profile",
                    json={"character_id": "aiko", "expression": "soft_smile"})
    assert r.status_code == 200
    job = client.get(f"/api/jobs/{r.json()['job_id']}").json()
    assert job["kind"] == "image.profile"
    assert job["payload"]["expression"] == "soft_smile"
    client.post(f"/api/jobs/{job['id']}/cancel")


def test_a_profile_headshot_404s_on_an_unknown_character(client, project):
    r = client.post(f"/api/projects/{project['id']}/images/profile",
                    json={"character_id": "nobody"})
    assert r.status_code == 404


def test_gallery_tracks_a_character_profile_image(client, project):
    """A headshot sitting on character.assets.profile_image is "in use" -- the
    General tab, not the Gallery, is where it gets replaced or removed."""
    pid = project["id"]
    from app.services import assets as asset_svc

    portrait = asset_svc.put(b"portrait-bytes", kind="img", ext="png")
    character = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                     if c["id"] == "aiko")
    character["assets"]["profile_image"] = portrait
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)

    by_hash = {a["hash"]: a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert by_hash[portrait]["used_by"][0]["kind"] == "character"
    assert by_hash[portrait]["used_by"][0]["role"] == "profile image"
    assert client.delete(f"/api/projects/{pid}/gallery/{portrait}").status_code == 409

    character["assets"]["profile_image"] = None
    client.put(f"/api/projects/{pid}/characters/aiko", json=character)
    assert client.delete(f"/api/projects/{pid}/gallery/{portrait}").status_code == 200


def test_gallery_refuses_to_delete_a_referenced_asset(client, project):
    pid = project["id"]
    gallery = client.get(f"/api/projects/{pid}/gallery").json()
    used = next(a for a in gallery["assets"] if a["use_count"] > 0)
    unused = next(a for a in gallery["assets"] if a["use_count"] == 0)

    assert client.delete(f"/api/projects/{pid}/gallery/{used['hash']}").status_code == 409
    assert client.delete(f"/api/projects/{pid}/gallery/{unused['hash']}").status_code == 200

    remaining = {a["hash"] for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert unused["hash"] not in remaining
    assert used["hash"] in remaining


def test_gallery_force_delete_overrides_the_guard(client, project):
    pid = project["id"]
    used = next(a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]
                if a["use_count"] > 0)
    assert client.delete(
        f"/api/projects/{pid}/gallery/{used['hash']}?force=true").status_code == 200


def test_gallery_attributes_each_asset_to_a_character(client, project):
    """Grouping needs a character per asset: an explicit meta tag, the
    sprite/profile owner, the speaker of a say node that uses it, or -- for an
    encoded clip -- whatever the clip it came from resolves to."""
    pid = project["id"]
    from app.services import assets as asset_svc

    tagged = asset_svc.put(b"grp-tagged", kind="img", ext="png", meta={"character": "aiko"})
    raw = asset_svc.put(b"grp-raw", kind="audio", ext="wav", meta={"character": "aiko"})
    encoded = asset_svc.put(b"grp-ogg", kind="audio", ext="ogg", meta={"source": raw})
    voiced = asset_svc.put(b"grp-voice", kind="audio", ext="ogg")
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}",
                 json={"data": {"voice_hash": voiced}})
    loose = asset_svc.put(b"grp-loose", kind="img", ext="png")

    by_hash = {a["hash"]: a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert by_hash[tagged]["character_id"] == "aiko"
    assert by_hash[tagged]["character_name"] == "Aiko"
    assert by_hash[encoded]["character_id"] == "aiko"   # followed meta.source
    assert by_hash[voiced]["character_id"] == "aiko"    # followed the say node's speaker
    assert by_hash[loose]["character_id"] is None
    assert by_hash[loose]["character_name"] is None


def test_gallery_bulk_deletes_only_unreferenced_assets(client, project):
    """One button clears every unused asset -- file and index row -- and never
    touches one that is still referenced anywhere."""
    pid = project["id"]
    from app.services import assets as asset_svc

    keep = asset_svc.put(b"bulk-keep", kind="img", ext="png")
    client.patch(f"/api/projects/{pid}/nodes/{project['merge']}",
                 json={"data": {"image_hash": keep}})
    drop_a = asset_svc.put(b"bulk-drop-a", kind="img", ext="png")
    drop_b = asset_svc.put(b"bulk-drop-b", kind="audio", ext="ogg")

    assert asset_svc.path_for(drop_a) is not None
    r = client.delete(f"/api/projects/{pid}/gallery-unused")
    assert r.status_code == 200
    body = r.json()
    assert set(body["deleted"]) >= {drop_a, drop_b}
    assert keep not in body["deleted"]
    assert body["count"] >= 2 and body["bytes"] > 0

    # gone from the index and from disk
    assert asset_svc.path_for(drop_a) is None
    remaining = {a["hash"] for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert drop_a not in remaining and drop_b not in remaining
    assert keep in remaining
    assert client.get(f"/api/projects/{pid}/gallery").json()["unused"] == 0


def test_bulk_delete_unused_spares_assets_used_by_another_project(client, project):
    """The sweep is store-wide, so it must honour references from every project,
    not just the one whose gallery triggered it."""
    pid = project["id"]
    from app.services import assets as asset_svc

    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    shared = asset_svc.put(b"shared-across-projects", kind="img", ext="png")
    node = client.post(f"/api/projects/{other}/nodes", json={
        "type": "narration", "data": {"text": "x", "image_hash": shared}}).json()
    assert node["data"]["image_hash"] == shared

    client.delete(f"/api/projects/{pid}/gallery-unused")
    assert asset_svc.path_for(shared) is not None

    client.delete(f"/api/projects/{other}")


def test_gallery_filters_combine_across_type_scene_character_and_use(client, project):
    """The filters are OR within one axis and AND across axes, so "unused CGs in
    scene 3" is one request rather than a client-side scan of everything."""
    pid = project["id"]
    from app.services import assets as asset_svc

    sprite = asset_svc.put(b"typed-sprite", kind="img", ext="png",
                           meta={"character": "aiko", "outfit": "uniform",
                                 "pose": "front", "expression": "neutral"})
    background = asset_svc.put(b"typed-bg", kind="img", ext="png",
                               meta={"location": "school_rooftop", "variant": "dusk"})
    cg = asset_svc.put(b"typed-cg", kind="img", ext="png",
                       meta={"node": project["say"], "seed": 7, "prompt": "a rooftop"})
    voice = asset_svc.put(b"typed-voice", kind="audio", ext="ogg",
                          meta={"character": "aiko", "emotion": "melancholy",
                                "intensity": 0.6})
    sfx = asset_svc.put(b"typed-sfx", kind="audio", ext="ogg")
    client.patch(f"/api/projects/{pid}/nodes/{project['say']}",
                 json={"data": {"sfx_hash": sfx, "sfx_cue": "the door sticks"}})

    def hashes(query=""):
        return {a["hash"] for a in
                client.get(f"/api/projects/{pid}/gallery?{query}").json()["assets"]}

    rows = {a["hash"]: a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]}
    assert rows[sprite]["type"] == "sprite"
    assert rows[background]["type"] == "background"
    assert rows[cg]["type"] == "cg"
    assert rows[voice]["type"] == "voice"
    assert rows[sfx]["type"] == "sfx"          # only typeable because sfx_hash counts as a use
    assert rows[background]["scene_names"] == ["Rooftop"]

    # OR within one axis
    both = hashes("types=sprite,background")
    assert {sprite, background} <= both and cg not in both

    # a scene claims what its nodes reference and what its location supplies
    in_scene = hashes(f"scenes={project['scene']}")
    assert {background, cg, sfx} <= in_scene
    assert voice not in in_scene and sprite not in in_scene

    # AND across axes
    scene_cgs = hashes(f"types=cg&scenes={project['scene']}")
    assert cg in scene_cgs and background not in scene_cgs

    of_aiko = hashes("characters=aiko")
    assert {sprite, voice} <= of_aiko and background not in of_aiko
    assert background in hashes("characters=__none__")

    assert sfx in hashes("used=used") and sprite not in hashes("used=used")
    assert sprite in hashes("used=unused") and sfx not in hashes("used=unused")


def test_gallery_pages_the_list_and_reports_what_is_left(client, project):
    """A page at a time, with the counts describing the whole filtered set --
    otherwise the gallery hands the browser every thumb in the store at once."""
    pid = project["id"]
    from app.services import assets as asset_svc

    made = [asset_svc.put(f"paged-bg-{i}".encode(), kind="img", ext="png",
                          meta={"location": "school_rooftop", "variant": f"v{i}"})
            for i in range(5)]

    first = client.get(f"/api/projects/{pid}/gallery?types=background&limit=2").json()
    second = client.get(
        f"/api/projects/{pid}/gallery?types=background&limit=2&offset=2").json()
    assert len(first["assets"]) == 2 and len(second["assets"]) == 2
    assert first["has_more"]
    assert first["total"] == second["total"] >= 5
    # Paging must not repeat or skip: the order is fixed server-side.
    assert not ({a["hash"] for a in first["assets"]} & {a["hash"] for a in second["assets"]})

    everything = client.get(f"/api/projects/{pid}/gallery?types=background").json()
    assert set(made) <= {a["hash"] for a in everything["assets"]}
    assert len(everything["assets"]) == everything["total"] and not everything["has_more"]
    # `library_*` ignores the filters -- the purge button acts on the whole store.
    assert everything["library_total"] > everything["total"]
    assert everything["facets"]["types"]["background"] == everything["total"]


# --------------------------------------------------------------------------
# regenerate with an instruction
# --------------------------------------------------------------------------

def test_regenerate_passes_a_one_off_instruction_to_the_prose_pass(client, project, monkeypatch):
    pid = project["id"]
    from app.clients import lmstudio
    from app.services import generation

    seen: list[str] = []

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        if json_schema is None:
            seen.append(messages[-1]["content"])
            return 'AIKO: "Fine."'
        return json.dumps({"scene_title": "x", "nodes": [
            {"type": "say", "speaker": "aiko", "text": "Fine.", "expression": "neutral",
             "emotion": "cold", "intensity": 0.3, "delivery": "", "shot_hint": ""}]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    r = client.post(f"/api/projects/{pid}/generate/scene", json={
        "anchor_id": project["say"], "instruction": "much shorter, and colder"})
    job = _await_job(client, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    assert seen and "much shorter, and colder" in seen[-1]


def test_instruction_is_optional(client, project, monkeypatch):
    pid = project["id"]
    from app.clients import lmstudio
    from app.services import generation

    seen: list[str] = []

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        if json_schema is None:
            seen.append(messages[-1]["content"])
            return 'AIKO: "Sure."'
        return json.dumps({"scene_title": "x", "nodes": [
            {"type": "say", "speaker": "aiko", "text": "Sure.", "expression": "neutral",
             "emotion": "neutral", "intensity": 0.5, "delivery": "", "shot_hint": ""}]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    r = client.post(f"/api/projects/{pid}/generate/scene",
                    json={"anchor_id": project["say"]})
    assert _await_job(client, r.json()["job_id"])["status"] == "done"
    # Continuing from a say node: the prompt must tell the model to pick up after
    # the last line, not restart the scene (item 13).
    assert seen and "Continue from immediately after" in seen[-1]


# --------------------------------------------------------------------------
# web (HTML) build + /play
# --------------------------------------------------------------------------

def test_build_info_reports_no_web_build_and_play_404s_helpfully(client, project):
    pid = project["id"]
    web = client.get(f"/api/projects/{pid}/build-info").json()["web"]
    assert web == {"built": False, "url": None, "built_at": None, "bytes": 0,
                   "support": False}

    r = client.get(f"/play/{pid}/")
    assert r.status_code == 404
    assert "Build for web" in r.text            # tells you what to do, not just "404"


def test_play_serves_a_staged_web_build_with_wasm_mime_and_isolation_headers(client, project):
    """Ren'Py's web runtime needs application/wasm and cross-origin isolation;
    getting either wrong is a blank canvas with a console error."""
    pid = project["id"]
    from app.services import build as build_svc

    root = build_svc.web_dir(pid)
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html><title>game</title>", encoding="utf-8")
    (root / "renpy.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (root / "game.zip").write_bytes(b"PK\x03\x04")

    info = client.get(f"/api/projects/{pid}/build-info").json()["web"]
    assert info["built"] is True
    assert info["url"] == f"/play/{pid}/"
    assert info["bytes"] > 0 and info["built_at"]

    page = client.get(f"/play/{pid}/")
    assert page.status_code == 200
    assert "<title>game</title>" in page.text
    assert page.headers["cross-origin-opener-policy"] == "same-origin"
    assert page.headers["cross-origin-embedder-policy"] == "require-corp"

    wasm = client.get(f"/play/{pid}/renpy.wasm")
    assert wasm.status_code == 200
    assert wasm.headers["content-type"] == "application/wasm"

    # The bare form redirects to the trailing slash, or index.html's relative
    # asset URLs would resolve one directory too high.
    assert client.get(f"/play/{pid}", follow_redirects=False).status_code == 307


def test_play_refuses_to_serve_outside_the_staged_build(client, project):
    """A plain ../ is collapsed by the client, so the guard has to hold for the
    encoded form, which arrives at the handler intact."""
    pid = project["id"]
    assert client.get(f"/play/{pid}/%2e%2e/%2e%2e/vn.sqlite3").status_code == 404
    assert client.get(f"/play/{pid}/%2e%2e/%2e%2e/assets/img").status_code == 404
    assert client.get(f"/play/{pid}/nope.js").status_code == 404


def test_projects_get_their_own_folder_inside_the_sdk(client, project, tmp_path, monkeypatch):
    """Never <sdk>/game itself: that name shadows launcher/game in renpy.py's
    gamedir search and stops the SDK opening the launcher at all."""
    pid = project["id"]
    from app import store
    from app.services import build as build_svc

    fake_sdk = tmp_path / "renpy-sdk"
    (fake_sdk / "renpy").mkdir(parents=True)
    (fake_sdk / "launcher").mkdir()
    (fake_sdk / "renpy.py").write_text("# bootstrap", encoding="utf-8")
    assert build_svc.is_renpy_sdk_root(fake_sdk)
    assert not build_svc.is_renpy_sdk_root(tmp_path)

    store.set_setting("renpy_sdk_path", str(fake_sdk))
    try:
        target = build_svc.project_dir(pid)
        assert target == fake_sdk / "features"      # project is named "Features"
        assert client.get(f"/api/projects/{pid}/build-info").json()["target_is_sdk"] is False

        # A project whose name collides with one of Ren'Py's own directories
        # gets a prefix rather than burying it.
        for name, expected in (("game", "vn_game"), ("Launcher", "vn_launcher")):
            hazard = client.post("/api/projects", json={"name": name}).json()
            assert build_svc.project_dir(hazard["id"]).name == expected
            client.delete(f"/api/projects/{hazard['id']}")

        # Two projects sharing a name still get one directory each: exactly one
        # keeps the bare slug, and the choice is stable across calls (a folder
        # must never move under a project).
        a = client.post("/api/projects", json={"name": "Twin"}).json()
        b = client.post("/api/projects", json={"name": "Twin"}).json()
        dir_a, dir_b = build_svc.project_dir(a["id"]), build_svc.project_dir(b["id"])
        assert dir_a != dir_b
        assert {dir_a.name, dir_b.name} & {"twin"}
        assert (build_svc.project_dir(a["id"]), build_svc.project_dir(b["id"])) == (dir_a, dir_b)
        for p in (a, b):
            client.delete(f"/api/projects/{p['id']}")
    finally:
        store.set_setting("renpy_sdk_path", "")

    # The guard stays as a backstop for anything that ever resolves a target to
    # the SDK root itself.
    monkeypatch.setattr(build_svc, "project_dir", lambda _pid: fake_sdk)
    with pytest.raises(ValueError, match="Ren'Py SDK itself"):
        build_svc.export(pid)
    assert not (fake_sdk / "game").exists(), "must not write anything before refusing"


def test_open_launcher_passes_no_basedir_and_refuses_a_hijacked_sdk(tmp_path, monkeypatch):
    """The launcher must run with config.basedir == the SDK root: its DLC
    downloader (how web support is installed) reads <basedir>/update/current.json,
    and <sdk>/launcher has no update/ -- passing it explicitly makes every
    download fail with "this project does not support updating"."""
    import asyncio
    from app import store
    from app.services import build as build_svc

    sdk = tmp_path / "sdk"
    (sdk / "launcher").mkdir(parents=True)
    (sdk / "update").mkdir()
    (sdk / "update" / "current.json").write_text("{}", encoding="utf-8")
    exe = sdk / "renpy.exe"
    exe.write_text("", encoding="utf-8")
    store.set_setting("renpy_sdk_path", str(sdk))

    spawned: dict = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(build_svc.subprocess, "Popen", fake_popen)
    try:
        out = asyncio.run(build_svc.open_launcher())
        assert out["launched"] is True
        assert spawned["cmd"] == [str(exe)], "no basedir argument, or DLC breaks"

        # A leftover <sdk>/game would boot that game instead; say so rather than
        # silently launching the wrong thing.
        (sdk / "game").mkdir()
        out = asyncio.run(build_svc.open_launcher())
        assert out["launched"] is False
        assert "launcher/game" in out["reason"]
    finally:
        store.set_setting("renpy_sdk_path", "")


def test_script_rows_carry_what_the_row_editor_needs(client, project):
    """The Script view edits a line without a round trip per row to fetch the
    node, so everything its inspector-parity panel shows has to ride along on
    the row itself."""
    pid = project["id"]
    say = next(r for r in _rows(client, pid) if r["kind"] == "say")

    # Present on every row, whatever its kind.
    for field in ("notes", "pinned_label", "gen_id", "continuations",
                  "variant_count"):
        assert field in say, field

    # Present on the text kinds, because the row panel edits them.
    for field in ("pose", "sfx_cue", "shot_hint", "cg"):
        assert field in say, field

    node_id = say["node_id"]
    client.patch(f"/api/projects/{pid}/nodes/{node_id}", json={
        "data": {"sfx_cue": "knock at the door", "cg": True, "pose": "leaning"},
        "notes": "check this against the brief",
        "pinned_label": True,
    })

    say = next(r for r in _rows(client, pid) if r["node_id"] == node_id)
    assert say["sfx_cue"] == "knock at the door"
    assert say["cg"] is True
    assert say["pose"] == "leaning"
    assert say["notes"] == "check this against the brief"
    assert say["pinned_label"] is True


def test_script_rows_surface_continuation_suggestions(client, project):
    """They live in node data under a private key; the row lifts them out so
    the Script view can offer the same buttons the canvas inspector does."""
    pid = project["id"]
    node_id = next(r for r in _rows(client, pid) if r["kind"] == "narration")["node_id"]

    client.patch(f"/api/projects/{pid}/nodes/{node_id}", json={
        "data": {"_continuations": ["she asks why you came", "you let it go"],
                 "_gen": "job_abc"}})

    row = next(r for r in _rows(client, pid) if r["node_id"] == node_id)
    assert row["continuations"] == ["she asks why you came", "you let it go"]
    assert row["gen_id"] == "job_abc"


# --------------------------------------------------------------------------
# missing audio
# --------------------------------------------------------------------------

def test_missing_report_lists_unresolved_sound_cues(client, project):
    """Generation writes sound *descriptions*; files only ever arrive by
    hand. Those cues compile to `# TODO sfx:` comments, so without this report
    the only way to find them is grepping the compiled script."""
    pid, say = project["id"], project["say"]
    stage = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "stage", "data": {"music_cue": "low strings under the argument"},
    }).json()
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"sfx_cue": "the roof door sticks"}})
    try:
        report = client.get(f"/api/projects/{pid}/batch/missing").json()
        by_node = {s["node_id"]: s for s in report["sounds"]}

        assert by_node[say]["kind"] == "sfx"
        assert by_node[say]["cue"] == "the roof door sticks"
        assert by_node[say]["field"] == "sfx_file"
        assert by_node[say]["scene"] == "Rooftop"
        assert by_node[stage["id"]]["kind"] == "music"
        assert by_node[stage["id"]]["field"] == "music_file"
        assert report["counts"]["sounds"] == len(report["sounds"])

        # Nothing generates audio, so a fill run must never try to: `total` is
        # what would be queued, and these are not in it.
        counts = report["counts"]
        assert counts["total"] == (counts["voice"] + counts["cgs"]
                                   + counts["backgrounds"] + counts["sprites"])

        # Attaching a file is what resolves a cue.
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"sfx_file": "door_stick.ogg"}})
        report = client.get(f"/api/projects/{pid}/batch/missing").json()
        assert say not in {s["node_id"] for s in report["sounds"]}
    finally:
        client.delete(f"/api/projects/{pid}/nodes/{stage['id']}")
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"sfx_cue": "", "sfx_file": None}})


# --------------------------------------------------------------------------
# hub nodes
# --------------------------------------------------------------------------

def test_hub_options_are_validated_against_declared_stats(client):
    """The reason hub conditions are structured rather than free text: a stat
    nobody declared is a mistake the tool can catch, where `affektion >= 20`
    would just evaluate False for the whole game."""
    pid = client.post("/api/projects", json={"name": "Hub validation"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"character_stats": [{"id": "affection"}]})
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})

    def add(ntype, data, connect_from=None, handle="out"):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": connect_from,
            "connect_handle": handle}).json()

    scene = add("scene", {"title": "Dorm"})
    hub = add("hub", {"prompt": "What do you ask?", "options": [
        {"id": "o1", "label": "The club",
         "requires": [{"stat": "affection", "character": "aiko",
                       "op": ">=", "value": "20"}]},
        {"id": "o2", "label": "Her sister",
         "requires": [{"stat": "affektion", "character": "aiko",
                       "op": ">=", "value": "20"}]},
        {"id": "o3", "label": "Nothing at all"},
        {"id": "o4", "label": "Her route", "target_route": "ghost"},
    ]}, scene["id"])
    add("say", {"speaker": "aiko", "text": "It is quiet."}, hub["id"], "o1")

    problems = client.get(f"/api/projects/{pid}/validate").json()["problems"]
    messages = [p["message"] for p in problems if p["node_id"] == hub["id"]]

    assert any("undeclared stat 'affektion'" in m for m in messages)
    assert any("'Nothing at all' goes nowhere" in m for m in messages)
    assert any("route 'ghost'" in m for m in messages)
    # o1 is declared, wired and gated correctly -- it must not be flagged.
    assert not any("'The club'" in m for m in messages)
    # A hub emits its own menu, so the generic "no outgoing edge" warning that
    # fires on a dead-end say node must not fire here.
    assert not any("no outgoing edge" in m for m in messages)


def test_a_hub_reads_back_as_a_script_row(client):
    """The Script view linearizes hubs like any other branch point, with the
    structured gates flattened so you can read what locks a topic."""
    pid = client.post("/api/projects", json={"name": "Hub script"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"character_stats": [{"id": "affection"}]})
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})

    def add(ntype, data, connect_from=None, handle="out"):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": connect_from,
            "connect_handle": handle}).json()

    scene = add("scene", {"title": "Dorm"})
    hub = add("hub", {"prompt": "What do you ask?", "options": [
        {"id": "o1", "label": "Her sister",
         "requires": [{"stat": "affection", "character": "aiko",
                       "op": ">=", "value": "20"}]},
    ]}, scene["id"])
    add("say", {"speaker": "aiko", "text": "She left."}, hub["id"], "o1")

    rows = client.post(f"/api/projects/{pid}/script", json={"choices": {}}).json()["rows"]
    row = next(r for r in rows if r["node_id"] == hub["id"])

    assert row["kind"] == "hub"
    assert row["text"] == "What do you ask?"
    assert row["options"][0]["condition"] == "affection_aiko >= 20"
    assert row["options"][0]["wired"] is True
    # ...and the walk carries on down the topic, so the line after it is there.
    assert any(r["text"] == "She left." for r in rows)


# --------------------------------------------------------------------------
# regenerating from the gallery


def _cancel(client, job_ids):
    for jid in job_ids:
        client.post(f"/api/jobs/{jid}/cancel")


def test_a_cg_regenerates_from_the_gallery_onto_its_own_node(client, project):
    """The gallery's regenerate runs the same job the inspector would, aimed
    at the node that references the file -- which is what makes the new take
    replace the old one on that node rather than land beside it."""
    from app.services import assets as asset_svc

    pid, say = project["id"], project["say"]
    digest = asset_svc.put(b"a cg to redo", kind="img", ext="png")
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg": True, "image_hash": digest}})
    try:
        asset = next(a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]
                     if a["hash"] == digest)
        use = next(u for u in asset["used_by"] if u["role"] == "image")
        assert use["regen"]["action"] == "cg"

        r = client.post(f"/api/projects/{pid}/gallery/{digest}/regenerate")
        assert r.status_code == 200, r.text
        queued = r.json()["queued"]
        assert len(queued) == 1
        job = client.get(f"/api/jobs/{queued[0]['job_id']}").json()
        assert job["kind"] == "image.generate"
        assert job["node_id"] == say
        _cancel(client, r.json()["job_ids"])
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"image_hash": None, "cg": False}})


def test_one_use_of_a_shared_asset_can_be_redone_without_the_others(client, project):
    """Assets are content-addressed, so the same image can hang off two nodes.
    Regenerating from one of them must not re-shoot the other: each `regen.ref`
    is a separate target, and the row's own button passes it."""
    from app.services import assets as asset_svc

    pid, say, narr = project["id"], project["say"], project["merge"]
    digest = asset_svc.put(b"one image, two nodes", kind="img", ext="png")
    for node_id in (say, narr):
        client.patch(f"/api/projects/{pid}/nodes/{node_id}",
                     json={"data": {"image_hash": digest}})
    try:
        asset = next(a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]
                     if a["hash"] == digest)
        refs = {u["id"]: u["regen"]["ref"] for u in asset["used_by"] if u["role"] == "image"}
        assert set(refs) == {say, narr}

        r = client.post(f"/api/projects/{pid}/gallery/{digest}/regenerate",
                        json={"ref": refs[narr]})
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 1
        assert client.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()["node_id"] == narr
        _cancel(client, r.json()["job_ids"])

        # ...and with no ref, every use is redone, one render each.
        r = client.post(f"/api/projects/{pid}/gallery/{digest}/regenerate")
        assert r.json()["count"] == 2
        _cancel(client, r.json()["job_ids"])
    finally:
        for node_id in (say, narr):
            client.patch(f"/api/projects/{pid}/nodes/{node_id}",
                         json={"data": {"image_hash": None}})


def test_regenerating_a_sprite_puts_it_back_in_the_same_slot(client, project):
    """The slot is outfit/pose/expression, and the regen payload has to carry
    all three -- a sprite that comes back as the default pose has quietly
    stopped being the sprite that node shows."""
    from app.services import assets as asset_svc

    pid = project["id"]
    slot = "uniform/front/looking_away"
    digest = asset_svc.put(b"a sprite to redo", kind="img", ext="png")
    aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                if c["id"] == "aiko")
    aiko["assets"]["sprite_sheet"] = {**(aiko["assets"]["sprite_sheet"] or {}), slot: digest}
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)
    try:
        asset = next(a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]
                     if a["hash"] == digest)
        regen = next(u["regen"] for u in asset["used_by"] if u["role"].startswith("sprite"))
        assert regen["payload"] == {"character_id": "aiko", "slot": slot,
                                    "outfit_id": "uniform", "pose": "front",
                                    "expression": "looking_away"}

        r = client.post(f"/api/projects/{pid}/gallery/{digest}/regenerate")
        assert r.status_code == 200, r.text
        job = client.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()
        assert job["kind"] == "image.sprite"
        assert job["payload"]["slot"] == slot
        _cancel(client, r.json()["job_ids"])
    finally:
        aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                    if c["id"] == "aiko")
        aiko["assets"]["sprite_sheet"] = {
            k: v for k, v in (aiko["assets"]["sprite_sheet"] or {}).items() if k != slot}
        client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)


def test_an_asset_no_generator_owns_refuses_to_regenerate(client, project):
    """An SFX file, a voice reference and a training-pool image come from the
    author. Offering to regenerate them would queue a job with nothing to run,
    so the endpoint says no rather than failing later in the dock."""
    from app.services import assets as asset_svc

    pid, say = project["id"], project["say"]
    digest = asset_svc.put(b"a door slam", kind="audio", ext="ogg")
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"sfx_hash": digest, "sfx_cue": "door slam"}})
    try:
        asset = next(a for a in client.get(f"/api/projects/{pid}/gallery").json()["assets"]
                     if a["hash"] == digest)
        assert all("regen" not in u for u in asset["used_by"])
        r = client.post(f"/api/projects/{pid}/gallery/{digest}/regenerate")
        assert r.status_code == 422
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"sfx_hash": None, "sfx_cue": ""}})


def test_a_script_row_carries_its_cg_so_the_view_can_show_it(client, project):
    """`has_image` says there is one; the row needs the hash itself to render a
    thumbnail without a fetch per row."""
    from app.services import assets as asset_svc

    pid, say = project["id"], project["say"]
    digest = asset_svc.put(b"a cg on a row", kind="img", ext="png")
    client.patch(f"/api/projects/{pid}/nodes/{say}",
                 json={"data": {"cg": True, "image_hash": digest}})
    try:
        rows = client.post(f"/api/projects/{pid}/script", json={"choices": {}}).json()["rows"]
        row = next(r for r in rows if r["node_id"] == say)
        assert row["has_image"] is True
        assert row["image_hash"] == digest
    finally:
        client.patch(f"/api/projects/{pid}/nodes/{say}",
                     json={"data": {"image_hash": None, "cg": False}})


def test_a_batch_cg_keeps_every_candidate_as_a_take(client, project, monkeypatch):
    """The point of rendering four is choosing one, so all four have to survive
    as takes with the first on the node -- a batch that kept only its first
    image would be four times the GPU time for exactly the old result."""
    from app.services import assets as asset_svc, images

    pid = project["id"]
    # Its own node: the takes are permanent, and hanging four of them off the
    # fixture's shared say node would leak into every test after this one.
    node_id = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "say", "data": {"speaker": "aiko", "text": "Four ways to shoot this.",
                                "cg": True, "cg_camera": "close-up"}}).json()["id"]
    digests = [asset_svc.put(f"candidate {i}".encode(), kind="img", ext="png")
               for i in range(4)]
    seen: dict = {}

    async def fake_run(workflow, on_progress=None, meta=None, meta_fn=None):
        seen["batch_size"] = next(n["inputs"]["batch_size"] for n in workflow.values()
                                  if "batch_size" in (n.get("inputs") or {}))
        seen["metas"] = [meta_fn(i) if meta_fn else meta for i in range(len(digests))]
        return digests

    monkeypatch.setattr(images, "run_and_store", fake_run)
    try:
        r = client.post(f"/api/projects/{pid}/images/cg",
                        json={"node_id": node_id, "batch": 4})
        job = _await_job(client, r.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        assert seen["batch_size"] == 4
        assert job["result"]["count"] == 4

        node = _node(client, pid, node_id)
        takes = [v for v in node["variants"] if v["kind"] == "image"]
        assert [v["asset_hash"] for v in takes] == digests
        # The node shows the first, and that is the take marked active -- the
        # strip highlighting a different image from the one on the node is the
        # bug this asserts against.
        assert node["data"]["image_hash"] == digests[0]
        assert node["active_variant"] == takes[0]["id"]
        # Position is recorded because the seed alone does not identify one
        # image of a batch: ComfyUI draws the whole batch's noise together.
        assert [v["payload"]["batch_index"] for v in takes] == [0, 1, 2, 3]
        assert seen["metas"][2]["batch_index"] == 2
    finally:
        client.delete(f"/api/projects/{pid}/nodes/{node_id}")


def test_a_cg_can_start_from_the_previous_lines_picture(client, project, monkeypatch):
    """Two shots of one moment with something changed: the earlier line's CG
    is the sampler's starting point, the result is a take on the later line,
    and the earlier line keeps its own picture."""
    from app.services import assets as asset_svc, images

    pid = project["id"]
    earlier_image = asset_svc.put(b"the first shot", kind="img", ext="png")
    changed = asset_svc.put(b"the same shot, one thing changed", kind="img", ext="png")
    earlier = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "narration", "data": {"text": "She turns.", "cg": True,
                                      "cg_hold": True, "image_hash": earlier_image}}).json()["id"]
    later = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "say", "connect_from": earlier,
        "data": {"speaker": "aiko", "text": "And smiles.", "cg": True}}).json()["id"]
    seen: dict = {}

    async def fake_stage(asset_hash, name):
        seen["staged"] = asset_hash
        return name

    async def fake_run(workflow, on_progress=None, meta=None, meta_fn=None):
        seen["meta"] = meta_fn(0) if meta_fn else meta
        return [changed]

    monkeypatch.setattr(images, "stage_asset", fake_stage)
    monkeypatch.setattr(images, "run_and_store", fake_run)
    try:
        r = client.post(f"/api/projects/{pid}/images/refine",
                        json={"node_id": later, "source_hash": earlier_image, "denoise": 0.5})
        job = _await_job(client, r.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        assert seen["staged"] == earlier_image

        node = _node(client, pid, later)
        assert node["data"]["image_hash"] == changed
        takes = [v for v in node["variants"] if v["kind"] == "image"]
        assert [t["payload"]["refined_from"] for t in takes] == [earlier_image]
        # The earlier line is untouched: still its own picture, still held.
        before = _node(client, pid, earlier)
        assert before["data"]["image_hash"] == earlier_image
        assert before["data"]["cg_hold"] is True
        assert not [v for v in before["variants"] if v["kind"] == "image"]
    finally:
        client.delete(f"/api/projects/{pid}/nodes/{later}")
        client.delete(f"/api/projects/{pid}/nodes/{earlier}")


def test_an_unbatched_cg_is_one_render_and_one_take(client, project, monkeypatch):
    """The overnight fill and the stale sweep both come through here and pass
    no batch. They must stay at one image per node."""
    from app.services import assets as asset_svc, images

    pid = project["id"]
    node_id = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "say", "data": {"speaker": "aiko", "text": "Just the one.",
                                "cg": True, "cg_camera": "wide shot"}}).json()["id"]
    digest = asset_svc.put(b"the only candidate", kind="img", ext="png")
    seen: dict = {}

    async def fake_run(workflow, on_progress=None, meta=None, meta_fn=None):
        seen["batch_size"] = next(n["inputs"]["batch_size"] for n in workflow.values()
                                  if "batch_size" in (n.get("inputs") or {}))
        seen["meta"] = meta_fn(0) if meta_fn else meta
        return [digest]

    monkeypatch.setattr(images, "run_and_store", fake_run)
    try:
        r = client.post(f"/api/projects/{pid}/images/cg", json={"node_id": node_id})
        job = _await_job(client, r.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        assert seen["batch_size"] == 1
        # No batch, no batch bookkeeping: a single take records seed and prompt
        # exactly as it always did.
        assert "batch_index" not in seen["meta"]
        node = _node(client, pid, node_id)
        takes = [v for v in node["variants"] if v["kind"] == "image"]
        assert len(takes) == 1
        assert "batch_index" not in takes[0]["payload"]
    finally:
        client.delete(f"/api/projects/{pid}/nodes/{node_id}")
