---
name: vn-review-scene
description: Voice-check a written vn scene against the character sheets with fresh context, fix drift, and append the scene's continuity entry to ledger.yaml. Run after every /vn-write-scene.
---

# /vn-review-scene <project> <scene id>

Read `DESIGN.md` §6. Read `scenes/<id>.yaml`, each present character's
`sheet.md` (Voice section especially), `style.md`, and the previous ledger
entry (`python -m vn pack` shows it). If there is an nsfw fill, read it too.

1. **Voice check.** For each character, list their lines alone, out of
   context, and ask of each: could only this character say it? Does it break
   a "never says"? Does it match the register? Fix in place; keep a count.
2. **Style check** against `style.md`'s checkable rules; fix.
3. **Continuity check** against the ledger: facts, who knows what, outfit,
   location, marks, how characters address each other. Fix the scene, never
   the ledger, unless the scene is deliberately changing state.
4. For nsfw fills: check continuity and voice only (DESIGN.md §7). Do not
   rewrite the explicit prose; if it breaks a fact, note the exact problem in
   `nsfw/<ref>.md` under `## Review` and in `NOTES.md` for the human.
5. Append the ledger entry (format in DESIGN.md §6): plot summary in 3–5
   sentences, then per character `knows / hiding / address / relationship /
   outfit / last_seen`, `flags_set`, `affection_delta` (sum of the scene's
   deltas on the default path; note branch-dependent ones in `threads`), and
   open `threads`. The character section is the important half.
6. `python -m vn validate projects/<slug>` must be clean.
7. Write one line to `projects/<slug>/NOTES.md`: scene id, number of lines
   changed, anything the human should look at.
