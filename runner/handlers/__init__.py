"""Job handlers. Each registers (kind, lane, fn); fn(job, ctx) returns a JSON-able result.

ctx has: cfg (Config), project(slug) -> vn Project, root (repo Path), queue.
"""
from __future__ import annotations

from . import comfy, kohya, local, tts


def register_all(queue) -> None:
    for mod in (local, comfy, kohya, tts):
        mod.register(queue)
