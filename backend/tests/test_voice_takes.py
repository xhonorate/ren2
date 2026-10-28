"""A voice re-take rolls several seeds and keeps every one as a take.

The first voicing is one take -- most reads are fine. Asking again means the
read was wrong, so the job voices the line N times at N fresh seeds, puts the
first on the node and leaves the rest in the take strip to pick from.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="vn_takes_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    from app.jobs import handlers
    from app.jobs.queue import JobContext
    from app.services import tts

    calls: list[dict] = []

    async def fake_synthesize(text, character, emotion, intensity, seed=0,
                              on_progress=None, delivery=""):
        calls.append({"seed": seed, "delivery": delivery, "text": text})
        digest = f"{len(calls):064x}"
        return {"hash": digest, "raw_hash": digest, "encoded": True, "seed": seed,
                "resolved": tts.resolve_emotion(emotion, intensity, character, delivery),
                "voice_path": f"audio/voice/say_{digest[:12]}.ogg"}

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)

    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "Takes"}).json()["id"]
        client.post(f"/api/projects/{pid}/characters",
                    json={"display_name": "Aiko", "age": 22, "id": "aiko"})
        node = client.post(f"/api/projects/{pid}/nodes", json={
            "type": "say", "data": {"speaker": "aiko", "text": "I waited.",
                                    "delivery": "trails off"}}).json()

        def run(payload):
            ctx = JobContext("job", pid, "tts.line", {"node_id": node["id"], **payload},
                             node["id"])
            return asyncio.run(handlers.synthesize_line(ctx))

        yield {"run": run, "calls": calls, "node_id": node["id"]}
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]


def _node(node_id):
    from app import store
    return store.get_node(node_id)


def test_first_voicing_is_a_single_take(env):
    env["run"]({"seed": 0})

    assert len(env["calls"]) == 1
    assert env["calls"][0]["seed"] == 0
    # the writer's delivery note reaches synthesis
    assert env["calls"][0]["delivery"] == "trails off"
    node = _node(env["node_id"])
    assert len(node.variants) == 1
    assert "takes" not in node.variants[0].payload


def test_a_retake_rolls_distinct_seeds_and_keeps_them_all(env):
    env["run"]({"seed": 0})
    out = env["run"]({"seed": 0, "takes": 3})

    retake_seeds = [c["seed"] for c in env["calls"][1:]]
    assert len(retake_seeds) == 3
    assert len(set(retake_seeds)) == 3 and 0 not in retake_seeds
    assert len(out["takes"]) == 3

    node = _node(env["node_id"])
    assert len(node.variants) == 4                  # the original take is kept
    new = node.variants[1:]
    assert [v.payload["take_index"] for v in new] == [0, 1, 2]
    assert all(v.payload["takes"] == 3 for v in new)
    assert [v.meta.seed for v in new] == retake_seeds
    # the first new take is the one on the node, and the active variant agrees
    assert node.data["voice_hash"] == new[0].asset_hash
    assert node.active_variant == new[0].id
    assert node.audio_status.value == "ready"


def test_takes_are_capped(env):
    from app.jobs.handlers import MAX_VOICE_TAKES
    env["run"]({"takes": 99})
    assert len(env["calls"]) == MAX_VOICE_TAKES
