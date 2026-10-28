# Prose Generation Overhaul — Implementation Plan

Addresses eight failure modes in LLM scene generation. The diagnosis is that
most of them are **structural, not model quality**: the passes are cut along the
wrong lines, so a cold segmenter is being asked to read emotional subtext and
the writer is being told to do things that guarantee the rhythm you don't want.

---

## 0. The core change: re-cut the passes

Today Pass 2 does three unrelated jobs — segmentation, performance tagging, and
shot direction — at temp 0.2 on one passage at a time. That is why expressions
repeat, why narration piles up, and why `shot_hint` reads as an afterthought.

| Pass | Temp | Owns |
|---|---|---|
| **1. Prose** | warm, unconstrained | The writing. |
| **2. Structure** | cold, schema | **Segmentation only** — say / narration / stage / choice / set, text verbatim, speaker ids. |
| **3. Perform** *(new)* | medium, schema, **whole scene at once** | expression, pose, emotion, intensity, delivery, `sfx`, `music`, redundant-narration `drop`, character `exits`, `cg` + `shot_hint`. |
| **4. Reconcile** *(new)* | **code, no LLM** | Entrance staging, applying drops/exits, present-set validation. |

Why: "don't repeat the last three expressions" and "this narration only
describes her face, cut it" are both scene-wide judgments a per-passage
segmenter structurally cannot make. Moving them to Pass 3 also lets Pass 2
become genuinely mechanical, which is the only condition under which a small
cold model is the right tool for it.

`cg` and `shot_hint` move to Pass 3 for the same reason the architecture doc
already gives for the CG director existing at all: the segmenter writes them
"as an aside while it is busy segmenting."

Entrances are handled in **code**, not by an LLM — walk the node list, track the
present-set, insert `stage {show:[speaker]}` when a speaker isn't in it.
Deterministic and unhallucinatable. Only *exits* need judgment, so those come
from Pass 3 as an `exits` field and the reconciler applies them.

Pipeline in `handlers.generate_scene`:

```
prose   = prose_pass(...)
parsed  = structure_pass(...)                       # segmentation only
perf    = perform_pass(parsed, ...)                 # annotations, by index
parsed  = apply_performance(parsed, perf)           # merge / drop / exits
parsed  = reconcile_staging(parsed, state, ids)     # entrances
nodes, edges = build_nodes(parsed, ...)
```

`apply_performance` and `reconcile_staging` operate on the parsed **dict**, not
on `Node` objects — pure functions over plain data, trivially unit-testable.

---

## Round 1 — build order

**Status: complete.** 221 backend tests, 12 frontend, TypeScript clean.

Stats land first because they touch the Character model, the choice-effects
namespace, card rendering, the state block, compiler `default` emission and the
UI. Doing templates first would mean writing them twice.

**Existing projects need one manual step.** Templates are stored per project
and win over `DEFAULT_TEMPLATES`, and their sampling params live on the stored
row — so a project whose `system.prose_gen` / `system.structuring` were edited
keeps the old bodies *and* the old sampling (no DRY, no XTC, temp 0.92). Reset
those two in Config → templates; the previous version is kept and diffable, so
nothing is lost. `system.perform` and `system.style_examples` are new keys with
no stored row, so they pick up defaults on their own.

Settings had the same problem and are handled automatically:
`store.migrate_superseded_settings` upgrades `context_token_budget` and
`prose_pc_policy` **only** while they still hold the exact old default, so an
edited value is never overwritten.

Two pre-existing bugs surfaced on the way and were fixed:

- `images.negative_for` dereferenced `character.appearance` outside its own
  `if character:` guard, so any CG with nobody in the shot — the establishing
  shot the CG director is explicitly told it may produce — crashed the render.
- `test_character_card_uses_composed_appearance_and_new_fields` asserted on
  `appearance.body_description`, a field `Appearance` does not have and pydantic
  was silently dropping. Repointed at `build`.

### 1. Character stats

Replaces ad-hoc `affection_diana` story variables with a first-class,
project-defined schema instantiated per character.

**Model** (`models.py`):

```python
class StatBand(BaseModel):
    min: int = 0
    label: str = ""      # "warming"
    note: str = ""       # optional one-liner for the writer; opt-in

class StatDef(BaseModel):
    id: str              # "affection"
    name: str = ""
    type: Literal["int", "bool", "str"] = "int"
    default: str = "0"
    min: int | None = 0
    max: int | None = 100
    note: str = ""
    bands: list[StatBand] = []
    in_card: bool = True   # off for stats that gate content but shouldn't
                           # steer prose (e.g. route progress)
```

- `ProjectMeta.character_stats: list[StatDef]` — the schema, defined once.
- `Character.stat_overrides: dict[str, str]` — per-character starting values.
- Variable naming: `{stat_id}_{character_id}` (`affection_diana`), matching the
  existing convention so nothing reads worse in Ren'Py.

**Two axes, deliberately not collapsed.** `affection_X` is *state* (how she
behaves, what dialogue unlocks); `progress_X` is a *content pointer* (which
scenes are unlocked). You can be deep in a route with low affection — that is
the case that makes affection choices matter, and collapsing them makes every
one of those choices cosmetic.

**Resolution.** Stat vars live in the same flag namespace `resolve_state`
already populates from `set` nodes and choice effects, so `state.flags` works
unchanged — it just needs seeding with stat defaults. Add
`resolve_stats(project, characters, state)` merging defaults under path flags.

**Dynamic card, zero extra authoring.** `character_card()` appends one derived
line from the current band:

> Toward the player: *[existing static text]*. Right now: **warming** (34/100).

The per-band `note` stays optional, so flavour can be added for main characters
and skipped for everyone else.

**Touches:** `models.py`, `graph.py` (`merged_flag_defaults`), `compiler/renpy.py`
(`_emit_flags`), `services/context.py` (`character_card`, `state_block`),
`services/schemas.py` (variables enum includes stat vars), `routers/projects.py`,
`routers/characters.py`, ConfigView (stat schema editor), CharactersView
(starting values).

Declared `project.variables` win on name collision, so existing projects don't
break.

### 2. Pass re-cut

**Pass 2 schema** (`schemas.py::scene_schema`) strips to segmentation:
- `say`: `{type, speaker, text}`
- `narration`: `{type, text}`
- `stage`: keep location / time / weather / present / hide / outfits; **drop**
  `expressions` and `poses` (Pass 3 owns them)
- `set`, `choice`: unchanged

**Pass 3 schema** (`perform_schema`) — one flat beat object per node index, all
fields optional but `i`, so the grammar stays simple and the code ignores what
doesn't apply to a given node type:

```
{ "beats": [ { "i", "expression", "pose", "emotion", "intensity", "delivery",
               "sfx", "music", "drop", "fold_expression", "exits",
               "cg", "shot_hint" } ] }
```

Prompt carries the numbered node list, the scene context, and an explicit
anti-flatness instruction naming the last few assigned expressions.

**Model:** `lm_studio_perform_model`, falling back to the **prose** model (not
the struct model). Reading emotional subtext is a prose-model job; a small cold
model is exactly what's failing today.

**New:** `generation.perform_pass`, `generation.apply_performance`,
`generation.reconcile_staging`, template `system.perform`, `PERFORM_SAMPLING`.

### 3. SFX and music as fields, not nodes

A field annotated onto an existing node can't mangle the graph the way node
insertion can.

- `SayData` / `NarrationData`: `sfx_cue: str`, `sfx_file`, `sfx_hash`
  (parallel to `cg_camera` / `image_hash`).
- `StageData`: `music_cue: str`, `music_file`, `stop_music: bool` — music is
  *state*, `stage` is the state-mutation node, `resolve_state` already tracks
  `state.music`, and `SceneData` already has these two fields.
- `NodeType.audio` stays as the manual escape hatch.
- `graph.text_hash()` gains `sfx_cue`, so editing a cue marks resolved audio
  stale once real files exist.
- Compiler emits `play sound "audio/sfx/<file>"` **only when a real file is
  set**; otherwise a `# TODO sfx: <cue>` comment, so a build never breaks on a
  missing file.

### 4. Templates

- **`system.prose_gen`** rewrite: drop the "physical action must be its own
  narration line" rule (it is what forces the Text→Narration alternation); add
  "do not narrate facial expressions, blushes, small gestures or tone — the
  sprite and voice carry these"; add a banned-tics block (no em-dash as
  dramatic pause, no "not X, but Y", no "a beat passed", no poignant
  scene-ending one-liner); add a grit clause (messy, interrupting, trailing
  off, fragments); add the `SFX:` notation.
- **`prose_pc_policy`** rewrite from all-prohibition to positive and specific:
  the player answers direct questions with a short plain line (3–12 words), no
  introspection, roughly one player line per 4–6 other-character lines; a
  `CHOICE:` block is for a real decision, not an ordinary reply.
- **`system.structuring`** rewrite to segmentation-only.
- **`system.perform`** new.
- **`system.style_examples`** new — project-level few-shot block, rendered into
  the prose prompt as `{{ examples }}`. `render()` gains an `explicit` boolean
  so adult material can be gated inline with `{% if explicit %}`. (Round 4
  moved where that boolean comes from: the scene's tone, not a project-wide
  rating.)

Examples are short (one exchange, ~150–250 words) and each must itself
demonstrate the wanted structure — back-to-back dialogue with no narration
between, a sparse narration line describing something the sprite *can't* show,
a short player line, an SFX cue. Small models copy example structure far harder
than they follow prose rules, so the examples are the real format spec.

### 5. Structured scene brief

`SceneData` gains `characters: list[str]`, `length: str`, `must_hit: str`,
`ends_with: str`, `tone: str`.

Rendered as **scene-wide constraints**, with an explicit note that the scene is
written across multiple passes and that not reaching the ending in this stretch
is correct — otherwise the model rushes the whole arc into one generation.

`characters` empty = auto (present + spoken-on-path + player, current
behaviour). Plus a **two-tier roster**: full cards for on-stage and the player,
one line each for every other character in the project, so someone can be
discussed without the model inventing them.

### 6. Sampling and context budget

```python
PROSE_SAMPLING = {
    "temperature": 1.0, "min_p": 0.05, "top_p": 1.0, "top_k": 0,
    "repetition_penalty": 1.0,                     # DRY replaces it
    "dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": 2,
    "xtc_threshold": 0.1, "xtc_probability": 0.5,
    "max_tokens": 1400,
}
```

`context_token_budget` 6000 → **18000**. At 6000 `assemble()` was silently
dropping character cards for anyone off-stage and trimming the verbatim tail,
which feeds the voice-drift and flat-emotion problems.

`verbatim_token_budget` stays ~2000 — more verbatim tail *worsens* the
pattern-lock-in, and prose `max_tokens` stays 1400 because longer single
generations drift more.

DRY and XTC are already plumbed through `lmstudio._body`; they just aren't in
the defaults.

### 7. Model selection UI

- `GET /llm/models` wrapping `lmstudio.list_models()`.
- ConfigView: dropdowns populated from it (free-text fallback preserved) for
  prose / struct / perform models.
- New setting `lm_studio_perform_model`.

**Recommended:** Cydonia 24B for prose and perform; **Qwen2.5-14B-Instruct**
(Q4_K_M, 8k context) for structuring — it must copy text verbatim, not improve
it, so a creative tune is the wrong tool. At 8k context it co-resides with
Cydonia on 24 GB, so no swapping.

### 8. Context preview in the Inspector

**Already built** — `ContextPreviewButton` was wired into the Inspector all
along ("Preview the assembled prompt"), so there was nothing to do. It is the
diagnostic that decides whether the `path_to` merge-point fix is needed: open
it on a node downstream of a merge and read the "scene so far" block. If it
shows the wrong branch's dialogue, that confirms the merge-point walk.

### 9. Inspector redo

Now load-bearing: stats, `sfx_cue`, Pass-3 fields, structured brief fields all
land in it this round. Sectioned per node type — **Content · Performance ·
Staging · Audio · Image · Advanced** — sections auto-collapsed when empty,
context preview under Advanced, and type-aware sub-editors rather than one
component branching internally.

---

## Round 2 — continuation buttons + regenerate-block

**Status: complete.** 237 backend tests, 12 frontend, TypeScript clean.

A stretch that does *not* end on a decision now ends on a `CONTINUE:` block —
two or three directions the scene could take. These are notes to the **author**,
never to the player: `generation.split_continuations` peels them off the prose
before the structuring pass sees them, so they can never become narration nodes
reading `- she follows him out`. A `CHOICE:` block always wins if the model
emits both, since that one becomes something the player actually sees.

The suggestions are parked on the **last node of the chain** as
`data._continuations` (authoring scaffolding, written after the variant loop so
a flip-back never restores stale ones), and a chain ending in a choice gets none
— the choice already *is* what happens next.

Clicking one does one of two things, and the difference matters:

| Node state | Action | Why |
|---|---|---|
| Nothing wired after it | `generate/scene` with `continuation` | Append the next stretch. |
| Already continued past | `generate/regenerate-from` on the successor, with `continuation` | Appending would splice a second chain *in front of* existing content, which is never what "actually, go the other way" means. |

`continuation` is deliberately a separate field from `instruction`: it says
where the scene goes, not how to write it, and renders as *"Take the scene in
this direction: …"*.

**Regenerate this block** (`generate/regenerate-block`) redoes the whole
generation a node belongs to. Distinct from the existing regenerate-from, which
starts at the selected node: if the first line of a six-node passage is the
problem, regenerating from the node you happened to click redoes only the tail.
`generation.generation_head` walks back to the group's topological root — which,
now that reconcile inserts staging, is usually the auto-inserted stage node
rather than the first line, and that is correct.

## Round 3 — Improv mode

**Status: superseded.** Improv was removed and replaced by Roleplay mode (see the
README). Everything below is kept as the record of what was tried and why, because
the reasons it did not work are the reasons Roleplay is built the way it is.

The short version: making the session *be* the graph is what made it unusable.
Every turn of dialogue paid for the whole scene pipeline, and it was prompted with
the scene-writing prompt — so a reply cost two LLM calls and a structuring pass,
and the prompt spent four thousand tokens describing machinery a conversation does
not have before being told to ignore most of it. A conversation now lives in its
own tables, is sent to the model as real chat turns, and is copied into the graph
only by an explicit one-way port.

**Status at the time: complete.** 242 backend tests, 12 frontend, TypeScript clean.

Its own tab, sharing the Script view's linearization so the two never disagree
about what is on the path. The whole thing rests on **the session being the
graph** — no chat log, no separate session state, each turn's prompt rebuilt by
`context.assemble` walking the path like any other generation.

That decision is what made A–F from the original brief mostly free:

| Requirement | Mechanism | New code? |
|---|---|---|
| A — regenerate the last reply | `generate/regenerate-block` | none, round 2 built it |
| B — reply as text, or offer a choice | insert a `say` node / insert a `choice` and generate down option 1 | none, `nodes/insert-after` + `generate/branch` |
| C — rewind and try another path | `generate/park-after` | **one endpoint** over the existing `orphan_subtree` |
| D — reconverge | create an edge → merge point | none, `POST /edges` |
| E — out-of-band direction | the `instruction` steer, made sticky in the UI | none |
| F — continue without replying | an ordinary generation | none |

So the backend addition is a single endpoint. The rest is presentation.

**A latent bug this surfaced:** `generation.orphan_subtree` only ever worked
with `include_self=True` (its one caller, regenerate-from-node). With
`include_self=False` the anchor counted as an outside referent reaching its own
subtree, so every node in it was "still reachable" and parking silently did
nothing. Rewind needs the `False` form, so the anchor is now excluded from that
scan. Two tests cover it, including that a merge point another branch still
feeds is never dragged off.

**Integration:** Canvas, Script and Improv now share the active scene through
`lib/store.readActiveScene` (the key the Canvas already persisted), so switching
views keeps you in the same scene. Script has an "Improvise →" button; Improv
has "Edit in Script". Player lines go through `store.insertAfter`, so Ctrl+Z
takes back a line you regret typing like any other edit.

## Do later

- **Dynamic tone-based prompt templating.** Per-scene example-set selection plus
  tone-conditioned template fragments beyond the examples. Deferred because
  round 1's project-level set plus the `nsfw` flag covers the axis that
  actually matters; revisit if tone shifts within a scene prove common enough
  that splitting the scene isn't the right answer.
- **`path_to` merge-point fix.** At a merge, `path_to` takes `incoming[0]`
  sorted by source UUID — so the reconstructed history is an arbitrary upstream
  branch. Fix is to have the frontend pass the actual navigated path. Gated on
  what the context preview reveals.
- ~~Continuation buttons + regenerate-last.~~ **Done — see Round 2 below.**
- ~~Improv authoring mode.~~ **Done — see Round 3 below.**
- ~~Missing-audio report.~~ **Done — see Round 4 below.**
- ~~Hub node.~~ **Done — see Round 4 below.**

## Round 4 — Missing-audio report and the Hub node

**Status: complete.** 259 backend tests, 18 frontend, TypeScript clean.

**Missing audio** hangs off `services/batch.scan`, as planned, and rides the
existing `/batch/missing` response as a `sounds` list — every `sfx_cue` and
`music_cue` with no file behind it, ordered by scene. It is deliberately *not*
in `counts.total`: total is what a fill run would queue, and nothing generates
audio, so counting it there would make the batch panel promise work it cannot
do. The view is a card on Build under the fill panel rather than a top-level
tab — "alongside the existing missing-asset scan" is where it is useful, and a
read-only table did not earn a tenth tab. Each row takes a filename inline and
goes through `store.patchNode`, so attaching a file is one edit and Ctrl+Z
takes it back.

**The Hub node** is `NodeType.hub` with `HubData` / `HubOption` /
`StatCondition`. Everything downstream that already knew about `choice` now
knows about `hub` too: label emission (a hub is *always* a label — `loop_back`
returns to it and a greyed option re-shows it), `walk_run` coverage, script
linearization, context assembly, and `generate/branch`, so generating a topic
is generating a branch and needed no new endpoint.

Three things the compiler does that `_emit_choice` could not:

| | How |
|---|---|
| Structured gates | `{stat, character, op, value}` → `affection_aiko >= 20`, built by the compiler against the same `{stat}_{character}` variable `flags.rpy` declares. The preview gets the triples themselves rather than the string — a Python expression is not a JavaScript one (`True` is not `true`). |
| Grey vs hide | Ren'Py's `menu` has no disabled item, so `grey` emits a *second* entry guarded by `not (…)` whose body jumps back to the hub. Picking it costs a click and changes nothing, which is what greyed means. |
| Route destinations | `jump expression vn_next_in_route("aiko")`, over a `vn_routes` table of scene labels and a `vn_visited` store variable each route scene adds itself to. |

`vn_visited` is a store variable rather than `renpy.seen_label`: seen is
persistent across playthroughs, so a second run would find every route already
exhausted on the first click. Being a store variable it also rolls back with
saves.

**A hazard the feature created, and closed.** A hub with `exhaust` + `loop_back`
runs out of topics, and Ren'Py raises on a menu with nothing in it. So
`HubOption.repeatable` forgets its own caption on the way through
(`$ hub_topics.discard("Leave")`) — the same trick the greyed twin uses — and
validation warns on a looping hub with no way out marked.

**Scene tone replaced the content rating.** `WorldBible.rating` is gone and
`SceneData.tone` is a closed list (`models.TONES`), one value of which —
`explicit` — is what turns on the adult register. The rating was doing that job
before, and doing it badly: one value over a whole story means a story with a
single explicit scene in it is either written as though every scene were, or
never gets one. Tone was already the per-scene steer and already reached the
prompt, so the gate moved onto it and the prose template gained an
`{% if explicit %}` direction next to the scene targets.

Nothing validates `tone` against the list on the way in: scenes written before
it existed hold free text, and dropping that on load would be a silent edit of
the author's work — so the editor offers the list and keeps whatever is already
there as its own entry. `assemble` still passes a derived `rating`
("adult (18+)" / "general audiences") purely for hand-edited templates that
still interpolate it; untouched templates adopt the new default automatically
(`store.active_template`).

**Two Improv fixes found while testing this.**

*Switching scene appeared to do nothing.* It was loading — `linearize` walks the
whole path onward from its start, so on the real project "Opening" returned 172
rows and "Meeting With Social Services" returned 124, and the last 124 of the
first were the second. The Improv log is pinned to its bottom, so both selections
showed the same closing lines. Improv now scopes the log to the scene at the top
of the walk (`scopeToScene`) and says where the path goes next; the Script view
keeps the full walk, which is right for a surface you read from the top.

*No way back to a fork.* Improv passed no `choices` to the linearizer, so it
always followed the first wired option — the three-way choice in the opening
scene could only ever be improvised down branch one. A choice turn now renders
its options as buttons: one already written re-walks the log down it, one with
nothing behind it generates that branch and follows it. Hubs get the same
treatment.

**Deliberately not done:** a route registry. A route exists because scenes claim
it by name, ordered by where they sit on the canvas; a first-class Route record
would need its own CRUD, its own view and its own reordering to say what a text
field already says.

---

## Round 5 — the perform pass is deleted; the writer tags its own lines

**Status: complete.** 272 backend tests, 18 frontend, TypeScript clean.

Three symptoms, one of them structural:

**Expressions and emotions looked random.** Pass 3 returned
`{"i": 7, "expression": ...}` and `apply_performance` wrote it to
`parsed["nodes"][7]` with no verification of any kind. An index is the one thing
a model cannot be relied on to keep: number a list 1-based, count only the
dialogue lines, skip a beat and lose the offset, and every annotation after that
point lands on its neighbour. From the graph that is indistinguishable from
random, and nothing anywhere could detect it.

So performance moves to where it cannot drift: **pass 1 writes it on the line**.

```
Aiko (looking_away, melancholy, 0.7, trails off): "I waited."
SFX: the door banging shut behind you
MUSIC: something sparser, one piano line
CG: sitting against the fence, not looking at each other
```

`generation.extract_performance` strips all of it before the segmenter sees the
passage and returns one record per written beat;
`generation.apply_performance` matches those back onto the segmented nodes **by
text**, with a short lookahead window. A line the segmenter merged or dropped
costs that one line its tag instead of shifting every tag after it. A beat that
matches nothing is left untagged, which is always better than wearing someone
else's face.

The tag parser classifies each token by what it *is* rather than by position, so
a swapped face and feeling, a missing one, brackets instead of parens, or the
tag written after the colon all still land on the right fields; a word the
vocabularies do not know becomes the free-text `delivery` note. `SFX:` and
`MUSIC:` bind to the beat that follows, `CG:` to the beat just written.

This also puts the anti-flatness instruction where it can actually act. Told
"if three lines running carry the same face, the third is probably wrong", a
writer can change the third *line*. A reader annotating finished text can only
change the label on it.

**What went with it.** `perform_pass`, `perform_schema`, `assemble_perform`,
`beat_lines`, `perform_subject_line`, `system.perform`, `PERFORM_SAMPLING` and
the `lm_studio_perform_model` setting. Generation is two model calls now.
Character *exits* were already in pass 2's stage schema (`hide`), so they did
not need moving. The one real loss is `drop`/`fold_expression` — the pass's
ability to delete narration that only described a face. The prose template
already forbids writing those lines, and post-hoc deletion of finished prose was
the riskiest thing pass 3 did.

**Doubled and empty staging nodes.** Two separate causes, both fixed in code:

- *Doubling* — `stage: show A` / `"A walks in"` / `stage: show A` — was the
  segmenter re-declaring what the prose had just narrated. `reconcile_staging`
  now walks the running present-set and strips every field that changes nothing
  (a `present` entry already on stage, a `hide` for someone who is not, the
  location that is already the location), drops the node if nothing survives,
  and folds adjacent stage nodes into one. Contradicting nodes are never folded:
  someone walking out and back in is two moments.
- *Empty* nodes were mostly not empty. A stage node carrying only `music_cue` or
  `time_of_day` rendered as `(no changes)`, because the node card and the script
  row both summarised only location / show / hide / outfits. Both now print
  time, weather and music.

**The defaults are now Cym's prompts.** Cym had been running on prompts several
revisions behind: `active_template` only adopts an improved default for a seeded
v1 with no v2, and Cym's `system.prose_gen` had been through eight versions. Its
base was the default from *before* the CONTINUE block and the per-scene
`explicit` clause existed, so what looked like the author deleting those was
never having received them. Two edits on top of that base were real and are now
the shipped default:

- the "narration should be the minority of what you write" paragraph is gone;
- so is the "dialogue is allowed to be messy" bullet.

Both were Round 1 additions aimed at rhythm, and both are edits the writer was
demonstrably ignoring while costing prompt budget. `system.structuring` v4 was
byte-identical to the outgoing default, so it needed no merge at all.

`image.sprite_wrapper` came across too: `(solo:1.2)` weighted, and the pose tag
moved to the front of the prompt at `:1.3`. A sprite prompt runs long — identity,
outfit, trigger, LoRA tag — and SDXL attends less to what trails at the end,
which is how a `sitting` sprite came back standing.

**Existing projects.** Four bodies are in `SUPERSEDED_TEMPLATES` for
`system.prose_gen`: the default this round replaced, the older one two demo
projects are still seeded with, and — the one deliberate exception to the
"outgoing defaults only" rule — Cym's own authored v8, because that body *is*
what the new default was merged from. Replacing it loses nothing and is the only
way the project it came from picks up the notation its own prompt now specifies.
A dry run against a copy of the live data dir re-adopts six rows and leaves
everything else alone. Any *other* edited prose prompt is still never touched,
and will not ask for the notation, so lines in that project stay `neutral` /
`front` until the bracket section is copied across.

---

## Round 6 — NSFW is a checkbox, not a tone

**Status: complete.** 276 backend tests, 18 frontend, TypeScript clean.

`SceneData.tone` was an enum with `explicit` as one of its sixteen values, which
made the adult register and *what the scene is* the same axis: picking explicit
threw away "tense" or "tender", and picking tender made an explicit scene
unwritable. It is now `SceneData.nsfw: bool` and a checkbox in the Inspector.

**What the flag gates is text, not behaviour.** `{% if nsfw %}` blocks render
for a scene that sets it and are absent from the prompt otherwise — not present
under an instruction to ignore them, which is a distinction a 24B does not
reliably hold. Two of them ship:

- the prose template's `## This scene is explicit` section, promoted out of the
  scene-wide targets list where it used to sit as a bullet. It was never a
  target — a target is something the scene has to reach, and the register
  applies to the first line;
- `Personality.nsfw`, a new free-text subsection on every character, rendered
  into the card only for an NSFW scene. It adds to the personality above rather
  than replacing it, and the template says so — the same voice, doing this.

**Migration.** `store.migrate_scene_tone_to_nsfw` carries `tone == "explicit"`
over to `nsfw: true` and drops the rest of the enum, which nothing reads any
more. A scene that was marked explicit silently ceasing to be is the one
direction this must not fail in, so it runs at startup ahead of the template
migrations. `system.prose_gen` and `character.card_format` are both superseded,
so an unedited project picks up the new gating without touching anything.
`assemble` still passes `explicit`, `tone` and `rating` alongside `nsfw`, purely
so a hand-edited template naming any of them keeps rendering.

**A latent bug this surfaced.** `context.render`'s forgiving fallback — the path
that exists so a template naming a variable nobody passes any more degrades to a
blank instead of failing a generation — built its environment with
`undefined=type("U", (), {...})()`, an *instance* where jinja2 wants the class.
It raised `TypeError` every single time, and `render` fell through to returning
the **unrendered template**. A prompt full of literal `{% if %}` tags is not a
degraded generation, it is a nonsense one, and it only ever happened on the path
built to prevent exactly that. Now `ChainableUndefined`, with a test.

---

## Not doing

- **Beat-sheet / outline pass.** Same feature as continuation buttons aimed at
  the opposite temperament — planned vs. discovery authoring — and pre-planning
  makes the buttons pointless. Structured scene brief fields capture the useful
  part without a new pass or new visualization.
- **Moving text generation to ComfyUI.** llama.cpp grammar sampling is
  load-bearing for every schema-constrained pass; ComfyUI's LLM node ecosystem
  has weaker schema support and a worse API for this. LM Studio's TTL
  auto-evict already handles the LLM↔SDXL swap.
- **Adding chat history.** The backend is already stateless —
  `lmstudio.chat()` sends `[system, user]` and nothing else.
