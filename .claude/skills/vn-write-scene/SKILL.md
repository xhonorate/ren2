---
name: vn-write-scene
description: Write one scene of a vn project from its context pack into scenes/<id>.yaml, with expressions, poses, deliveries, CG shot specs and choices chosen from the vocab. Handles the explicit split via nsfw/ briefs.
---

# /vn-write-scene <project> <scene id>

Read `DESIGN.md` §5 and §7. Then run and read, in full:

```
python -m vn pack projects/<slug> <scene id>
```

Work from the pack only. It contains the premise, style, the sheets of
everyone present, the route arc, the outline entry, the ledger so far, and
the vocab ids. If something you need is missing from the pack, fix the bible
file or the pack, not the scene.

## Writing

1. Plan the scene in 4–6 beats from the outline's `beats`, each with: what
   changes, who wants what, the line that lands it. Note where the CG(s) go.
2. Write the scene file. Rules:
   - Every `say` carries `expr`; change `pose` only when the body does
     something; set `delivery` whenever it is not `neutral`. Choose from the
     vocab in the pack. Never invent ids.
   - Every character's lines pass their sheet's Voice section. Re-read the
     three sample lines before writing each character's first line.
   - Stutters, cut-offs and interjections are written in the text
     (`"H-hi."`, `"I just—"`); the TTS layer handles them.
   - Narration obeys `style.md` limits (characters per line, lines per block).
   - Choices: 2–4 options, in the player's voice, each with a visible
     consequence (a different line at least). `affection` effects per
     `arcs.md`; `set` only declared flags.
   - CG: a full shot spec (`shot`, `lighting`, each character's `pose` from
     `cg_poses`, `expr`, `outfit`, `gaze`) plus a one-line `note` that says
     what the moment *is*, for the reviewer. Always `cg_end`.
   - End with `next:` matching the outline unless the scene branches.
3. For `rating: explicit` scenes: write the lead-in and the aftermath as
   normal steps; put a single `nsfw: {ref: <scene>.<n>}` step where the
   explicit section goes. Then ask the human for their direction for that
   section (unless the outline entry has `direction:`), and write
   `nsfw/<scene>.<n>.md` with the sections from DESIGN.md §7: Brief,
   Direction, Skeleton (beats + the CG specs it must hit + the step format),
   Prompt (complete, self-contained, for the local model). Leave `## Fill`
   absent; the human or `/vn-direct` adds it. Do not write explicit prose.
4. `python -m vn validate projects/<slug> <scene id>` must be clean.
5. Self-review before finishing: read the scene as the player would; cut any
   line that only restates a previous one; check each character's lines
   against their "never says"; check the line count is within ±30% of the
   outline's neighbours.

The first two scenes of a project are a **checkpoint**: show them and take
notes. After that, do not ask; continue with `/vn-review-scene`.
