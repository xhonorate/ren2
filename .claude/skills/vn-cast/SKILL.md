---
name: vn-cast
description: Build the cast and the world for a vn project - character sheets, appearance specs, voices, locations - from the human's short descriptions, offering options for the gaps. Run after /vn-style.
---

# /vn-cast <project>

Read `DESIGN.md` §3.3–§3.6, `premise.md`, `style.md`, and
`templates/character/`.

1. Ask how many love interests (default 3 for `routes`), and for whatever the
   human already has: names, one-liners, a reference image, anything. Short
   is fine.
2. For each character, before writing the sheet, offer **three distinct
   takes** on the gaps (one paragraph each: personality hook + look + what
   their route would be about). Let the human pick or mix. Do this per
   character, not all at once.
3. Create each with `python -m vn new-character projects/<slug> <id>` and fill
   `sheet.md` completely. The **Voice** section is the most important part:
   register, tics, sentence length, what they never say, and three sample
   lines that could only be theirs. Every personality trait gets a "how it
   shows". Age 18+ always; `appearance.yaml` is validated.
4. Fill `appearance.yaml`: `base_tags` as Danbooru-style tags the anime
   checkpoint knows (see `docs/images.md`), every defining mark with its exact
   placement, 2–4 outfits with both tags and a description, a `color` for the
   name box. Also fill `voice.yaml`'s `pronunciation` for any name or term a
   TTS engine would get wrong.
5. Write `world.md`: the setting, then `## Locations` with 5–10 `### <id>`
   entries, each with `times` and a `bg_prompt`. Every outline scene must use
   one of these ids.
6. Cast chemistry pass: read all sheets together; make sure no two voices
   could be confused and that each love interest wants something the others
   don't.
7. `python -m vn validate projects/<slug>` must be clean.
8. **Checkpoint**: present the cast (one card per character: hook, three
   sample lines, look, marks, outfits) and the location list. Revise until
   approved. Next: `/vn-arcs` (and `/vn-art` on the GPU machine, which can run
   in parallel with arcs and outline).

Output: `characters/*/`, `world.md`.
