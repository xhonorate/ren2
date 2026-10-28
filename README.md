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
