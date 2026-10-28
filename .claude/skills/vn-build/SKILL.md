---
name: vn-build
description: Validate a vn project and compile it to a playable Ren'Py game directory; report what art and audio is still placeholder.
---

# /vn-build <project> [--template <renpy project>]

1. `python -m vn validate projects/<slug>`; fix or report every error.
2. `python -m vn build projects/<slug>` (add `--template` if the human has a
   launcher-created Ren'Py project to borrow `gui/` and `screens.rpy` from;
   without it Ren'Py's default layout is used).
3. Report: scene count, how many sprite images / backgrounds / CGs / voice
   lines are real vs. placeholder (compare `vn lines` and `vn cgs` output and
   the image declarations in `build/game/characters.rpy` against the files).
4. Tell the human how to run it: open the Ren'Py launcher, add
   `projects/<slug>/build` as a projects directory, Launch Project.
