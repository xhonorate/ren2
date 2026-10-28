"""SQLite store.

A single-user tool, but a big JSON file stops working once you have thousands
of nodes with generation history (doc S7). Assets live on disk, content
addressed; the DB only holds hashes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(os.environ.get("VN_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
DB_PATH = ROOT / "vn.sqlite3"
ASSET_DIR = ROOT / "assets"
BUILD_DIR = ROOT / "builds"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    type          TEXT NOT NULL,
    data          TEXT NOT NULL DEFAULT '{}',
    x             REAL NOT NULL DEFAULT 0,
    y             REAL NOT NULL DEFAULT 0,
    parent_id     TEXT,
    pinned_label  INTEGER NOT NULL DEFAULT 0,
    slug          TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    text_hash     TEXT NOT NULL DEFAULT '',
    text_status   TEXT NOT NULL DEFAULT 'none',
    image_status  TEXT NOT NULL DEFAULT 'none',
    audio_status  TEXT NOT NULL DEFAULT 'none',
    variants      TEXT NOT NULL DEFAULT '[]',
    active_variant TEXT,
    orphaned      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_id);

CREATE TABLE IF NOT EXISTS edges (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    source        TEXT NOT NULL,
    target        TEXT NOT NULL,
    source_handle TEXT NOT NULL DEFAULT 'out',
    label         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_edges_project ON edges(project_id);

CREATE TABLE IF NOT EXISTS characters (
    id         TEXT NOT NULL,
    project_id TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS locations (
    id         TEXT NOT NULL,
    project_id TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS templates (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    body       TEXT NOT NULL DEFAULT '',
    params     TEXT NOT NULL DEFAULT '{}',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_templates_key ON templates(project_id, key, version);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0,
    label       TEXT NOT NULL DEFAULT '',
    node_id     TEXT,
    payload     TEXT NOT NULL DEFAULT '{}',
    result      TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS summaries (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    upstream_hash TEXT NOT NULL,
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_hash ON summaries(project_id, upstream_hash);

CREATE TABLE IF NOT EXISTS assets (
    hash       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    ext        TEXT NOT NULL DEFAULT 'png',
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Roleplay mode. Deliberately its own pair of tables with no foreign key into
-- nodes: a conversation is not a scene and must never be walked, linearized,
-- staled or compiled. The one bridge is an explicit, one-way port that builds
-- fresh nodes from a finished conversation (services/roleplay.port_to_scene).
CREATE TABLE IF NOT EXISTS rp_conversations (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    -- The character whose history this conversation leads in. `cast` holds any
    -- others in the scene, and it appears in each of their histories too.
    character_id   TEXT NOT NULL,
    cast           TEXT NOT NULL DEFAULT '[]',
    title          TEXT NOT NULL DEFAULT '',
    scene_context  TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    -- Set once the author edits the summary by hand, so ending the scene again
    -- never overwrites their words with a fresh distillation.
    summary_edited INTEGER NOT NULL DEFAULT 0,
    state          TEXT NOT NULL DEFAULT 'open',   -- open | ended
    -- Kept in the list but excluded from every later scene's memory.
    hidden         INTEGER NOT NULL DEFAULT 0,
    -- Manual order. A float seeded from created_at, so dragging one row writes
    -- one row instead of renumbering the list.
    sort_key       REAL NOT NULL DEFAULT 0,
    location_id    TEXT,
    time_of_day    TEXT,
    nsfw           INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rp_conv_project ON rp_conversations(project_id, character_id);

CREATE TABLE IF NOT EXISTS rp_messages (
    id          TEXT PRIMARY KEY,
    conv_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,                  -- player | cast
    -- As written. The player's asterisk actions and bracketed directions are
    -- kept intact here; they are parsed out on the way into the prompt, not on
    -- the way into the row, so an edit can always see what was typed.
    text        TEXT NOT NULL,
    -- Swipes: the same shape as Node.variants, so regenerating never destroys
    -- the take that was there.
    takes       TEXT NOT NULL DEFAULT '[]',
    active_take TEXT,
    voice       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rp_msg_conv ON rp_messages(conv_id, seq);
"""

_local = threading.local()


def _ensure_dirs() -> None:
    for path in (ROOT, ASSET_DIR, BUILD_DIR, ASSET_DIR / "img",
                 ASSET_DIR / "audio", ASSET_DIR / "voice_ref", ASSET_DIR / "sheets"):
        path.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        _ensure_dirs()
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def init_db() -> None:
    _ensure_dirs()
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> None:
    conn = connect()
    conn.execute(sql, params)
    conn.commit()


def executemany(sql: str, seq: list[tuple]) -> None:
    conn = connect()
    conn.executemany(sql, seq)
    conn.commit()


def loads(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default if default is not None else {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else {}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
