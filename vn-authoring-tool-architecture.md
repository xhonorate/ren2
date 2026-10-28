# AI-Assisted Visual Novel Authoring Tool — Architecture & Workflow

A browser-based node editor that generates writing, images, and voice from local models, and compiles to a runnable Ren'Py project.

---

## 0. The two decisions that shape everything else

**Decision 1: Ren'Py is a compile target, not your source of truth.**

Store your project as a JSON intermediate representation (IR) that your editor owns. Compile IR → `.rpy` one way. Never parse `.rpy` back.

Why this matters:

- `.rpy` can contain arbitrary Python, ATL, and screen language. Round-tripping it is a parsing nightmare — this is exactly where existing tools like RenPy-VisualEditor get fragile.
- You need to attach a lot of metadata that has nowhere to live in `.rpy`: generation prompts, seeds, LoRA references, emotion vectors, asset staleness hashes, per-node generation history. That's IR territory.
- One IR can compile to *two* targets: Ren'Py for the real build, and a lightweight web player for instant in-editor playtesting.

Keep a "raw Ren'Py" node type as an escape hatch for anything the IR doesn't model.

**Decision 2: Use Ren'Py, don't write a custom engine.**

You asked whether custom might be better. It isn't. Ren'Py gives you saves/load, rollback, dialogue history, text speed, auto-forward, skip-read-text, preferences, per-character voice muting, CG galleries, translations, and Windows/Mac/Linux/Android/web builds — for free. Reimplementing that is months of work with zero payoff.

Build a *preview* player in the browser (a few hundred lines interpreting your IR) so you can test a branch in one second instead of launching Ren'Py. Ship Ren'Py.

---

## 1. Graph model and Ren'Py mapping

### Node types

| Editor node | Emits |
|---|---|
| **Scene** (container) | `label sc_<slug>_<hash>:` + `scene bg_x with dissolve` |
| **Say** | optional `voice "..."`, `show <char> <expr>`, then `c "text"` |
| **Narration** | `"text"` (no speaker) |
| **Stage/State** | `show`/`hide` with layeredimage attributes; sets outfit/pose/location state |
| **Choice** | `menu:` with per-option blocks, each ending in `jump`/`call` |
| **Condition** | `if` / `elif` / `else` |
| **Set** | `$ flag = value` |
| **Jump / Call** | `jump label` / `call label` |
| **Audio** | `play music`, `play sound`, `stop music fadeout 1.0` |
| **Transition** | `with dissolve` / `with fade` / custom |
| **Raw** | verbatim passthrough |
| **End** | `return` or `jump ending_<x>` |

### Label emission rules

This is where node-graph tools usually break. Do **not** emit a label per node — it bloats the script and hurts rollback. Emit a label only at:

1. Scene container start
2. Any choice-branch target
3. **Merge points** — any node with in-degree > 1
4. Nodes the user explicitly pins as jump targets

Merge points are the one you must get right. Compute in-degree over the graph; when two branches converge, that node becomes a label and both branches `jump` to it. Everything else is a straight-line run inside the parent label.

### Stable label naming

Label names must be **deterministic and stable across recompiles**. Use `slug + short hash of node UUID`, never a positional index. If labels shift, every existing player save breaks. Non-negotiable.

### Scene reset and locking/unlocking

You asked how Ren'Py handles this. Three mechanisms:

**Reset to neutral:** `scene bg_x` (not `show`) clears the entire master layer, then you re-show sprites. Emit this at the top of every Scene container. Pair with `stop music fadeout` and `hide screen` where relevant.

**Per-playthrough locks:** `default flag_x = False` in an init block, `$ flag_x = True` from a Set node, and guarded menu options — Ren'Py natively supports an `if` clause on choices:

```renpy
menu:
    "Ask about the letter" if knows_letter:
        jump letter_convo
    "Leave":
        jump hallway
```

**Cross-playthrough unlocks:** `persistent.cg_beach_unlocked = True`. Survives save deletion — this is what you use for gallery/replay.

**Repeatable hub scenes:** Ren'Py's `menuset` pattern auto-hides already-taken options:

```renpy
default menuset = set()
label hub:
    menu (set=menuset):
        "Ask about her family":
            call topic_family
        "Ask about the club":
            call topic_club
    jump hub
```

Your Choice node needs a `call` vs `jump` toggle and an `exhaust` flag to support this. Hub scenes are the standard pattern for "talk to character about X."

---

## 2. LLM integration (LM Studio)

### Model recommendations

Purpose-trained creative fine-tunes, tiered by VRAM:

| VRAM | Model | Notes |
|---|---|---|
| 24 GB | **Cydonia 24B v4.x** (Mistral Small 3.2 base) | Best default. 131K context, strong character consistency, prose that isn't over-flowery. Q4_K_M / Q5_K_M. Use bartowski's iMatrix GGUF. |
| 48 GB+ | **Anubis 70B v1.1** (Llama 3.3 base) | Better prose depth; reviewers consistently report it performing above its parameter class. Q4_K_M ≈ 48 GB with context. |
| 12 GB | Mistral Nemo 12B roleplay tunes (Rocinante / UnslopNemo) | Workable, noticeably weaker at long-branch consistency. |

**On abliterated models specifically:** ablation removes refusal directions but usually costs you instruction-following and structured-output reliability. That's a bad trade here, because your entire pipeline depends on schema-constrained JSON. A purpose-built creative fine-tune (Cydonia/Anubis class) gives you the same creative range *and* better format adherence. Start there; only reach for an abliterated variant if you hit a specific wall.

Licensing, if you ever plan to sell: Mistral-based tunes are Apache 2.0. Llama 3.3 derivatives carry Meta's acceptable-use policy and a 700M MAU cap. Command-R derivatives are CC-BY-NC — non-commercial only.

### Sampling parameters

```
temperature        0.85 – 1.0
min_p              0.05 – 0.1     (and disable top_k / top_p entirely)
repetition_penalty 1.03 – 1.08    (higher makes the model drop character traits
                                   to avoid "repeating itself" — a real failure mode)
DRY / XTC          enable if your backend supports it, for long branches
```

Expose all of these on the config page per-template, since the structuring pass wants very different values than the prose pass.

### The two-pass generation technique

**This is the most important design decision in the LLM layer.**

LM Studio enforces JSON schemas at the *token* level via llama.cpp grammar sampling, which is great for reliability — but heavy grammar constraint measurably degrades prose quality. So split it:

**Pass 1 — Prose.** No schema. Full creative sampling (temp 0.9, min_p 0.05). The model writes the scene as natural screenplay-ish prose. This is where writing quality lives.

**Pass 2 — Structuring.** Feed pass-1 output back with the JSON schema, temp 0.2, instruction: *"Segment this into nodes. Do not rewrite or embellish."* Can use a smaller, faster model — a 7–12B does this fine.

Pass 2 emits something like:

```json
{
  "scene_title": "Rooftop, after class",
  "nodes": [
    { "type": "stage", "location": "school_rooftop",
      "present": ["aiko"], "outfits": {"aiko": "uniform"} },
    { "type": "narration", "text": "..." },
    { "type": "say", "speaker": "aiko", "text": "...",
      "expression": "looking_away", "emotion": "melancholy",
      "intensity": 0.6, "shot_hint": "upper body, from below, wind" }
  ],
  "choice": {
    "prompt": "What do you say?",
    "options": [
      { "label": "Push her on it", "branch_hint": "..." },
      { "label": "Let it go", "branch_hint": "..." }
    ]
  }
}
```

Note that `expression`, `emotion`, and `shot_hint` are all filled here — that's what downstream image and TTS generation consume. Constrain `emotion` and `expression` to enums in the schema so you always get valid values.

### Context assembly

Layer the prompt in this order. Everything except (7) is auto-assembled:

1. **System prompt / style bible** — voice, tone, prose conventions, pacing rules
2. **World bible** — setting, era, POV, tense (no rating: the content register
   is a scene property, see `SceneData.tone`)
3. **Character cards** for every character present, plus the player character
4. **Current state** — flags set on this path, affinity values, outfit/location
5. **Rolling summary** of the path so far (see below)
6. **Verbatim recent nodes** — last ~2000 tokens of actual dialogue
7. **Scene brief** — the user's short prompt for what should happen
8. **Output instruction** — schema, or "write prose" for pass 1

**Critical: context is the path from root to the insertion node, not a chat log.** You walk *up* the graph from the insertion point. This is what makes branch-aware regeneration correct — regenerating on branch B never sees branch A's content.

### Rolling summarization with caching

Long branches will blow context. When path token count exceeds a threshold, summarize the oldest chunk via a second LLM call and **cache the summary on the node**, keyed by a hash of all upstream node contents. Reuse it until an upstream edit invalidates the hash. Without caching you'll re-summarize on every single generation and the tool will feel unusably slow.

The summary should include a **character state block**, not just plot — "Aiko has stopped using formal speech with the player; she's still hiding the transfer." Plot-only summaries are why LLM character voice drifts over long branches.

### Regeneration

- **Regenerate scene:** discard the whole generated block, re-run with same context.
- **Regenerate from node:** keep everything upstream, move the downstream subtree to an "orphaned" holding area (don't delete — you'll want it back), re-run from that point.
- Store `{model, params, seed, prompt_hash, timestamp}` with each generation so you can diff takes and roll back.
- Keep N variants per node with a flip-between UI. Regenerating should never destroy the previous take.

### Prompt wrapper config page

Named, versioned templates with variable interpolation (Jinja-style). At minimum:

```
system.prose_gen        system.structuring      system.summarize
image.positive_wrapper  image.negative_global   image.style_prefix
tts.emotion_map         character.card_format
```

Give each a diff view against its previous version so you can A/B changes without losing the one that worked.

---

## 3. Character system

### Field structure

Every appearance field stores **both** a human label and a booru tag, because your image model reads Danbooru-style comma-separated tags:

```json
{
  "id": "aiko",
  "display_name": "Aiko",
  "age": 22,
  "gender": "female",
  "appearance": {
    "hair_color":  { "label": "platinum blonde", "tag": "platinum blonde hair" },
    "hair_style":  { "label": "long, straight, hime cut", "tag": "long hair, hime cut, straight hair" },
    "eye_color":   { "label": "pale grey", "tag": "grey eyes" },
    "eye_quality": { "label": "sharp, guarded", "tag": "tsurime, half-closed eyes" },
    "build":       { "label": "lean, athletic", "tag": "slender, toned" },
    "height":      { "label": "tall (175cm)", "tag": "tall female" },
    "distinguishing": [{ "label": "mole under left eye", "tag": "mole under eye" }]
  },
  "outfits": [
    { "id": "uniform", "name": "School uniform",
      "tags": "school uniform, pleated skirt, black thighhighs, red ribbon" },
    { "id": "casual", "name": "Weekend", "tags": "oversized hoodie, denim shorts" }
  ],
  "personality": {
    "summary": "...", "speech_style": "...", "quirks": [...],
    "wants": "...", "fears": "...", "relationship_to_player": "..."
  },
  "voice": {
    "description": "low, dry, slightly hoarse; unhurried",
    "reference_clip": "assets/voice_ref/aiko.wav",
    "emotion_scale": 0.7
  },
  "assets": {
    "lora": "loras/chr_aiko_v3.safetensors",
    "trigger": "chr_aiko",
    "lora_weight": 0.85
  }
}
```

**Wizard fields to include** beyond your list: distinguishing marks (scars, moles, freckles, piercings), skin tone, typical expression at rest, posture/carriage, accessories (glasses, jewelry), and speech register (formal/casual/dialect). Each with a curated dropdown of tag-mapped options plus a free-text fallback that stores raw tags.

**Age handling:** make age a required numeric field with a hard 18+ floor enforced in the wizard, and gate any character record from reaching the image pipeline until it's set. This isn't just a policy checkbox — appearance fields feed directly into an image generator, and every distribution channel you'd eventually publish through (itch.io, Steam, Patreon) requires you to be able to demonstrate this. Cheaper to enforce at the schema level on day one than to audit 40 characters later.

### Which fields go where

| Consumer | Gets |
|---|---|
| **LLM** | personality, speech style, wants/fears, relationship, current state; a *short* appearance line only |
| **Image gen** | appearance tags, current outfit tags, LoRA trigger + weight; **never** personality prose |
| **TTS** | reference clip path, emotion scale; voice description only during initial casting |

Keeping these separate matters. Dumping the full character card into an image prompt is a common mistake that produces mush.

**Player character** gets the same record with `is_player: true` and `voiced: false`. The PC is **never rendered as a sprite**: both compile targets (Ren'Py and the web preview) drop any `show`/`hide`/`image` for a player character — a `stage` or `say` node that references the PC on screen is silently ignored rather than erroring on a missing image.

---

## 4. Image generation

### Runner: ComfyUI, headless

Run it with `--listen`, API-only, no browser UI. It's the only local runner where a workflow *is* a JSON graph you can template and POST.

```
POST /prompt                 queue a workflow, returns prompt_id
WS   /ws                     live progress + currently-executing node
GET  /history/{prompt_id}    output filenames + metadata
GET  /view?filename=...      fetch the image
POST /upload/image           push reference images for img2img / ControlNet
GET  /object_info            node schema — cache at startup
POST /free                   VRAM recovery after OOM
```

Store workflow templates with `{{placeholder}}` tokens and inject prompt/seed/LoRA/dimensions per job.

Two practical notes: **ComfyUI has no authentication.** Bind to LAN only and reverse-proxy it if it lives on another machine. And a bonus — the same ComfyUI instance can run IndexTTS-2 via a custom node, so image *and* voice go through one API surface.

### Model: Illustrious-family SDXL

| Choice | When |
|---|---|
| **WAI-illustrious-SDXL** | Default. The "just works" option — huge LoRA ecosystem, style-neutral base, frequently updated. Start here. |
| **NoobAI-XL V-Pred 1.0** | Best tag comprehension and anatomy in the family. Requires v-prediction config: Euler sampler, CFG 4–5, ~28 steps, **no Karras scheduler** (produces artifacts in v-pred mode). |
| Illustrious XL v2.0 | Merge base only — don't generate with it raw. |

**Not Flux.** Flux is better at natural-language prompts and text-in-image, but worse at booru-tag anime and has a far thinner anime LoRA ecosystem. Illustrious is booru-tag-native, which is exactly what you want when you're assembling prompts programmatically from structured character fields. It also hits native 1536×1536, which is useful for CGs.

### The three asset classes

**Don't generate a full illustration for every node.** Use the VN idiom — this is the single biggest quality and throughput win:

| Class | Generated per | Volume | Cost |
|---|---|---|---|
| **Backgrounds** | Location (+ time of day, weather) | ~20–40 total | Trivial, reused everywhere |
| **Sprites** | Character × outfit × expression | ~15–30 per character | Moderate, reused everywhere |
| **Event CGs** | Individual node, only where flagged | ~30–80 total | Expensive, high impact |

Sprites get background-removed (BiRefNet or rembg node in ComfyUI) to transparent PNG and compiled into a Ren'Py `layeredimage`:

```renpy
layeredimage aiko:
    always "aiko_base"
    group outfit:
        attribute uniform default
        attribute casual
    group face:
        attribute neutral default
        attribute smiling
        attribute angry
        attribute crying
```

Then `show aiko casual angry` in script. Expression changes cost zero generation time at authoring or runtime.

This also solves your multi-character problem for free: each character is generated **alone**, so there's no LoRA conflict, and Ren'Py composites them at runtime. You only need regional-conditioning multi-character generation for the handful of event CGs — and there, masked conditioning with per-region LoRA hooks works but is genuinely fiddly, so reserve it for shots that earn it.

### Character consistency — three tiers

**Tier 0 — Prompt + locked seed.** Fixed style prefix, exhaustive tag block, locked seed. ~60–70% consistency. Fine for prototyping and for backgrounds.

**Tier 1 — Character LoRA. This is the real answer.** 15–30 images, varied pose/lighting/angle, constant face, properly captioned. Roughly 20–40 min on a 4090 for an SDXL character LoRA via Kohya_ss. Dataset quality matters far more than size — 20 clean varied images beat 100 messy ones.

**Automate the bootstrap loop.** This should be a first-class feature of your app:

1. User completes the character wizard → tag block assembled
2. App generates a **character sheet** batch: N angles × M expressions, fixed seed, ControlNet OpenPose grid for consistent framing
3. User culls to the ~20 that agree with each other most
4. App auto-captions via a WD14 tagger node in ComfyUI
5. App kicks off LoRA training, writes the result into `character.assets.lora`
6. Every future generation for that character loads it automatically

**Tier 2 — Per-shot fidelity.** LoRA + ControlNet OpenPose for staging + a face-detailer/ADetailer pass. *The face-detailer half is built* — see "the face-detail pass" below; ControlNet staging is not. Optionally an image-edit model (Qwen-Image-Edit with InScene LoRAs, or Flux Kontext class) for "same character, new shot in this scene" when you already have a good reference frame — this is very strong for CG variations within one scene.

### Prompt assembly (the wrapper)

```
POSITIVE:
  [quality prefix]        masterpiece, best quality, newest, absurdres, highres
  [style tags]            (project-level, from config)
  [shot / framing]        ← from LLM shot_hint
  [LoRA trigger]          <lora:chr_aiko_v3:0.85> chr_aiko
  [locked identity tags]  ← from character record, NEVER from the LLM
  [outfit tags]           ← from current path state
  [expression tags]       ← from LLM
  [pose / action tags]    ← from LLM
  [location tags]         ← from Stage node
  [lighting / time]       ← from Stage node

NEGATIVE:
  [global negative] + [per-character negative]
```

**The discipline that makes this work: the LLM only ever writes the *variable* parts** — shot, expression, pose, action. Identity tags come from the character record, every time, unchanged. Let the LLM write identity tags and consistency collapses immediately.

### Event CGs specifically — the director pass

Sprites and backgrounds get their variable parts from a dropdown. A CG can't: "what does an illustration of *this beat* show" is a reading of the scene, not a lookup. Originally that reading was a single `shot_hint` field written by the **structuring** pass — a model busy segmenting a whole passage, writing the shot as an aside, in casual English.

So event CGs get their own pass (`system.cg_director`, `services/generation.cg_director_pass`). It sees one beat, the beats around it, the beat *after* it, who is on stage and where — and nothing else — and returns four fields under a JSON schema: `camera`, `action`, `mood`, `negative`. Those land on the node as `cg_camera` / `cg_action` / `cg_negative`, where the inspector shows them for editing before anything renders.

Two decisions worth recording:

**It emits Danbooru tags, not prose.** The limitation of an Illustrious-family checkpoint is not "tags rather than sentences" — it's that SDXL's CLIP encoders bind a bag of concepts, so *any* phrasing of a relational description degrades the same way and prose just spends the budget on syntax the encoder discards. Meanwhile Danbooru's vocabulary is unusually good at what a VN CG needs: `pov`, `pov hands`, `over the shoulder`, `dutch angle`, `imminent kiss`, `hand on another's shoulder`, `eye contact`, `foreshortening`. So the director is handed that vocabulary (`defaults.CG_VOCAB`) and told to prefer it. A tag filter (`generation.clean_cg_tags`) strips anything that reads as identity or clothing, because the instruction not to write them is not something to bet a render on.

This is also why the answer to "should CGs use a natural-language model instead" is no. An SDXL LoRA is weight deltas on the SDXL UNet and its two CLIP encoders; Flux, SD3.5 and Qwen-Image are different architectures with different text encoders, and nothing transfers. Switching the base model means retraining every character from scratch *and* rebuilding the caption pipeline, since WD14 emits booru tags and a T5-based model wants natural language. Where Illustrious genuinely walls out is two named characters interacting with distinct identities — and that needs regional conditioning, which no base-model swap fixes either.

**The two halves go to opposite ends of the wrapper.** `camera` fills the `shot` slot up front, because framing decides what kind of picture this is. `action` fills the `pose` slot *after* the identity block, where body tags stop competing with the face and eye tags for attention — the same placement, and the same reason, as the sprite wrapper's note.

### CG character LoRAs, and the face-detail pass

A character LoRA is trained on the character sheet, and until it had its own wrapper the sheet was 30 front-facing full-body shots on white. A LoRA learns that as a fact about the character and then insists on it, so running it on a CG at sprite strength drags every shot back to a studio portrait. Run it weak and the pose is free but the face stops being hers.

The resolution is to split the two jobs across one graph (`workflows.CG_FACE_SUFFIX`):

| Pass | LoRA | Owns |
|---|---|---|
| Main render | the selected slot at `cg_lora_weight` (0.55), or none if not trained | composition, pose, staging |
| FaceDetailer | the character's LoRA at `face_lora_weight` (0.9) | the likeness |

The detail pass loads its LoRA on its own branch off the checkpoint rather than the main pass's `LoraLoader`, which is what lets the two run at different weights in one graph. It needs ComfyUI-Impact-Pack; without it the CG still renders and the job result says the pass was skipped.

**Event CGs use the character's LoRA by default** (`cg_lora_mode: "auto"`, overridable per project or per node) — the same consistency benefit sprites get, just at the lower `cg_lora_weight` so it doesn't drag the composition back to the sheet's front-facing prior, with the face pass covering the rest of that gap. Auto prefers `lora_cg` (the **CG LoRA** — trained on a varied dataset shot with the Sprite LoRA; lora-training.md §10) and falls back to `lora` (the **Sprite LoRA** — sheet-trained, what sprites always use); a character with neither yet just renders without one rather than blocking the job. Explicit `none`/`sprite`/`cg` skip that fallback.

The CG LoRA has its own tab on the character editor — **CG LoRA**, next to **Sprites** — that appears once the Sprite LoRA exists (it's trained *with* it). Same shape as the Sprites tab (generate a batch → cull → caption and train) but pointed at its own pool (`cg_pool`, never mixed with `sheet_pool`) and its own generation controls: which outfits, which project locations, which angles/expressions/times of day to cycle through, and how many. "v1"/"v2"/"v3" is deliberately not the vocabulary here — an author training several passes of either LoRA wants that numbering for their own output filenames, and reusing it as the category name would collide with that.

### Outfits and where clothing lives

You asked where clothing fits in the tree. Answer: **outfits are objects on the character, and a Stage node in the graph mutates the active one.**

Path state is computed by walking root→node, so any node knows "at this point Aiko is in the uniform, in the classroom, at dusk." That resolved state feeds the image prompt, feeds back into the LLM context, and compiles to `show aiko uniform smiling` in Ren'Py. Clean, and no per-node duplication.

---

## 5. Text-to-speech

### Recommendation: IndexTTS-2

It's the right fit because it does exactly the two things your requirements need:

**Speaker–emotion disentanglement.** Timbre comes from a fixed reference clip; emotion is supplied *separately*. The character's voice doesn't drift when they get angry — which is the failure mode that kills every other approach.

**Three emotion input modes:** reference audio, an 8-dimensional emotion vector, or natural-language description (via a fine-tuned Qwen3 text-to-emotion module). It also does precise duration control, and outperforms other zero-shot TTS on WER, speaker similarity, and emotional fidelity in its own evals.

There's a ComfyUI wrapper, so it shares the API surface with your image generation.

### Answering your question directly: reference clips, not training, not descriptions

- **Description alone → fails.** Voice drifts between generations. This breaks your core requirement.
- **Fine-tuning per character → unnecessary.** Zero-shot cloning quality in 2026 makes the training cost hard to justify.
- **Fixed reference clip + per-line emotion control → correct.**

One clean 8–15 second mono clip, ~22–24 kHz, neutral delivery, no music or background noise, consistent mic. That clip **is** the character's voice identity. Store it on the character record and freeze it — changing it mid-project re-voices your entire game.

**Casting trick:** you don't need voice actors to source these. Use a model with strong *described* voice control — [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI, MIT) works well — to synthesize candidate clips from the character's voice brief, listen, pick one, then freeze it as the IndexTTS-2 timbre prompt forever. This turns the voice brief from a per-line gamble into a one-time casting step — which is what you actually wanted from that field. The brief itself is never a model input: IndexTTS-2 has no described-voice mode, and the writer model gets `personality.speech_style`.

One caution: only clone voices you have rights to. Get explicit consent before cloning a real person — using an actor's or streamer's voice without permission is genuine legal exposure the moment you distribute, and a frozen clip becomes every line that character speaks.

### Emotion in the tree

Add to every Say node:

```json
{ "emotion": "melancholy", "intensity": 0.6,
  "delivery": "trails off at the end", "voice_override": null }
```

The structuring pass fills these from a schema-constrained enum, so you always get valid values. Map the enum → IndexTTS-2 emotion vectors in a config table the user can tune:

```
angry     → { anger: 0.9 }
whisper   → { calm: 0.4 }, speed 0.85, gain -4dB
crying    → { sad: 0.8, fear: 0.2 }
mean      → { anger: 0.5, happy: 0.3 }
```

Wrapper constraints to respect: 8 sliders, each 0.0–1.4, **sum ≤ 1.5**. Keep `emo_alpha` at 0.6 or below for text-based emotion control or it gets unnatural. And note that strong emotion causes slight timbre leakage in IndexTTS-2 — so a per-character intensity cap (`voice.emotion_scale`) is a genuinely useful config knob.

### Alternatives worth knowing

| Model | Why you'd pick it | License catch |
|---|---|---|
| **Chatterbox** (Resemble) | MIT, cleanest license, emotion exaggeration control, easy setup | None — safest for commercial |
| **Fish Speech / S2** | Free-form inline emotion tags like `[whisper in small voice]` embedded at word positions | Check current terms |
| **F5-TTS** | Fast (RTF ~0.15), good zero-shot | Code MIT, **weights CC-BY-NC** — non-commercial only |
| **XTTS-v2** | Mature, 17 languages | Non-commercial |
| **Kokoro-82M** | Tiny, fast, CPU-viable | No cloning — not suitable here |

If commercial release is on the table, Chatterbox is the safe default and IndexTTS-2 the quality pick — verify IndexTTS-2's license terms before you ship.

### Ren'Py output

Emit `voice` immediately before the say statement:

```renpy
voice "audio/voice/say_a3f9c2.ogg"
aiko "I said I was fine."
```

Give each Character a `voice_tag` so players can mute per-character in preferences:

```renpy
define aiko = Character("Aiko", image="aiko", voice_tag="aiko")
```

Player character: emit no `voice` line at all. Encode to Ogg Vorbis — dramatically smaller than WAV for a fully-voiced build.

---

## 6. Job orchestration

Everything here — LLM, image, TTS, LoRA training — is a long-running job. Structure it properly from the start:

```
Browser (React Flow)
    ↕ REST + SSE/WebSocket
Backend (FastAPI)
    ↕
Job queue (SQLite-backed, single-user; Redis+RQ if you scale)
    ↓
Workers → LM Studio :1234    (OpenAI-compatible /v1/chat/completions)
        → ComfyUI   :8188    (image + TTS via custom node)
        → Kohya              (LoRA training, long-running)
        → Ren'Py SDK         (compile + launch)
```

**Never call ComfyUI directly from the browser.** You need a server anyway for filesystem writes, LoRA training, and the Ren'Py compile step.

### Node status badges

```
∅ none  ·  ⏳ queued  ·  ⚙ running  ·  ✓ ready  ·  ⚠ stale  ·  ✗ error
```

Separate badges for text / image / audio on each node.

### Staleness — the underrated feature

Hash each node's text content. When text is edited after image/audio generation, mark those assets **stale**. Without this you will absolutely ship a build where the voice line doesn't match the displayed text. Add a "regenerate all stale assets" batch action and run it before every build.

### Asset storage

Content-addressed: `assets/img/<sha256>.png`, node references the hash. You get dedup for free, and regeneration never destroys the previous take — keep a variant list per node with a flip-between UI.

---

## 7. Suggested stack

| Layer | Pick | Why |
|---|---|---|
| Canvas | **React + React Flow** | Good perf to a few thousand nodes, custom node components, solid edge routing. Rete/tldraw are alternatives. |
| Graph state | Zustand | Simple; add Yjs later only if you want multi-user |
| Auto-layout | dagre or elkjs | Essential past ~100 nodes |
| Backend | **Python + FastAPI** | The whole ecosystem (ComfyUI, Kohya, Ren'Py) is Python |
| Store | SQLite + `assets/` folder | Better than a big JSON file once you have thousands of nodes with history |
| Queue | SQLite-backed asyncio queue | Don't reach for Celery for a single-user tool |
| Compiler | Python module, IR → `.rpy` | Emits `characters.rpy`, `images.rpy`, `script_*.rpy` |

### Ren'Py project template

```
game/
  script.rpy         hand-written entry point — NEVER overwritten
  generated/
    characters.rpy   define statements, voice_tags
    images.rpy       layeredimage definitions
    scenes/*.rpy     one file per Scene container
  custom.rpy         your own screens/logic — never overwritten
  images/            backgrounds, sprites, CGs
  audio/voice/       generated voice lines
```

Regenerate only `generated/`. Never touch anything else. This lets you hand-edit the game freely alongside the tool.

---

## 8. Build order

Ship in this sequence. Each phase produces something testable.

**Phase 1 — Prove the compile target.** IR schema + Ren'Py compiler. Hand-build a 20-node graph with branches, a merge point, and a conditional. Get it running in Ren'Py. Do this *before* writing any UI. Every downstream decision depends on the compile working.

**Phase 2 — Editor.** React Flow canvas, node CRUD, manual text entry, no AI. Playable output. Add the web preview player here.

**Phase 3 — Character system.** Wizard, tag mapping, path-state resolution, `layeredimage` emission.

**Phase 4 — LLM.** LM Studio client, path-context assembly, two-pass generation, regenerate-from-node, prompt template config page.

**Phase 5 — Images, tier 0.** ComfyUI client, backgrounds + CGs, prompt wrappers, seed locking, job queue with progress.

**Phase 6 — Sprites + LoRA.** Character sheet generation, culling UI, auto-caption, LoRA training pipeline, background removal, sprite compositing.

**Phase 7 — TTS.** IndexTTS-2 integration, casting flow, emotion mapping table, `voice` emission, Ogg encoding.

**Phase 8 — Polish.** Staleness tracking, batch regeneration, variant history, search, sub-graphs, minimap.

---

## 9. Known risks

**Graph readability collapses past ~200 nodes.** Plan for scene-level sub-graphs (collapse a Scene container to a single node), a minimap, auto-layout, and full-text search from day one. Don't retrofit this.

**Merge points and label naming are where compiler bugs live.** Write tests. Specifically test: two branches converging, three-way merges, a branch that jumps backward into an earlier label, and nested conditionals inside menu options.

**One GPU can't run LLM + SDXL + TTS concurrently at 24 GB.** You'll need either sequenced jobs with model swapping (LM Studio's TTL auto-evict helps — it unloads idle models), or two machines. Worth deciding early, because it affects queue design.

**Throughput reality check.** A 2,000-line VN is ~2,000 TTS generations plus ~300 images. At roughly 5s per voice line and 15s per image on a single 4090, that's ~3 hours of TTS and ~1.5 hours of image gen — before retries and rejects, which in practice at least double it. Design for overnight batch runs, not interactive generation of everything.

**Character voice drift on long branches.** The rolling summary must include a character-state block, not just plot. This is the fix.

**Ren'Py saves break when labels change.** Stable hash-based label naming, and never renumber.
