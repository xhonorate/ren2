# Design

A framework for producing a complete anime-style visual novel / dating sim
with minimal human input: the human picks the premise, approves the cast
and the art, steers the arcs; Claude does the writing, directing and
reviewing; local models do the rendering, voicing and (where Claude won't)
the explicit prose; Ren'Py runs the result.

This document is the contract every skill and tool follows. If something
here and something in a skill disagree, this document wins; fix the skill.

## 1. Principles

1. **Files on disk are the source of truth.** A project is a directory of
   Markdown and YAML. Every step reads files and writes files. Nothing of
   value lives only in a conversation, so any step can run in a fresh
   session, and a whole game never has to fit one context window.
2. **Structure and prose are produced together.** The writer emits lines
   already tagged with expression, pose and delivery. There is no second
   model guessing structure from prose; that pass is where the old project
   lost consistency.
3. **Finite vocabularies.** Expressions, poses, shots, lighting, deliveries
   and outfits are chosen from `vocab.yaml`, never invented. Every vocab
   entry maps to something tested (a prompt fragment, a pose reference, a
   TTS setting). The models render; they do not interpret.
4. **Assemble, don't remember.** Each writing step gets a *complete* context
   pack: style, the sheets of everyone present, the outline entry, the
   continuity ledger as of the previous scene. Consistency comes from the
   pack, not from the model's memory.
5. **Few, explicit human checkpoints.** The human approves: premise → cast →
   canon art → arcs → outline → first two scenes. Everything after that runs
   unattended with review piles, not approvals.
6. **Ren'Py is a one-way build target.** `.rpy` is generated, never parsed.
7. **The explicit split.** Claude writes the brief, lead-in, aftermath and
   skeleton of an explicit scene and reviews the fill for continuity; a local
   uncensored model writes the explicit section from that brief plus the
   human's own direction. Every character is an adult.

## 2. Project layout

```
projects/<slug>/
  premise.md              §3.1  genre, audience, tone, rating, branching level, player
  style.md                §3.2  prose rules, calibrated with the human
  world.md                §3.3  setting, lore, locations (ids match vocab/locations)
  vocab.yaml              §4    the finite vocabularies for this project
  characters/<id>/
    sheet.md              §3.4  personality, voice, backstory, relationship, arc hooks
    appearance.yaml       §3.5  canonical visual spec, marks with placement, outfits
    voice.yaml            §3.6  TTS: reference clips per delivery, pronunciation
    art/                  canon/ (approved refs), sprites/<pose>_<expr>.png, lora/
  arcs.md                 §3.7  overall arc + per-character route arcs
  outline.yaml            §3.8  scene list (the story graph)
  scenes/<id>.yaml        §5    scripts
  nsfw/<scene>.<n>.md     §7    briefs for, and fills from, the local model
  includes/<name>.yaml    §5    shared step blocks spliced by `include:` (hub ladders)
  ledger.yaml             §6    continuity state at the end of each scene
  audio/voice/<scene>/<n>.ogg
  images/bg/<loc>_<time>.png, images/cg/<id>.png
  build/                  generated Ren'Py project (ignored by git)
```

`templates/project/` is the empty skeleton `/vn-new-project` copies.
`examples/demo/` is a tiny complete project that must always validate and
build; it is the compiler's fixture.

## 3. The bible files

### 3.1 premise.md
Headings, in order: Title · Logline · Genre & tone · Audience · Content
rating (`sfw` | `suggestive` | `explicit`) · Branching level (§8) · The
player (name, what they are, what they want, how they speak) · Setting in
one paragraph · What makes this one different · Things to avoid.

### 3.2 style.md
Prose rules the writer obeys: narration person and tense, sentence rhythm,
interiority budget, dialogue:narration ratio, humour level, how much is
told vs shown, line-length limits for the text box, choice phrasing, taboo
words/tics. Produced by `/vn-style` (§9) from the human's picks among
generated options, then revised once after the first real scene.

### 3.3 world.md
Setting prose plus a `## Locations` section. Each location is `### <id>`
with a one-paragraph description, the `times` it can be shown at, and a
`bg_prompt` line (tags) used to render its background plates.

### 3.4 characters/<id>/sheet.md
Headings: Name & role · One-line hook · Personality (3–5 traits with how
each *shows*) · Voice (register, tics, sentence length, what they never
say, three sample lines) · Backstory · Relationship to the player at start ·
What they want / what they fear · Route arc hooks (filled by `/vn-arcs`) ·
Boundaries (what the character would never do; rating notes).

The Voice section is the most load-bearing text in the project: the writer
and the voice-check both read it.

### 3.5 characters/<id>/appearance.yaml
```yaml
id: mira
display_name: Mira
age: 22                      # all characters are adults; validated
base_tags: "1girl, long silver hair, blue eyes, slender, pale skin"
marks:                       # defining characteristics with placement
  - {what: "small scar", where: "left eyebrow", tags: "scar on face"}
hair: {...} ; eyes: {...} ; body: {...}
outfits:
  casual:  {tags: "white blouse, blue skirt", description: "..."}
  pajamas: {tags: "...", description: "..."}
default_outfit: casual
style_tags: "anime screencap, flat color"   # project-wide override allowed
lora: {file: "mira_v1.safetensors", trigger: "m1ra", weight: 0.8}  # after training
negative_tags: ""
```
After the canon art is approved, this file is corrected to match the art,
not the other way round.

`nsfw_source` names a file in the character directory (conventionally
`nsfw.txt`) holding the human's own explicit notes: kinks, example dialogue,
nude/hidden tag sets. Claude never writes or edits it; the runner appends
it to the local model's prompt for explicit fills and to the tag set for
explicit CG jobs.

### 3.6 characters/<id>/voice.yaml
```yaml
engine: gptsovits            # per character; see docs/tts.md
model: characters/mira/voice/model/    # fine-tuned weights
reference:                   # one clip per delivery tag; mined (§10) or supplied
  neutral: ref/neutral.wav
  soft: ref/soft.wav
pronunciation:               # applied before synthesis
  Mira: "MEER-ah"
  Aldric: "ALL-drik"
```

### 3.7 arcs.md
`## Overall` (acts, the spine, the ending(s)) then `## <character id>` per
route: premise of the route, affection thresholds, 4–6 beats, the choice
that locks the route, endings.

### 3.8 outline.yaml
The story graph. Scenes are nodes; `next` and choice targets are edges.
```yaml
branching: routes           # linear | routes | branching  (§8)
start: c01_s01
scenes:
  - id: c01_s01
    title: First day
    act: 1
    route: common           # common | <character id>
    location: campus_gate
    time: morning
    present: [mira]
    rating: sfw             # sfw | suggestive | explicit
    intimacy: 0             # 0..5, see §7
    goal: "Establish the player, meet Mira, plant the scar question."
    beats: ["...", "...", "..."]
    requires: null          # condition expression, or null
    next: c01_s02           # default continuation; choices can override
    cg: ["pov_medium: Mira at the gate, wind in hair"]   # intent only; spec lives in the scene
    links: []               # targets the scene will jump/call/goto to; used for reachability until it is written
    uses: []                # includes it will splice (their edges count too), e.g. [hub_choice]
```

## 4. vocab.yaml

Every list the writer chooses from. The project template ships a default
set; the human may extend it, and each new entry needs its render mapping.

```yaml
expressions:   # sprite expressions: id -> prompt tags
  neutral: "neutral expression"
  smile: "smile"
  shy_smile: "shy, smile, blush"
  ...
poses:         # sprite poses: id -> {tags, ref}  (ref = openpose png, optional)
  idle: {tags: "standing, arms at sides"}
  arms_crossed: {tags: "standing, crossed arms"}
  ...
cg_poses:      # CG body poses, POV-framed: id -> {tags, ref}
  sitting_bed_leaning_in: {tags: "sitting, on bed, leaning forward, looking at viewer", ref: poses/sitting_bed_leaning_in.png}
shots:         # camera: id -> tags
  pov_close: "pov, close-up, looking at viewer"
  pov_medium: "pov, upper body, looking at viewer"
  pov_wide: "pov, full body"
  over_shoulder: "from behind, over shoulder"
lighting:
  day_soft: "soft lighting, daylight"
  lamp_warm: "warm lighting, lamp light, night"
  ...
deliveries:    # TTS delivery tags: id -> {description, speed, emotion_ref_required}
  neutral: {description: "even, conversational"}
  soft: {description: "quiet, gentle, close"}
  whisper: {description: "whispered"}
  excited: {...} ; shout: {...} ; tearful: {...} ; laughing: {...} ; sigh: {...}
times: [morning, afternoon, evening, night]
transitions: [dissolve, fade, none]
```

The validator rejects any scene that uses an id not in the vocab.

## 5. Scene files (`scenes/<id>.yaml`)

```yaml
id: c01_s03
title: Coffee
location: cafe
time: afternoon
present: [mira]
outfits: {mira: casual}
rating: sfw
steps:
  - narr: "The cafe is louder than it looked from outside."
  - say: mira
    expr: smile
    pose: idle          # optional; carries forward
    delivery: soft      # optional; default neutral
    text: "H-hi. You came."
  - stage: {time: evening, transition: dissolve}     # any subset of location/time/present/outfits
  - enter: {char: mira, pose: idle, expr: neutral, at: center}
  - exit: mira
  - set: {met_mira: true}
  - affection: {mira: +1}
  - cg:
      id: c01_s03_cg1
      shot: pov_medium
      lighting: lamp_warm
      characters:
        - {id: mira, pose: sitting_bed_leaning_in, expr: shy_smile, outfit: casual, gaze: at_viewer}
      note: "the moment she decides to tell him"       # for the human and the reviewer
  - cg_end: {transition: dissolve}
  - choice:
      prompt: "She's waiting."
      options:
        - text: "Tell her about the scar."
          effects: {affection: {mira: +2}}
          steps: [ ...inline steps... ]
        - text: "Change the subject."
          goto: c01_s04b
          requires: "flags.met_mira"
  - if: "affection.mira >= 3"
    then: [ ... ]
    else: [ ... ]
  - nsfw: {ref: c01_s03.1}       # §7: expands to the fill file's steps at build time
  - jump: c01_s04
  - call: alice_s02      # run another scene and come back; it must have next: null
  - include: hub_choice  # splice includes/hub_choice.yaml (a list of steps) here
next: c01_s04
```

Rules the validator enforces: every `say` speaker is in `present` (or
`narrator`/`player`); every id is in vocab; every outfit exists for that
character; conditions reference declared flags or `affection.<id>`; every
`goto`/`jump`/`next` target exists; every scene is reachable from `start`;
all characters have `age >= 18`; CG ids are unique project-wide; a `cg`
without `cg_end` before the scene ends is an error.

The player's name: if `premise.md`'s `## The player` says the name is
*player-entered*, the build asks for it at start (with the stated default)
and `[player_name]` in any text is substituted at runtime. Voiced lines
should avoid it (pet names, "you"); where unavoidable, `vn lines` exports
`tts_text` with the token removed and sets `has_player_name` so the runner
can leave a beat of silence.

Line text is the final on-screen text. Stutters ("H-hi"), trailing
cut-offs ("I just—") and interjections are written in the text; the TTS
layer normalises them per engine (docs/tts.md).

## 6. ledger.yaml

Written by `/vn-review-scene` after each scene. One entry per scene, in
story order, recording end-of-scene state so the next writer sees it.

```yaml
- scene: c01_s03
  summary: "..."                 # 3–5 sentences, plot
  characters:
    mira:
      knows: ["the player has a sister"]
      hiding: ["the transfer"]
      address: "uses first name now, no honorific"
      relationship: "warming; still guarded"
      outfit: casual
      last_seen: cafe
  flags_set: {met_mira: true}
  affection_delta: {mira: +2}
  threads: ["the scar question is still open"]   # unresolved hooks
```

The pack for a common scene includes every common entry that precedes it
in outline order. The pack for a route scene includes the common entries
of earlier acts (never the hubs of its own act, since it may play in any
week) and its own route's earlier entries; other routes' scenes are
path-dependent and never assumed. Entries may live in `ledger.yaml` or in
`ledger/<route>.yaml`; all are loaded. Parallel writers each own one file. Spokes therefore must not depend on
another route's events. For `branching` projects (§8) entries may carry
`branch:` and the pack follows the path that reaches the scene.

## 7. Explicit scenes

Every scene carries an `intimacy` level, 0–5, set in the outline and
repeated in the scene file (the validator checks they agree and that the
rating allows the level: 2 needs `suggestive`+, 3–5 need `explicit`):

| level | meaning |
|---|---|
| 0 | none |
| 1 | charged: a touch, a look, the almost |
| 2 | the first intimate scene: kissing, closeness, more PG than what follows |
| 3 | a short explicit moment (~¼ of the scene) arising from the story beat |
| 4 | a substantial explicit section (~½) with story either side |
| 5 | culmination: the explicit section carries the scene |

After a route's first intimate scene, every later scene on that route is
level 3 or higher: story progresses, and the scene also has its explicit
moment, situational (what is happening makes it happen) rather than
appended. Levels only rise along a route; the rate differs per character
and is set in `arcs.md`. The brief sizes the fill by the level.

`rating: explicit` scenes use the split. In the scene file the explicit
section is a single `nsfw: {ref: <scene>.<n>}` step. `/vn-write-scene`
writes `nsfw/<scene>.<n>.md` containing:

- **Brief**: stakes, what each character wants and fears, relationship
  state from the ledger, hard continuity constraints (location, outfits,
  marks, who knows what), voice notes, intensity target, approximate length.
- **Direction**: the human's own instructions for what happens. The skill
  asks for this before writing the brief; it can also be pre-filled on the
  outline entry as `direction:`.
- **Skeleton**: the beats, the CG specs the section must land on, and the
  tagged line format the fill must use.
- **Prompt**: the full prompt for the local model, assembled from the above,
  ending with `Append: characters/<id>/nsfw.txt`; the runner attaches that
  file at fill time, so the brief never copies it.
- **Fill**: pasted or generated output, in scene-step YAML, under a
  `## Fill` heading. The build inlines it; the validator checks it like any
  other steps.

Claude reviews fills for continuity and voice (did it break a fact on the
ledger, move the location, lose a mark) and updates the ledger from them.
It does not write or rewrite the explicit prose.

Image review (§10) routes explicit CGs to the human's review pile; the
automated checks (face count, framing, transparency) still run.

## 8. Branching levels

Set in `premise.md` and `outline.yaml`. The same scene format serves all
three; what changes is the outline shape and how the ledger is assembled.

- **linear**: `next` chains; choices are flavour only (no `goto`, effects
  limited to `affection` which may colour lines but never gates scenes).
- **routes** (default): a common route, then either
  - *exclusive*: a route lock (`choice` whose options `goto` each
    character's first route scene, gated by affection), then per-character
    chains with a few affection-gated `if`s and 2–3 endings; or
  - *hub calendar* (the "middle ground"): a chain of hub scenes (one per
    week), each ending in a `choice` of whose evening it is; each option
    `call`s that route's next spoke (an `if` ladder on `flags.<id>_step`),
    the spoke sets its step flag and ends with `next: null` so it returns
    to the hub, and the hub's `next` is the next week. Routes progress in
    parallel; a late lock scene offers an ending for each route whose step
    flag reached the end. Spokes are written week-agnostic; the hubs carry
    the season.
- **branching**: `goto` and `requires` anywhere. To keep continuity
  tractable the rule is *bounded divergence*: a branch must rejoin the
  spine within N scenes (default 3) or end the game. The validator reports
  every path and the ledger is kept per path. This is supported, but the
  writing cost scales with path count, so the outline step reports the
  count before anything is written.

## 9. Skills (the repeatable process)

Each is `.claude/skills/<name>/SKILL.md`. Each states its inputs, outputs,
checkpoint, and the exact context pack it assembles.

| Skill | Reads | Writes | Human checkpoint |
|---|---|---|---|
| `/vn-new-project` | — | project dir from template, `premise.md` | approve premise |
| `/vn-style` | premise | `style.md` | pick among options |
| `/vn-cast` | premise, style | `characters/*/sheet.md`, `appearance.yaml`, `world.md` | approve cast |
| `/vn-art` (local) | appearance | candidates → canon → LoRA job → sprites | cull canon art |
| `/vn-arcs` | premise, cast | `arcs.md` | approve arcs |
| `/vn-outline` | arcs, world | `outline.yaml` | approve outline |
| `/vn-write-scene <id>` | pack (§1.4) | `scenes/<id>.yaml`, `nsfw/*` | first two scenes only |
| `/vn-review-scene <id>` | scene, sheets | voice-check fixes, `ledger.yaml` entry | — |
| `/vn-direct` (local) | scenes | CG/sprite/bg jobs, review results | explicit CG pile |
| `/vn-voice` (local) | scenes, voice.yaml | TTS jobs, scored takes | — |
| `/vn-build` | everything | `build/` Ren'Py project | — |
| `/vn-setup` (local) | `models.yaml` | checks/installs models | — |

`/vn-write-all` loops write → review over the outline in story order.

## 10. The local job runner (phase 2, built on the GPU machine)

A small Python service, no web UI: a queue, one GPU job at a time per
device, HTTP clients for ComfyUI and the TTS server, a Kohya/GPT-SoVITS
subprocess wrapper, and result files written into the project tree.
Two devices (5080 16 GB, 3070 8 GB): images and training on the 5080; TTS,
Whisper scoring and the local LLM on the 3070 where they fit.

Image jobs: `bg_plate`, `char_candidates`, `sprite_sheet`, `cg`,
`lora_train`. CG jobs use the pose reference (ControlNet) and the location
plate (img2img / depth) so camera, pose and environment are forced rather
than requested. Every image job produces a contact sheet; Claude reviews
non-explicit sheets against the spec and marks pass / regenerate-with-note;
explicit sheets go to the human.

Voice jobs: `voice_finetune`, `emotion_mine`, `tts_line`. `tts_line`
renders N takes, transcribes each with Whisper, normalises both script and
transcript (strip stutter hyphens, cut-off dashes, interjections), scores
WER + speaker similarity + duration sanity + clipping/silence, keeps the
best, flags lines where the best is still poor. `emotion_mine` renders a
neutral line many times with high variability under each delivery tag;
Claude/the human pick the best per tag and it becomes the reference clip.

## 11. The compiler (`vn build`)

`vn/` is the Python package: `schema.py` (pydantic models + validation),
`vocab.py`, `project.py` (loading, context packs), `compile_renpy.py`,
`cli.py`. Output:

```
build/game/
  script.rpy        # label start -> jump <outline.start>
  characters.rpy    # define, image declarations (Placeholder when art is missing)
  scenes/<id>.rpy   # one label per scene
  options.rpy       # title, build name, defaults
  images/, audio/   # copied from the project
```

Affection is a dict (`default affection = {...}`), flags are a dict,
conditions are compiled from `affection.mira >= 3` / `flags.met_mira` to
Python. Voice lines emit `voice "audio/voice/<scene>/<n>.ogg"` when the file
exists. Missing sprites/CGs/backgrounds compile to `Placeholder` so a
project is playable at every stage.

## 12. The review console (`runner/` + `ui/`)

A review and asset console, not an editor: it reads `projects/<slug>/`
and the job runner, and everything a human does in it lands as a file
Claude can read. It never owns state.

```
python -m runner                 # http://localhost:8000
python -m runner --lan --pin 4821   # also on the Wi-Fi, PIN exchanged once for a cookie
```

**Screens.** Board (every outline scene: written, ledgered, notes, missing
sprites/backgrounds/CGs/voice; validate, build, launch, "queue missing" as
one overnight batch) · Scene (the script as prose with per-line notes,
inline audio and takes, CG cards, the explicit-section briefs, the YAML,
"Revise with Claude") · Cast (sheet; candidates culled into canon with
one tap; sprite grid with pass/redo; LoRA wizard; voice references,
emotion mining, fine-tune checklist) · CGs (to review / not rendered /
done; pick a candidate; Claude's review verdicts; explicit CGs flagged for
human eyes) · Voice (lines without audio; queue all) · Jobs (progress,
logs, cancel, retry) · Setup (service checks, config, model checklist).

**Files the console writes.**
- `review/notes/<scene>.yaml`: `[{id, step, kind, quote, note, created, applied, reply?}]`;
  `/vn-revise` consumes them and sets `applied`.
- `review/backup/<scene>.<ts>.yaml` before any YAML save from the console.
- `characters/<id>/art/{candidates,canon,rejected,sprites,lora}/`,
  `characters/<id>/voice/{source,ref,mining}/`, `voice.yaml: reference`.
- `review/cg/<id>/{NN.png, spec.json, review.json}` then `images/cg/<id>.png` on pick.
- `audio/takes/<scene>/<nnn>/{take_k.wav, scores.json}` then `audio/voice/<scene>/<nnn>.ogg` (or `.wav`).

**Jobs** (`runner/handlers/`): `validate`, `build`, `launch`, `revise`,
`review_images` (local lane); `char_candidates`, `sprite_sheet`,
`bg_plate`, `cg` (images lane, ComfyUI API-format workflow templates in
`runner/workflows/`); `lora_train` (train lane, kohya); `tts_line`,
`emotion_mine`, `voice_finetune` (audio lane, GPT-SoVITS api_v2;
best-of-N scoring with faster-whisper and resemblyzer when installed). One
job at a time per lane; `runner/config.yaml` maps lanes to GPUs. Jobs
persist in `runner/data/jobs.json` and resume after a restart.

The ComfyUI, kohya and GPT-SoVITS handlers were written against their
documented HTTP/CLI interfaces and have not been run against live
services in this repository; the first run on the GPU machine is where
they get verified, one job kind at a time, from the Setup and Jobs pages.
