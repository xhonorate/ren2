from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class Vocab:
    """The finite vocabularies of a project (DESIGN.md §4)."""

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.expressions: dict[str, str] = dict(data.get("expressions") or {})
        self.poses: dict[str, dict] = dict(data.get("poses") or {})
        self.cg_poses: dict[str, dict] = dict(data.get("cg_poses") or {})
        self.shots: dict[str, str] = dict(data.get("shots") or {})
        self.lighting: dict[str, str] = dict(data.get("lighting") or {})
        self.deliveries: dict[str, dict] = dict(data.get("deliveries") or {})
        self.times: list[str] = list(data.get("times") or [])
        self.transitions: list[str] = list(data.get("transitions") or ["dissolve", "fade", "none"])
        self.positions: list[str] = list(data.get("positions") or ["left", "center", "right"])

    @classmethod
    def load(cls, path: Path) -> "Vocab":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})
