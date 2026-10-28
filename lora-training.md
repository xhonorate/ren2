# Character LoRA training — install, setup, and getting good results

A character LoRA is the difference between "usually looks like her" and "is
her." Prompt-plus-locked-seed gets you ~60–70% consistency; a LoRA trained on
15–30 good images gets you a character you can put in any pose, outfit and
scene for the rest of the project.

This is the _optional_ half of the image pipeline. Everything in
[`getting-started.md`](getting-started.md) works without it — you just get
weaker character consistency. Read §4 of that document first; this one assumes
ComfyUI is already up and generating images.

---

## 0. The shape of the thing

The app implements the whole bootstrap loop, on the character's
**Sprites/assets tab**:

| Step | What happens                                                     | Runs where                    |
| ---- | ---------------------------------------------------------------- | ----------------------------- |
| 1    | **Character sheet** — 6 angles × 5 expressions at one fixed seed | ComfyUI (`image.sheet` job)   |
| 2    | **Cull** — you click the ones that agree                         | browser, no job               |
| 3    | **Auto-caption** — WD14 tagger over the culled set               | ComfyUI (`image.caption` job) |
| 4    | **Train** — dataset written to disk, Kohya spawned               | kohya_ss (`lora.train` job)   |
| 5    | **Wire it up** — put the file where ComfyUI can load it          | automatic if configured (§8)  |

Steps 1–4 are buttons. **Step 5 is automatic once you set `comfyui_lora_dir`**
(Config → services) — the training job hardlinks its output into ComfyUI's
`models/loras/` and writes the bare filename onto the character. Leave that
setting blank and step 5 is manual and is where most people get stuck — see §8.

Two things worth knowing before you start:

- **The trainer is a subprocess, not ComfyUI.** The backend writes a Kohya
  dataset under `data/lora/<character>/` and runs `sdxl_train_network.py`
  directly. ComfyUI is only involved in steps 1 and 3.
- **`lora.train` is GPU-serialised** like every other GPU job
  (`gpu_serialize`), so it queues behind image/TTS/LLM work and blocks them for
  its whole 20–40 minutes. Kick it off when you don't need the card.

---

## 1. Prerequisites

| Need                                     | Notes                                                                                                                |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| NVIDIA GPU, **12 GB VRAM minimum**       | The shipped config (1024px, batch 1, AdamW8bit, gradient checkpointing) lands around 10–12 GB. 24 GB is comfortable. |
| **Ampere or newer** (RTX 30xx/40xx/50xx) | The handler hardcodes `--mixed_precision bf16`. Turing (20xx) and older have no bf16 — see §11.                      |
| Python 3.10.x                            | What kohya_ss targets. 3.11 works on recent versions; 3.12 is still rough.                                           |
| Git                                      | The training scripts come as a git submodule.                                                                        |
| ~10 GB disk                              | Torch + CUDA wheels in kohya's own venv, plus your datasets.                                                         |
| An SDXL base checkpoint                  | For **training**. Not necessarily your generation checkpoint — see §3.2.                                             |

Time budget: ~20–40 min per character on a 4090 at the shipped settings. Roughly
double that on a 3060 12 GB.

---

## 2. Install the trainer

### 2.1 Which repo — read this before cloning

The app runs **`sdxl_train_network.py`**, one of the kohya _sd-scripts_. In
modern `bmaltais/kohya_ss` (v22+) those scripts are **not at the repo root** —
they live in the `sd-scripts/` submodule. The app never touches the Gradio GUI,
so you have two options:

| Option                                                           | Clone this                                                      | `kohya_path` points at  |
| ---------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------- |
| **Just the scripts** (recommended — smaller, fewer moving parts) | [`kohya-ss/sd-scripts`](https://github.com/kohya-ss/sd-scripts) | the repo root           |
| **Full kohya_ss** (if you also want the GUI for manual runs)     | [`bmaltais/kohya_ss`](https://github.com/bmaltais/kohya_ss)     | `<kohya_ss>/sd-scripts` |

Either way the rule is the same: **`kohya_path` must be the folder that
literally contains `sdxl_train_network.py`.** The Config field is labelled
"kohya_ss path" and the error message says "is kohya_path the kohya_ss root?",
but the check is `<kohya_path>/sdxl_train_network.py` and nothing else.

### 2.2 Install — sd-scripts (recommended)

```bash
git clone https://github.com/kohya-ss/sd-scripts
cd sd-scripts

# Windows
python -m venv venv
venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
pip install xformers bitsandbytes

# Linux / WSL: same, with `source venv/bin/activate`
```

Then write an accelerate default config once (single GPU, no distributed):

```bash
accelerate config default
```

The app invokes the script with plain `python`, not `accelerate launch`, which
is fine for one GPU — but having the config written avoids first-run surprises.

Verify before you leave the shell:

```bash
python -c "import torch, xformers, bitsandbytes; print(torch.cuda.is_available())"
ls sdxl_train_network.py          # must exist here
```

`bitsandbytes` is what provides `AdamW8bit`; `xformers` is what keeps VRAM
inside 12 GB. Both are non-optional at the shipped settings.

### 2.3 Install — full kohya_ss

```bash
git clone --recursive https://github.com/bmaltais/kohya_ss
cd kohya_ss
setup.bat            # Windows  (./setup.sh on Linux)
```

`--recursive` matters: without it `sd-scripts/` is empty and the app's path
check fails. If you already cloned without it,
`git submodule update --init --recursive` fixes it. `setup.bat` builds the venv
and runs `accelerate config` for you.

---

## 3. Point the app at it

**Config → services** (the Endpoints card), then **Save**:

### 3.1 `kohya_path`

The folder containing `sdxl_train_network.py` — see the table in §2.1. Windows
paths are fine as-is (`C:\ai\sd-scripts`).

### 3.2 `lora_base_model` — and why it isn't automatically your generation checkpoint

Path to the SDXL checkpoint you **train against**. This is passed to
`--pretrained_model_name_or_path`; it can be an absolute `.safetensors` path or
a HuggingFace repo id.

Train against the same base your generation checkpoint descends from:

| You generate with           | Train against                                    |
| --------------------------- | ------------------------------------------------ |
| WAI-illustrious / NoobAI-XL | `Illustrious-XL` base, or WAI-illustrious itself |
| A generic SDXL anime merge  | `stabilityai/stable-diffusion-xl-base-1.0`       |

A LoRA trained on a wildly different base still "works" but bleeds that base's
style into every generation. Pointing it straight at your generation checkpoint
is the safe, boring choice and what most people should do.

### 3.3 The `python` gotcha

The handler builds its command with the literal string `python`, and the API's
`TrainRequest` has no field to override it. That means training runs under
**whatever `python` resolves to on the backend process's PATH** — which by
default is your system Python, _not_ kohya's venv, and that venv is where torch
and bitsandbytes live.

Fix it by activating kohya's venv in the shell _before_ launching the backend.
The backend still runs on its own interpreter; the child process just inherits
the PATH:

```bash
# Windows
C:\ai\sd-scripts\venv\Scripts\activate
cd C:\Users\you\Desktop\ren\backend
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

```bash
# Linux / macOS
source ~/ai/sd-scripts/venv/bin/activate
cd ~/ren/backend
.venv/bin/python -m uvicorn app.main:app --port 8000
```

Sanity check from the same shell: `python -c "import torch"` must succeed.

The alternative is a one-line edit to
[`backend/app/jobs/handlers.py`](backend/app/jobs/handlers.py) — hardcode the
venv interpreter in the `ctx.payload.get("python") or "python"` fallback. That
survives restarts and is the better answer if you only ever train on one
machine.

### 3.4 Also required

- **ComfyUI running** with the SDXL checkpoint (step 1) and the
  `WD14Tagger|pysssss` node (step 3) — both covered in getting-started §4.
- A character that is **18+** (enforced; under-18 characters can't reach the
  image pipeline at all), with appearance tags filled in and at least one
  outfit.

---

## 4. Step 1 — generate the character sheet

Character → **Sprite LoRA** tab → **Generate N image(s)**.

Pick the outfits, angles and expressions you want represented plus a **count**,
and the run _samples_ that many combinations from the grid — **one seed held
fixed across the whole batch, every outfit included** — using the character's
own Sprite LoRA if it already has one (which is what makes the CG LoRA tab's
training set work — §10).

Sampled rather than multiplied out, because the product is a number that grows
without anyone noticing: 6 angles × 5 expressions is 30 for one outfit, but 90
for three, and a LoRA wants 15–30 good images, not every cell of a grid. Every
distinct combination is used once before anything repeats, so a count at or
below the grid size never duplicates a recipe. §9.4 covers why every outfit is
the default and how to scope it down.

| Angles                                                                          | Expressions                                    |
| ------------------------------------------------------------------------------- | ---------------------------------------------- |
| facing viewer · profile · from behind · three-quarter · from above · from below | neutral · smiling · angry · crying · surprised |

The fixed seed is the point: it maximises the chance that the same face comes
back across every image — same outfit or not — so the cull has something to
converge on. ~15–20 min per outfit on a 4090.

**Before you run it, fix the identity tags.** The sheet is generated from
`identity_tags(character)` — hair, eyes, build, height, distinguishing marks,
accessories — plus the outfit. Whatever is vague there stays vague in all 30
images and then gets _baked into the LoRA_. "brown hair" produces 30 different
browns; `brown hair, long hair, hime cut, blunt bangs` produces one haircut. Ten
minutes tightening the character record here saves you a whole retrain.

**Custom angles/expressions, or fewer outfits,** are only reachable over the
API, which accepts `angles`, `expressions` and `outfit_ids` arrays (any of
the three can be omitted — omitted `outfit_ids` means every outfit, as from
the button):

```bash
curl -X POST http://localhost:8000/api/projects/<pid>/images/sheet \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"aiko","outfit_ids":["uniform"],"angles":["facing viewer, looking at viewer","three-quarter view","from side, profile","upper body, close-up","cowboy shot"],"expressions":["neutral","smiling","angry","surprised","embarrassed"]}'
```

---

## 5. Step 2 — cull

The sheet appears as a thumbnail grid. Click to pick; the count is in the header.

**This is the step that decides whether the LoRA is good.** You are not picking
the prettiest images — you are picking the ones that are _the same person_.

- Pick the modal face. Find the face that recurs most, then keep only images
  that match it. Reject a beautiful outlier without hesitation.
- **Reject anything with broken anatomy** — bad hands, melted ears, a third arm.
  The LoRA learns defects as enthusiastically as it learns cheekbones.
- Reject wrong hair length, wrong eye colour, a lost accessory, an outfit that
  drifted. Any detail that's inconsistent across your set gets learned as
  "sometimes."
- **Keep the angle spread.** A set that's all front-facing gives you a LoRA that
  only knows front-facing. If "from below" produced two usable images and "front
  view" produced six, that's still worth keeping both.
- Keep expression spread for the same reason, but weight it toward neutral —
  neutral is what the sprite pipeline generates most.

Target **15–30 images**. The trainer refuses below 8 and the button stays
disabled until then. If a 30-image sheet only yields 9 keepers, don't train on
9 — fix the identity tags and re-run the sheet. Two sheets culled to 20 beats one
sheet culled to 12.

> 20 clean, varied, agreeing images beat 100 messy ones. Every time.

---

## 6. Step 3 — auto-caption

**Auto-caption (N)** runs each picked image through the WD14 tagger in ComfyUI
and holds the results in the browser. Wait for "captions ready" before training —
the captions are sent from the browser as part of the training request, so
training before the job finishes silently produces a **trigger-word-only
dataset**.

The final caption written next to each image is:

```
<trigger>, <wd14 tags>
```

The trigger comes first, and if the character has no trigger set the app
generates `chr_<character_id>`.

### Why captioning matters, in one paragraph

Everything you _caption_ becomes detachable; everything you _don't_ caption gets
absorbed into the trigger word. Caption "smiling" and the LoRA learns smiling is
a separable attribute it can turn off. Leave every image's hair colour
uncaptioned and the hair colour fuses into the trigger, so the character becomes
inseparable from it. For a character LoRA that trade is usually what you want for
identity (face, hair, eyes — leave them fused) and _not_ what you want for pose,
expression, framing, and background (caption them so they stay controllable).
WD14 tags all of it indiscriminately, which is a reasonable default and slightly
under-fuses identity — the visible symptom is needing a weight above 0.9 to get a
strong likeness.

If you care enough to hand-tune: after the training job runs once, the dataset is
on disk at `data/lora/<character>/10_<trigger>/` as `000.png` + `000.txt` pairs.
Edit the `.txt` files — delete the tags describing permanent identity (hair
colour, eye colour, face shape), keep the tags describing variables (pose,
expression, angle, background, clothing) — and re-run training. **Do not edit and
then re-run from the UI**: the handler wipes that folder at the start of every
run. Re-run Kohya by hand against the edited dataset instead (Appendix A has the
exact command).

---

## 7. Step 4 — train

**Train LoRA**. It queues a `lora.train` job; expect 20–40 min on a 4090.

What the handler does:

1. Writes `data/lora/<character_id>/10_<trigger>/` — `000.png`/`000.txt` pairs,
   **wiping that folder first**.
2. Spawns `sdxl_train_network.py` with `cwd=kohya_path` (full command in
   Appendix A).
3. On success, writes the absolute output path into `character.assets.lora` and
   sets the trigger.

Progress reporting is coarse — the job posts "training on N images" at 2% and
then goes quiet until it finishes, because Kohya's output is captured rather than
parsed. It is not stuck. The last 60 lines of Kohya's log are kept and returned
as `log_tail`, and the last 3000 characters are what you see if it fails. For
live progress, watch the terminal you started the backend from.

Cancelling from the job dock marks the job cancelled but **does not kill the
Kohya subprocess** — it runs to completion and keeps holding the GPU. Kill it by
hand if you need the card back.

### The defaults, and which are reachable

| Kohya arg             | Value         | Change it from                                 |
| --------------------- | ------------- | ---------------------------------------------- |
| `--max_train_epochs`  | 10            | API `epochs`                                   |
| `--network_dim`       | 32            | API `network_dim`                              |
| `--output_name`       | `chr_<id>_v1` | API `output_name`                              |
| base checkpoint       | —             | Config → `lora_base_model`                     |
| repeats (folder name) | 10            | **handler edit only**                          |
| `--network_alpha`     | 16            | **handler edit only**                          |
| `--learning_rate`     | 1e-4          | **handler edit only**                          |
| `--resolution`        | `1024,1024`   | **handler edit only**                          |
| `--train_batch_size`  | 1             | **handler edit only**                          |
| `--mixed_precision`   | `bf16`        | **handler edit only** (required on pre-Ampere) |

The UI sends only `character_id`, `hashes` and `captions`, so from the buttons
you always get **10 epochs at dim 32**. The three API-reachable knobs need a
direct call:

```bash
curl -X POST http://localhost:8000/api/projects/<pid>/lora/train \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"aiko","hashes":["<sha>","<sha>"],"captions":{},"epochs":8,"network_dim":16,"output_name":"chr_aiko_v2"}'
```

Note this endpoint takes **any** asset hashes, not just sheet images — that is
the supported path for training on a hand-assembled dataset (Gallery images, your
own art, a mix). The UI's cull grid is limited to the sheet; the API is not.

### Are the defaults good?

For an SDXL character LoRA, yes, with one caveat. 20 images × 10 repeats × 10
epochs ÷ batch 1 = **2000 steps**, which is at the upper end of the healthy
1000–2000 range. Dim 32 / alpha 16 (alpha = dim/2) at 1e-4 is a standard,
well-behaved character-LoRA recipe.

The caveat: `--save_every_n_epochs` is **not** passed, so only the final epoch is
saved. You get one checkpoint and no way to compare epoch 6 against epoch 10 —
which is the normal way to find the sweet spot between "doesn't look like her"
and "overcooked." If you plan to train more than a couple of characters, adding
`"--save_every_n_epochs", "2",` to the command list in the handler is the single
highest-value edit in this document.

---

## 8. Step 5 — actually use the LoRA

ComfyUI's `LoraLoader` resolves names **relative to `ComfyUI/models/loras/`** —
it cannot load an absolute path. The trained file lands in the project tree at
`data/lora/<character>/out/`, so something has to bridge the two.

### The automatic way — set `comfyui_lora_dir`

**Config → services → ComfyUI loras dir**, point it at your
`ComfyUI/models/loras/` folder, **Save**. From then on every `lora.train`:

- hardlinks its output into that folder (a plain copy if it's on another
  volume — no extra disk either way for a hardlink), and
- writes just the filename (`chr_aiko_v1.safetensors`) into the character's
  **LoRA path** field.

Nothing else to do — generate a sprite and a CG (below). This needs the backend
and ComfyUI to see the same filesystem (the normal single-box setup); if ComfyUI
is on another machine, leave the setting blank and use a network mount or the
manual steps.

> ComfyUI caches its LoRA **dropdown** list per process, so a just-linked file
> may not appear in the picker until you hit _Refresh_ or restart ComfyUI. The
> loader node still resolves the path regardless — generation works immediately.

### The manual way — if `comfyui_lora_dir` is blank

The training job then leaves the absolute path
`C:\...\ren\data\lora\aiko\out\chr_aiko_v1.safetensors` in the **LoRA path**
field, and every generation for that character fails with a ComfyUI
missing-LoRA error until you:

1. **Copy the file** into `ComfyUI/models/loras/`:

   ```bash
   cp data/lora/aiko/out/chr_aiko_v1.safetensors  /path/to/ComfyUI/models/loras/
   ```

2. **Rewrite the LoRA path field** to just the filename:
   `chr_aiko_v1.safetensors` — no directories, no absolute path. Subfolders are
   fine if you use them (`characters/chr_aiko_v1.safetensors`), as long as the
   path is relative to `models/loras/`.

Then set, on the same tab:

- **Trigger word** — already filled in by the training job. It's injected into
  every prompt for this character automatically; **never type it into a prompt
  yourself.**
- **LoRA weight** — 0.85 default. This is the real control (see below). Range
  0.2–1.2 in the UI.

Now generate one sprite and one CG. If the character snaps into focus, you're
done.

> **On the `<lora:name:weight>` text in prompts:** the prompt template emits an
> A1111-style inline tag alongside the real `LoraLoader` node. ComfyUI's
> `CLIPTextEncode` doesn't parse that syntax — it's inert text in the prompt. The
> **LoRA weight** field is what actually applies the LoRA, via the loader node.
> Don't try to control strength by editing the tag.

---

## 9. Getting good results

Ranked by how much they actually matter.

**1. Fix the character record before the sheet.** A LoRA can only be as
consistent as the 30 images it learned from, and those come from your identity
tags. This is the highest-leverage step and it happens before you press anything.

**2. Cull ruthlessly.** 15 images that are unmistakably one person beat 30 that
are "mostly her." Every inconsistency you keep is taught as variance, and
variance is exactly what you're paying a LoRA to remove.

**3. Vary everything that isn't identity — in the dataset that needs it.** Angle,
expression, framing, lighting, background, action, who else is in frame. A dataset
where every image is a front-facing full-body shot on white produces a LoRA that
quietly insists on front-facing full-body shots on white.

Which is fine, and in fact wanted, for the **Sprite LoRA** — a sprite _is_ a
front-facing full-body cut-out on white, so `image.sheet_wrapper` bakes exactly
that in (`solo, full body, simple background, white background`) and the sheet
only varies outfit, angle and expression. Matching the dataset to what the LoRA
will actually be asked to draw is the point of it. (An earlier pass tried to make
the sheet carry the variety too; it made sprites worse and CGs no better, which is
why the two datasets are now separate.)

The variety belongs to the **CG LoRA** and its own set (§10, `image.cg_set`,
`image.cg_set_wrapper`), which varies four axes the sheet deliberately holds
constant:

| Axis                                          | Why the sheet can't teach it                                                                                                                    |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Action** (`CG_SET_ACTIONS`)                 | A CG prompts on context and action, not a pose enum. The sheet only ever varies angle.                                                          |
| **Company** (`CG_SET_COMPANY`)                | A CG is shot from the player's POV, so someone else is often in frame or right against the camera. A solo-only dataset fights every two-hander. |
| **Framing** (`SHEET_FRAMINGS`)                | Full body only teaches full body.                                                                                                               |
| **Place** (project locations, `SHEET_SCENES`) | Every sheet image is a studio shot.                                                                                                             |

You can still hand-assemble a dataset instead: generate shots through the
Gallery/CG path and pass those hashes to the training endpoint (§7).

**4. If the character has multiple outfits, train on a mix — never just one.**
Clothing isn't identity, but if every training image wears the same outfit the
LoRA can't tell the difference: "her" and "in the school uniform" become the
same fused concept, and generating her in anything else either drags uniform
details back in or fights the prompt at normal weights. The fix is the same
mechanism as point 3 — vary it, and make sure it's captioned (§6) so WD14 tags
like `school uniform, pleated skirt` land in the `.txt` file instead of being a
silent constant.

You don't need even coverage of every outfit she owns. A split around 60–70%
her default/most-generated outfit, the rest spread across one or two others,
keeps the dominant look strong while teaching the LoRA that clothing is a
variable, not a fact about her.

**Generate character sheet** runs the full angle × expression grid for
_every_ outfit the character has, one shared seed across all of them — one
button press, no per-outfit API calls needed. This is why it exists: training
on a single outfit is the more common mistake, so the default does the right
thing. Two things follow from that:

- **The cull grid is where you shape the 60/70 split**, not the generation
  step — it generates evenly across outfits, so lean harder on the outfits
  you want less of when picking (§5). Thumbnails are labeled with their
  outfit so you can tell at a glance.
- **A character with five outfits generates five times the images** — angles
  × expressions × outfit count, all at the default sizes that's 150 images for
  five outfits instead of 30, and proportionally longer. If that's too much,
  pass a specific `outfit_ids` list (2–3 outfits, not all of them) or a
  shorter `angles`/`expressions` list — both are API-only, same as custom
  angles in §4:

```bash
curl -X POST http://localhost:8000/api/projects/<pid>/images/sheet \
  -d '{"character_id":"aiko","outfit_ids":["uniform","swimsuit"]}'
```

**5. Train against the right base.** §3.2. Mismatched base = the LoRA drags a
foreign style into every image.

**6. Tune weight before you retrain.** A weight sweep costs one image each; a
retrain costs 40 minutes. Generate the same prompt at 0.6 / 0.75 / 0.85 / 1.0 and
read the result:

| Symptom                                                          | Weight to try | Real fix if that doesn't work                    |
| ---------------------------------------------------------------- | ------------- | ------------------------------------------------ |
| Barely resembles the character                                   | 0.95–1.1      | more/better dataset; more epochs                 |
| Looks right but poses are stiff, backgrounds always similar      | 0.6–0.75      | more varied dataset (point 3)                    |
| Fried, over-saturated, artifacty; ignores the rest of the prompt | 0.5–0.7       | fewer epochs (6–8) — it's overtrained            |
| Right face, wrong outfit — clothes bleed in from training        | 0.7–0.8       | caption the outfits so they stay detachable (§6) |

Most characters settle between 0.7 and 0.9. If you need above 1.0 for a likeness,
the dataset was too small or too inconsistent.

**7. One character per LoRA.** Never train two characters into one file. Each
character is generated alone and Ren'Py composites the sprites at runtime, so
there's no reason to and every reason not to — mixed LoRAs blend faces.

**8. Use a distinctive trigger.** The default `chr_<id>` is good precisely
because it's not a real word. A trigger like `aiko` collides with whatever the
base model already thinks "aiko" means.

**9. Don't retrain what a sprite can fix.** Expressions are a compiled Ren'Py
`layeredimage`, generated once per outfit × expression. If a character is right
but you want more expressions, generate sprites — that's not a LoRA problem.

---

## 10. Iterating — the CG LoRA, and beyond

The loop is designed to feed itself, and the Sprite LoRA and the **CG LoRA**
are **separate slots on the character** — different files, different fields,
and (as of this pass) a whole separate tab: **CG LoRA** appears next to
**Sprites** once the Sprite LoRA exists, with its own generation controls, its
own pool, and its own caption/train card. Training the CG LoRA never overwrites
the file your sprites are already keyed to, so you can A/B them freely.

The CG LoRA tab's own loop, in order:

1. **1 · CG training set** (needs the Sprite LoRA already trained — that's why
   the tab doesn't appear before then). Pick the outfits, scenes (project
   locations), angles, expressions, times of day, **actions** and **who else is
   in frame** you want represented, and a count; it generates that many images
   _with the Sprite LoRA loaded_, but deliberately not as studio shots — every
   axis sampled independently per image, with a fresh seed each time. (The
   sheet's fixed seed exists to make faces comparable while culling; here it
   would just produce the same picture repeatedly.) Leaving an axis with nothing
   selected means "all of it", which is the right default for actions and
   company — they are exactly what the sheet could not teach. Results land in
   the CG LoRA's own pool, never the sheet.
2. **2 · Cull**. Same UI as the Sprites tab's — delete what doesn't agree,
   regenerate one image in place — just pointed at this pool.
3. **3 · Caption and train**. **Auto-caption** as before — everything you
   caption stays detachable, so caption the framing and the backgrounds, or the
   varied dataset fuses them into the trigger just like the flat one did (§6).
   **Train CG LoRA** writes the result to the character's `lora_cg` field as
   `chr_<id>_cg.safetensors`, and this card is also where the CG-specific
   weights live: **CG weight** (what an event CG actually renders the LoRA at)
   and **face-pass weight** (§9's FaceDetailer pass). The trigger word itself is
   shown read-only here — it's shared with the Sprite LoRA and only editable on
   the Sprites tab, so the two can never drift onto different trigger words for
   the same character.

Why this is worth a second training run at all: the Sprite LoRA knows the face
and nothing else, because a studio backdrop is all it ever saw. The CG LoRA
learns that the backdrop, the distance and the angle are variables rather than
facts about her — which is what makes it usable for an event CG. One iteration
is usually worth it; two rarely is.

**Which LoRA gets used where.** Sprites always load the Sprite LoRA — it _is_
the flat front-facing look it was trained on. Event CGs load one **by default**
too (`cg_lora_mode: auto`): the CG LoRA if you've trained it, else the Sprite
LoRA, else no LoRA at all — a character you haven't gotten to yet just renders
without one rather than blocking the CG. It runs at the character's **CG
weight** (0.55 by default) rather than the sprite weight, specifically so the
LoRA still helps the likeness without dragging the pose back to the sheet's
front-facing shot; the face-detail pass covers the rest of that gap. Override
the default per project (Config → _Event CG character LoRA_) or per node (the
inspector's CG panel) — `none` to skip the LoRA entirely, or `sprite`/`cg` to
pin one without the auto fallback.

If you want your own iteration numbering ("v3 of the CG LoRA") on top of that,
type it into **Output name** when you train — the category ("sprite" vs. "cg")
and your own version number are deliberately different things, so they don't
collide.

Two things to watch when you iterate:

- **Change the output name** if you train more than one pass of the same slot.
  Training writes to `data/lora/<char>/<slot>/out/chr_<id>_<slot>.safetensors`
  by default (`<slot>` is `sprite` or `cg`), so a second run at the same slot
  with the default name overwrites the first. With `comfyui_lora_dir` set, the
  linked copy in `ComfyUI/models/loras/` is refreshed on every run, so it at
  least stays consistent with `out/`. On the manual path it's worse: the old
  file sits in `models/loras/` under a name that now claims to be current.
  Version the filename.
- **Don't change the trigger word between runs.** The dataset folder is named
  `<repeats>_<trigger>` under that slot's directory, and `--train_data_dir` is
  its **parent**. Change the trigger and you get a second folder next to the
  first — and Kohya trains on _both_, silently, doubling your dataset with
  stale images. If you must change it, delete the old
  `data/lora/<character>/<slot>/<n>_<oldtrigger>/` folder first.

(The `out/` directory also sits inside `--train_data_dir`, but Kohya skips
subfolders whose names don't parse as `<repeats>_<name>`, so it's ignored.)

---

## 11. Troubleshooting

| Symptom                                                                            | Cause / fix                                                                                                                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `kohya_path is not set in settings`                                                | Config → services → kohya_ss path, then **Save**.                                                                                                                                                                                                                                                     |
| `<path>/sdxl_train_network.py not found -- is kohya_path the kohya_ss root?`       | You pointed at the kohya_ss root instead of `sd-scripts/`, or cloned without `--recursive`. See §2.1.                                                                                                                                                                                                 |
| `set lora_base_model in settings`                                                  | Config → services → LoRA base checkpoint. §3.2.                                                                                                                                                                                                                                                       |
| `only N images selected; 15-30 varied images is the working range`                 | Fewer than 8 picked in the cull grid.                                                                                                                                                                                                                                                                 |
| `kohya exited 1: ... ModuleNotFoundError: No module named 'torch'`                 | The `python` on the backend's PATH isn't kohya's venv. §3.3.                                                                                                                                                                                                                                          |
| `kohya exited 1: ... No module named 'bitsandbytes'` (or `xformers`)               | Same venv problem, or those two weren't installed. `pip install bitsandbytes xformers` in kohya's venv.                                                                                                                                                                                               |
| `CUDA out of memory`                                                               | Something else is holding the card — click **Free ComfyUI VRAM**, set LM Studio to auto-unload, keep `gpu_serialize` on. Still OOM at 12 GB: drop `--resolution` to `768,768` in the handler.                                                                                                         |
| `Your setup doesn't support bf16` / bf16 errors on a 20xx or older card            | Pre-Ampere. Change both `bf16` values to `fp16` in the handler's command list.                                                                                                                                                                                                                        |
| Training finishes, every generation then fails with a ComfyUI LoRA-not-found error | `comfyui_lora_dir` is blank and you skipped the §8 manual steps — set it, or copy the file into `ComfyUI/models/loras/` and set the field to the bare filename. If it _is_ set, check the job result's `note`: a permissions/volume error there means the link failed and the path was left absolute. |
| `comfyui_lora_dir` set but the new LoRA isn't in ComfyUI's dropdown                | Cosmetic — ComfyUI caches the list per process. Hit _Refresh_ or restart it. The loader resolves the path anyway, so generation already works.                                                                                                                                                        |
| LoRA has no visible effect                                                         | Trigger word field empty (the trigger is what activates it), or weight far too low.                                                                                                                                                                                                                   |
| Trained on the trigger word only, no tags                                          | You hit **Train** before the caption job reported "captions ready". Re-caption, wait, retrain.                                                                                                                                                                                                        |
| `ComfyUI has no 'WD14Tagger\|pysssss' node`                                        | Tagger not installed, or registered under another class — getting-started §4.3, or set `tagger_class` in Config → workflows.                                                                                                                                                                          |
| Sheet images don't look like each other at all                                     | Identity tags are too thin. §4. Don't cull your way out of this — fix the record and regenerate.                                                                                                                                                                                                      |
| Second training run produced a worse LoRA                                          | You changed the trigger and Kohya trained on both dataset folders. §10.                                                                                                                                                                                                                               |
| Job dock says cancelled but the GPU is still pegged                                | Cancel doesn't kill the Kohya subprocess. Kill it from Task Manager / `kill`.                                                                                                                                                                                                                         |
| Job sits at 2% for half an hour                                                    | Expected — progress isn't parsed from Kohya's output. Watch the backend terminal.                                                                                                                                                                                                                     |

---

## Appendix A — the exact command the handler runs

From [`backend/app/jobs/handlers.py`](backend/app/jobs/handlers.py)
(`lora.train`), with `cwd=kohya_path`:

```bash
python <kohya_path>/sdxl_train_network.py \
  --pretrained_model_name_or_path <lora_base_model> \
  --train_data_dir   <data>/lora/<character_id> \
  --output_dir       <data>/lora/<character_id>/out \
  --output_name      chr_<character_id>_v1 \
  --resolution 1024,1024 \
  --network_module networks.lora \
  --network_dim 32 --network_alpha 16 \
  --learning_rate 1e-4 \
  --max_train_epochs 10 \
  --train_batch_size 1 \
  --mixed_precision bf16 --save_precision bf16 \
  --optimizer_type AdamW8bit \
  --xformers --cache_latents --gradient_checkpointing \
  --save_model_as safetensors
```

Run this by hand (from kohya's venv, inside `kohya_path`) to train on an edited
dataset, to add `--save_every_n_epochs 2`, or to debug an environment problem
without going through the job queue.

## Appendix B — where the files live

```
data/lora/<character_id>/
  sprite/
    10_<trigger>/                 ← dataset: 000.png + 000.txt pairs
                                    WIPED at the start of every training run
    out/
      chr_<id>_sprite.safetensors ← the trained Sprite LoRA
  cg/
    10_<trigger>/                 ← the CG LoRA's own dataset -- never the same
                                    folder as sprite/, even though the trigger
                                    word is shared
    out/
      chr_<id>_cg.safetensors     ← the trained CG LoRA

ComfyUI/models/loras/        ← lora.train hardlinks the file here and sets the
                               character's field to the bare filename, IF
                               comfyui_lora_dir is set; otherwise do it yourself (§8)
```

`data/` relocates with `VN_DATA_DIR`.

## Appendix C — settings reference

| Setting            | Where               | Value                                                                                       |
| ------------------ | ------------------- | ------------------------------------------------------------------------------------------- |
| `kohya_path`       | Config → services   | Folder containing `sdxl_train_network.py` (§2.1)                                            |
| `lora_base_model`  | Config → services   | SDXL checkpoint to train against (§3.2)                                                     |
| `comfyui_lora_dir` | Config → services   | Path to `ComfyUI/models/loras/`. Set it and step 5 is automatic (§8); blank = manual        |
| `cg_lora_mode`     | Config → models     | Which slot an event CG loads by default: `auto` / `none` / `sprite` / `cg` (§10)            |
| Sprite LoRA path   | Character → Sprites | Filename relative to `ComfyUI/models/loras/` — **not** the absolute path the trainer writes |
| Trigger word       | Character → Sprites | Auto-injected into every prompt; `chr_<id>` by default; shared with the CG LoRA             |
| Sprite LoRA weight | Character → Sprites | 0.85 default, 0.7–0.9 typical                                                               |
| CG LoRA path       | Character → CG LoRA | Same filename contract as the Sprite LoRA (§10)                                             |
| CG weight          | Character → CG LoRA | 0.55 default — lower than the sprite weight on purpose                                      |
| Face-pass weight   | Character → CG LoRA | 0.9 default — the FaceDetailer pass (§9) runs at full strength                              |
| `gpu_serialize`    | Config → services   | Leave on — training holds the GPU for 20–40 min                                             |
