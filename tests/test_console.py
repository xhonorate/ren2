"""The review console API against the demo project (no GPU services needed)."""
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from runner import app as appmod
    from runner.config import Config
    cfg = Config(appmod.Config.load.__func__(Config, tmp_path / "config.yaml"))
    cfg.path = tmp_path / "config.yaml"
    # keep job state out of the repo
    monkeypatch.setattr(appmod, "ROOT", ROOT)
    import runner.queue as q
    orig = q.Queue.__init__
    def init(self, path, lanes):
        orig(self, tmp_path / "jobs.json", lanes)
    monkeypatch.setattr(q.Queue, "__init__", init)
    return TestClient(appmod.create_app(cfg))


def test_projects_and_board(client):
    r = client.get("/api/projects"); assert r.status_code == 200
    slugs = [p["slug"] for p in r.json()]
    assert "demo" in slugs
    b = client.get("/api/projects/demo").json()
    assert len(b["board"]) == 5 and b["board"][0]["written"]
    assert b["board"][1]["missing"]["sprites"] > 0


def test_scene_items_and_notes(client):
    s = client.get("/api/projects/demo/scenes/c01_s02").json()
    assert s["written"] and s["items"][0]["kind"] == "narr"
    says = [i for i in s["items"] if i["kind"] == "say" and i.get("line")]
    assert says[0]["line"] == 1 and says[0]["speaker"] == "ren"
    notes_dir = ROOT / "examples" / "demo" / "review" / "notes"
    try:
        r = client.post("/api/projects/demo/scenes/c01_s02/notes", json={"step": 2, "kind": "line", "quote": "x", "note": "sharper"})
        assert r.status_code == 200 and r.json()[-1]["note"] == "sharper"
        nid = r.json()[-1]["id"]
        assert client.get("/api/projects/demo").json()["board"][1]["notes"] >= 1
        assert client.delete(f"/api/projects/demo/scenes/c01_s02/notes/{nid}").status_code == 200
    finally:
        shutil.rmtree(notes_dir, ignore_errors=True)


def test_yaml_roundtrip_rejects_bad_yaml(client):
    y = client.get("/api/projects/demo/scenes/c01_s01/yaml").json()["yaml"]
    assert client.put("/api/projects/demo/scenes/c01_s01/yaml", json={"yaml": "id: [unclosed"}).status_code == 400
    r = client.put("/api/projects/demo/scenes/c01_s01/yaml", json={"yaml": y})
    assert r.status_code == 200 and r.json()["errors"] == []
    shutil.rmtree(ROOT / "examples" / "demo" / "review", ignore_errors=True)


def test_jobs_validate_and_build(client, tmp_path):
    j = client.post("/api/jobs", json={"kind": "validate", "payload": {"project": "demo"}}).json()
    for _ in range(50):
        d = client.get(f"/api/jobs/{j['id']}").json()
        if d["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert d["status"] == "done" and d["result"]["valid"]
    assert client.post("/api/jobs", json={"kind": "nope"}).status_code == 400
    assert client.get("/api/setup").json()["checks"]


def test_file_paths_are_confined(client):
    assert client.get("/files/demo/../../DESIGN.md").status_code in (400, 404)
    assert client.get("/files/demo/outline.yaml").status_code == 200
