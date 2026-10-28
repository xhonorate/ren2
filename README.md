# AI-Assisted Visual Novel Authoring Tool

A browser-based node editor that generates writing, images and voice from local
models, and compiles to a runnable Ren'Py project.

Built to the architecture in [`vn-authoring-tool-architecture.md`](vn-authoring-tool-architecture.md).

---

## The two decisions everything else follows from

**The JSON IR is the source of truth; Ren'Py is a one-way compile target.**
`.rpy` is never parsed back. That is what makes it safe to attach generation
prompts, seeds, LoRA references, emotion vectors, staleness hashes and per-node
take history to the graph — none of which has anywhere to live in `.rpy`.

**One IR, two compile targets.** `compiler/renpy.py` emits the real build;
`compiler/preview.py` emits a program the in-browser player interprets, so a
branch can be tested in a second instead of launching Ren'Py. Both run off the
same label set (`compiler/labels.py`), so what you play is what you build.

---

## Running it

Requires Python 3.10+ and Node 18+.

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000
```

`npm run build` writes `frontend/dist`, which the backend then serves itself on
`http://localhost:8000` — no second process needed.

### On your phone, over the local network

```bash
cd frontend && npm run build      # once, so the server has a UI to serve
cd ../backend
python serve.py --lan --pin 4821
```

It prints the address to open on the phone:

```
  VN Authoring Tool

    this computer   http://localhost:8000
    phone / tablet  http://192.168.2.13:8000

    PIN required (VN_ACCESS_TOKEN is set)
```

Both devices need to be on the same Wi-Fi. `--lan` is opt-in on purpose: this
process writes to your filesystem, launches Ren'Py and Kohya, and talks to an
unauthenticated ComfyUI, so it binds to loopback unless you say otherwise. On a
home network that is usually fine; `--pin` adds a shared code for anything less
private, and the server says plainly which mode it is in.

The PIN is exchanged once for a session cookie, because `<img>`, `<audio>` and
the job event stream cannot send headers. `VN_ACCESS_TOKEN` works as an
environment variable if you would rather not put the code in your shell history.
For scripted access the same value works as an `X-VN-Token` header or a `?token=`
query parameter.

If the phone cannot connect, it is almost always the firewall: Windows will ask
to allow Python on private networks the first time, and that prompt has to be
accepted.

**On the phone the layout adapts:** the node palette and inspector become
swipe-away drawers behind the ☰ and ✎ buttons, tapping a node opens its editor,
tap targets and text inputs grow (16px, so iOS does not zoom on focus), and the
canvas pans and pinch-zooms. The Script, Play, Gallery and Build views are the
ones worth using on a small screen — checking an overnight batch or reading
through dialogue from the sofa works well; wiring a complex graph still wants a
mouse.

### Try it without any models running

```bash
cd backend
python seed_demo.py --export ../demo_renpy
```

That builds a 22-node graph with a three-way merge, a guarded choice, an
`if/elif/else`, and a `menuset` hub scene, compiles it, and writes the Ren'Py
project. Point the Ren'Py launcher at `../demo_renpy` and it runs. Everything
after this point is optional AI on top of a working compiler.

### External services

None are required to author, compile or export. Configure them under **Config →
services** as you need them. For a full walkthrough of standing up ComfyUI for
image and voice generation — models, custom nodes, workflow templates — see
[`getting-started.md`](getting-started.md); for character LoRA training, see
[`lora-training.md`](lora-training.md).

| Service | Default | Used for |
|---|---|---|
| LM Studio | `http://127.0.0.1:1234` | prose + structuring + summarization |
| ComfyUI (headless, `--listen`) | `http://127.0.0.1:8188` | images, and TTS via a custom node |
| Ren'Py SDK path | — | launching and linting the built game |
| kohya_ss path | — | character LoRA training |
| ffmpeg | on `PATH` | encoding voice lines to Ogg Vorbis |

ComfyUI has no authentication. Bind it to LAN only. The browser never talks to
it — every call goes through this server.

---

## What is implemented

**Graph and compiler.** All 14 node types. Labels are emitted only at scene
starts, choice-branch targets, merge points (in-degree > 1) and user-pinned
nodes — not one per node. Label names are `slug + short hash of the node UUID`,
so they are stable across recompiles and player saves keep working. Merge
points, three-way merges, backward jumps and nested conditionals are covered by
tests, because that is where compiler bugs live.

**Editor.** React Flow canvas with per-branch output ports, a branch-aware
auto-layout (each option's branch in its own column, in option order, with
edges routed around cards), minimap, full-text search, per-node text/image/audio status badges, and a
validation pass that catches unwired choice options before a build. The canvas
shows one scene at a time, chosen from a dropdown (with a "New scene" entry), so
the graph stays legible however large the story grows; branches and jumps that
leave the scene are counted in the toolbar. Undo/redo (`Ctrl+Z` /
`Ctrl+Shift+Z`) covers every edit, and restores deleted nodes with their
original ids so labels never move.

A node that has a CG shows it, on the card and on the Script row -- which shot
a beat actually got is the one thing a "CG ready" badge cannot tell you, and
opening the inspector on each node in turn to find out is how an afternoon
goes. Auto-layout reserves the height, so a scene full of images lays out
without cards overlapping.

**Script view.** A linear editing surface over the same graph, for when you want
to write rather than wire, and at feature parity with the canvas inspector --
being sent to another view mid-sentence is what breaks a writing session. Rows
stay bound to their node ids: typing patches the node in place, so a rewrite
never churns a Ren'Py label. Enter makes the next line, Backspace on an empty
line removes it and heals the path, and a Choice row shows every option with a
picker for which branch to follow.

Every row has a `⋯` panel carrying what the inspector carries -- performance
fields, the sound cue, the CG flag and shot hint, notes, the label pin, the take
strip, and the full generation menu (regenerate from here, regenerate this
block, either with a one-off note, voice line, prompt preview). The `+` inserts
any node type, not just the two text ones, so a branching scene can be written
without leaving the page; shift-clicking `×` deletes everything below.
Continuation suggestions from the last generation appear under the row that
carries them.

Paste a block of screenplay text and it parses into nodes (`NAME:` for a line, a
bare paragraph for narration, `$` for a flag, `?`/`-` for a choice, ``` for raw
Ren'Py); importing only ever *creates* nodes, so nothing existing is rewritten.

Stage, set, condition, audio and end rows still open on the canvas for their
type-specific fields -- five different editors crammed into a row would make the
writing surface worse to write in -- but everything not type-specific is inline.

**Roleplay mode.** Talk to a character. You type your side, they answer, and the
conversation is kept as a conversation — not as a scene.

That is the decision it rests on, and it is the opposite of the Improv mode it
replaces: **a conversation is not the graph.** It lives in its own two tables
(`rp_conversations`, `rp_messages`), nothing in it is a node, and nothing the
compiler reads is touched. Improv made every typed line a `say` node spliced into
a scene's walk, which meant one turn of dialogue paid for the whole scene pipeline
— prose pass, structuring pass, staging reconciliation, nodes, edges, variants,
staleness — and got written by a scene-writing prompt that then had to be argued
out of emitting choice blocks.

The one bridge is an explicit, one-way port: a finished conversation can be turned
into a canvas scene, and the two have no link afterwards.

**The port is mostly code, and that was the point of the notation.** Cast lines
were written as `Alice (smirking, amused, 0.6): "line"` from the first turn —
the same house format a scene generation writes — so porting reuses
`extract_performance` unchanged, and the performance the model chose while you
were talking arrives on the say node. Only the structuring pass runs, and it is
the same cold segmenter the canvas uses, so nothing can hallucinate the
conversation away. What code has to do is the three things chat carries that the
graph does not:

| In the conversation | In the scene |
|---|---|
| `*leans in*` | a narration beat, conjugated to second person (`You lean in.`) |
| `[she should deflect]` | gone — nobody said it and nobody heard it |
| the persona's name | `{player}`, so the scene never calls the reader by somebody else's name |

Nobody is ever staged in a conversation, so the port starts from an empty stage
and `reconcile_staging` walks the cast on before their first line. The result is
recorded on the new scene node (`data._rp_conv`) rather than on the conversation
row: delete the scene and the conversation is simply un-ported, with nothing to
clean up. Porting twice builds a second scene rather than merging into the
first, and asks first. `rp.to_scene` is an optional tidy-up pass in front of the
segmenter (poses, run-on narration) and is **off by default** — the transcript
is already correct, so every extra pass is only a chance to lose a line of it.

**What makes the replies behave** (`services/roleplay.py`):

- **The transcript is sent as real chat turns** — `user` for your lines,
  `assistant` for theirs — instead of as one block of recent text. A 24B instruct
  model is trained on that shape and stops at your turn by reflex, so "never write
  the player's lines" is enforced by the shape of the request rather than by a
  regex racing the token stream. The regex is still there, as a net that should
  never fire.
- **The turn directive rides on your last message**, not in the system prompt.
  Last position is where a local model actually obeys, and appending rather than
  adding a message keeps the strict alternation Mistral-family chat templates
  need.
- **Memory is summaries, not transcript.** Ending a scene distils it into plot,
  what changed between you, what is now true, and what is unresolved; later scenes
  read those back. The previous scene's last few lines come along too, under a
  heading saying it is finished, purely so speech style carries over — sent as
  turns it reads as still live and the model just continues it.
- **The prompt is lean.** ~1,400 tokens of system prompt against Improv's ~4,000,
  because a bloated system prompt measurably reads worse and most of what Improv
  sent described machinery a conversation does not have.

| Action | What it actually is |
|---|---|
| Send an empty message | "Carry on without me" — no message is written, and the directive tells the cast to fill the silence |
| `*like this*` | An action, kept inline with your words; the model reads the convention natively |
| `[like this]` | A direction to the writer. Said by nobody, heard by nobody, and stripped out of the transcript on every later turn |
| ◀ n/m ▶ | Swipes. Arrowing past the last take writes a new one; the take that was showing is never destroyed |
| Rewind to here | Deletes this message and everything after. Nothing is parked — a conversation is a line with no branches |
| Hide a scene | Keeps it readable but takes it out of what later scenes remember |
| Port to scene | Builds a canvas scene from the conversation and takes you to it. One way: editing either afterwards does not touch the other |
| Prompt | Exactly what the model is sent, verbatim. There from the first version: tuning a prompt by reading replies and guessing what it was looking at is how Improv's prompt ended up the way it did |

Each character has their own chronological history, and a scene with two of them
in it appears in both. The order is what decides which summaries a later scene
remembers, so dragging the list re-orders the memory with it.

**Two things worth knowing about the model.** Both measured here, not assumed:

- **DRY and XTC never reach the model through LM Studio.** DRY is not in LM
  Studio 0.4.24 at all — there is no `dry*` key anywhere in its config schema. XTC
  *is* in the schema (`llm.prediction.llama.xtcProbability`), but it is applied
  neither from the request body nor from a per-model default config, both tested.
  Unknown keys are accepted silently rather than rejected, which is why this went
  unnoticed: asked to say one word twelve times with `dry_multiplier: 0.9,
  dry_allowed_length: 2` Cydonia 24B says it eleven times, and with
  `repeat_penalty: 1.8` it manages one.

  Verified as actually working from the body: `temperature`, `top_p`, `top_k`,
  `min_p`, `repeat_penalty`, `frequency_penalty` (1.5 cut a forced repetition from
  twelve to six), `seed`. `presence_penalty` appears inert. `rp.system`'s sampling
  uses only those. `PROSE_SAMPLING` still carries the inert DRY/XTC keys and the
  comments that believe in them — left alone deliberately, since changing how every
  scene in an existing project is written is not a safe side effect of this change.
  **If you want DRY, it needs a different server** — llama.cpp's `llama-server` or
  KoboldCPP both support it over an OpenAI-compatible endpoint. The cost is that
  `services/vram.py` drives model residency through the `lms` CLI, so the
  swap-for-image-jobs path would need reworking; Roleplay itself would not care,
  since it keeps both models resident anyway.
- **Some model builds reproduce their own context.** Measured on Cydonia 24B v4.3
  in two forms. A long multi-paragraph reply comes back near-verbatim, with
  invented `Human:` / `AI:` markers between the parts. And on the *opening* turn,
  where there is no assistant message to continue, it continues the system prompt
  instead and begins by reciting the character card. Neither is a prompt fault —
  the first survives a shorter system prompt, no directive, a different opening
  turn, an extra exchange in front and a busted prompt cache, and
  mistral-nemo-12b on the identical requests does neither.

  So both are caught after the fact, in `services/roleplay.py`: a reply that
  repeats the last one is written again, and if the second attempt repeats too the
  copy is trimmed and whatever real material sits behind it is kept
  (`echoes` / `strip_echo`); leading lines lifted out of the prompt are dropped
  (`strip_prompt_echo`); an invented turn marker ends the reply (`turn_end`).

**Real-time voice, and why it is off by default.** Both halves of this were
measured rather than assumed, and both came out worse than the plan expected:

- **IndexTTS-2.5 costs 6.5 GB resident** (6974 MiB peak during a synthesis, 6469
  MiB still held afterwards). The plan assumed 2–3 GB from the model's file size,
  which is not its footprint under load. It has to be resident for the whole
  session, because a cold load costs ~12 s — a per-line swap would be twelve
  seconds of silence before every spoken line.
- **Synthesis runs at roughly real-time**, with a fixed overhead that dominates
  short lines: 3.2 s for 4 words, 4.0 s for 15. So audio starts a few seconds into
  a reply and trails the text rather than keeping pace with it, and a reply of
  several short lines falls behind.

Against a 24B at Q4_K_M that does not fit on this machine (16 GB RTX 5080 + 8 GB
RTX 3070): the LLM takes 16.9 GB across both cards at 16k context, the desktop
takes 3.0 GB, and the most any single card has free is 2.4 GB. The voice model is
one model and ComfyUI does not shard it, so the largest free *card* is the number
that matters, not the total.

`GET /rp/vram` reports this live (Config → Writing → Roleplay shows it), because
the answer moves with the context length the LLM was loaded at. To make voice fit,
the LLM has to come down to about 13 GB so that one card is left clear — a smaller
quantisation or a shorter context. `rp_tts` is the toggle; `rp_tts_vram_mb` is the
measured figure to re-check after changing the TTS workflow.

**Tuning the prompt.** `backend/bench_roleplay.py` scores template variants on
eight objective measures — format compliance, player-boundary violations, emotion
spread, dialogue ratio, lockstep, distinct openings, length variety, banned
constructions — across seven fixed conversation fixtures, so variants are compared
on identical inputs:

```bash
.venv/Scripts/python bench_roleplay.py --variants variants.json --seeds 3 --out run.json
.venv/Scripts/python bench_roleplay.py --rescore run.json     # re-measure, no GPU
```

Every reply is saved, so improving a metric costs nothing and never invalidates an
earlier round — which matters, because five of the nine metrics turned out to
be measuring the wrong thing and each correction is recorded in the comment above
it. Two returned *identical values across every variant*, which looks exactly like a
result; one scored a perfect 1.000 on output containing unattributable dialogue.
Nothing here scores prose quality; the harness exists to eliminate the measurably
broken variants so human reading is spent on a shortlist.

**What four rounds (~460 generations) actually settled**, and what they cost:

- **Adopted:** shape *examples* for reply rhythm (round 1), and one clause in the
  turn directive requiring a speaker name on every spoken line (round 3). The
  second roughly halves speaker-less dialogue — pooled across rounds, 6.2% with the
  clause against 11.9% without.
- **Rejected:** the three opening-directive rewrites, whose whole justification was
  card-echo from the broken model; and showing the wrong-vs-right format example
  with no rule text, which scored no better than doing nothing at all.
- **Adopted then reversed:** the long emotion-tagging rewrite. It won round 2 by
  0.021 at three seeds, then at five seeds lost on format (0.893 vs 0.940),
  dialogue ratio (0.493 vs 0.532) and speaker-less lines (8.7% vs 5.3%) — and its
  own target metric came out tied (0.847 vs 0.845), so the number that justified
  adopting it was noise. The mechanism is visible in the counts: it made replies
  more narration-heavy, and narration→dialogue is exactly where the speaker name
  gets dropped.
- **Not established:** whether the narration rule earns its place on top of the
  directive clause. Kept because the configuration that measured best overall
  contains it, not because it was isolated.

- **A wall, found in round 5.** Across 105 generations, **not one reply came in
  under 15 words**, and the shortest was 18. The variant that asked for it as
  directly as it can be asked — "roughly one reply in three should be SHORT, a
  single line, under fifteen words" in the highest-weight slot — produced the
  *longest* minimum of the three (29 words). Reply lengths cluster at CV 0.30
  around a mean of 59. Prompt wording does not move this.

Three lessons worth keeping. **Three seeds screens, five seeds decides** — a
variant scored 0/37 on format at three seeds and 6/69 at five, so the perfect run
did not replicate. **"Examples beat instructions" is not a general law here**: it
held for teaching a reply *shape* and failed completely for enforcing a format
*constraint*, where the rule text did all the work. And **a static prompt cannot
produce variety across turns** — it is identical on every turn, so it can shift the
centre of a distribution but not deliberately alternate. Reply-length variety needs
the rotating per-turn directive that was left out of the original plan; round 5 is
the evidence for that rather than an argument about it.

Of the four complaints the mode was built to fix, three turn out not to be real on
a healthy model (emotion flatness, shape repetition, lockstep) and one is real and
unreachable from the prompt (length uniformity). What was real and fixable was
format compliance, now at 0.971 with 2.9% speaker-less lines.

The shipped `rp.system` and `rp.turn_directive` match the best-measured
configuration byte for byte.

**Playtest from anywhere.** Any node is directly playable: the preview
synthesises an entry that first replays the background and everyone on screen
from resolved path state, then runs from that line. Combined with the starting
flag editor, you can test the high-affinity branch without earning it first.

**Edit while you play.** `✎ Edit line` (or `E`) opens the canvas inspector
beside the stage, pointed at the line on screen, and it follows playback onto
each new line. Click a sprite or the backdrop to open its tab instead: it
shows which sprite or background variant the stage wanted and which one it
fell back to, and regenerates that exact slot (with one-off extra tags for a
sprite). A line's expression and pose can be changed from its speaker's tab.
An edit re-runs only the line on screen, against the stage this playthrough
built, so flags, visited scenes and taken options survive and nothing needs
replaying to see the change. A finished render swaps in without a recompile.

**Asset gallery.** Every generated file with what still references it -- nodes,
sprites, backgrounds, takes -- grouped by the character it belongs to (its
sprite/profile owner, the speaker of a say node that uses it, or an explicit
tag), with everything unattributable collected at the end. Unused assets are
flagged, anything still referenced refuses to delete without a force, and one
button sweeps every unreferenced asset off disk and out of the index at once
(store-wide: an asset another project still uses is left alone).

Anything a generator made can be re-shot from here, and the new file lands on
whatever pointed at the old one -- a CG back onto its node, a line back onto
its say node, a sprite into the same outfit/pose/expression slot, a background
into the same time-of-day variant. The job that runs is the one the inspector
would have run; the gallery only aims it. Which job that is, and the payload
for it, is written where the hash was read out of the record rather than
guessed back out of the label, so there is no second table to drift. An SFX or
a voice reference has no regenerate button at all, because those come from you.

Assets are content-addressed, so one image can hang off two nodes: each use
gets its own button and its own render, and the old file stays in the store --
a regenerated CG is kept as a take on its node, so this is never destructive.
Every `used by` row is also a link to the thing that uses it, which opens the
node on the canvas with its inspector, the character on their own page, or the
location under Setting.

**Batch fill.** One action queues every asset the project is missing, built to
be started and left overnight. Scoped to what the script actually uses: only
sprite combinations some node really shows, and only the time-of-day background
variants some scene asks for. It reports blocked work (a character with no voice
reference, say) instead of queueing jobs that would fail.

**Missing audio.** The same scan also lists every sound and music cue with no
file behind it — the one kind of missing asset nothing generates, since the
writer marks a *description* of the sound and the file comes from you.
Until it does, the cue compiles to a `# TODO sfx:` comment, which keeps the
build green and makes the silence easy to miss; the report on the Build view is
the view over them, grouped by scene, with a box to drop the filename straight
in.

**Routes view.** Balancing a branching story is otherwise blind: route B reads
as "about the same" as route A right up until it is voiced and turns out to be
half the length. Every scene is measured — words, lines, choices, branch depth,
estimated playtime, % voiced, % imaged — grouped under the route it claims, with
a bar per route scaled against the longest so the imbalance is one glance rather
than a column of numbers to compare by eye.

Counted across every branch in a scene, not one linearized path: the branch
nobody walks still had to be written and still gets voiced. Scene rows are
links — the title opens it on the canvas, `script` opens it in the writing
surface — and `＋ New scene in <route>` creates one already tagged, at the end
of the route, because an untagged scene is invisible to every hub dispatching
over that route and nothing complains, since claiming no route is legal.
Renaming a route carries the hub options targeting it in the same pass; moving
only the scenes would leave every one of those options pointing at a route
nobody belongs to. A cast table underneath ranks who actually carries the story,
by share of words, lines and routes they appear in.

**Hub nodes.** A Choice with structured conditions: `{stat, op, value}` picked
against the project's declared stats rather than typed as an expression, so a
gate on a stat nobody declared is a validation error instead of something that
quietly evaluates False for the whole game. A topic that is not yet earned can
either vanish or show greyed with its own label (Ren'Py has no disabled menu
item, so grey compiles to a second entry under the negated guard that drops
back to the menu). A topic can also target *the next scene in a route the
player has not reached yet* — scenes claim a route by name, so the hub does not
need rewiring every time one is added.

**Characters.** Wizard with tag-mapped dropdowns and free-text fallback; every
appearance field stores both a human label and a booru tag. Age is a hard 18+
floor enforced on the schema, so no record reaches the image pipeline without
it. Outfits are objects on the character; a Stage node mutates the active one,
and path state resolves by walking root → node.

**Which tags a CG may use** is a switchboard in the node's Event CG panel:
every tag the cast contributes to *this* shot, identity and outfit both,
grouped by character, as badges you click to strike out. A tag in a prompt is
a request to find room for the thing, so `brown loafers` on a frame that ends
at the shoulders does not get ignored — it gets shoes, or a pair of feet where
they cannot be.

It is per shot, not per character, because that is what it is a property of:
the same character two beats later in a full-body shot still wants her shoes,
and the sheet the LoRA trains on has to keep her whole. Within a shot it
applies to everyone in frame — "this frame does not show feet" is a fact about
the frame, and a second character standing in it does not get shoes either.

Matching is on the tag text rather than on which field produced it, because
shoes come from an outfit's free-text block and a chest tattoo from a
distinguishing mark, and a per-field switch would have to be built twice and
still miss the outfit string. The cost is that re-tagging an outfit orphans a
switch, so orphans are listed rather than left silent. The badge list is the
render's own per-subject breakdown, so it can never offer a toggle for a tag
the shot does not emit.

**LLM.** Generation runs in two model calls and a code pass, cut so each one
has a coherent job:

1. **Prose** — unconstrained, creative sampling (temp 1.0, min_p, DRY and XTC
   on). This is where writing quality lives, and it writes the performance too:
   each spoken line carries its own bracket — `Aiko (smirking, amused, 0.4):
   "..."` — plus `SFX:`, `MUSIC:` and `CG:` cue lines. The writer knows what her
   face is doing at the moment it writes the line, and a tag written on the line
   cannot come off it.
2. **Structure** — cold, schema-constrained, **segmentation only**. It copies
   text verbatim into nodes and decides nothing else, which is the only
   condition under which a small strict instruct model is the right tool. The
   notation is stripped in code first, so it only ever sees `Name: "line"`.
3. **Apply and reconcile** — no model. Each written tag is matched back onto its
   node *by text*, so a line the segmenter merged or dropped cannot shift every
   expression after it onto the wrong speaker. Then the present-set is walked:
   anyone about to speak who is not on stage gets staged, and staging that
   changes nothing — a character already visible, a `hide` for someone who is
   not, the location that is already the location — is deleted rather than
   rendered as a node that does nothing.

This replaced a third model call that read the finished passage back and
returned annotations keyed by node index. An index is the one thing a model
loses track of: one skipped beat or one 1-based list and every expression landed
on its neighbour, which is what "the emotions are random" looks like from the
graph.

Any generation takes an optional one-off instruction ("shorter", "colder",
"more subtext") that applies to that run only and is never saved to the
template. Context is assembled as the path from root to the insertion node, not
a chat log — which is what makes branch-aware regeneration correct. Character
cards carry each character's **current** stat bands, so the writer gets the
relationship as it stands rather than the one written at character creation,
and everyone not in the scene appears as a one-line roster so they can be
discussed without being invented. Rolling summaries are cached against a hash
of upstream content and include a character-state block, not just plot. Prompt
templates are versioned and diffable.

**NSFW is a checkbox on the scene.** Tick it and `{% if nsfw %}` blocks render:
the adult-register instruction in the prose prompt, and each character's NSFW
personality notes in their card. Leave it off and that text is not in the prompt
at all — absent, rather than present under an instruction to ignore it, which is
a distinction a 24B does not reliably hold.

It is per scene because there is no project-wide content rating: one value over
a whole story cannot describe a story with a single explicit scene in it, and
the register is the only thing a rating was ever used for. It is a flag rather
than one value of a tone enum for the same reason in miniature — a scene is
tender or confrontational *and* explicit or not, and folding those into one
dropdown meant picking "explicit" threw away what the scene actually was.

**Character stats.** Declared once per project and instantiated for every
character as `{stat}_{character}` (`affection_diana`), with optional named
bands. Choice effects and conditions read and write them, they compile to
Ren'Py `default` statements, and a hand-declared variable of the same name
always wins so existing projects are untouched.

**Images.** ComfyUI workflows are JSON templates with `{{placeholder}}` tokens.
Three asset classes: backgrounds per location × time, sprites per character ×
outfit × expression (compiled into a Ren'Py `layeredimage`, so expression changes
cost nothing at runtime), and event CGs only where flagged. The LLM writes only
the variable parts — shot, expression, pose. Identity tags come from the
character record every time, unchanged.

**The CG director** used to write its tags under a grammar `enum` over a
curated 276-tag palette. That made invention impossible — measured: 47% of its
tag uses had been tags that do not exist — and cost more than it bought. The
palette is 276 tags against a six-figure dictionary, so `window`, `wet clothes`
and `school desk` were unsayable; and an enum with `minItems` forces N members
whether or not the model has N things to say, so raising the floor for density
bought filler. Those are one knob and it cannot satisfy both.

Now the tag fields are free and the dictionary checks them afterwards.
`services/tags` holds the real Danbooru corpus, imported at ≥100 posts — below
that a checkpoint has seen a handful of images, which is indistinguishable from
noise — and every tag the director writes is snapped to a real spelling or
dropped. Invention is still caught; it is caught by data instead of by
shrinking what can be said. The curated palette stays as the *suggested*
vocabulary, priming good choices without forbidding the rest. The register gate
moved with it: an SFW scene's adult tags are screened out by the validator
rather than being absent from its grammar, with the curated SFW palette
outranking the screen so `all fours` and `straddling` survive on an ordinary
shot.

Four more things changed with it:

- **It describes the shot in English first.** `shot_description` is the first
  property in the schema, and llama.cpp builds its grammar in property order —
  so the model commits to a reading of the beat before it picks a single tag,
  and every tag is conditioned on it. Chain-of-thought for one line of schema.
  It is kept on the node and editable, because it is the only human-readable
  record of *why* these tags.
- **Framing gates the body.** A lookup, not a model decision: `close-up` and
  `portrait` reach the shoulders, `upper body` the chest, `cowboy shot` the
  knees. Everything below the cut is dropped from identity and outfit before
  assembly, so a frame that ends at the shoulders stops asking for shoes — a
  tag for something outside the frame is not ignored, it is a request to find
  room for it. An ambiguous framing (`close-up, full body`, which directors
  write more often than they should) keeps the wider reading, because cropping
  on the tighter one is the destructive mistake. The per-shot switches are the
  manual override, and the inspector says which tags the framing took.
- **Tags carry emphasis.** ComfyUI's stock `CLIPTextEncode` parses
  `(hug:1.15)`, so this needs no node and no dependency. Weights are assigned
  from the tag's own category — what the bodies are doing goes up, the room and
  the light step back, identity is never touched — and never written by the
  model, which has no calibration for what 1.3 does to a sampler and would
  treat a decimal field as one more thing to invent.
- **The camera cannot repeat itself.** The prompt has always been shown the
  scene's recent shots and always treated "vary it" as a request, returning
  `close-up, from above, pov` four beats running. Now a framing this scene has
  just used is removed from the answer — but only when another framing
  survives it, because a shot with no framing at all hands the composition back
  to the checkpoint, which picks the same mid-shot every time.

A CG renders **N candidates at once** (×1/×2/×4 next to the button, or up to 8
under Config), because the cost of a CG is not the sampling — it is the round
trip of queue, wait, look, dislike, click again, and a batch collapses four of
those into one. Every candidate is kept as a take, the first goes on the node,
and the take strip is the picker: thumbnails, click one to apply it. Nothing is
discarded, so changing your mind later costs nothing.

It is the latent's `batch_size` rather than N copies of the sampler graph,
because a workflow is a template you can rewrite on the config page and
duplicating nodes inside an edited one is how that breaks. The trade is that a
batch's images are not individually reproducible — ComfyUI draws the whole
batch's noise from one generator, so the seed alone gets you image 0, not image
2 — which is why each take records its position as well as its seed.

The unattended paths deliberately stay at one image per node: an overnight fill
and the stale sweep have nobody there to choose, and four times the candidates
is four times the hours.

**A CG can stay up across lines.** By default a CG is shown for its own line
and hidden after it. Tick **Keep it on screen after this line** and the lines
after it play over the picture until the next CG replaces it, a scene or
location change clears the stage, or a later line is set to **Clear it before
this line**. Ren'Py keeps an image up until something hides it, so this is
only a question of which `hide cg` the compiler leaves out. A CG replacing a
held one, and a clear, both dissolve rather than cut, since the usual case is
two versions of one picture in a row. CGs sit above the sprites (`zorder 10`),
so a character staged mid-CG stays behind it.

A line flagged for a CG ends a held run whether or not it has been rendered —
the held picture stands in for it until it is — so rendering it later never
changes where the run ends. Every CG statement goes before the `voice` line: a
dissolve is an interaction, and a voice queued ahead of one would start on the
dissolve instead of on the line. The rule lives once in `graph.resolve_state`,
which both compilers read; the Play view draws a held CG full-stage across the
same lines, and cards and Script rows mark who holds one (`CG ⇣`) and who plays
under one (`⇣ CG`). `frontend/src/lib/cg.ts` is its browser twin, for the
inspector and those markers, and has to stay in step with it.

**Start from the previous CG.** A line's Image section shows the nearest CG
above it in the scene. **Keep it on screen** sets that line's hold from here;
**Start this line's CG from it** opens the refine panel on the earlier picture.
Paint the part that changes to inpaint just that region, or leave it unpainted
and lower adherence to vary the whole frame. The prompt is this line's own, so
distill it first. Results land as takes on this line and record the image they
came from; the earlier line keeps its CG.

**LoRA bootstrap.** Character sheet at a fixed seed → cull in a grid → WD14
auto-caption → Kohya training → the result is written back onto the character
and used automatically from then on.

**TTS.** IndexTTS-2.5 through ComfyUI: a frozen reference clip carries timbre,
emotion is supplied separately as an 8-dim vector. The emotion enum maps to
vectors through an editable table that enforces the wrapper's constraints (each
slider ≤ 1.2, sum ≤ 1.5), with a per-character
`emotion_scale` cap for timbre leakage. Includes the casting flow: audition the
seed clip across a spread of emotions — one line per emotion, written to fit it
— to see how far the timbre holds before freezing the neutral take forever.

The writer's delivery note ("trails off", "flat", "too quick") shapes the read
through `DELIVERY_RULES`: pacing, intensity, and punctuation on the spoken copy
only. Pacing runs inside the model (`duration_factor`), not as a stretch on the
finished clip. A line's first voicing is one take; voicing it again rolls
`voice_retakes` seeds (3 by default) and keeps them all in the take strip, each
with a ▶ to compare by ear.

**Jobs.** SQLite-backed queue that survives restarts, with SSE progress to the
canvas badges. GPU-class jobs take a single lock, because one 24 GB card cannot
hold an LLM, SDXL and TTS at once.

**Staleness.** Every generated asset records the text hash it came from. Edit
the text and its image and voice go stale, with a batch "regenerate all stale"
before a build — without this you ship a build where the voice line does not
match the words on screen.

A voice line has a second input no hash of its own text can see: the reference
clip. IndexTTS-2.5 takes timbre from that clip and nothing else, so recasting a
character mid-project leaves every line already rendered in a voice the game no
longer uses — audio that matches its text perfectly and is still wrong. The
clip is stamped alongside the text hash, freezing a new one sweeps that
character's lines to stale and says how many, and the report separates the two
causes, because one means re-voicing a line and the other means re-voicing a
cast member. Tuning `emotion_scale` or `speed` does not trip it: those get
nudged all through casting, and flagging the whole game on each nudge would
make the badge noise.

---

## Layout

```
backend/
  app/
    models.py            the IR: nodes, characters, projects, templates
    graph.py             in-degree, label naming, path walk, state resolution
    compiler/
      labels.py          shared label rules -- both targets use exactly these
      renpy.py           IR -> .rpy
      preview.py         IR -> web player program
    services/
      script.py          linearization + the paste parser
      batch.py           scan for every missing asset
      pacing.py          route + scene metrics; counts, never stores
      context.py         layered prompt assembly + cached rolling summary
      generation.py      the four stages, splicing, orphaning, variants
      images.py          prompt assembly per asset class
      workflows.py       ComfyUI workflow templates
      tts.py             emotion vectors, synthesis, Ogg encoding
      staleness.py       content hashing
      build.py           export, validate, launch, lint
    jobs/                SQLite queue + handlers
    routers/             the HTTP API
  net.py                 LAN origin policy, optional shared PIN
  serve.py               launcher: --lan, --pin, prints the phone URL
  tests/                 493 tests: compiler rules, API flows, generation,
                         script round-trip, batch scan, gallery, LAN auth
  seed_demo.py           the doc's Phase 1 check, runnable
frontend/src/
  lib/auth.ts            401 -> re-lock signalling (never a page reload)
  lib/history.ts         undo/redo journal of inverse operations
  lib/collapse.ts        scene folding + edge re-routing
  components/            canvas, custom nodes, inspector, job dock, batch panel
  views/                 script, characters, gallery, config, build, player
```

## Tests

```bash
cd backend  && .venv/Scripts/python -m pytest tests/ -q   # 493 tests
cd frontend && npm test                                    # 84 tests
```

The compiler tests assert the cases the doc flags as risky: two branches
converging, three-way merges, a branch jumping backward into an earlier label,
nested conditionals, label stability under node reordering, and that every node
is emitted exactly once.

The script tests assert the round-trip property that matters: parse → emit →
parse is stable and does not mangle words, and neither editing a line nor
importing a pasted block moves any existing label.

---

## Ren'Py project layout

Only `game/generated/` is ever rewritten. `script.rpy`, `custom.rpy` and
`options.rpy` are scaffolded once if missing and then left alone, so you can
hand-edit the game freely alongside this tool.

```
game/
  script.rpy         hand-written entry point -- never overwritten
  custom.rpy         your screens and logic -- never overwritten
  generated/
    characters.rpy   define statements with voice_tags
    images.rpy       layeredimage definitions, backgrounds, CGs
    flags.rpy        default statements, menusets, persistent unlocks
    scenes/*.rpy     one file per Scene container
  images/  audio/voice/
```

## Notes on scale

Graph readability degrades past a few hundred nodes; the minimap, auto-layout,
search and scene list are all in from the start rather than retrofitted. A fully
voiced 2,000-line VN is an overnight batch — roughly 3 hours of TTS and 1.5
hours of images on a single 4090 before retries — so the queue is built for
unattended runs, not interactive waiting.
