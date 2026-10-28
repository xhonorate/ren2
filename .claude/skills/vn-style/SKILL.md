---
name: vn-style
description: Calibrate the prose style for a vn project by having the human pick among generated sample passages, then write style.md. Run after /vn-new-project.
---

# /vn-style <project>

Read `premise.md` and `calibration/style-options.md` (the axes and the
standing sample set).

1. Write one short scene beat (~120 words, one character + the player, the
   premise's tone) **five ways**, each isolating one axis from
   `calibration/style-options.md` (person/tense, rhythm, interiority,
   dialogue ratio, humour). Label them A–E. Keep the content identical so only
   the style differs. Present them in one artifact or message.
2. Ask the human to pick per axis, and to mark any *lines* they dislike in any
   sample ("too purple", "he wouldn't say that"). Line-level notes matter more
   than the picks; ask for them explicitly.
3. Write `projects/<slug>/style.md` using the template headings. Every rule
   must be checkable by reading a scene ("one thought per beat", "no line over
   180 characters"), not a mood word.
4. Write one more ~150-word sample in the resulting style; ask for a final
   yes/no and line notes. Fold them in.

Revisit once after `/vn-review-scene` on the first real scene: the human's
edits to that scene are the best style data there is; turn them into rules.

Output: `style.md`. Checkpoint: the picks and the final sample.
