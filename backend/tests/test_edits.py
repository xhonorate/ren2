"""API tests for the batch of authoring-tool edits.

Own project fixture so this file stays independent of the others. Covers:
the story-variable registry, choice-option effects, delete-cascade + undo,
generation rollback/reapply, and the character-card / continuation-context
prompt changes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def client():
    tmp = tempfile.mkdtemp(prefix="vn_edits_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def project(client):
    pid = client.post("/api/projects", json={"name": "Edits"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})
    client.post(f"/api/projects/{pid}/characters",
                json={"display_name": "You", "age": 21, "is_player": True, "id": "pc"})
    client.post(f"/api/projects/{pid}/locations", json={"name": "Rooftop", "tags": "rooftop"})

    def add(ntype, data, connect_from=None, handle="out"):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": connect_from,
            "connect_handle": handle}).json()

    scene = add("scene", {"title": "Rooftop", "background": "rooftop", "brief": "Aiko deflects."})
    say = add("say", {"speaker": "aiko", "text": "You came.", "expression": "looking_away",
                      "emotion": "melancholy", "intensity": 0.6}, scene["id"])
    narr = add("narration", {"text": "The door sticks."}, say["id"])
    return {"id": pid, "scene": scene["id"], "say": say["id"], "narr": narr["id"]}


def _await_job(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish")


# --------------------------------------------------------------------------
# item 9 -- story variables
# --------------------------------------------------------------------------

def test_variables_registry_merges_declared_and_inferred(client, project):
    pid = project["id"]
    # A Set node introduces `affinity` without it being declared.
    client.post(f"/api/projects/{pid}/nodes", json={
        "type": "set", "connect_from": project["narr"],
        "data": {"assignments": [{"var": "affinity", "op": "+=", "value": "1"}]}})

    listed = client.get(f"/api/projects/{pid}/variables").json()["variables"]
    inferred = next(v for v in listed if v["name"] == "affinity")
    assert inferred["derived"] is True and inferred["type"] == "int"

    # Declaring it flips derived off and lets a default ride along.
    client.put(f"/api/projects/{pid}/variables", json={"variables": [
        {"name": "affinity", "type": "int", "default": "2", "note": "warmth"}]})
    listed = client.get(f"/api/projects/{pid}/variables").json()["variables"]
    declared = next(v for v in listed if v["name"] == "affinity")
    assert declared["derived"] is False and declared["default"] == "2"


def test_variables_registry_lists_stat_instances(client, project):
    """`affection_aiko` exists only because the stat schema implies it -- nobody
    ever typed it -- so without it in this list every picker that gates on a
    stat would come up empty."""
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={"character_stats": [
        {"id": "affection", "name": "Affection", "type": "int", "default": "0"}]})

    listed = client.get(f"/api/projects/{pid}/variables").json()["variables"]
    stat = next(v for v in listed if v["name"] == "affection_aiko")
    assert stat["source"] == "stat" and stat["derived"] is True
    assert stat["stat_id"] == "affection" and stat["character_id"] == "aiko"
    assert "Aiko" in stat["label"] and "Affection" in stat["label"]
    # One entry per character, and the player counts.
    assert sum(1 for v in listed if v.get("source") == "stat") == 2

    client.patch(f"/api/projects/{pid}", json={"character_stats": []})


def test_choice_effects_compile_and_seed_a_default(client, project):
    pid = project["id"]
    graph = client.get(f"/api/projects/{pid}/graph").json()
    setnode = next(n for n in graph["nodes"] if n["type"] == "set")
    choice = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "choice", "connect_from": setnode["id"],
        "data": {"prompt": "?", "options": [
            {"id": "o1", "label": "press",
             "effects": [{"var": "pressed", "op": "=", "value": "true"}]},
            {"id": "o2", "label": "wait", "effects": []},
        ]}}).json()
    tail = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "narration", "connect_from": choice["id"], "connect_handle": "o1",
        "data": {"text": "You press."}}).json()

    compiled = client.get(f"/api/projects/{pid}/compile").json()
    scenes = "\n".join(v for k, v in compiled["files"].items() if "/scenes/" in k)
    assert "$ pressed = True" in scenes
    assert "default pressed = False" in compiled["files"]["game/generated/flags.rpy"]

    preview = client.get(f"/api/projects/{pid}/preview").json()
    menu = next(op for block in preview["blocks"].values() for op in block
                if op.get("op") == "menu")
    press = next(o for o in menu["options"] if o["id"] == "o1")
    assert press["effects"] == [{"var": "pressed", "op": "=", "value": "true"}]
    assert preview["flags"].get("pressed") is False


def test_picked_gates_survive_the_api_round_trip(client):
    """A gate the author picks has to come back off the wire.

    `update_node` re-validates the merged data through the node's payload
    model, and pydantic drops what the model does not declare -- so a field the
    compiler reads but the model omits saves, echoes back empty and vanishes
    from the inspector a frame after it was added. The compiler tests build
    Node objects directly and never touch this path, which is how choice
    `requires` and both condition-branch fields stayed missing.
    """
    pid = client.post("/api/projects", json={"name": "Gates"}).json()["id"]
    gate = {"stat": "affection", "character": "aiko", "op": ">=", "value": "20"}
    bump = {"var": "affection_aiko", "op": "+=", "value": "5"}

    cond = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "condition", "data": {"branches": [
            {"id": "b1", "expr": "", "requires": [gate], "effects": [bump]}]}}).json()
    assert cond["data"]["branches"][0]["requires"] == [gate]
    assert cond["data"]["branches"][0]["effects"] == [bump]

    # The flicker path: the inspector replaces its node with this response.
    patched = client.patch(f"/api/projects/{pid}/nodes/{cond['id']}", json={
        "data": {"branches": [
            {"id": "b1", "expr": "flag", "requires": [gate], "effects": [bump]},
            {"id": "b2", "expr": ""},
        ]}}).json()
    assert patched["data"]["branches"][0]["requires"] == [gate]
    assert patched["data"]["branches"][0]["effects"] == [bump]
    assert patched["data"]["branches"][1]["requires"] == []

    choice = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "choice", "data": {"prompt": "?", "options": [
            {"id": "o1", "label": "press"}]}}).json()
    opt = client.patch(f"/api/projects/{pid}/nodes/{choice['id']}", json={
        "data": {"options": [
            {"id": "o1", "label": "press", "requires": [gate]}]}}).json()
    assert opt["data"]["options"][0]["requires"] == [gate]


# --------------------------------------------------------------------------
# item 12 -- delete a node and everything below it
# --------------------------------------------------------------------------

def test_delete_cascade_then_undo_restores_the_ids(client):
    pid = client.post("/api/projects", json={"name": "Cascade"}).json()["id"]

    def add(ntype, data, cf=None, h="out"):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": cf, "connect_handle": h}).json()

    s = add("scene", {"title": "S"})
    a = add("narration", {"text": "a"}, s["id"])
    b = add("narration", {"text": "b"}, a["id"])
    c = add("narration", {"text": "c"}, b["id"])

    r = client.delete(f"/api/projects/{pid}/nodes/{b['id']}?cascade=true")
    assert set(r.json()["removed"]) == {b["id"], c["id"]}
    ids = {n["id"] for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"]}
    assert ids == {s["id"], a["id"]}

    # The frontend's undo path: restore the captured nodes + edges verbatim.
    client.post(f"/api/projects/{pid}/nodes/restore", json={
        "nodes": [
            {"id": b["id"], "type": "narration", "data": {"text": "b"}},
            {"id": c["id"], "type": "narration", "data": {"text": "c"}},
        ],
        "edges": [
            {"id": "e_ab", "source": a["id"], "target": b["id"], "source_handle": "out"},
            {"id": "e_bc", "source": b["id"], "target": c["id"], "source_handle": "out"},
        ],
    })
    ids = {n["id"] for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"]}
    assert ids == {s["id"], a["id"], b["id"], c["id"]}


# --------------------------------------------------------------------------
# item 10 -- generation undo / redo
# --------------------------------------------------------------------------

def _fake_two_pass(monkeypatch):
    from app.clients import lmstudio
    from app.services import generation

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        if json_schema is None:
            return 'AIKO: "Take the stairs next time."'
        return json.dumps({"scene_title": "later", "nodes": [
            {"type": "say", "speaker": "aiko", "text": "Take the stairs next time.",
             "expression": "neutral", "emotion": "cold", "intensity": 0.3,
             "delivery": "", "shot_hint": ""}]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)


def test_generation_rollback_and_reapply(client, monkeypatch):
    pid = client.post("/api/projects", json={"name": "GenUndo"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})

    def add(ntype, data, cf=None):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": cf}).json()

    s = add("scene", {"title": "S"})
    a = add("say", {"speaker": "aiko", "text": "You came."}, s["id"])
    tail = add("narration", {"text": "end."}, a["id"])

    _fake_two_pass(monkeypatch)
    job = _await_job(client, client.post(
        f"/api/projects/{pid}/generate/scene", json={"anchor_id": a["id"]}).json()["job_id"])
    assert job["status"] == "done", job.get("error")
    desc = job["result"]
    assert desc["anchor_id"] == a["id"] and desc["inserted"]

    after_gen = client.get(f"/api/projects/{pid}/graph").json()
    assert any(n["data"].get("text") == "Take the stairs next time."
               for n in after_gen["nodes"])

    # rollback -> the generated line is off the live graph, tail is reconnected
    client.post(f"/api/projects/{pid}/generate/rollback", json=desc)
    g = client.get(f"/api/projects/{pid}/graph").json()
    assert not any(n["data"].get("text") == "Take the stairs next time." for n in g["nodes"])
    assert any(e["source"] == a["id"] and e["target"] == tail["id"] for e in g["edges"])

    # reapply -> back to the post-generation state
    client.post(f"/api/projects/{pid}/generate/reapply", json=desc)
    g = client.get(f"/api/projects/{pid}/graph").json()
    assert any(n["data"].get("text") == "Take the stairs next time." for n in g["nodes"])


# --------------------------------------------------------------------------
# items 5 / 13 / 15 -- prompt assembly
# --------------------------------------------------------------------------

def test_character_card_uses_composed_appearance_and_new_fields(client):
    pid = client.post("/api/projects", json={"name": "Cards"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})
    aiko = client.get(f"/api/projects/{pid}/characters").json()[0]
    aiko["appearance"]["hair_color"] = {"label": "platinum blonde", "tag": "blonde hair"}
    aiko["appearance"]["build"] = {"label": "tall and lean", "tag": "slender"}
    aiko["personality"]["speech_style"] = "Short sentences."
    aiko["personality"]["loves"] = "the sea in winter"
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)

    s = client.post(f"/api/projects/{pid}/nodes",
                    json={"type": "scene", "data": {"title": "S"}}).json()
    a = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "say", "connect_from": s["id"],
        "data": {"speaker": "aiko", "text": "You came."}}).json()

    ctx = client.get(f"/api/projects/{pid}/context-preview?node_id={a['id']}").json()
    assert "platinum blonde" in ctx["system"]
    assert "tall and lean" in ctx["system"]
    assert "the sea in winter" in ctx["system"]
    assert "Short sentences." in ctx["system"]


def test_continue_is_framed_as_a_continuation_not_a_restart(client):
    pid = client.post("/api/projects", json={"name": "Continue"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})

    def add(ntype, data, cf=None):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": cf}).json()

    s = add("scene", {"title": "S", "brief": "Aiko admits she is leaving."})
    a = add("say", {"speaker": "aiko", "text": "You came."}, s["id"])

    at_scene = client.get(f"/api/projects/{pid}/context-preview?node_id={s['id']}").json()
    at_line = client.get(f"/api/projects/{pid}/context-preview?node_id={a['id']}").json()

    # Continuing from a written line: fenced tail, explicit "do not restart".
    assert "END OF THE SCENE SO FAR" in at_line["system"]
    assert "do not restart" in at_line["user"].lower() or "not recap" in at_line["user"].lower()
    # Starting from the empty scene: no such fence.
    assert "END OF THE SCENE SO FAR" not in at_scene["system"]


def test_a_superseded_default_is_upgraded_but_an_edited_value_is_not(client):
    """Settings seed once and are never re-seeded, so raising a default here
    does nothing for a project that already exists. This upgrades the two that
    were actively harmful -- and only while they still hold the old default."""
    from app import store
    from app.defaults import DEFAULT_SETTINGS, SUPERSEDED_SETTINGS

    old_budget = SUPERSEDED_SETTINGS["context_token_budget"][0]
    old_policy = SUPERSEDED_SETTINGS["prose_pc_policy"][0]

    store.set_setting("context_token_budget", old_budget)
    store.set_setting("prose_pc_policy", "Mine. He never shuts up.")

    changed = store.migrate_superseded_settings()

    assert "context_token_budget" in changed
    assert store.get_setting("context_token_budget") == DEFAULT_SETTINGS["context_token_budget"]
    # Edited: left exactly as the author wrote it.
    assert "prose_pc_policy" not in changed
    assert store.get_setting("prose_pc_policy") == "Mine. He never shuts up."

    # And it is idempotent -- a second run has nothing left to do.
    store.set_setting("prose_pc_policy", old_policy)
    assert store.migrate_superseded_settings() == ["prose_pc_policy"]
    assert store.migrate_superseded_settings() == []


# --------------------------------------------------------------------------
# continuation suggestions, end to end
# --------------------------------------------------------------------------

def test_continuations_land_on_the_tail_node_and_steer_the_next_run(client, monkeypatch):
    """The prose pass ends a stretch that has not reached a decision with a few
    directions. They must never become nodes, and picking one must reach the
    next prompt."""
    pid = client.post("/api/projects", json={"name": "Continuations"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})
    scene = client.post(f"/api/projects/{pid}/nodes",
                        json={"type": "scene", "data": {"title": "S"}}).json()

    from app.clients import lmstudio
    from app.services import generation

    seen: list[str] = []

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        seen.append(messages[-1]["content"])
        if json_schema is None:
            return ('AIKO: "You always take the long way."\n\n'
                    "CONTINUE:\n"
                    "- She finally asks why you came\n"
                    "- You let the silence run\n")
        return json.dumps({"scene_title": "later", "nodes": [
            {"type": "say", "speaker": "aiko",
             "text": "You always take the long way."}]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    job = client.post(f"/api/projects/{pid}/generate/scene",
                      json={"anchor_id": scene["id"]}).json()["job_id"]
    result = _await_job(client, job)["result"]

    assert result["continuations"] == ["She finally asks why you came",
                                       "You let the silence run"]

    nodes = client.get(f"/api/projects/{pid}/graph").json()["nodes"]
    made = [n for n in nodes if n["data"].get("_gen")]
    says = [n for n in made if n["type"] == "say"]

    # The CONTINUE block is never story content: one spoken line, and no stray
    # narration node holding a bullet.
    assert len(says) == 1
    assert "CONTINUE" not in says[0]["data"]["text"]
    assert not any(n["type"] == "narration" and n["data"]["text"].lstrip().startswith("-")
                   for n in made)
    # The other node is reconcile_staging putting Aiko on stage before she
    # speaks -- nothing upstream had.
    assert [n["type"] for n in made if n["type"] != "say"] == ["stage"]

    # They hang off the last node of the chain, which is where "carry on from
    # here" belongs.
    assert says[0]["data"]["_continuations"] == result["continuations"]

    # Picking one reaches the next prose prompt as a direction, not an order.
    job2 = client.post(f"/api/projects/{pid}/generate/scene", json={
        "anchor_id": says[0]["id"],
        "continuation": "She finally asks why you came"}).json()["job_id"]
    _await_job(client, job2)
    assert any("Take the scene in this direction: She finally asks why you came" in s
               for s in seen)


def test_regenerate_block_starts_where_the_generation_did(client, monkeypatch):
    """Regenerating from whichever node is selected would redo only the tail."""
    pid = client.post("/api/projects", json={"name": "Blocks"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})
    scene = client.post(f"/api/projects/{pid}/nodes",
                        json={"type": "scene", "data": {"title": "S"}}).json()

    from app.clients import lmstudio
    from app.services import generation

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        if json_schema is None:
            return 'AIKO: "One."\n\nAIKO: "Two."'
        return json.dumps({"scene_title": "s", "nodes": [
            {"type": "say", "speaker": "aiko", "text": "One."},
            {"type": "say", "speaker": "aiko", "text": "Two."},
        ]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    job = client.post(f"/api/projects/{pid}/generate/scene",
                      json={"anchor_id": scene["id"]}).json()["job_id"]
    _await_job(client, job)

    graph = client.get(f"/api/projects/{pid}/graph").json()
    nodes, edges = graph["nodes"], graph["edges"]
    last = next(n for n in nodes if n["data"].get("text") == "Two.")

    # Asked from the *last* node, it still rewinds to where the block began.
    r = client.post(f"/api/projects/{pid}/generate/regenerate-block",
                    json={"node_id": last["id"]})
    assert r.status_code == 200
    head = r.json()["head"]
    assert head != last["id"]

    # "Where it began" means the topological root of the generation, not the
    # first line: reconcile_staging put a stage node in front of Aiko's first
    # line, and that is genuinely the start of the block.
    members = {n["id"] for n in nodes if n["data"].get("_gen")}
    assert head in members
    assert not any(e["target"] == head and e["source"] in members for e in edges)


def test_regenerate_block_refuses_a_hand_written_node(client):
    pid = client.post("/api/projects", json={"name": "HandWritten"}).json()["id"]
    n = client.post(f"/api/projects/{pid}/nodes",
                    json={"type": "narration", "data": {"text": "mine"}}).json()
    r = client.post(f"/api/projects/{pid}/generate/regenerate-block",
                    json={"node_id": n["id"]})
    assert r.status_code == 422
    assert "regenerate-from" in r.json()["detail"]


# --------------------------------------------------------------------------
# improv: rewind parks, it never deletes
# --------------------------------------------------------------------------

def test_rewind_parks_the_subtree_and_it_comes_back(client):
    """Improv's rewind is `park-after`. Trying a different direction has to be
    safe, so the lines go to the holding area rather than the bin."""
    pid = client.post("/api/projects", json={"name": "Rewind"}).json()["id"]

    def add(ntype, data, cf=None):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": cf}).json()

    s = add("scene", {"title": "S"})
    a = add("narration", {"text": "a"}, s["id"])
    b = add("narration", {"text": "b"}, a["id"])
    c = add("narration", {"text": "c"}, b["id"])

    r = client.post(f"/api/projects/{pid}/generate/park-after",
                    json={"node_id": a["id"]})
    assert r.status_code == 200
    assert set(r.json()["parked"]) == {b["id"], c["id"]}

    # Gone from the live graph, still on the books.
    live = {n["id"] for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"]}
    assert live == {s["id"], a["id"]}
    assert {n["id"] for n in client.get(f"/api/projects/{pid}/orphans").json()} \
        == {b["id"], c["id"]}

    client.post(f"/api/projects/{pid}/orphans/restore",
                json={"node_ids": [b["id"], c["id"]]})
    live = {n["id"] for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"]}
    assert live == {s["id"], a["id"], b["id"], c["id"]}


def test_rewind_leaves_a_merge_point_another_branch_still_feeds(client):
    """Rewinding one improvised path must not tear out a later scene both
    paths reach -- that is the whole reason rejoining is safe."""
    pid = client.post("/api/projects", json={"name": "RewindMerge"}).json()["id"]

    def add(ntype, data, cf=None, h="out"):
        return client.post(f"/api/projects/{pid}/nodes", json={
            "type": ntype, "data": data, "connect_from": cf,
            "connect_handle": h}).json()

    s = add("scene", {"title": "S"})
    left = add("narration", {"text": "left"}, s["id"])
    right = add("narration", {"text": "right"}, s["id"])
    merge = add("narration", {"text": "both"}, left["id"])
    client.post(f"/api/projects/{pid}/edges",
                json={"source": right["id"], "target": merge["id"],
                      "source_handle": "out"})

    parked = client.post(f"/api/projects/{pid}/generate/park-after",
                         json={"node_id": left["id"]}).json()["parked"]

    # `right` still points at it, so it stays.
    assert merge["id"] not in parked
    live = {n["id"] for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"]}
    assert merge["id"] in live


def test_park_after_rejects_a_node_that_is_gone(client):
    pid = client.post("/api/projects", json={"name": "RewindMissing"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/generate/park-after",
                    json={"node_id": "n_nope"})
    assert r.status_code == 404


def test_preview_shows_the_steer_note_and_does_not_repeat_the_brief(client):
    """The user turn is what is specific to *this* generation.

    The brief used to be restated there verbatim, so the block read as a copy
    of the system prompt's `## Scene brief` -- while the one thing the author
    actually typed, the "Continue with a note" instruction, had no way to
    reach the preview at all.
    """
    pid = client.post("/api/projects", json={"name": "Steer"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"display_name": "Aiko", "age": 22})

    s = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "scene", "data": {"title": "S", "brief": "Aiko admits she is leaving."},
    }).json()
    a = client.post(f"/api/projects/{pid}/nodes", json={
        "type": "say", "connect_from": s["id"],
        "data": {"speaker": "aiko", "text": "You came."}}).json()

    base = f"/api/projects/{pid}/context-preview?node_id={a['id']}"
    plain = client.get(base).json()

    # The brief is carried once, under its own heading, in the system prompt.
    assert "Aiko admits she is leaving." in plain["system"]
    assert "Aiko admits she is leaving." not in plain["user"]

    steered = client.get(base + "&instruction=much shorter, and colder"
                                "&continuation=she follows him out"
                                "&branch_hint=the player stayed quiet").json()
    assert "much shorter, and colder" in steered["user"]
    assert "she follows him out" in steered["user"]
    assert "the player stayed quiet" in steered["user"]
    # Still not duplicated once steering is in play.
    assert "Aiko admits she is leaving." not in steered["user"]
    # A steer is additive: it never costs the continuation framing.
    assert "not recap" in steered["user"].lower()


# --------------------------------------------------------------------------
# per-shot CG tag switches, through the API that actually saves them
# --------------------------------------------------------------------------

def test_a_per_shot_tag_switch_survives_the_round_trip(client, project):
    """The node PATCH validates `data` through the type's pydantic model, so a
    key the model does not declare is silently dropped on save.

    That is invisible from the UI in the worst way: the optimistic update makes
    the click look like it worked, and the server echo a moment later quietly
    reverts it. `cg_forced_tags` shipped without being declared and did exactly
    that. Exercised through HTTP rather than `store.update_node`, because the
    validation that drops it is the layer a direct store write skips.
    """
    pid, nid = project["id"], project["say"]

    saved = client.patch(f"/api/projects/{pid}/nodes/{nid}", json={
        "data": {"cg_forced_tags": ["pleated skirt"],
                 "cg_hidden_tags": ["brown loafers"]}}).json()
    assert saved["data"]["cg_forced_tags"] == ["pleated skirt"]
    assert saved["data"]["cg_hidden_tags"] == ["brown loafers"]

    # And it is still there on the next read, not just in the response.
    graph = client.get(f"/api/projects/{pid}/graph").json()
    again = next(n for n in graph["nodes"] if n["id"] == nid)
    assert again["data"]["cg_forced_tags"] == ["pleated skirt"]

    # Clearing works too -- an empty list must not read as "unset" and revert.
    cleared = client.patch(f"/api/projects/{pid}/nodes/{nid}", json={
        "data": {"cg_forced_tags": [], "cg_hidden_tags": []}}).json()
    assert cleared["data"]["cg_forced_tags"] == []


def test_the_prompt_preview_reports_a_forced_tag_as_in_frame(client, project):
    """The inspector draws the switch from `out_of_frame`, so the round trip is
    only half the fix: the preview has to stop calling the tag missing, or the
    badge renders cropped again the moment the cast refetches."""
    pid, nid = project["id"], project["say"]
    aiko = next(c for c in client.get(f"/api/projects/{pid}/characters").json()
                if c["id"] == "aiko")
    aiko["outfits"] = [{"id": "u", "name": "U",
                        "tags": "school uniform, pleated skirt"}]
    aiko["default_outfit"] = "u"
    client.put(f"/api/projects/{pid}/characters/aiko", json=aiko)
    client.patch(f"/api/projects/{pid}/nodes/{nid}", json={
        "data": {"cg_camera": "close-up", "cg_forced_tags": []}})

    def out_of_frame():
        preview = client.get(
            f"/api/projects/{pid}/images/cg/preview?node_id={nid}").json()
        return preview, [t for c in preview["cast"] for t in c["out_of_frame"]]

    preview, cut = out_of_frame()
    assert "pleated skirt" in cut
    assert "pleated skirt" not in preview["positive"]

    client.patch(f"/api/projects/{pid}/nodes/{nid}", json={
        "data": {"cg_forced_tags": ["pleated skirt"]}})
    preview, cut = out_of_frame()
    assert "pleated skirt" not in cut, "still reported as cut after being forced in"
    assert "pleated skirt" in preview["positive"]
    assert preview["forced_tags"] == ["pleated skirt"]
