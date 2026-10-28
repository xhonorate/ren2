# Getting started — zero to a fully running instance

This walks you from a fresh clone to generating prose, images and voice, and
exporting a Ren'Py build. The compiler and editor work with nothing else
installed; everything AI is layered on top and is independently optional.

Read [`vn-authoring-tool-architecture.md`](vn-authoring-tool-architecture.md)
for the _why_. This document is the _how_.

---

## 0. The one thing to understand about ComfyUI first

**You do not build a workflow graph by hand for image or TTS generation.** The
graphs already exist, as JSON templates in
[`backend/app/services/workflows.py`](backend/app/services/workflows.py):

| Template key      | What it is                                              | Used by                                    |
| ----------------- | ------------------------------------------------------- | ------------------------------------------ |
| `txt2img`         | SDXL checkpoint → LoRA → KSampler → SaveImage           | CGs, sprites, sheets for LoRA'd characters |
| `txt2img_no_lora` | same without the LoRA loader                            | backgrounds, un-LoRA'd characters          |
| `sprite_suffix`   | background-removal node appended to txt2img             | sprites (transparent PNG)                  |
| `cg_face_suffix`  | FaceDetailer pass appended to txt2img                   | event CGs (likeness at a low/absent LoRA)  |
| `tagger`          | LoadImage → WD14 tagger → PreviewAny                    | LoRA bootstrap auto-caption                |
| `tts`             | LoadAudio + emotion vector → IndexTTS-2.5 engine → TTS Text → SaveAudio | every voice line, casting auditions |
| `tts_indextts2`   | the previous IndexTTS-2 graph, kept to paste back into `tts`            | reverting to the older wrapper pack |

Each is a **ComfyUI API-format graph** (the JSON you get from _Save (API Format)_
in the ComfyUI web UI, not the editor-format `.json`) with `{{placeholder}}`
tokens. On every job the backend:

1. deep-copies the template,
2. substitutes the tokens (prompt, seed, dimensions, checkpoint, LoRA, the emotion
   vector, …) — see [token reference](#appendix-a--workflow-token-reference),
3. `POST`s it to `http://<comfyui>/prompt`,
4. follows progress over the ComfyUI websocket,
5. downloads the output via `GET /view` and stores it content-addressed under
   `data/assets/`.

The browser never talks to ComfyUI. Everything goes through the backend.

**What you actually have to do** is therefore:

- install ComfyUI + the models the templates reference,
- install the **custom nodes** the templates reference — background removal
  (`ComfyUI_essentials` → `RemBGSession+` + `ImageRemoveBackground+`),
  `ComfyUI-WD14-Tagger`, and **TTS Audio Suite** 5.8.0+
  (`diodiogod/TTS-Audio-Suite`, which is where IndexTTS-2.5 lives). Easiest via
  **ComfyUI Manager** ("Install Custom Nodes"),
- if your installed nodes register different class names, point the app at them
  in Config → workflows (or edit the template JSON).

The backend stages timbre reference clips into ComfyUI's `input/` dir for you, so
there is no shared-filesystem step for TTS — see [§5.3](#53-the-audio-loader).

You only ever open the ComfyUI web editor to _design a replacement graph_ when a
default template doesn't fit your install — then you export it as API format and
paste it into Config → workflows, keeping the `{{tokens}}`.

---

## 1. Prerequisites

| Need                                                               | For                                | Required?        |
| ------------------------------------------------------------------ | ---------------------------------- | ---------------- |
| Python 3.10+                                                       | backend                            | yes              |
| Node 18+                                                           | frontend                           | yes              |
| An NVIDIA GPU (≈12 GB+, 24 GB comfortable)                         | any local generation               | yes for AI       |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI)               | images **and** TTS                 | for images/voice |
| [LM Studio](https://lmstudio.ai) (or any OpenAI-compatible server) | prose / structuring / summaries    | for text gen     |
| ffmpeg on `PATH`                                                   | Ogg encoding **and** voice speed   | recommended      |
| Ren'Py SDK 8.x                                                     | launching / linting the built game | optional         |
| [kohya_ss](https://github.com/bmaltais/kohya_ss)                   | character LoRA training            | optional         |

One GPU cannot hold an LLM, SDXL and IndexTTS-2.5 resident at once. The job queue
serialises GPU work by default (`gpu_serialize`), and there is a **Free ComfyUI
VRAM** button in Config → services. Set LM Studio to JIT-load / auto-unload if you
run it on the same card.

---

## 2. Run the app itself

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt          # Windows
# source .venv/bin/activate && pip install -r requirements.txt    # macOS/Linux
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api to :8000
```

Or build the UI once and let the backend serve it on `http://localhost:8000`:

```bash
cd frontend && npm run build
cd ../backend && .venv/Scripts/python serve.py           # add --lan for phones
```

Data lives in `./data/` (`data/vn.sqlite3`, `data/assets/`, `data/builds/`).
Override with the `VN_DATA_DIR` env var.

### Smoke test with no models

```bash
cd backend && .venv/Scripts/python seed_demo.py --export ../demo_renpy
```

Builds a 22-node branching graph, compiles it, writes a Ren'Py project. If that
runs, the core is healthy. Then open the app and either keep the demo project or
create a new one, and add at least one **character** (18+ enforced) and one
**location** — image and voice jobs resolve their prompts from those records.

---

## 3. Wire up text generation (LM Studio)

Scene generation needs it, and the structuring pass is what fills the `emotion` /
`expression` / `shot_hint` fields that image and TTS jobs read.

1. In LM Studio, start the local server (default `http://127.0.0.1:1234`) and
   load a prose model. A creative writing fine-tune is worth it here.
2. App → **Config → services**: set **LM Studio URL**, click **Check services**.
3. Leave the model fields blank to use whatever LM Studio has loaded, or name a
   specific model for the prose vs structuring passes.

---

## 4. Wire up ComfyUI

### 4.1 Install and launch ComfyUI

Install per its README, then run it headless:

```bash
python main.py                 # binds 127.0.0.1:8188 — fine if same machine as the app
python main.py --listen        # binds 0.0.0.0:8188 — needed if the app is elsewhere
```

Notes:

- ComfyUI has **no authentication**. Bind to loopback, or to the LAN only behind
  a firewall. Never expose `:8188` publicly.
- The backend uses the websocket (`/ws`) for progress and falls back to polling
  `/history` if it goes quiet for 60 s. A reverse proxy in front of ComfyUI must
  allow WebSocket upgrades.
- After you install any custom node, **restart the backend** too — it caches
  ComfyUI's `/object_info` node schema at startup.

### 4.2 Download the image models

| File                                                   | Put it in                     | Default the app expects             |
| ------------------------------------------------------ | ----------------------------- | ----------------------------------- |
| A WAI-illustrious / Illustrious-family SDXL checkpoint | `ComfyUI/models/checkpoints/` | `waiILLUSTRIOUS_v140.safetensors`   |
| (optional) character LoRAs                             | `ComfyUI/models/loras/`       | per-character, set on the character |

Then in **Config → services**:

- set **ComfyUI URL**,
- click **Check services** — you should see `up` and a non-zero checkpoint count,
- set **SDXL checkpoint** to your exact filename if it isn't the default,
- **Steps / CFG / Scheduler**: defaults are `28 / 5.0 / normal` for WAI. If you
  switch to **NoobAI-XL V-Pred**, keep the sampler `euler`, CFG 4–5, and
  **do not** use a `karras` scheduler (it produces artifacts in v-prediction
  mode).

Sprite dimensions default to 832×1216, CGs to 1344×768, backgrounds to 1344×768.

### 4.3 Install the image custom nodes

| Node class (default)                                   | Provided by                                                                 | Needed for                       | If missing                                      |
| ------------------------------------------------------ | --------------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------- |
| `RemBGSession+` + `ImageRemoveBackground+`              | [ComfyUI_essentials](https://github.com/cubiq/ComfyUI_essentials)           | transparent sprite PNGs          | sprite job falls back to a flat PNG and says so |
| `WD14Tagger\|pysssss` (model `wd-v1-4-moat-tagger-v2`) | [ComfyUI-WD14-Tagger](https://github.com/pythongosssss/ComfyUI-WD14-Tagger) | LoRA-bootstrap auto-caption only | caption step errors with a clear message        |

Background removal is two nodes, not one: `RemBGSession+` builds a rembg
session (which model, which execution provider), and `ImageRemoveBackground+`
consumes it alongside the image. Both come from the same `ComfyUI_essentials`
install — if you have one you have the other.

If your install registers these under different names, fix them in **Config →
workflows → Custom node classes** (`rembg_class`, `rembg_session_class`,
`rembg_model`, `rembg_provider`, `tagger_class`, `tagger_model`) and **Save
node classes**. If your remover isn't session-based at all (a single node that
takes just an image, e.g. BiRefNet from a different pack), the class-name
fields aren't enough — edit the **`sprite_suffix`** template itself
(Config → workflows) to drop the session node and wire the image straight into
your remover. `LoraLoader`, `KSampler`, `CheckpointLoaderSimple`,
`CLIPTextEncode`, `EmptyLatentImage`, `VAEDecode`, `SaveImage` are all core
ComfyUI.

### 4.4 Test image generation

1. Create a location, give it booru-ish `tags` (e.g. `classroom, indoors, window`).
2. Editor: select a Scene node → generate its background, **or** Gallery / Batch
   fill. On a `say` or `narration` node the Inspector has an **Event CG** panel:
   **Distill prompt** reads that beat, the beats around it, and who is on stage,
   and writes the shot as Danbooru tags into two editable boxes — camera/framing
   and action/staging. Edit them, check **Show final prompt**, then **Generate**.
3. Watch the job dock. On success the asset appears in the node badge and the
   Gallery, stored at `data/assets/img/<sha256>.png`.

If it fails, the error text is verbatim from ComfyUI — usually a wrong checkpoint
filename or a missing node class.

### 4.5 About LoRAs

Training one is its own walkthrough — installing Kohya, the character-sheet
→ cull → caption → train loop, and what makes a dataset good:
[`lora-training.md`](lora-training.md).

- The trained-LoRA path written back onto a character by the Kohya job is an
  **absolute path**. `LoraLoader` wants a name **relative to
  `ComfyUI/models/loras/`**. After training, copy the `.safetensors` into that
  folder and set the character's **LoRA path** field to just the filename.
- `LoRA weight` and `trigger word` are on the character's Sprites/assets tab. The
  trigger is injected into every prompt for that character automatically; you
  never type it into a prompt.

---

## 5. Wire up TTS (IndexTTS-2.5 through ComfyUI)

The default `tts` template is built for **TTS Audio Suite** 5.8.0+
([`diodiogod/TTS-Audio-Suite`](https://github.com/diodiogod/TTS-Audio-Suite)),
which is where IndexTTS-2.5 lives. Among other nodes it registers an engine
(`IndexTTSEngineNode`), an 8-slider emotion vector (`IndexTTSEmotionOptionsNode`)
and a synthesis node (`UnifiedTTSTextNode`) — and the app uses exactly that
trio: timbre from the reference clip, emotion from the 8 sliders, the
speaker/emotion split the whole design depends on (doc S5).

2.5 over 2.0: faster inference, Japanese/Spanish/Arabic alongside Chinese and
English, and a native pacing control. The emotion vector is unchanged, which is
why nothing above the template had to move.

> **Coming from IndexTTS-2?** 2.5's weights don't load in the old kaola pack and
> 2.0's don't load in the new one — this is a node-pack switch, not a model
> swap. The old graph ships as the `tts_indextts2` template: paste it into `tts`
> in Config → workflows to go back. And re-voicing a line with 2.5 will not
> match a take rendered by 2.0 — same clip, same vector, slightly different read
> — so re-voice a project in one batch rather than half of it.

### 5.1 Install the nodes + model

1. **ComfyUI Manager → Install Custom Nodes → "TTS Audio Suite"** (or
   `git clone https://github.com/diodiogod/TTS-Audio-Suite` into
   `ComfyUI/custom_nodes/`). Restart ComfyUI. It wants **Python 3.12+** — an
   older ComfyUI portable will not load it.
2. **The weights download themselves** on the first generation, into
   `ComfyUI/models/TTS/IndexTTS/`. Nothing to do unless you are offline or on a
   metered connection — then stage `IndexTeam/IndexTTS-2.5` there yourself and
   the suite prefers the local copy:

   ```bash
   # from the ComfyUI root; `hf` is the current name of the huggingface-cli
   hf download IndexTeam/IndexTTS-2.5 --local-dir models/TTS/IndexTTS/IndexTTS-2.5
   ```

   Let it finish: `gpt.pth` is ~500 MB–1.5 GB and `s2mel.pth` ~200 MB; a file
   only a few KB means an interrupted download — delete it and retry. Which
   checkpoint the engine loads is its `model_path` input, exposed as
   **Config → workflows → Custom node classes → `tts_model_path`**
   (`IndexTTS-2.5` by default; the node also offers `IndexTTS-2`).
3. **Install the pack's Python deps into ComfyUI's venv.** ComfyUI Manager often
   skips or half-applies `requirements.txt`. Use the venv Python of *your*
   ComfyUI (a Comfy-Desktop install lives at e.g.
   `…\ComfyUI\.venv\Scripts\python.exe`):

   ```bash
   # from the pack folder
   <comfy-venv-python> -m pip install -r requirements.txt
   ```

   Restart ComfyUI, then confirm in its log that the pack loads with no import
   error. If a version it pins breaks a *different* custom node, run a second
   ComfyUI in its own venv just for TTS and point the app's ComfyUI URL at
   whichever one you are generating with.
4. Restart the backend so it re-reads `/object_info`.
5. Check IndexTTS-2.5's license terms (bilibili Model Use License Agreement)
   before you distribute a build.

### 5.2 The `tts` template, and when to touch it

Shipped default ([`workflows.py`](backend/app/services/workflows.py), `TTS`):

```
node 1  LoadAudio                 audio: {{reference_clip}}
node 2  {{tts_emotion_class}}     Happy / Angry / Sad / Afraid / Disgusted /
                                  Melancholic / Surprised / Calm ← emotion tokens
node 3  {{tts_engine_class}}      model_path: {{tts_model_path}}, device auto,
                                  emotion_control ← 2, emotion_alpha 1.0,
                                  duration_factor 1.0, language English,
                                  use_random: {{tts_use_random}}, use_fp16 false
                                  (edit use_fp16 → true here to save ~1 GB VRAM)
node 4  {{tts_text_class}}        TTS_engine ← 3, opt_narrator ← 1 (the timbre
                                  clip), text: {{text}}, narrator_voice none,
                                  seed: {{seed}}
node 5  SaveAudio                 audio ← 4, filename_prefix: vn/voice/{{prefix}}
```

Every input the engine marks *required* is written out, node defaults included,
so a pack update that drops a default cannot 400 the whole workflow.

The app maps its dimension names onto the node's slider names, so you don't have
to:

| app dim (`EMOTION_DIMS`) | token            | node slider   |
| ------------------------ | ---------------- | ------------- |
| happy                    | `emo_happy`      | `Happy`       |
| anger                    | `emo_anger`      | `Angry`       |
| sad                      | `emo_sad`        | `Sad`         |
| fear                     | `emo_fear`       | `Afraid`      |
| disgust                  | `emo_disgust`    | `Disgusted`   |
| melancholy               | `emo_melancholy` | `Melancholic` |
| surprise                 | `emo_surprise`   | `Surprised`   |
| calm                     | `emo_calm`       | `Calm`        |

You only edit the template if you install a **different** wrapper pack:

1. Build the graph in the **ComfyUI web UI**, run it once with a real clip.
2. _Save (API Format)_, and in **Config → workflows → `tts`** paste the JSON.
3. Re-insert the tokens: `{{reference_clip}}` on the loader, `{{text}}`,
   `{{seed}}`, `{{tts_use_random}}`, and each `{{emo_*}}` on whatever that pack
   calls its emotion inputs. Unmapped tokens silently resolve to `""`, so name
   them exactly (`emo_happy`, `emo_anger`, …, following `EMOTION_DIMS`).
4. **Config → workflows → Custom node classes**: set `tts_emotion_class`,
   `tts_engine_class`, `tts_text_class` and `tts_model_path` to match. If the
   pack does everything in one node, point that node's class setting at it and
   delete the nodes you no longer need — the tokens are what matter, not the
   node count.

`speed` and `emo_alpha` are still passed as tokens for templates that want them.
The shipped one leaves `emotion_alpha` at 1.0, because the vector has already
been scaled by the line's intensity and the character's `emotion_scale`, and
leaves speed to §5.3.

### 5.3 The audio loader, speed, and seeds — handled for you

- **Reference clip.** Timbre clips live in the content-addressed store
  (`data/assets/voice_ref/<hash>.<ext>`). `LoadAudio` only reads
  `ComfyUI/input/`, so before every TTS job the backend **uploads the clip
  there** (via `/upload/image`, ComfyUI's audio-capable endpoint) and rewrites
  `{{reference_clip}}` to the uploaded name. Cached per process — an overnight
  batch sends each clip once. Works local or remote. On any upload failure it
  falls back to passing the raw stored path, so a custom path-based loader node
  on the same machine still works; if that node can't see the file either, drop
  a clip into `ComfyUI/input/` manually and set the character's reference-clip
  field to the bare filename.
- **Speed.** 2.5 does have a pacing control (`duration_factor`), but it is one
  value for the whole workflow and pacing here is per delivery (whisper 0.85,
  excited 1.08) — so the emotion map's speed is applied as a pitch-preserving
  `atempo` filter during the Ogg encode instead, and the template leaves
  `duration_factor` at 1.0. Set one or the other, not both. Needs ffmpeg;
  without it lines play at natural speed.
- **Determinism.** `use_random` is `false` everywhere and the job's seed reaches
  the synthesis node, so a line re-voices identically until its text changes
  (that is what staleness tracking assumes), and an audition re-renders
  identically until you change `emotion_scale` or `speed`, so re-auditioning
  after a tweak is a clean A/B.

### 5.4 Give a character a voice

Per character, on the **Voice** tab:

1. **Set the timbre reference** — **Upload a reference clip**: one clean 8–15 s
   mono clip, ~22–24 kHz, neutral delivery, no music or noise. It freezes on
   upload. Then **Test emotion range** to hear it back.

   The audition is a check on the clip you froze, not a way to pick a different
   one — timbre comes from the clip, so no take can sound like a different
   person, and re-rolling the same neutral line at N sampler seeds gave N
   near-identical clips. The takes read one line per emotion (neutral, happy,
   angry, melancholy, afraid), each written for its delivery, and what they show
   you is how much of the voice survives strong emotion: IndexTTS leaks timbre
   as emotion strength rises, which is what `emotion_scale` caps. If the angry
   or afraid take stops sounding like the character, lower Emotion scale and
   test again.

   Takes are previews, never candidates. There is deliberately no way to promote
   one to the timbre prompt: a take is IndexTTS-2.5's re-read of the clip you
   already have, so freezing it would swap a clean original for a
   second-generation clone of itself.

   IndexTTS-2.5 clones a clip — it can't synthesise a voice from the text
   description alone, so casting needs a seed clip to start from. Whatever you
   freeze _is_ the character's voice for the whole project; changing it later
   re-voices every line.

2. **Voiced** on (off = silent, like a player character).
3. **Emotion scale** scales every emotion vector down per character (default
   0.7), because strong emotion causes slight timbre leakage.
4. **Emotion preview** shows the resolved 8-dim vector for an emotion/intensity
   pair before you generate anything.

**Casting trick:** you don't need voice actors to source these. Use a model with
strong _described_ voice control — [Chatterbox](https://github.com/resemble-ai/chatterbox)
(Resemble AI, MIT) works well — to synthesize candidate clips from the
character's **Voice brief**, listen, pick one, then freeze it as the IndexTTS-2.5
timbre prompt forever. This turns the voice brief from a per-line gamble into a
one-time casting step — which is what you actually wanted from that field.

That is the field's _only_ job. **Voice brief** is a note to a human (or a prompt
you paste elsewhere); it is sent to nothing — not IndexTTS-2.5, which has no
described-voice mode, and not the writer model, which gets
`personality.speech_style` instead.

One caution: only clone voices you have rights to. Get explicit consent before
cloning a real person — using an actor's or streamer's voice without permission
is genuine legal exposure the moment you distribute, and a frozen clip becomes
every line that character speaks.

### 5.5 The emotion map

**Config → emotion** maps the structuring pass's emotion enum → the 8-dim
vector. Constraints, enforced automatically: TTS Audio Suite's emotion-vector
node caps each slider at **1.2** and the **sum at 1.5**; anything over is scaled
back. The values in the table are _base_ values — a line's intensity and the
character's `emotion_scale` scale them down from there.

### 5.6 Test a line

Select a `say` node whose speaker is a voiced, non-player character with a
reference clip → **Voice line** in the Inspector. On success the node gets a
voice badge and `data/assets/audio/…`; with ffmpeg it's transcoded to `.ogg`
(and speed-adjusted), otherwise the raw clip is kept.

Failure modes: `has no reference clip` (do §5.4); `produced no audio — check the
TTS workflow's SaveAudio node and the tts_text_class / tts_engine_class
settings` (a node class is wrong — §5.2); a ComfyUI execution error naming
`IndexTTSEngineNode` or `UnifiedTTSTextNode` (its inputs don't match the
template — §5.2); a model-not-found from the engine (`tts_model_path` names a
checkpoint the pack doesn't have — §5.1).

---

## 6. Batch fill and build

- **Batch fill** (Batch panel): queues every asset the _script actually uses_ —
  sprite combos some node shows, background variants some scene asks for, missing
  CGs, missing voice lines. Built to start and leave overnight (~3 h TTS + ~1.5 h
  images for a 2,000-line VN on a 4090). It reports blocked work (e.g. a
  character with no reference clip) instead of queueing jobs that would fail.
- **Regenerate stale** before every build: editing a node's text marks its image
  and voice stale so you never ship a voice line that doesn't match the words.
- **Build view**: validate → export. Only `game/generated/` is ever rewritten;
  `script.rpy`, `custom.rpy`, `options.rpy` are scaffolded once. Set the **Ren'Py
  SDK path** in Config → services to launch or lint from the app.
- **Resolution and scaling**: generated art is much larger than Ren'Py's raw
  800×600 default, and `config.default_transform` anchors images *bottom*-centre
  — so an unscaled 1216px sprite loses its head off the top of the screen, not
  its feet off the bottom. `generated/images.rpy` therefore defines two
  transforms and wraps every image in them:

  | Transform   | Applies to            | Behaviour                                    |
  | ----------- | --------------------- | -------------------------------------------- |
  | `vn_sprite` | character sprites     | scales to `vn_sprite_fill` (0.95) of screen height |
  | `vn_bg`     | backgrounds, event CGs| scales to cover the screen                   |

  Both are relative to `config.screen_*`, so they stay correct whatever
  resolution the project runs at. Newly scaffolded projects get
  `gui.init(1920, 1080)` in `options.rpy`; an **existing** project keeps its own
  `options.rpy`, so add that line yourself if you want more than 800×600.
  Override either transform in `custom.rpy` — `generated/` is rewritten every
  export, `custom.rpy` never is.
- **Where it writes**: one folder per project *inside the SDK*, named after the
  project — `<sdk>/<project_name>/`. There is no per-project path to configure.
  That is the directory the launcher scans for projects
  (`launcher/game/project.rpy` → `scan_directory(config.renpy_base)`), so every
  project shows up in the launcher's list automatically, which is what you need
  to reach the one-time Web support download. With no SDK configured it falls
  back to `data/builds/<project>/`.

  A project named `game`, `launcher`, `renpy`, … gets a `vn_` prefix rather than
  colliding with Ren'Py's own directories — `<sdk>/game/` in particular would
  stop the SDK opening the launcher at all (see Troubleshooting). Two projects
  with the same name each get their own folder: one keeps the plain name and the
  rest get a short id suffix, chosen deterministically so a folder never moves
  under a project. Renaming a project moves future exports to the new folder and
  leaves the old one behind for you to delete.

### Playing it in a browser

**Build view → Play in browser → Build for web (HTML)** exports the project and
then runs the SDK launcher's own `web_build` command, writing Ren'Py's
Emscripten output straight into `data/web/<project>/`. **▶ Play** opens it at
`/play/<project>/` — served by this backend, so if you started it with `--lan`
the same URL works on a phone on the same network (**Copy link** gives you the
full address to paste).

Exactly:

```
<sdk>/renpy  <sdk>/launcher  web_build  <project dir>  --destination  data/web/<project>
```

`web_build` is registered by the *launcher* project, not by Ren'Py core, which
is why the launcher is the base directory and the real project is an argument.
(`distribute --package web` is a different thing — it only builds the inner
`game.zip`, not a playable page.)

### One-time: install web support

A fresh SDK has no `web/` folder, and without it `web_build` stops with
*"Web support is not available"*. Only the launcher can fetch it:

1. Run the SDK's `renpy` executable **with no arguments** (or the Build view's
   *Open Ren'Py launcher*). Passing a project path — which is what this tool's
   *Export and launch* does — runs that game directly and you never see the
   launcher. Do not pass `<sdk>/launcher` either: `config.basedir` must be the
   SDK root, because the downloader reads `<basedir>/update/current.json` and
   `<sdk>/launcher/update/` does not exist.
2. Pick your project in the left-hand list.
3. Click **Web** in the actions column (its own button, next to *Build
   Distributions*).
4. Accept the "download RenPyWeb?" prompt. Ren'Py restarts and installs
   `<sdk>/web/`.

The Build view checks for that folder and warns you before starting a build that
cannot work.

Ren'Py repacks every asset, so a voiced project takes a few minutes. The staged
build lives outside the Ren'Py project directory on purpose — inside it, the
next build would try to package the previous one. `web_build` also leaves a
`data/web/<project>.zip` beside it, which is the same files as an
upload-ready bundle (itch.io and similar).

---

## 7. Troubleshooting

| Symptom                                                       | Cause / fix                                                                                                                  |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Config → services: ComfyUI `down`                             | Not running, wrong URL, or (from another machine) not started with `--listen`.                                               |
| ComfyUI `up`, `0 checkpoints`                                 | No SDXL checkpoint in `models/checkpoints/`.                                                                                 |
| `ComfyUI rejected the workflow (400)`                         | A node class in the template isn't installed, or an input name is wrong. Read the message; fix in Config → workflows.        |
| Image job: `finished but produced no images`                  | Your edited workflow lost its `SaveImage` node.                                                                              |
| Sprites have a background                                     | `RemBGSession+` / `ImageRemoveBackground+` not installed, or `rembg_class` / `rembg_session_class` wrong.                    |
| `ImageRemoveBackground+` node error: `required_input_missing` — `rembg_session`, `image` | Your `ComfyUI_essentials` version split background removal into a session loader (`RemBGSession+`) feeding the remover — update ComfyUI_essentials, or if it's already current this is already fixed by the shipped `sprite_suffix` template (§4.3). |
| Voice job: `produced no audio`                                | A node class is wrong (`tts_engine_class` / `tts_emotion_class` / `tts_text_class`), or `SaveAudio` is missing from an edited template. |
| TTS nodes missing from ComfyUI entirely, or import errors in its log | TTS Audio Suite's Python deps didn't install, or your ComfyUI runs Python < 3.12. See §5.1 step 3. |
| `Error(s) in loading state_dict for UnifiedVoice` | Wrong-version weights: 2.5 files under a 2.0 loader or the reverse. `size mismatch` / many missing keys → you are on the `tts_indextts2` template with 2.5 weights (or vice versa). A truncated `gpt.pth` (few KB) → interrupted download; delete and retry. See §5.1 step 2. |
| Voice: engine errors, model or `config.yaml` not found        | The checkpoint named by `tts_model_path` isn't installed — see §5.1.                                                                    |
| Voice: an emotion slot has no effect                          | Edited template token renamed outside `EMOTION_DIMS` — see §5.2.                                                             |
| Voice: ComfyUI error about a missing file / empty audio input | A custom loader node can't see the auto-uploaded clip — drop it into `ComfyUI/input/` manually (§5.3).                       |
| Voice: lines all play at one speed                            | ffmpeg not on `PATH` — speed is applied at encode time.                                                                      |
| Installed a node, app still says it's missing                 | Restart the backend (it caches `/object_info` at startup).                                                                   |
| Sprites huge, heads cut off the top of the screen             | Pre-existing exports still have the old un-scaled `generated/images.rpy`. Re-export — sprites are now wrapped in `vn_sprite`. If they are still bigger than you want, lower `vn_sprite_fill` in `custom.rpy`. |
| Event CGs never appear in a build (but do in the preview)     | Fixed: the CG image was declared and its file copied, but no `show` was ever emitted. Re-export. |
| Missing-image error / placeholder for the **player** character | Fixed: the player is never rendered as a sprite. Both the Play tab and the export now drop any `show`/`hide`/`image` for an `is_player` character, even if a `stage` or `say` node puts them on screen. Re-export. |
| Everything OOMs when generating after a scene write           | LLM still resident. Click **Free ComfyUI VRAM**, set LM Studio to auto-unload, keep `gpu_serialize` on.                      |
| LoRA not applied                                              | Character's **LoRA path** must be a filename relative to `models/loras/`, not an absolute path.                              |
| Web build: "Ren'Py's web support (RenPyWeb) is not installed" | No `<sdk>/web/` folder. Run the SDK's `renpy` with **no arguments** → launcher → pick the project → **Web** → accept the download. See §6. |
| Launcher: "Either this project does not support updating, or the update status file was deleted" | The launcher was started with a basedir that has no `update/` folder. Its DLC downloader reads `<config.basedir>/update/current.json`, which only exists at the **SDK root** — so start Ren'Py with no arguments, never with `<sdk>/launcher`. |
| Launching Ren'Py drops straight into the game, no launcher UI | Either you passed a project path (`renpy <project>` — what *Export and launch* runs), **or** a `<sdk>/game/` folder exists. `renpy.py`'s `path_to_gamedir()` searches basedir for `game`, `data`, `launcher/game` and takes the first that exists, so an export aimed at the SDK root permanently hijacks the bare launch. Delete `<sdk>/game/` — nothing in it belongs to Ren'Py. The Build view's **Open Ren'Py launcher** button passes `launcher/` explicitly and works either way. |
| ▶ Play opens a blank page                                     | Almost always the browser refusing `SharedArrayBuffer`. The `/play/` route sends the COOP/COEP isolation headers itself, so check you are on `http://<host>:<port>/play/...` from this server and not opening `index.html` off disk. |

---

## Appendix A — workflow token reference

Tokens the backend substitutes. A string that is _exactly_ one token keeps the
value's type (ints/floats/bools stay themselves); a token inside a longer string
is interpolated. Unknown tokens become `""`.

**Image (`txt2img`, `txt2img_no_lora`, `sprite_suffix`):**
`positive`, `negative`, `seed`, `width`, `height`, `steps`, `cfg`, `sampler`,
`scheduler`, `checkpoint`, `lora_name`, `lora_weight`, `prefix`, `rembg_class`,
`rembg_session_class`, `rembg_model`, `rembg_provider`

**Tagger (`tagger`):** `image`, `tagger_class`, `tagger_model`

**TTS (`tts`):** `reference_clip`, `text`, `prefix`, `seed`, `tts_use_random`,
`emo_happy`, `emo_anger`, `emo_sad`, `emo_fear`, `emo_disgust`, `emo_melancholy`,
`emo_surprise`, `emo_calm`, `tts_emotion_class`, `tts_engine_class`,
`tts_text_class`, `tts_model_path` — plus `speed` and `emo_alpha` for templates
that want them, and `tts_class` / `tts_model_loader_class` / `tts_model_dir` for
the older `tts_indextts2` graph.

Reset any template to its shipped default from Config → workflows.

## Appendix B — default node classes / config strings

| Setting                  | Default                  | Change if…                                                    |
| ------------------------ | ------------------------ | ------------------------------------------------------------- |
| `rembg_class`            | `ImageRemoveBackground+` | you use rembg / BiRefNet / another remover                    |
| `rembg_session_class`    | `RemBGSession+`          | your remover's session-loader node registers a different class (delete it from `sprite_suffix` entirely if your remover isn't session-based) |
| `rembg_model`            | `isnet-anime: anime illustrations` | you want a different rembg model — the combo's exact option string, from that node's dropdown in the ComfyUI web UI |
| `rembg_provider`         | `CPU`                    | you have `onnxruntime-gpu` installed and want rembg on `CUDA` instead |
| `tagger_class`           | `WD14Tagger\|pysssss`    | your WD14 node registers a different class                    |
| `tagger_model`           | `wd-v1-4-moat-tagger-v2` | you want a different tagger model                             |
| `tts_engine_class`       | `IndexTTSEngineNode`     | you use a different IndexTTS-2.5 pack                         |
| `tts_emotion_class`      | `IndexTTSEmotionOptionsNode` | your pack's emotion-vector node registers a different class |
| `tts_text_class`         | `UnifiedTTSTextNode`     | your pack's synthesis node registers a different class        |
| `tts_model_path`         | `IndexTTS-2.5`           | you want the engine's other checkpoint (`IndexTTS-2`) or a local install — the combo's exact option string, from the node's dropdown in the ComfyUI web UI |
| `tts_model_loader_class` / `tts_class` / `tts_model_dir` | `IndexTTS2ModelLoader` / `IndexTTS2EmotionVector` / `IndexTTS-2` | only read by the `tts_indextts2` template — they are what the old kaola pack registered |

## Appendix C — where files live

```
data/assets/img/<sha>.png        ← generated images   (fetched from ComfyUI /view)
data/assets/audio/<sha>.{flac,ogg}← generated voice    (fetched from ComfyUI /view)
data/assets/voice_ref/<sha>.<ext>← frozen timbre clips (auto-uploaded to ComfyUI input/ per job)
data/assets/sheets/              ← LoRA character sheets
data/lora/<char>/                ← Kohya dataset + output
data/builds/<project>/           ← exported Ren'Py projects

ComfyUI/models/checkpoints/      ← SDXL checkpoint
ComfyUI/models/loras/            ← character LoRAs (copy trained ones here)
ComfyUI/models/TTS/IndexTTS/     ← IndexTTS-2.5 weights (the suite downloads them here)
ComfyUI/input/                   ← LoadAudio reads reference clips here (backend uploads them)
ComfyUI/output/vn/               ← ComfyUI writes here; backend pulls each result
```
