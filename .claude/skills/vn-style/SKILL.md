---
name: vn-style
description: Confirm or adjust the house prose style for a vn project and write style.md. Run after /vn-new-project.
---

# /vn-style <project>

`templates/project/style.md` is the **house style**, calibrated once with the
human (picks recorded at the top of `calibration/style-options.md`). A new
project already has a copy. This skill confirms it fits the premise and
adjusts it; it does not start from zero.

1. Read `premise.md` and the project's `style.md`.
2. Write one ~150-word sample in the house style, on a beat from this
   premise (one love interest + the player). Obey every rule in `style.md`.
3. Ask the human: keep the house style as is, or adjust for this project?
   Ask explicitly for *line-level* notes on the sample ("too purple", "he
   wouldn't say that"); those matter more than a verdict.
4. If adjusting: edit only the rules the notes touch, keep every rule
   checkable by reading a scene, and write a second sample. One round.
5. If the human wants a different style altogether, run the full pick from
   `calibration/style-options.md` (five axes, A/B/C each, line notes), then
   write `style.md` from the template headings.

Revisit once after `/vn-review-scene` on the first real scene: the human's
edits to that scene are the best style data there is; turn them into rules.

Output: `style.md`.
