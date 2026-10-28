# vn — AI-authored visual novels

Read `DESIGN.md` first; it is the contract. Skills live in `.claude/skills/`
and are the repeatable process (`/vn-new-project` → … → `/vn-build`).

## Conventions
- Projects live in `projects/<slug>/`; `examples/demo` is the fixture and must
  always pass `python -m vn validate examples/demo` and `python -m pytest -q`.
- Every writing step runs `python -m vn pack <project> <scene>` and works from
  that pack only. Do not rely on anything remembered from earlier in a session.
- Use only ids from the project's `vocab.yaml`. If a scene needs something the
  vocab lacks, add the vocab entry (with its render mapping) first, then use it.
- After writing or editing any scene: `python -m vn validate <project>`. Fix
  every error before moving on.
- Characters are adults. Content follows `premise.md`'s rating. Explicit prose
  is never written here; it goes through the `nsfw/` split (DESIGN.md §7).
- Human checkpoints are exactly: premise, cast, canon art, arcs, outline, the
  first two scenes. Do not ask for approval elsewhere; leave notes in
  `projects/<slug>/NOTES.md` instead.
- Ren'Py output is generated; never hand-edit `build/`.
- The job runner (`runner/`, phase 2) is the only code that talks to ComfyUI,
  TTS or trainers. It runs on the GPU machine; cloud sessions never try to
  reach it.

## Commands
```
python -m vn new projects/<slug>
python -m vn new-character projects/<slug> <id>
python -m vn status projects/<slug>
python -m vn validate projects/<slug> [scene ...]
python -m vn pack projects/<slug> <scene>
python -m vn lines projects/<slug> [scene ...]     # TTS job list
python -m vn cgs projects/<slug> [scene ...]       # CG job list
python -m vn build projects/<slug> [--template <renpy project>]
python -m pytest -q
```
