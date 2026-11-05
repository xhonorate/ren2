# vn

A framework for producing a complete anime-style visual novel / dating sim
with minimal human input. Claude designs, writes, directs and reviews; local
models render, voice and (where needed) write the explicit parts; Ren'Py runs
the result. See [DESIGN.md](DESIGN.md).

## Status

Phase 1 (this repo today): the project format, the validator, the context
packs, the Ren'Py compiler, and the authoring skills. Works anywhere Claude
Code runs, no GPU needed.

Phase 2 (built on the GPU machine): the job runner for ComfyUI, TTS, LoRA and
voice fine-tuning, the image review loop, and the `/vn-art`, `/vn-direct`,
`/vn-voice`, `/vn-setup` skills. See `docs/`.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q
python -m vn validate examples/demo
python -m vn build examples/demo          # -> examples/demo/build/game
```

Open `examples/demo/build` with the Ren'Py launcher (add it as a project
directory) and it plays with placeholder art. Pass `--template <path to a
launcher-created project>` to borrow real `gui/` and `screens.rpy`.

Then, in Claude Code: `/vn-new-project` and follow the skills in order.

## The review console

```bash
python -m runner                   # http://localhost:8000
python -m runner --lan --pin 4821  # also reachable from your phone on the same Wi-Fi
```

Board, script reader with per-line notes and "Revise with Claude", cast and
art review, CG review, voice takes, jobs, setup and training wizards. Copy
`runner/config.example.yaml` to `runner/config.yaml` and point it at
ComfyUI, GPT-SoVITS, kohya and your checkpoints (the Setup page edits it
too). See DESIGN.md §12.
