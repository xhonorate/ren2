"""Default prompt templates, sampling params, emotion mapping, tag vocabulary.

Everything here is a starting point the user edits from the config page; the
DB copy wins once it exists.
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# sampling (doc S2 'Sampling parameters')
# --------------------------------------------------------------------------
# top_k / top_p are disabled outright -- min_p does the work. Repetition
# penalty stays low: push it higher and the model starts dropping character
# traits to avoid "repeating itself", which is a real failure mode.

PROSE_SAMPLING: dict[str, Any] = {
    "temperature": 1.0,
    "min_p": 0.05,
    "top_p": 1.0,
    "top_k": 0,
    # DRY replaces the flat repetition penalty. A flat penalty punishes any
    # repeated token, which is why pushing it up made the model drop character
    # traits to avoid "repeating itself"; DRY penalises repeated *sequences*,
    # which is the actual failure -- the Text/Narration/Text lockstep and the
    # same three sentence shapes every beat.
    "repetition_penalty": 1.0,
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 2,
    # XTC drops the most-predictable tokens when a likelier alternative exists,
    # which is precisely where the em-dash pause, the "not X, but Y" antithesis
    # and the tidy closing one-liner live. Lower `xtc_probability` toward 0.2
    # if coherence suffers on long branches.
    "xtc_threshold": 0.1,
    "xtc_probability": 0.5,
    "max_tokens": 1400,
    "presence_penalty": 0.0,
}

# One turn of a live conversation, not a stretch of scene.
#
# IMPORTANT, and measured rather than assumed: **LM Studio's HTTP API silently
# ignores DRY and XTC.** Both `/v1/chat/completions` and `/api/v0/...` accept
# `dry_multiplier`, `dry_base`, `dry_allowed_length`, `xtc_probability` and
# `xtc_threshold` without complaint -- unknown keys are not rejected -- and then
# sample as though they were absent. Asked to "say banana 12 times" with
# `dry_multiplier: 0.9, dry_allowed_length: 2`, Cydonia 24B says it eleven times;
# with the documented `repeat_penalty: 1.8` it manages one. They are configured
# per model inside the LM Studio app, not over the wire.
#
# So this block uses only what the endpoint actually honours -- verified the same
# way: `temperature`, `top_p`, `top_k`, `min_p`, `repeat_penalty`,
# `presence_penalty`, `frequency_penalty`, `seed`.
#
# (PROSE_SAMPLING above carries the same inert DRY/XTC keys and the same belief
# in its comments. Left alone here deliberately: changing how every scene in an
# existing project is written is not a side effect this change gets to have.)
#
# What that leaves for repetition, which is the failure this mode is most prone
# to -- the model can see its own last twenty replies, and drifts toward
# restating them:
#
# * `frequency_penalty` does the work DRY was supposed to. llama.cpp applies it
#   across the context window, prompt included, so it pushes directly against
#   reusing the transcript's own phrasing. Kept low: it also penalises the
#   characters' names and the words the scene is about.
# * `repeat_penalty` stays near 1. The project learned once already that pushing
#   a flat penalty up makes the model drop character traits to avoid "repeating
#   itself", and a character card is nothing but repeated traits.
# * `max_tokens` is a fifth of the prose cap. A reply that runs to 1400 tokens
#   has written several beats past the player's turn, and the cheapest way to
#   stop that is to make it impossible.
RP_SAMPLING: dict[str, Any] = {
    "temperature": 1.0,
    "min_p": 0.035,
    "top_p": 1.0,
    "top_k": 0,
    "repetition_penalty": 1.05,
    "frequency_penalty": 0.25,
    "presence_penalty": 0.0,
    "max_tokens": 320,
}

STRUCTURING_SAMPLING: dict[str, Any] = {
    # Pure segmentation now -- copy text into node fields, split on line
    # boundaries, decide nothing. Cold is exactly right for that.
    "temperature": 0.15,
    "min_p": 0.0,
    "top_p": 1.0,
    "top_k": 0,
    # Stays at 1.0, and must. llama.cpp applies repeat_penalty over the last
    # repeat_last_n tokens of the *context*, prompt included -- and this pass
    # reproduces the prompt's prose verbatim by design. Any value above 1.0
    # pushes it away from the words it was told to copy, which is the one
    # failure this pass cannot have.
    "repetition_penalty": 1.0,
    # A safety bound on a runaway, not a budget: generation stops at the
    # grammar's closing brace, and measured real output is ~280 tok median /
    # 760 tok worst across the sample corpus. Sized from the worst observed
    # JSON-to-prose ratio (~1.8x) against the prose pass's own 1400-token cap,
    # so a legitimate long passage can never be truncated -- a truncated
    # response is invalid JSON, and that discards the whole passage.
    "max_tokens": 2600,
}

SUMMARY_SAMPLING: dict[str, Any] = {
    "temperature": 0.35,
    "min_p": 0.05,
    "top_p": 1.0,
    "top_k": 0,
    "repetition_penalty": 1.02,
    "max_tokens": 700,
}

CG_DIRECTOR_SAMPLING: dict[str, Any] = {
    # Translation, not writing: pick the right tags from a fixed vocabulary.
    # Warm sampling here invents tags that are not Danbooru tags, which is the
    # exact failure the vocabulary exists to prevent.
    "temperature": 0.25,
    "min_p": 0.0,
    "top_p": 1.0,
    "top_k": 0,
    "repetition_penalty": 1.0,
    "max_tokens": 500,
}

# Settings are seeded into the DB once, on the first startup that sees them,
# and never touched again -- so raising a default here does nothing for a
# project that already exists. That is the right behaviour for anything the
# user may have tuned, and the wrong one for a default that turned out to be
# actively harmful.
#
# This maps a setting to the previous default values that should be upgraded.
# `store.migrate_superseded_settings` replaces a stored value ONLY when it
# still matches one of these exactly -- an edited value is always left alone,
# because the point is to fix defaults nobody chose, not to overwrite choices.
_OLD_PC_POLICY = (
    "The player character speaks rarely and briefly. Prefer showing their "
    "action, attention and physical reaction in narration over giving them "
    "a spoken line. When they do speak, keep it to one short line, and "
    "never have them explain their own feelings -- the player supplies "
    "those. Never write the player character's internal monologue as "
    "dialogue, and never let them narrate what another character is "
    "thinking."
)

SUPERSEDED_SETTINGS: dict[str, list[Any]] = {
    # 6000 silently dropped character cards and trimmed the verbatim tail on
    # any scene with three people and some history.
    "context_token_budget": [6000],
    # Five consecutive "never"s, which a 24B reads as "do not write this
    # character at all" -- the player stood mute through direct questions.
    "prose_pc_policy": [_OLD_PC_POLICY],
}

# The relationship axis every project starts with.
#
# The stat machinery was complete but unseeded. A new project declared no
# stats, so `context.stat_line` had nothing to render and every character card
# reported the day-one `relationship_to_player` for the whole game, however far
# the story had actually moved. One declared stat is enough: `graph.stat_defaults`
# instantiates it for every character in the project automatically, as
# `affection_<character id>`, and picks up characters created later with no
# extra step.
#
# The bands are the working half, not decoration. A bare number steers a writer
# model nowhere -- "affection 34" is not a register -- so each band carries the
# behaviour that number should produce, and `stat_line` renders the label and
# the note into the card under RIGHT NOW.
#
# The ladder starts at *distant* rather than *cold* deliberately. Zero on a
# character the player has barely met means "nothing tracked yet", not "she
# dislikes you", and a hostile floor would contradict the
# `relationship_to_player` line sitting directly above it in the same card.
# Notes avoid pronouns for the same reason they avoid names: one ladder is
# rendered for every character in the project.
DEFAULT_CHARACTER_STATS: list[dict[str, Any]] = [
    {
        "id": "affection",
        "name": "Affection",
        "type": "int",
        "default": "0",
        "min": 0,
        "max": 100,
        "note": "How close this character feels to the player right now.",
        "in_card": True,
        "bands": [
            {"min": 0, "label": "distant",
             "note": "civil and careful; nothing assumed in either direction"},
            {"min": 20, "label": "warming",
             "note": "answers properly now, and sometimes speaks first"},
            {"min": 40, "label": "close",
             "note": "seeks the player out, and lets small things slip"},
            {"min": 60, "label": "attached",
             "note": "assumes the player is on their side -- teasing, unguarded"},
            {"min": 80, "label": "devoted",
             "note": "puts the player ahead of their own interest, and shows it"},
        ],
    },
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "lm_studio_url": "http://127.0.0.1:1234",
    "lm_studio_prose_model": "",        # blank = whatever LM Studio has loaded
    # Segmentation only: it must copy text verbatim, not improve it, so a
    # strict instruct model beats a creative tune here. Qwen2.5-14B-Instruct at
    # 8k context co-resides with a 24B prose model on 24 GB, so the split costs
    # no swapping.
    "lm_studio_struct_model": "",
    "comfyui_url": "http://127.0.0.1:8188",
    # Filesystem path to ComfyUI/models/loras/. When set, a finished lora.train
    # links its output straight in and writes the bare filename onto the
    # character, so LoraLoader can use it with no manual copy (doc S8). Requires
    # the backend and ComfyUI to share a filesystem.
    "comfyui_lora_dir": "",
    "renpy_sdk_path": "",
    # At 6000 this quietly dropped character cards for anyone off-stage and
    # then trimmed the verbatim tail on any scene with three people and some
    # history -- which is a direct cause of voice drift and flat emotion. The
    # trimming path stays as a backstop; it should now essentially never fire.
    "context_token_budget": 18000,
    # Deliberately NOT raised with the budget above. More verbatim tail makes
    # the model pattern-match its own recent structure harder, which is half of
    # the Text/Narration lockstep. Summary plus a moderate tail is the shape.
    "verbatim_token_budget": 2000,
    "summarize_threshold": 4000,
    # How much prose one "continue" produces. Small local models drift and
    # start recapping past roughly this much, so the ceiling is deliberate.
    "prose_beat_target": "6-12 beats (roughly 200-350 words)",
    # The single biggest lever on whether a generated scene feels like *your*
    # VN: whether the player character talks, and how much.
    # Written as permissions, not prohibitions. The previous version was five
    # consecutive "never"s, and a 24B reads that as "do not write this
    # character at all" -- so the player stood mute through direct questions.
    "prose_pc_policy": (
        "The player character TAKES PART in conversations. When someone asks "
        "them a direct question, they answer it out loud, in their own say "
        "line -- do not skip the answer, do not replace it with narration, and "
        "do not turn every reply into a choice. They also speak up to redirect, "
        "prod, agree, refuse, or change the subject.\n"
        "Keep their lines SHORT: three to a dozen words, plain, no speeches. "
        "Aim for roughly one player line for every four to six lines the other "
        "characters get -- present in the conversation, never dominating it.\n"
        "They never explain their own feelings and never narrate what someone "
        "else is thinking; the player supplies that. A CHOICE block is for a "
        "real decision, not for an ordinary reply."
    ),
    # --- Roleplay mode ----------------------------------------------------
    # A live chat with the cast, stored in its own tables and never in the
    # graph (services/roleplay.py). Unlike a scene generation, a turn here is
    # one side of a conversation, so the ceilings are much lower and the
    # transcript is sent as real user/assistant turns rather than as one block
    # of recent text -- which is what makes stopping at the player's turn the
    # model's default instead of something a regex has to impose.
    #
    # The whole prompt, including cards and remembered scenes. Lower than
    # `context_token_budget` on purpose: a conversation turn does not need the
    # scene-writing apparatus, and a leaner prompt measurably reads better.
    "rp_context_budget": 12000,
    # How much of the transcript goes in verbatim, as turns. Oldest turns are
    # dropped first and the last four are never dropped.
    "rp_history_budget": 5000,
    # How long one reply should be. Deliberately a range with a "let what is
    # happening decide" rider: a fixed target is how every reply ends up the
    # same size, which is the failure this mode is most prone to.
    "rp_reply_length": (
        "usually one to four beats. A single line is a perfectly good reply; so "
        "is a run of five. Let what is happening decide, and do not aim for a "
        "length"
    ),
    # Messages of the PREVIOUS conversation shown in the system prompt, under a
    # heading saying it is finished, purely so speech style carries over. Not
    # sent as turns -- that reads as the scene still being live, and the model
    # continues it.
    "rp_prev_scene_tail": 6,
    # Speak cast dialogue as it streams. OFF by default, because on the machine
    # this was measured on it does not fit next to a 24B -- see `rp_tts_vram_mb`.
    # Turn it on once `GET /rp/vram` says the voice model fits.
    "rp_tts": False,
    # What IndexTTS-2.5 costs while resident, in MiB. **Measured, not estimated:**
    # 6469 MiB held after a synthesis and 6974 MiB peak during one, on this
    # workflow. The earlier plan assumed "2-3 GB" from the model's file size,
    # which is not the same thing as its footprint under load, and that error is
    # the whole reason real-time voice looked affordable when it is not.
    #
    # A setting rather than a constant because it is a property of the machine and
    # the TTS workflow, not of this code. Re-measure after changing either.
    "rp_tts_vram_mb": 6500,
    # Keep LM Studio and ComfyUI resident together for a whole roleplay session
    # instead of swapping per line. Swapping is not a slow path here, it is a
    # broken one: loading the voice model costs ~12s, so a per-line swap is
    # ~12s of silence before every spoken line.
    #
    # Whether it is *possible* is a separate question from whether it is wanted,
    # and `vram.roleplay_voice_fit` answers the first one from the live cards
    # rather than from an assumption.
    "rp_coresident": True,
    "image_width": 832,
    "image_height": 1216,
    "cg_width": 1344,
    "cg_height": 768,
    # The character-overview headshot (image.profile). Square by default -- it
    # fills a card in a grid, not a sprite slot.
    "profile_width": 768,
    "profile_height": 768,
    "image_steps": 28,
    "image_cfg": 5.0,
    "image_sampler": "euler",
    "image_scheduler": "normal",        # NOT karras: artifacts in v-pred mode
    "image_checkpoint": "waiILLUSTRIOUS_v140.safetensors",
    # Which character LoRA an event CG loads by default (models.CG_LORA_MODES).
    # "auto": the CG LoRA if the character has one, else the Sprite LoRA, else
    # render without a LoRA. A CG should use whatever is available, the same
    # as sprites do -- it should just never be *blocked* by a character that
    # has not been trained yet. The face-detail pass is what keeps the pose
    # free of the sheet's front-facing prior even when a LoRA is loaded.
    # Switch to "none"/"sprite"/"cg" per project, or per node in the inspector.
    "cg_lora_mode": "auto",
    # Re-render the face crop at high LoRA weight after the main pass. This is
    # what makes a low-LoRA (or no-LoRA) CG still look like the character.
    "cg_face_detail": True,
    "cg_face_denoise": 0.45,
    # Candidates per CG render, remembered from the inspector's picker. Only
    # the interactive path reads it -- an overnight fill stays at one per node.
    "cg_batch": 1,
    # Emphasis on CG tags, assigned by category (images.CATEGORY_WEIGHT) rather
    # than written by the model. Off gives the old flat prompt.
    "cg_tag_weights": True,
    # The floor under a tag the director spends, in Danbooru posts. Matches the
    # corpus import default, so it normally changes nothing -- it bites when
    # the config page has fetched a wider dump and a tag with twelve posts is
    # real, passes the dictionary check, and still conditions on nothing.
    "cg_tag_min_posts": 100,
    # Send the director's English sentence to the renderer as well as its tags.
    # The Illustrious family takes natural language as support alongside booru
    # tags, and the sentence is written before any tag is picked, so it carries
    # the staging the tags only approximate. How much it helps depends on the
    # checkpoint -- a v1.x-era merge has weaker natural-language support than
    # v2.0 -- so it is a switch rather than an assumption.
    "cg_prompt_description": True,
    "tts_emo_alpha": 0.6,               # above this it gets unnatural
    "tts_sample_rate": 24000,
    # Takes per voice *re*-take. A line's first voicing is one take -- most are
    # fine, and an overnight fill of four per line is four times the hours. A
    # re-take means the first one was wrong, so it rolls this many seeds at
    # once and the author picks by ear from the take strip.
    "voice_retakes": 3,
    "gpu_serialize": True,              # one GPU can't run LLM + SDXL + TTS at once
    # Unload the other engine's models before a job that needs the card.
    # Serializing jobs stops two of them *running* at once; it does nothing
    # about LM Studio and ComfyUI both sitting on dedicated VRAM while idle,
    # which is what actually makes a 24 GB card refuse the second one.
    # Costs one model load per swap (5-20s for a 24B off an SSD), so it earns
    # its keep on a single card and should be turned off on two.
    "vram_exclusive": True,
    # Path to LM Studio's `lms` CLI. Blank means "find it on PATH", which is
    # where `lms bootstrap` puts it. Needed only for unloading: /v1 has no
    # residency concept, so without this the LLM half of a swap cannot happen.
    "lms_cli_path": "",
    # Extra flags for `lms load` when the LLM is swapped back in. The model key
    # and its context length are restored automatically from what was resident
    # at unload time; /api/v0/models does not report the parallel count or GPU
    # offload ratio, so anything like `--parallel 4` belongs here.
    "lms_load_args": "",
    # The last models seen resident, remembered so a swap can put back exactly
    # what was there -- including when the model fields are blank, which in
    # this project means "whatever LM Studio has loaded". Written by
    # services.vram, not meant to be edited by hand.
    "lm_studio_last_loaded": [],

    # --- Ren'Py presentation ---------------------------------------------
    # These feed game/generated/theme.rpy (rewritten on every compile) and
    # the write-once game/screens.rpy scaffold. Anything here can be
    # overridden by hand in custom.rpy, which loads after theme.rpy.
    "renpy_accent_color": "#e0819b",    # namebox fill, hover, NVL speaker
    "renpy_choice_buttons": True,       # centred framed buttons vs. a plain text list
    "renpy_choice_dim": 0.6,            # 0-1 scrim drawn behind an in-scene menu; 0 = none
    "renpy_textbox_opacity": 0.85,      # 0-1
    "renpy_text_size": 33,
    "renpy_text_cps": 0,                # initial text speed; 0 leaves the engine default
    "renpy_dialogue_font": "",          # path under game/ to a .ttf/.otf; "" = engine default
    "renpy_quick_menu": True,           # the in-game quick-menu row
    "renpy_nvl_narration": False,       # route narrator lines through an NVL screen
}


# --------------------------------------------------------------------------
# prompt templates
# --------------------------------------------------------------------------

_PROSE = """\
You are the prose writer for a visual novel. You continue the scene from
exactly where it left off -- you never restart it or re-tell earlier beats.

## Output format -- follow exactly
Write plain lines, one beat per line, nothing else:

  Narration lines have no prefix.
  Name (face, feeling): "Spoken line."
  SFX: short description of a sound this moment makes
  MUSIC: what the music changes to
  CG: what an illustration of the moment just written shows

Rules for the format:
- Dialogue is always `Name (face, feeling): "line"` using the character's exact
  display name from the cards below, with the spoken words in double quotes.
  The parenthetical is how the line is played; the next section is what goes
  in it.
- Never use markdown, headers, bullet points, asterisks, or stage directions in
  square brackets.
- An `SFX:` line is optional and rare -- a knock, a door, rain starting, a
  phone. Only when the sound is part of the beat, never more than one in a row,
  and always on the line *before* the beat it sounds under.
- A `MUSIC:` line is rarer still: at most one in a stretch, only when the
  emotional register genuinely shifts ("something colder, sparse piano"), and
  also before the beat it starts under. `MUSIC: stop` cuts the music out.
- `SFX:`, `MUSIC:` and `CG:` are directions, not story text. They are never
  spoken and never narrated.
- Blank line between beats is fine. Nothing else is.

## How each line is performed
The parenthetical on a spoken line is the performance. Write it as you write
the line -- you already know what her face is doing, and nothing downstream can
read your mind about it later.

  Aiko (smirking, amused): "You're late again."
  Aiko (looking_away, embarrassed, 0.7): "...I waited."
  Aiko (neutral, cold, 0.2, too quick): "It's fine."

- **First: the face the sprite wears.** One of:
  {{ expressions }}
- **Second: what drives the voice.** One of:
  {{ emotions }}
- **Optional: a number from 0.0 to 1.0** -- how far from composure the line is.
  Ordinary conversation sits around 0.3-0.5; 0.8 is a real break, and a stretch
  that never goes there is fine. Left out, it is 0.5.
- **Optional: a short note on how it is said** -- "trails off", "talking over
  her", "flat", "half-laughing". Only when the line implies one.
- **Optional: a body pose**, and only when the character actually moves:
  {{ poses }}. Every new pose is a sprite somebody has to draw, so leave it out
  and they keep the one they had.

Tag every spoken line. The failure to avoid is a flat readout -- the same face
and the same feeling line after line because nothing obviously screamed
otherwise. That is what makes a scene feel dead. Read your own stretch as a
performance with a shape: where does the temperature change, who is losing
ground. A character can be `neutral` out loud while the scene has moved
underneath them, and there is a tag for that. If three lines running carry the
same face, the third one is probably wrong.

The face and the feeling do not have to agree. Someone smiling through anger is
a real thing people do, and it is worth writing when it happens.

## The one illustration
If a single moment in this stretch would be worth drawing -- a physical turn, a
first reveal, contact, something the player should see rather than infer -- put
a `CG:` line directly after the beat that is that moment. At most one per
stretch, and often the right answer is none.

Write it as short comma-separated tags about what the BODIES are doing:
posture, action, where the eyes go ("leaning over the desk, reaching for her
hand, looking away"). Never hair, eyes, build or clothing -- those come from the
character record. Never the player character -- the image is from their point
of view, so tag what they are looking at.

## What narration is for -- read this twice
The player can SEE these characters. Their sprite carries expression and
posture; their voice carries tone. Narrating those is telling the player
something the screen is already showing them.

Do NOT write narration for:
- faces: expressions, blushes, blinking, narrowed eyes, raised eyebrows
- small gestures: shifting, fidgeting, glancing, tilting a head
- how a line sounded: softly, sharply, after a pause, with a smile
- what someone is feeling, when their line already shows it

DO write narration for:
- someone entering the scene or leaving it -- always, explicitly
- large physical action, movement through space, contact between people
- the place, the light, the weather, objects that matter to the scene
- what the player character does, reaches for, or notices
- time passing

## Voice
- Write in {{ pov }}, {{ tense }} tense.
- Each character must sound like their SPEECH line in the cards below: sentence
  length, rhythm, vocabulary, register. Recall their wants, loves and dislikes
  before they speak. A flattened or generic voice is a failure. Two characters
  who would phrase the same thought differently must phrase it differently.

## Never write like this
These constructions read as machine-written. Avoid them completely:
- The em dash as a dramatic pause. Use a period, a comma, or nothing.
- The "not X, but Y" antithesis, in any form: "It wasn't anger. It was
  something quieter." / "Not a question, an accusation." Say the thing once.
- Naming a silence: "a beat passed", "the silence stretched", "a long moment".
- Closing a stretch on a poignant summarising one-liner.
- Three-item parallel lists used for rhythm.
- Any sentence that restates the previous one in prettier words.

{% if nsfw %}
## This scene is explicit
Write the adult material directly and in the same voice as everything else: no
fade to black, no euphemism, no shift into clinical or purple register. The
characters stay themselves throughout -- the SPEECH line on each card still
governs every line they say here, and the constructions banned above are still
banned.

Use LOTS of dirty talk from characters while matching their personality and speaking style. Use visceral descriptions of sensory details where applicable - sounds, sights, smells, tastes, feelings. Recall the current positions and actions of all characters involved to ensure any movement or action makes anatomical sense - a character must stop licking one thing before they can start sucking another. Make sexual encounters as erotic and exciting as possible - highlighting fetishes and kinky elements through dialog and actions. Cursing, depravity, and sexual language are encouraged.
{% endif %}
## The player character
{{ pc_policy }}
{% if examples %}
## House style -- match the voice and shape of these
{{ examples }}
{% endif %}
## Ending on a decision
If this stretch reaches a point where the player would genuinely decide
something -- what to say, whether to push or let it go, who to believe -- end
your output with a choice block in exactly this format and nothing after it:

CHOICE: <the question facing the player, in their own framing>
- <option the player picks> => <where it takes the scene, one clause>
- <option the player picks> => <where it takes the scene, one clause>

Two to four options. They must lead somewhere genuinely different -- not three
phrasings of agreement. Only write a choice block if the scene has earned one;
a stretch that is still building does not need one.

## Still building
If this stretch does NOT end on a decision, end your output instead with two or
three ways the scene could go from here. These are notes to the AUTHOR, not to
the player -- nobody picks between them in the finished game, and they are not
part of the story:

CONTINUE:
- <one direction the next stretch could take>
- <a different one>

Write a CHOICE block or a CONTINUE block, never both, and nothing after
whichever one you write.

## Length
Write roughly {{ beat_target }} before stopping. Stop on a beat with pull -- a
question left open, a gesture unfinished -- never mid-sentence.

## Setting
{{ world }}

## Characters in this scene
{{ character_cards }}
{% if roster %}
## Other people in this story
Referred to but not present. Do not bring them on stage; this is so you get
their facts right if someone mentions them.
{{ roster }}
{% endif %}
## Current state
{{ state }}

## The story so far
{{ summary }}

## The scene so far -- continue immediately after the last line
{{ recent }}

## Scene brief
{{ brief }}
{% if must_hit or ends_with or scene_length %}
### Targets for the scene AS A WHOLE
This scene is written across several generations. These describe where the
whole scene is going, NOT what this stretch must accomplish. If this stretch
does not reach them, that is correct -- do not rush the arc to hit them.
{% if scene_length %}- Overall length: {{ scene_length }}
{% endif %}{% if must_hit %}- Must happen somewhere in the scene: {{ must_hit }}
{% endif %}{% if ends_with %}- Where it ends up, eventually: {{ ends_with }}
{% endif %}{% endif %}
{{ task }}\
"""

# Few-shot examples do more work on a small model than any prose rule, because
# structure gets copied from examples far harder than it gets followed from
# instructions. So the shipped default demonstrates the exact things the rules
# above ask for -- back-to-back dialogue, narration only for what a sprite
# cannot show, a short player line answering a direct question, one SFX cue --
# rather than being a generic sample of good writing.
#
# `nsfw` is the scene's own flag (models.SceneData.nsfw), so one template can
# carry both registers behind `{% if nsfw %}` and the gate costs no extra
# machinery. Per scene rather than per project: a story with one explicit scene
# in it should not be written as though every scene were.
# Few-shot examples beat instructions when the two disagree, which is what
# went wrong here: the prose prompt says "Dialogue is always `Name (face,
# feeling): "line"`" and every dialogue line in these examples was bare, so
# the examples were quietly teaching the model to skip the performance it was
# being told to write.
#
# Two rules for editing this:
#
# 1. Tag every NPC line, and vary how. The point is to demonstrate the
#    *proportion* -- face and feeling always, a number or a note only
#    sometimes, the face and the feeling not always agreeing -- because none
#    of that is teachable by a rule. Player lines stay bare: the PC has no
#    sprite and no voice, so a tag on one is inert.
# 2. Keep every spoken line on ONE physical line, however long. Segmentation
#    splits on line boundaries, so a wrapped line of dialogue becomes a say
#    node plus a narration node holding the tail. The previous version wrapped
#    Kade's last line and demonstrated exactly that: the model learned the
#    habit, and the tail arrived as narration with an orphaned quote mark in
#    it.
_STYLE_EXAMPLES = """\
short passages showing the house style. Match their rhythm and their
proportion of dialogue to narration. Do not reuse their content.

--- example 1 ---
SFX: knock at the door, three times, unhurried
Mira lets herself in before you answer.

Mira (smiling, excited, 0.7): "Good morning, sleepy head~! Get up! Get up!"
You: "Ughh… what time is it?"
Mira (thinking, neutral): "Uhh. About 5:00AM?"
Mira (smiling, excited): "In other words, time for you to get up! We’ve got a lot to do today!"

She whips the backpack off of her shoulder and slams it down on the counter next to your bed. Before you have time to protest, she quickly starts unpacking it with a practiced precision. She pulls out a flashlight, map, and dark leather gloves, which she tosses to you.

You: "What’s all this for?"
Mira (smirking, serious, 0.4): "We’re breaking in!"
--- end example 1 ---

--- example 2 ---
Kade doesn't get up when you come in. He keeps his eyes on the board.

Kade (frowning, irritated): "You're going to ask me about Sunday."
You: "I was."
Kade (looking_away, irritated, 0.5): "Well, ask something else."
You: "Why?"

He thinks for a moment before moving a piece. It’s an unexpected play that puts you in a difficult position. 

Kade (frowning, irritated): "Because I don't have a good answer. I've been working on one all week and it's still no good."
Kade (smirking, smug, 0.2): “Check, by the way.”
--- end example 2 ---

{% if nsfw %}
--- example 3 ---
Carrie (blushing, excited, 0.6): “Yes, YES! Good boy~ keep fucking me just like that!”
Carrie (blushing, affectionate, 0.4): “Ahhh. You’re such a dirty boy, fucking me out in public like this.”

The look on her face is a perfect mix of ecstasy and lunacy, but you’re way too horny to care how crazy she is - this is HOT.

Carrie (smiling, excited): “Fuck me harder! Choke me, slap me, make me scream!”

Her eyes roll back as her tongue rolls out of her mouth, dripping a constant stream of drool over her perfect tits. 
She grabs your throat and pulls you in until your ear is right next to her mouth.

Carrie (blushing, whisper): “Fucking RAPE ME, {player}”
--- end example 3 ---
{% endif %}\
"""

_STRUCTURING = """\
You segment finished prose into visual-novel nodes.

Do not rewrite, embellish, shorten, or improve the text. Copy it verbatim into
the node fields, splitting only at natural line boundaries.

**Segmentation is your only job.** You do not tag emotion, expression, pose or
delivery, you do not decide what deserves an illustration, and you do not judge
anything about how a line is performed. The writer already marked all of that
on the lines themselves. Emit the nodes and nothing else.

## Text rules
- A `Name: "line"` line becomes a `say` node: `speaker` is that character's id,
  `text` is the words inside the quotes, without the name and without the
  quote marks.
- A name may still carry a performance tag in brackets -- `Name (smirking,
  amused): "line"`. That is direction, not text: take the speaker from the
  name, drop the bracket entirely, and copy only the words inside the quotes.
- An `SFX:`, `MUSIC:` or `CG:` line is NOT a node. Skip it completely -- those
  are directions, and they are attached to the surrounding beats in code.
- A `CONTINUE:` line and the `- ...` lines under it are NOT nodes either. They
  are notes to the author about where the scene could go next. Skip them.
- Every other line becomes a `narration` node with the line as `text`.
- Never invent a line that is not in the prose, and never drop one that is.

## Staging
The state below is what is already true when this passage starts. Emit a
`stage` node ONLY when this passage actually changes it:
- someone arrives (`present` gains them) or leaves (`hide`),
- the location, time of day or weather changes,
- someone changes outfit -- only ids listed in the outfit table below.
Do not re-declare state that is already true, and do not emit a stage node at
the start out of habit. A character who is already present stays present.

## Choices
If the prose ends with a `CHOICE:` block, turn it into the `choice` object:
the question becomes `prompt`, each `- option => outcome` line becomes an
option whose `label` is the option text and whose `branch_hint` is the outcome
clause. Do not put the CHOICE block lines into narration nodes. If the prose
has no CHOICE block, set `choice` to null -- never invent one.

## Flags
Declared variables: {{ variables }}
{% if stat_schema %}
{{ stat_schema }}
{% endif %}
These are the only names you may write to; anything else is discarded. Use
them sparingly and only when the text earns it:
- an option's `effects` record what picking it changes -- `[{"var": "affinity",
  "op": "+=", "value": "1"}]` on the warm option, and nothing at all on the
  neutral one. Not every option needs effects.
- a `set` node records a change the prose states outright ("you agree to meet
  her after school" -> `met_after_school = true`). One per real commitment,
  not per beat.
- an option's `condition` gates it on a flag ("affinity >= 3") when the prose
  implies the player could only say that having already earned it.
Never invent a variable name, and never emit a flag the text does not support.

## Allowed values
- Speaker ids: {{ character_ids }}
- Location ids: {{ location_ids }}
- Outfit ids per character: {{ outfit_table }}

## State at the start of this passage
{{ state }}

Prose to segment:
{{ prose }}\
"""

_SUMMARIZE = """\
Summarize this stretch of a visual novel for use as story memory.

Return two sections, nothing else:

PLOT: what happened, in 4-8 sentences. Concrete events and revealed facts.

CHARACTER STATE: one line per character who appeared. Track how they have
changed -- what they now know, what they are hiding, how they address the
other characters, what shifted in the relationship. Be specific about speech
register changes, e.g. "Aiko has stopped using formal speech with the player;
she is still hiding the transfer."

The character state section is the important half. A plot-only summary makes
character voice drift on long branches.

Text to summarize:
{{ text }}\
"""

_CG_DIRECTOR = """\nYou are the shot director for a visual novel's event CGs. You are given one
moment from the script and you decide what an illustration of that moment
shows. You do not write prose and you do not describe the characters.

## What you produce
Danbooru tags, picked from the vocabulary at the bottom of this prompt. The
renderer is an Illustrious-family SDXL checkpoint: it was trained on tag
captions, so a tag it knows is worth more than any phrase you could write.

**Use real Danbooru tags.** The lists below are the palette: hand-picked,
reliable, and the first place to look. They are not the limit. Any real
Danbooru tag is allowed, and the ones you need for a specific room or a
specific state of clothing are mostly not in the lists -- `window`,
`curtains`, `school desk`, `chain-link fence`, `wet clothes`, `dappled
sunlight`, `steam`, `falling petals`. Reach for those by name when the beat
has them.

What is checked afterwards is whether the tag exists. A tag nobody has ever
put on a Danbooru post is silently dropped before the render, so a phrase of
your own is not a richer answer -- it is a tag that never arrives.

That is worth being concrete about, because the failure has a shape. These are
not tags, and none of them draw anything:

  warm atmosphere · clinical atmosphere · gentle embrace · gentle touch
  white knuckles · trembling slightly · relaxed posture · breath visible
  hand cradling head · tears streaming down face · mouth slightly open

Each is an English description of a feeling or a movement, not a tag. The
real tag does the job: `hug` for the embrace, `clenched hand` for the
knuckles, `trembling` for the tremble, `tears` for the tears, `open mouth`
for the mouth. Adjectives ("gentle", "soft", "clinical"), adverbs
("slightly", "carefully") and mood nouns ("atmosphere", "feeling") never
belong in a tag. If you find yourself writing two words that describe a
quality rather than naming a thing, it is not a tag.

## Point of view -- the single most important rule
The story is told from the player's point of view, and the player is the
camera. {% if player_name %}{{ player_name }} is the player character.
{% endif %}So nearly every shot is a POV shot: put `pov` in the camera field
unless this particular beat has a reason not to.

**Their hands and body may be in frame.** That is what separates a POV shot
from a security camera -- an arm entering frame, a hand she is holding, a
chest she has leaned her head against. `pov hands` for the player's own
hands; `hand on viewer`, `reaching towards viewer`, `incoming hug`,
`straddling viewer` for someone reaching them; `looking at viewer` or
`eye contact` for being addressed.

**Their head and face may not be.** The camera sits behind the player's eyes
and cannot photograph them. When the beat physically brings their head into
the frame -- a hug, a kiss, a head on their shoulder -- the head has to read
as anonymous, and there are exactly three ways to say that:

  `faceless male` / `faceless female`  the body is there, the face is not.
                                        Pair it with the contact tag.
  `head out of frame`                   the frame stops below their chin.
  `from behind` or `back focus`         their back is to the camera.

Pick the one the beat supports and put it in with the contact tag. A contact
tag on its own is the failure case: it asks for a second body, nothing says
whose, and the renderer builds one out of the other character's hair and
clothes -- which is how the player ends up wearing her uniform.

Never write a tag for the player's hair, eyes, face, expression or clothes,
and never a count tag for anyone (`1girl`, `2girls` and the rest are computed
from the cast and will be dropped from your answer). Their clothes, where the
frame shows any, come from their own record the same way hers do.

The script is written in second person, so it says "your shoulder", "she
holds you". Those words are for the reader, not for the prompt. The person
being held is the camera, so the tag is `hug` plus `pov`, never a tag with
"your" in it.

The shot contains exactly these characters and no one else, plus the player's
own body wherever the beat puts it in frame:
{{ subjects }}

## Never describe identity or the face
Hair, eyes, face, body, skin, height, clothing and accessories are supplied
from the character records automatically, every time, unchanged. So is the
expression: the beat carries its own, and it reaches the prompt on its own.
Writing any of it here would duplicate or contradict what is already there.
The location and time of day come from the stage state for the same reason.

You describe only what is *variable in this frame*: the camera, the bodies,
the hands, the gaze, the light.

The framing you choose also decides how much of the body reaches the prompt:
on a `close-up` or `portrait` the legs and shoes are removed automatically,
on `upper body` everything below the chest is. So a tight frame is not a
reason to describe less -- it is a reason to spend every tag on what is
actually in it: the face, the hands, the light on skin, what is behind the
head.

## Getting the bodies right -- work in this order
This is the half that goes wrong most, and it goes wrong by being vague. Build
`action` by answering four questions in order, one or two tags each:

1. **What is the body doing?** One posture tag, taken from the beat's own
   verbs. "She leans down" is `leaning forward`; "she sits on the edge of the
   bed" is `sitting`; "she stands over you" is `standing`. Read the sentence
   and use what it says -- do not reach for `sitting` because it is a safe
   answer. A posture that contradicts the beat is worse than none, because
   the renderer will draw it.
2. **Is anyone touching?** One contact tag if so, and read it literally
   before you pick it. `hug` is face to face; `hug from behind` means she is
   behind them and only fits if the beat says so. `hand on another's
   shoulder`, `hand on another's cheek`, `headpat`, `wrist grab`,
   `lap pillow` each name *who* touches *what*, which is the part a phrase
   like "hand on head" leaves out. If nobody is touching, skip this slot --
   an unearned contact tag invents a second body in the frame.
3. **What are the hands doing?** One hands tag when they are doing something
   the posture does not already say -- `clenched hand`, `covering mouth`,
   `outstretched hand`, `own hands together`.
4. **Where do the eyes go?** One gaze tag. `looking at viewer`, `looking
   away`, `looking down`, `closed eyes`, `averting eyes`. `looking at
   viewer` is the default only when she is actually looking at the player --
   asleep, distracted or reading is `closed eyes` or `looking down`.

Body-part tags (`collarbone`, `palms`, `tongue`, `nape`) are for when the
part is *what the frame is about* -- a shot composed on her hands, a
close-up on a throat. They are not decoration, and one dropped in at random
tells the renderer to make room for it.

Answer all four questions. Every tag you can return is a real one -- the
vocabulary makes a vague tag impossible to emit -- so a short list is not
restraint, it is a half-directed shot: an under-filled `action` is what makes
a CG come back as a generic portrait of the same two people standing there.

## The bar: this is the one illustration in the scene
An event CG is the shot the player stops on. It is not a screenshot of two
people standing in a room -- it is the frame you would put on the cover. Every
shot you direct should be able to answer: what makes this one *striking*?

- **Commit to a camera, and vary it.** A deliberate angle -- `from below`,
  `from above`, `dutch angle`, `from behind`, `close-up` -- reads as
  authored. A neutral eye-level mid-shot reads as a placeholder. Get
  uncomfortably close when the beat is intimate or violent. But a scene where
  every CG is `close-up, pov` is its own kind of flat: look at what the
  recent beats already showed and pick a different distance or angle when the
  moment allows it. A framing this scene has just used is dropped from your
  answer when anything else survives it, so repeating one costs you the slot
  rather than winning the argument. Wide for a reveal, `upper body` for a conversation,
  `from side` for a profile, `full body` when the posture is the point.
- **Give the light a direction.** `backlighting`, `rim lighting`,
  `harsh shadow`, `dappled sunlight`, `window light`. Flat ambient light is
  what makes a render look like a render.
- **Name one physical detail the eye lands on.** Not a mood -- a thing.
  `wet hair`, `clenched hand`, `tears`, `sweat`, `trembling`, `messy hair`,
  `floating hair`, `dust particles`. This is the tag that makes the frame feel
  like it is happening to a body, and it is the one most often left out.

Restraint still applies: a quiet beat gets quiet treatment. "Striking" does
not mean "loud" -- a still, close, softly-lit face can be the strongest frame
in the scene. What is never right is *undirected*.

## Output -- JSON, in this order
`shot_description` comes first and you write it first. It is a plain-English
sentence or two saying what the picture shows, and it exists so that the tags
after it are chosen to fit a decision you have already made, instead of being
picked one at a time and hoping they add up. Name concrete things: "she leans
over the bed, both arms around his shoulders, face turned into his neck" gives
the tag fields a posture, a contact and a gaze. "She is upset" gives them
nothing.

Then convert your own sentence into tags, clause by clause. A clause with no
tag is a part of the picture that will not be drawn.

- `camera`: framing, angle and point of view. 3-6 tags.
- `action`: what the bodies are doing -- posture, contact between characters,
  hands, gaze, and any body part the frame is composed on. 4-10 tags, and
  this is the field that is habitually too short. Four tags is a pose; ten is
  a picture of one. If your description mentions it, tag it.
- `mood`: light direction first, then air and texture (steam, dust, rain,
  light particles), then lens (depth of field, bloom, motion blur). 2-5 tags.
- `negative`: a short comma-separated string of things this shot must not
  contain, or empty.

Those counts are guidance, not arithmetic. Nothing forces a field to reach
its number, because a model made to fill a quota fills it with whatever is
cheapest -- which is exactly the random-looking tag that does not belong in
the shot. Say what is there, then stop.

There is no minimum. A slot you have nothing to say in is better left short
than padded -- a tag nobody asked for is not free, because the encoder splits
its attention across everything you send and every other tag gets weaker.

## Weights -- say which part is the point
A bare tag list tells the renderer that everything matters equally, and the
first thing it gives up on is usually the pose. Write a weight on the **one or
two** tags the shot fails without, using `(tag:1.2)` form:

  "action": ["leaning forward", "(arms around neck:1.25)", "hug",
             "looking down", "closed eyes"]

- leave most tags bare -- that is 1.0, and weighting everything is the same
  as weighting nothing
- `1.15` to `1.25` for the tag the shot is about
- up to `1.35` for a contact or pose the renderer keeps dropping
- `0.9` for something that belongs in frame but keeps taking it over

Do not go past 1.4. Above that SDXL distorts anatomy before it changes
composition, which reads as the prompt not working and invites going higher
still.

## Two worked examples

Beat: *She sits down on the edge of the bed without asking, and after a
moment her hand finds the back of your neck.*

  {"shot_description": "Close on her from slightly above, sitting on the bed
   beside him with one hand curled around the back of his neck, looking down
   at him. Low evening light from the window behind her.",
   "camera": ["close-up", "from above", "pov"],
   "action": ["sitting", "(hand on viewer:1.2)", "leaning forward",
              "looking at viewer", "on bed"],
   "mood": ["dim lighting", "backlighting", "depth of field"],
   "negative": ""}

The hand is the beat, so it carries the weight and nothing else does. The hand
is on the *player*, so it is `hand on viewer` -- `hand on another's head`
would have invented a third person for her to touch. Nothing here needs a
`faceless` tag: a close-up from above shows her, and the player is behind the
lens rather than in the frame. The description named the bed, so `on bed` is
in the tags; it named the window light, so the mood field has a direction
rather than a brightness.

Beat: *Claire does not look up from the folder. "You understand what that
means." Behind her the window is white with morning.*

  {"shot_description": "Her in profile at the desk, reading, deliberately not
   raising her eyes. The window behind her is blown out with morning light so
   she reads as a shape against it.",
   "camera": ["upper body", "from side", "solo focus"],
   "action": ["sitting", "(looking down:1.15)", "holding folder",
              "crossed arms"],
   "mood": ["backlighting", "window", "harsh shadow"],
   "negative": "looking at viewer, smile"}

Nobody is touching, so that slot is skipped rather than filled with something
vague. The refusal to look up *is* the shot, so it takes the weight and the
negative protects it.

Beat: *She crosses the room in three steps and puts her arms around you
before you can say anything.*

  {"shot_description": "She has her arms around him, her face turned into his
   shoulder, eyes shut. Seen from just behind and above his shoulder, so he is
   a dark shape at the edge of frame with the back of his head toward the
   camera and no face showing.",
   "camera": ["upper body", "pov", "from behind", "close to viewer"],
   "action": ["(hug:1.3)", "faceless male", "standing", "closed eyes",
              "arms around neck", "head on another's shoulder"],
   "mood": ["rim lighting", "depth of field", "blurry background"],
   "negative": "looking at viewer"}

This is the shape that goes wrong. A hug needs two bodies, so the player's is
in frame whether or not it was asked for -- and `hug` alone leaves it
undescribed, which is the renderer's invitation to dress it in her clothes and
give it a face. `faceless male` names that body and refuses it a face in one
tag; `from behind` puts the back of the head to the camera, which is what the
description already said. The hug carries the weight because it is the beat
and because a contact tag is the first thing the encoder drops.

## Use the tags the checkpoint actually knows
Prefer the suggested vocabulary below, and the candidates retrieved for this
specific beat -- those are the words the renderer was trained on. A phrase of
your own invention is not a richer answer; it is dropped before the render,
because it conditions on nothing.

These are the shapes that get dropped, and they are all English rather than
tags: `warm atmosphere`, `gentle embrace`, `white knuckles`, `trembling
slightly`, `hand cradling head`. The vocabulary already holds the tag that
does the job -- `hug`, `clenched hand`, `trembling`, `hand on another's head`.
Adjectives, adverbs and mood nouns never belong in a tag.

## Suggested vocabulary

{{ vocabulary }}

## The world
{{ world }}

## Stage state at this moment
{{ state }}

## Who is in the shot
{{ cards }}

## The scene so far
{{ recent }}

{% if candidates %}
## Tags retrieved for this beat
Pulled from the checkpoint's own dictionary using the text of the moment
below, so these are the specific words for what is happening here -- the ones
a fixed vocabulary is too small to hold. Use them where they fit.

{{ candidates }}
{% endif %}
## THE MOMENT TO ILLUSTRATE
This is the beat the CG is for. Everything above is context for it.

{{ moment }}
{% if next_beat %}
What happens immediately after (for reading the moment correctly -- do NOT
illustrate this instead):
{{ next_beat }}
{% endif %}{% if recent_shots %}
Cameras this scene has already used, most recent last -- pick a different
distance or angle unless this beat genuinely calls for a repeat:
{{ recent_shots }}
{% endif %}{% if shot_hint %}
The writer's rough note on this shot: {{ shot_hint }}
{% endif %}{% if instruction %}
Additional direction from the author -- follow it over your own judgement:
{{ instruction }}
{% endif %}"""

# Ordered the way the Illustrious family was captioned, which is not the order
# this used to use: subject count, then character, then appearance, then
# costume, then composition, with quality and era tags LAST. It previously
# opened with `masterpiece, best quality, newest, absurdres, highres` plus the
# style block -- twelve tokens of the highest-attention region of the prompt
# spent on words that describe no content, with the count tag (`1girl, 1boy`)
# buried in the middle. `newest` is an era tag and belongs at the very end.
#
# The count leads because it leads `identity`; it is not split into a slot of
# its own so that a wrapper an author has already edited keeps working -- a new
# required variable would silently drop the count from their prompt.
#
# `{{ lora }}` is gone. It rendered `<lora:name:0.55>` into the text, which is
# A1111 syntax that ComfyUI's CLIPTextEncode does not parse: the LoRA is
# applied by the LoraLoader node, and those ten tokens were being encoded as
# literal characters directly in front of the identity block. (The sprite,
# sheet and cg_set wrappers still do this -- same fix, not made here.)
#
# `{{ description }}` is the director's own English sentence. Illustrious takes
# natural language as *support* alongside tags, and the sentence is written
# before any tag is chosen, so it says the thing the tags approximate. It sits
# after the tags for that reason -- support, not subject -- and the
# `cg_prompt_description` setting turns it off for A/B against a checkpoint
# whose natural-language support is weaker.
_IMAGE_POSITIVE = """\
{% if identity %}{{ identity }}, {% endif %}{% if trigger %}{{ trigger }}, {% endif %}\
{% if outfit %}{{ outfit }}, {% endif %}{% if expression %}{{ expression }}, {% endif %}\
{% if shot %}{{ shot }}, {% endif %}{% if pose %}{{ pose }}, {% endif %}\
{% if location %}{{ location }}, {% endif %}{% if lighting %}{{ lighting }}, {% endif %}\
{% if description %}{{ description }}, {% endif %}\
{{ style_tags }}, {{ quality_prefix }}\
"""

_IMAGE_NEGATIVE = (
    "worst quality, low quality, lowres, bad anatomy, bad hands, missing fingers, "
    "extra digits, fewer digits, cropped, jpeg artifacts, signature, watermark, "
    "username, blurry, text, error, multiple views, multiple girls, multiple boys, "
    "2girls, 2boys"
)

_STYLE_PREFIX = "anime screencap, clean lineart, soft cel shading, detailed background"

# "standing, looking at viewer" used to be hardcoded here, which fought every
# pose that is not the default front view. The pose tags supply stance and
# gaze now; this keeps only what is true of every sprite.
#
# The pose carries an attention weight -- a character LoRA trained on the
# default sheet (every image front-facing and standing) has a strong
# front-view prior, and a bare pose tag loses to it. The (:1.25) emphasis is
# what makes "three_quarter" / "leaning" / "sitting" land. Raise toward 1.4 if
# a pose still won't take; lower toward 1.1 if faces/eyes come out soft --
# over-weighting one token renormalises CLIP conditioning away from the
# identity block, where the eye/face tags live. Pose stays *after* identity
# for the same reason: earlier was measurably worse for faces. (ComfyUI's
# CLIPTextEncode parses (text:weight); the inline <lora:...> tag it does not.)
# `solo` and the pose are both weighted, and the pose is moved to the front of
# the tag list. Sprite prompts run long -- identity, outfit, trigger, LoRA tag --
# and SDXL attends less to whatever trails at the end, which is how a
# `sitting` sprite came back standing. Weighting `solo` is the other half:
# a pose tag is exactly the kind of thing that invites a second figure into a
# shot that must contain one.
_SPRITE_POSITIVE = """\
{{ quality_prefix }}, {{ style_tags }}, (solo:1.2), full body, \
simple background, white background\
{% if pose %}, ({{ pose }}:1.3){% endif %}\
{% if lora %}, {{ lora }}{% endif %}{% if trigger %}, {{ trigger }}{% endif %}\
{% if identity %}, {{ identity }}{% endif %}{% if outfit %}, {{ outfit }}{% endif %}\
{% if expression %}, {{ expression }}{% endif %}\
"""

# The character sheet trains the **Sprite** LoRA, and a sprite is always the
# same thing: one figure, full body, cut out on white. So the sheet keeps that
# look deliberately -- matching what the LoRA will actually be asked to draw is
# the whole point of it, and a sheet full of varied crops and backdrops made
# sprites worse, not better.
#
# The variety a LoRA needs in order to *not* insist on that composition
# (lora-training.md S4) belongs on the CG side instead: that is what the CG
# LoRA and its own training set (image.cg_set / _CG_SET_POSITIVE below) exist
# for. Two datasets, two looks, two LoRAs -- rather than one dataset trying to
# be both and doing neither well.
_SHEET_POSITIVE = """\
{{ quality_prefix }}, {{ style_tags }}, solo, full body, \
simple background, white background\
{% if lora %}, {{ lora }}{% endif %}{% if trigger %}, {{ trigger }}{% endif %}\
{% if identity %}, {{ identity }}{% endif %}{% if outfit %}, {{ outfit }}{% endif %}\
{% if pose %}, ({{ pose }}:1.25){% endif %}\
{% if expression %}, {{ expression }}{% endif %}\
"""

# The CG LoRA's training set. Everything the sheet deliberately holds constant
# is a variable here, because an event CG is the opposite of a sprite: varied
# framing and backdrop, the character *doing* something, and -- crucially --
# often not alone. A CG is shot from the player's point of view, so the
# dataset has to contain images where someone else is in frame or right up
# against the camera; a LoRA trained only on solo studio shots fights every
# one of those at render time.
#
# `company` says who else is in frame (see CG_SET_COMPANY): solo, a faceless
# second figure, or a POV framing where the viewer *is* the other person. It is
# rendered with the frame's count tag in front of it, worked out from the
# subject plus whoever the entry adds -- images.company_count.
_CG_SET_POSITIVE = """\
{{ quality_prefix }}, {{ style_tags }}\
{% if company %}, {{ company }}{% endif %}\
{% if framing %}, {{ framing }}{% endif %}\
{% if lora %}, {{ lora }}{% endif %}{% if trigger %}, {{ trigger }}{% endif %}\
{% if identity %}, {{ identity }}{% endif %}{% if outfit %}, {{ outfit }}{% endif %}\
{% if pose %}, ({{ pose }}:1.15){% endif %}\
{% if action %}, {{ action }}{% endif %}\
{% if expression %}, {{ expression }}{% endif %}\
{% if scene %}, {{ scene }}{% endif %}\
"""

_BG_POSITIVE = """\
{{ quality_prefix }}, {{ style_tags }}, no humans, scenery, \
{{ location }}{% if lighting %}, {{ lighting }}{% endif %}\
{% if weather %}, {{ weather }}{% endif %}\
"""

# Every field is conditional. A blank line like "Wants:  | Fears:" is not
# neutral filler -- it spends context telling a small model that this
# character has no wants, and "check the line against that SPEECH line" is
# actively harmful when the SPEECH line is empty.
_CARD_FORMAT = """\
### {{ display_name }} (id: {{ id }}){% if is_player %} [PLAYER CHARACTER -- see the player character rules]{% endif %}
{{ age }}, {{ gender }}.{% if appearance %} Appearance: {{ appearance }}.{% endif %}
{% if personality.summary %}Who they are: {{ personality.summary }}
{% endif %}{% if personality.speech_style %}SPEECH -- write every one of their lines in this voice: {{ personality.speech_style }}{% if personality.speech_register %} | register: {{ personality.speech_register }}{% endif %}
Before writing a line for {{ display_name }}, check it against that SPEECH line. If it would fit any other character in this scene equally well, rewrite it.
{% endif %}{% if personality.wants %}Wants: {{ personality.wants }}. {% endif %}{% if personality.fears %}Fears: {{ personality.fears }}.{% endif %}
{% if personality.relationship_to_player and not is_player %}Toward the player: {{ personality.relationship_to_player }}
{% endif %}{% if stats %}RIGHT NOW: {{ stats }} -- write them as they are at this point in the story, not as they were when they met.
{% endif %}{% if personality.loves %}Loves: {{ personality.loves }}. {% endif %}{% if personality.likes %}Likes: {{ personality.likes }}. {% endif %}{% if personality.dislikes %}Dislikes: {{ personality.dislikes }}.{% endif %}
{% if personality.hobbies %}Hobbies: {{ personality.hobbies }}. {% endif %}{% if personality.background %}History: {{ personality.background }}{% endif %}
{% if nsfw and personality.nsfw %}
IN THE ADULT REGISTER -- this scene is explicit, and this is who they are in it: {{ personality.nsfw }}
Everything above still holds. This does not replace their voice; it is what that voice does here.{% endif %}\
"""


# --------------------------------------------------------------------------
# Roleplay mode (services/roleplay.py)
# --------------------------------------------------------------------------
# Deliberately NOT a trimmed copy of `_PROSE`. Three things are different in a
# live conversation, and each one changes the prompt rather than the wrapper:
#
# 1. The transcript is sent as real `user`/`assistant` turns, so the prompt does
#    not have to describe the format of a conversation -- the message list IS
#    the format, and a 24B instruct model stops at the user's turn by reflex.
#    Everything the old Improv prompt spent on talking the model out of writing
#    the player's lines is gone.
# 2. It is short. A bloated system prompt reads measurably worse, and the
#    ~4000-token scene prompt is most of what made Improv's output bad: it
#    describes CHOICE blocks, CONTINUE blocks, scene-wide targets and a
#    structuring contract, none of which exist here.
# 3. It has to survive fifty turns in a row. A scene generation is read once, so
#    a tic in it costs one passage; a chat tic compounds until every reply has
#    the same skeleton. That is what the rhythm section is for, and it is
#    written as checks the model can actually apply to its own draft rather than
#    as a request to be varied.
_RP_SYSTEM = """\
You are running a scene, live, for one reader. You narrate it and you play every
character in it EXCEPT {{ persona_name }}. The reader plays {{ persona_name }},
and types {{ persona_name }}'s lines themselves as the scene happens.

This is not a story you are telling them. It is a scene they are inside.

## Output format -- follow exactly
One beat per line, and nothing else:

  Narration lines have no prefix.
  Name (face, feeling): "Spoken line."

- Dialogue is always `Name (face, feeling): "line"` -- the character's exact
  display name from the cards below, the words in double quotes, and the whole
  line kept on ONE line however long it runs.
- The parenthetical is how the line is played:
  - **the face**, one of: {{ expressions }}
  - **the feeling behind the voice**, one of: {{ emotions }}
  - optional **number, 0.0 to 1.0** -- how far from composure the line is.
    Ordinary talk sits around 0.3-0.5. 0.8 is a real break.
  - optional **short note on how it is said** -- "trails off", "talking over
    him", "flat", "half-laughing". Only when the line implies one.
- The face and the feeling need not agree. Smiling through anger is a real thing
  people do, and worth writing when it happens.
- No markdown, no asterisks, no bullets, no headers, no stage directions in
  square brackets.
- Never write dialogue as prose with an attribution -- not `"Shop's closed," she
  says, flat.` The name, the bracket and the quotes, every time, including the
  very first line of the scene.
- **Narration does not carry the name forward.** After a line of narration, the
  next spoken line still begins with the character's name and their bracket. This
  is the one place it slips, so check it every time. Like this:

      She sets the till drawer down and looks at you properly.
      Alice (frowning, serious, 0.4): "You planning on taking that with you."

  Never like this:

      She sets the till drawer down and looks at you properly.
      "You planning on taking that with you."

Tag every spoken line, and mean it. The failure to avoid is a flat readout: the
same face and the same feeling line after line because nothing obviously
screamed otherwise. That is what makes a scene feel dead, and it is also what
the voice is read from, so a scene tagged `neutral` throughout is spoken in a
monotone. Read your own reply as a performance with a shape -- where does the
temperature change, who is losing ground. If three lines running carry the same
face, the third one is wrong.

## {{ persona_name }} is not yours
Never write a spoken line for {{ persona_name }}. Never narrate what
{{ persona_name }} does, thinks, feels, notices or decides. Do not answer on
their behalf and do not describe their reaction. Everything on that side of the
scene belongs to the reader, and taking it from them is the one mistake that
cannot be undone by the next reply.

You may write anything the rest of the world does to them, at them, or around
them.

## Dialogue carries the scene
These people talk. Dialogue is the default; narration is the exception you reach
for when dialogue cannot do the job.

Narrate: someone arriving or leaving, real physical action, movement through
space, contact between people, the place itself, an object that matters, time
passing.

Do not narrate: faces, blushes, narrowed eyes, small gestures, fidgeting,
glancing, how a line sounded, or what somebody obviously feels given what they
just said. The performance bracket already carries all of that.

## Voice
- {{ pov }}, {{ tense }} tense.{% if style_notes %}
- {{ style_notes }}{% endif %}
- Every character sounds like the SPEECH line on their card. Check each line you
  write against it: if the line would fit another character in this scene
  equally well, it is wrong and you rewrite it.

## Never write like this
These read as machine-written. Avoid them completely:
- The em dash as a dramatic pause. Use a period, a comma, or nothing.
- The "not X, but Y" antithesis, in any form: "It wasn't anger. It was something
  quieter." / "Not a question, an accusation." Say the thing once.
- Naming a silence: "a beat passed", "the silence stretched", "a long moment".
- Closing on a poignant one-liner that sums the moment up.
- Three-item parallel lists used for rhythm.
- Any sentence that restates the previous one in prettier words.

## Do not settle into a rhythm
The reader is going to read fifty of your replies in a row. The failure to avoid
is not a bad reply -- it is fifty replies built the same way. Before you send
one, check it against the replies above it:

- **Length is not a target.** Some replies are a single line. Some run five or
  six. If your last three were all about the same size, this one is wrong.
- **Dialogue, narration, dialogue, narration is a rhythm.** Break it. Look at
  the shape of your last reply before you write this one, and do not repeat it.
  These are all whole, finished replies:
      one spoken line, nothing else
      three spoken lines from the same person, nothing else
      one line of narration, nothing else
      two people talking four times with nothing between them
      narration, then two spoken lines, then stop
  A reply that goes line, narration, line, narration is one shape out of many and
  you have probably just used it.
- **Do not open two replies the same way.** Read the first three words of your
  last reply. If this one would start with the same kind of thing -- her
  reacting, the room being described, her name -- start somewhere else.
- **Do not close on a tidy line.** Stop where the moment is still open --
  mid-gesture, on a question, on somebody not answering.
- **A reply need not hand the turn back politely.** Someone can talk over
  {{ persona_name }}, change the subject, get up and leave, or simply not
  answer.

## Where to stop
Stop at the moment {{ persona_name }} would naturally speak or act: a question
put to them, a look that waits, a silence that is theirs to fill. Stopping a
beat early is always better than writing one past it.

The reader answers in a few words most of the time, and sometimes not at all.
{% if nsfw %}
## This scene is explicit
Write the adult material directly and in the same voice as everything else: no
fade to black, no euphemism, no shift into a clinical or purple register. The
characters stay themselves -- the SPEECH line on each card still governs every
line they say here, and the constructions banned above are still banned.

Use plenty of dirty talk, in each character's own voice and vocabulary. Visceral
sensory detail where it belongs -- sounds, sights, smells, tastes, textures.
Track where every body actually is and what it is doing, so that each movement
follows physically from the last. Cursing, depravity and explicit language are
encouraged.
{% endif %}
## Who you are playing
{{ character_cards }}
{% if roster %}
## Other people in this story
Referred to but not here. Do not bring them on stage; this is so you get their
facts right if someone mentions them.
{{ roster }}
{% endif %}
## {{ persona_name }} -- who the reader is
{{ persona }}
{% if world %}
## The world
{{ world }}
{% endif %}{% if memory %}
## What has already happened between them
Earlier scenes, oldest first. This is memory, not material -- do not replay it,
and do not contradict it.
{{ memory }}
{% endif %}{% if previous %}
## How they were talking last time
The end of the previous scene, which is over. It is here so their speech and the
temperature between them carry over -- not to be continued.
{{ previous }}
{% endif %}{% if scene_context %}
## This scene
{{ scene_context }}
{% endif %}{% if examples %}
## House style
{{ examples }}
{% endif %}\
"""

# Few-shot examples do more work on a small model than any prose rule, because
# structure is copied from examples far harder than it is followed from
# instructions. Which cuts both ways, and is the whole reason these exist in
# their own template: if every example shares a skeleton, the model copies the
# skeleton, and no amount of "vary your replies" in the rules above will undo
# four samples that were all built the same way.
#
# So the examples are chosen for their SHAPES, not for their content. Read down
# the list: one line alone; pure narration with no dialogue at all; a four-line
# volley between two characters with nothing between them; and a reply that gets
# interrupted and ends on somebody not answering. Adding a fifth example that
# looks like any of these makes the set worse, not richer.
#
# Two rules for editing:
#
# 1. Keep every spoken line on ONE physical line, however long. The port to a
#    canvas scene splits on line boundaries, so a wrapped line of dialogue
#    becomes a say node plus a narration node holding the tail.
# 2. Player lines stay bare -- no performance bracket. The player has no sprite
#    and no voice in this mode, so a tag on one is inert, and demonstrating one
#    teaches the model to write the player's lines.
_RP_EXAMPLES = """\
Four replies, chosen to show how differently a reply can be built. Match their
range, not their content -- and never their content.

--- a reply that is one line ---
Mira (smirking, amused): "That's not what you said yesterday."
--- end ---

--- a reply with no dialogue in it ---
She takes the cup out of your hands, looks at how little is left in it, and puts
it down on the far side of the table where you cannot reach.
--- end ---

--- a reply that is all dialogue ---
Kade (frowning, irritated): "You told her."
Mira (looking_away, embarrassed, 0.6): "I told her something."
Kade (glaring, angry, 0.7): "You told her the part I asked you not to."
Mira (neutral, cold, 0.4): "Then ask me better next time."
--- end ---

--- a reply that ends on somebody not answering ---
The lift doors open on an empty corridor and she does not get out.

Mira (thinking, anxious, 0.5): "Can I ask you something stupid."
Mira (eyes_closed, anxious, 0.6, half-laughing): "No. Forget it. It's fine."

She presses the button for a floor that is not hers.
--- end ---\
"""

# The post-history instruction: appended to the FINAL user turn rather than sent
# as a trailing system message.
#
# Both halves of that matter. Last-position injections carry far more weight
# than anything in the system prompt -- the rules that actually need obeying on
# every single turn live here, not up there. And appending to the user turn
# rather than adding a message keeps the list strictly alternating
# user/assistant, which Mistral-family chat templates require; a trailing system
# message either errors or gets silently folded somewhere less useful.
#
# Kept short on purpose. This is read on every turn, and a long directive here
# competes with the scene for the model's attention.
_RP_TURN = """\
{% if opening %}[Open the scene. Put us where it is happening, let somebody speak, and stop where {{ persona_name }} would answer.]{% elif silent %}[{{ persona_name }} says nothing and does nothing. Carry the moment on by yourself -- somebody fills the silence, or something happens -- and stop where it is {{ persona_name }}'s turn again.]{% else %}[Write what happens next.]{% endif %}
{% if steer %}
[Direction from the reader. Nobody in the scene said this or heard it -- make it true: {{ steer }}]
{% endif %}
[You are {{ cast_names }} and the narrator. Not {{ persona_name }} -- not one word of them. Length: {{ reply_length }}. Do not build this reply like the last one. Every spoken line starts with the speaker's name, including the ones after narration. Stop at {{ persona_name }}'s turn.]\
"""

# End-of-scene distillation. Sectioned rather than free prose for the same
# reason `system.summarize` is: a plot-only summary makes voice drift, and the
# sections are what force the half that actually matters to be written.
#
# ESTABLISHED is the section this one adds over its scene-writing sibling, and
# it exists because of a specific failure: a chat remembers what happened but
# not what is now *true*, so the next scene cheerfully has her wearing the coat
# she gave away and standing in a city she left.
_RP_SUMMARIZE = """\
A scene between {{ persona_name }} and {{ cast_names }} has just finished. Distill
it into memory for the scenes that come after it.
{% if scene_context %}
It began here: {{ scene_context }}
{% endif %}
Return these four sections and nothing else. No preamble.

PLOT: what actually happened, in 3-6 sentences. Concrete events, in order.

BETWEEN THEM: what changed. Be specific about register and about what each
person now knows or suspects -- "she has stopped using his surname"; "he knows
about the transfer and has not said so". This is the section that matters most:
without it their voices drift on the next scene.

ESTABLISHED: short factual lines that a later scene must not contradict --
promises made, things revealed, where they physically ended up, what time it
was, anything either of them is now carrying or wearing or missing.

UNRESOLVED: what was left hanging, in one or two lines.

The scene:
{{ transcript }}\
"""

# Proposing the next scene when the reader does not write a context line. Runs
# under a JSON schema (services/schemas.RP_SEED_SCHEMA) because the output is
# picked from in a UI rather than read: three separate ideas have to come back
# as three strings, and a model asked for that in prose returns two of them in
# one bullet about a third of the time.
_RP_SCENE_SEED = """\
You are helping set up the next scene between {{ persona_name }} and
{{ character_name }}.
{% if memory %}
What has happened between them so far, oldest first:
{{ memory }}
{% else %}
Nothing has happened between them yet -- this is the first time.
{% endif %}
Propose {{ count }} different openings for the next scene. Each one is a single
sentence in the present tense saying where they are and what is starting, the
way a writer notes a scene down: "{{ persona_name }} has come over to
{{ character_name }}'s flat for their first dinner together."

Make them genuinely different from each other -- a different place, a different
reason, a different temperature. At least one should follow on from something
left unresolved above, and at least one should be somewhere they have not been.
No dialogue, no outcomes, no more than one sentence each.\
"""

# The optional polish pass in front of the port to a canvas scene. Optional
# because it is not needed for correctness: cast lines are already in house
# notation, and the code pre-pass handles the player's side and the director
# notes, so the existing structuring pass can segment the transcript directly.
#
# What this buys is the things code cannot infer -- a body pose worth a sprite,
# a staging change the prose implies, run-on narration that should be two beats.
_RP_TO_SCENE = """\
Below is a finished roleplay transcript, already in the house format. Prepare it
to become a visual-novel scene.

Change as little as possible. This is a tidy-up, not a rewrite:

- Keep every line's words. Do not improve the prose, do not add lines, do not
  drop lines, and do not merge two speakers into one line.
- `{player}` is the player character and must stay written exactly that way.
  Never give them a name and never expand the tag.
- A `You:` line is the player speaking. Leave it as `You:` with no performance
  bracket -- they have no sprite.
- Split a narration line that is plainly two beats into two lines. Do not split
  dialogue.
- Add a body pose to a performance bracket only where a character actually
  moves, from: {{ poses }}. A new pose is a sprite somebody has to draw, so
  leave it out and they keep the one they had.
- Where the scene changes -- somebody arrives or leaves, they move to another
  room, time passes -- say so in a plain narration line if the transcript does
  not already.

Return the prepared transcript and nothing else. No commentary, no headings.

{{ transcript }}\
"""


DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "system.prose_gen": {"body": _PROSE, "params": PROSE_SAMPLING},
    "system.style_examples": {"body": _STYLE_EXAMPLES, "params": {}},
    "system.structuring": {"body": _STRUCTURING, "params": STRUCTURING_SAMPLING},
    "system.summarize": {"body": _SUMMARIZE, "params": SUMMARY_SAMPLING},
    "system.cg_director": {"body": _CG_DIRECTOR, "params": CG_DIRECTOR_SAMPLING},
    "rp.system": {"body": _RP_SYSTEM, "params": RP_SAMPLING},
    "rp.style_examples": {"body": _RP_EXAMPLES, "params": {}},
    "rp.turn_directive": {"body": _RP_TURN, "params": {}},
    "rp.summarize": {"body": _RP_SUMMARIZE, "params": SUMMARY_SAMPLING},
    "rp.scene_seed": {"body": _RP_SCENE_SEED, "params": SUMMARY_SAMPLING},
    "rp.to_scene": {"body": _RP_TO_SCENE, "params": STRUCTURING_SAMPLING},
    "image.positive_wrapper": {"body": _IMAGE_POSITIVE, "params": {}},
    "image.negative_global": {"body": _IMAGE_NEGATIVE, "params": {}},
    "image.style_prefix": {"body": _STYLE_PREFIX, "params": {}},
    "image.sprite_wrapper": {"body": _SPRITE_POSITIVE, "params": {}},
    "image.sheet_wrapper": {"body": _SHEET_POSITIVE, "params": {}},
    "image.cg_set_wrapper": {"body": _CG_SET_POSITIVE, "params": {}},
    "image.background_wrapper": {"body": _BG_POSITIVE, "params": {}},
    "character.card_format": {"body": _CARD_FORMAT, "params": {}},
}


# `store.migrate_superseded_templates` re-adopts the shipped default ONLY for a
# stored template whose body still hashes to one of these -- a default that
# turned out to be wrong and that nobody has edited since. Same value-match
# rule as SUPERSEDED_SETTINGS, and for the same reason: this exists to fix
# defaults nobody chose, never to overwrite a prompt the author wrote.
#
# `store.active_template` already adopts an improved default in place, but only
# for a seeded v1 with no later version. A project that has been through a
# template revision sits on v2+ and that path can never fire -- which is how a
# live project stayed on a prompt the shipped default had already fixed.
#
# sha256 of the body, so a superseded prompt costs a hash instead of a second
# copy of the whole text. Add the outgoing default's hash here whenever a
# system prompt is corrected.
SUPERSEDED_TEMPLATES: dict[str, list[str]] = {
    # The version that told the director it could invent tags -- "only invent
    # one when nothing in the list covers what is happening, and then keep it
    # to two or three words in the same style" -- and then demonstrated the
    # habit in its own prose, offering "white knuckles" and "breath in cold
    # air" as examples of a good visceral detail. Neither is a Danbooru tag.
    # It also asked the mood field for "atmosphere", and duly got "warm
    # atmosphere" and "clinical atmosphere" twenty times between them.
    #
    # Measured on one real project before the fix: 47% of the tag uses the
    # director emitted were tags that do not exist. The output schema now
    # constrains the tag fields to the vocabulary, so invention is impossible
    # rather than discouraged, and this prompt no longer argues about it.
    "system.cg_director": [
        "5aee052082f099ac3982ffaf2e3d86dfb8a266e0ee28f489f78cb96728d07905",
        # The version whose output spec asked for 2-5 / 2-8 / 1-4 tags and told
        # the director that "four tags beat eight vague ones". Both were
        # written when the fields were free strings and vagueness was the
        # failure mode; with the enum constraint a vague tag cannot be emitted
        # at all, so the advice now only buys under-directed shots. The schema
        # floors moved with it (services/schemas.cg_prompt_schema), and a
        # project left on this body would be reading one set of numbers while
        # the grammar enforced another.
        "2b6ae56bafa118e5585efb8cfc6f89871de98939ff5353fb0f5bed9bb87ae94a",
        # The last enum-era version: it told the director it could not invent
        # tags and that the output format only accepted the listed values,
        # which stopped being true when the schema dropped the enum. A project
        # left on this body would be reading a promise the grammar no longer
        # makes, and spending its effort inside a palette it is no longer
        # confined to.
        "805fb9accef3ea4293056941da4a55c95e851da94077084cb8c2ad8b37d68328",
    ],
    # Every dialogue line in these was bare -- no performance bracket -- while
    # the prose prompt they ship alongside says dialogue is *always*
    # `Name (face, feeling): "line"`. Examples win that argument, so the
    # examples were teaching the model to drop the tagging it was being asked
    # for, and a project that never edited them was pushing uphill on every
    # generation. The same version also wrapped one line of dialogue across
    # two physical lines, which segments into a say node plus a narration node
    # holding the tail.
    "system.style_examples": [
        "91497ed97d3872a7cb87699b250297feafb76bbe4e3b1d7b5a04a3ccefb463d1",
        # The tagged version that preceded the Cym project's examples, which
        # replaced it as the shipped default (adds the NSFW-gated example 3).
        "83bfe7c57b5e1fd7a3e999faea164e837219ef8ce142dfb5e91bb173030e03db",
    ],
    # Both of these told the segmenter: "If a line clearly repeats something
    # that already happened before this passage (a recap the writer slipped
    # in), drop it." The pass is handed the fresh passage and nothing else --
    # no summary, no verbatim tail -- so "already happened before this passage"
    # is not a question it can answer. It could only guess, and every wrong
    # guess silently deleted a line of finished prose. Recap suppression is the
    # prose pass's job; it is the one that has the history.
    # Opened with the quality and style blocks -- twelve tokens of the region
    # the encoder attends to most, spent on words that describe no content --
    # and buried the subject count in the middle, which is the reverse of how
    # the Illustrious family was captioned. It also rendered `<lora:name:0.55>`
    # into the text, A1111 syntax that ComfyUI's CLIPTextEncode reads as
    # literal characters, directly in front of the identity block.
    "image.positive_wrapper": [
        "e31d85a4bd448ae33025e8d3c15605e6a1a9af3d688e1924a0a50ac01c3c80e6",
    ],
    "system.structuring": [
        # the rule, with the CONTINUE: skip
        "c31f134600bc5baf25fd5be8a11acdbc43da5f91ebb23df80a5e2aa605f9a3ae",
        # the rule, before the CONTINUE: skip was added
        "91c693476300e3e0db6245c88d373278dceadd0114d6d777a595f16a073fc97a",
        # Segmentation-only, but written when a third pass still did the
        # tagging: it says nothing about the performance brackets the writer
        # now puts on its lines, so a stretch that slipped past the code
        # stripper had `(smirking, amused)` copied into the dialogue text.
        "438c417870d78b5fd8fa3aed3955afbc4c10f57260e859d3c1d97c823793741c",
        # Listed the writable variable names and nothing else, so a character
        # stat was writable but unjudgeable: nothing said `affection_diana`
        # ran 0-100 or that the band it has to cross was twenty points off.
        # The one worked example nudges by 1, which on that ladder is twenty
        # warm choices per band -- a relationship that never visibly moves.
        "f1a37e7900f9c0bdf862d934e34de95e937faa57176b595b15e5d6bcd12f7f6b",
    ],
    # Versions that did not ask for performance notation at all. Left in place
    # none of these is a degraded prompt but a dead pipeline: nothing else
    # writes an expression now, so every line in the project would come out
    # `neutral` / `front` forever.
    "system.prose_gen": [
        # Gated the adult register on `{% if explicit %}` and carried a
        # `- Tone: {{ tone }}` line, from when the register was one value of a
        # tone enum. Both names still resolve -- `assemble` passes them for
        # exactly this reason -- so it is not a broken prompt, but a scene
        # marked NSFW in the editor gets nothing from it, because nothing
        # writes the tone string it tests any more.
        "0671ab2a30dc8f321312e5c0ed5f0f9278e1a5bfef8c2ec62188b90711ed9e30",
        # the default before that
        "ecbb465bf9dd1ace422430182f18648c13004113658d3f5a7ddafb66737dc1e0",
        # the one before it, from before the CONTINUE block and the per-scene
        # explicit clause existed -- still seeded into projects created back
        # then, and past the version where `active_template` adopts in place
        "f872863ce4d03f03ca26c3fa0501d88656318aade311057b821fd9646b9ac4e5",
        # An authored body rather than an outgoing default, and the one
        # exception to that rule: it *is* the body this default was merged
        # from. Its two edits are in the text above, so replacing it loses
        # nothing and is the only way the project it came from picks up the
        # notation its own prompt now specifies.
        "4874e9b9388db1ef573e5676cb6414b00da81705a1f3c7e7822c42f4f8f6c531",
        # Closed the explicit section with "This applies to this scene and
        # nothing else" and gave no direction on content. Replaced by the Cym
        # project's version of that paragraph when it became the default.
        "680db6c16c2cb2fb5988bd60420e0cdae4532f00d11517c50ba3e58efc5c018f",
    ],
    # Had no NSFW subsection, so a character's adult-register notes would be
    # written in the editor and then never reach a prompt.
    "character.card_format": [
        "c7649c23eb51a6d373c89e100a4a3a95467019b016425b396e8e416758bbd6eb",
    ],
    # Unweighted `solo`, and the pose tag last in a long prompt. See the
    # comment on _SPRITE_POSITIVE: this is why a `sitting` sprite came back
    # standing.
    "image.sprite_wrapper": [
        "36a9503ef6d941e4c74880bc4859267be660062b8597b599b00bc1d47fe36087",
    ],
}


# --------------------------------------------------------------------------
# emotion -> IndexTTS-2.5 vector (doc S5)
# --------------------------------------------------------------------------
# 8 sliders; the sum must stay <= 1.5. TTS Audio Suite's emotion node caps each
# slider at 1.2, so that is EMOTION_SLIDER_CAP (the older kaola wrapper stopped
# at 1.0 -- lower it again if you paste the `tts_indextts2` template back).
# Map dim names -> wrapper slots happen in workflows.TTS:
# fear->Afraid, disgust->Disgusted, melancholy->Melancholic, surprise->Surprised,
# anger->Angry. `speed` and `gain_db` ride along for pacing (speed becomes the
# model's duration_factor; see tts.build_workflow).

EMOTION_MAP: dict[str, dict[str, Any]] = {
    "neutral":      {"vec": {"calm": 0.3}},
    "happy":        {"vec": {"happy": 0.7}},
    "excited":      {"vec": {"happy": 0.9, "surprise": 0.3}, "speed": 1.08},
    "affectionate": {"vec": {"happy": 0.5, "calm": 0.3}, "speed": 0.96},
    "amused":       {"vec": {"happy": 0.5, "surprise": 0.2}},
    "smug":         {"vec": {"happy": 0.4, "anger": 0.15}, "speed": 0.95},
    "melancholy":   {"vec": {"melancholy": 0.7, "sad": 0.3}, "speed": 0.93},
    "sad":          {"vec": {"sad": 0.8}, "speed": 0.92},
    "crying":       {"vec": {"sad": 0.8, "fear": 0.2}, "speed": 0.88},
    "lonely":       {"vec": {"melancholy": 0.6, "sad": 0.35}, "speed": 0.9},
    "anxious":      {"vec": {"fear": 0.6, "surprise": 0.2}, "speed": 1.06},
    "afraid":       {"vec": {"fear": 0.9}, "speed": 1.1},
    "angry":        {"vec": {"anger": 0.9}},
    "irritated":    {"vec": {"anger": 0.55}},
    "mean":         {"vec": {"anger": 0.5, "happy": 0.3}},
    "disgusted":    {"vec": {"disgust": 0.8}},
    "surprised":    {"vec": {"surprise": 0.9}, "speed": 1.05},
    "confused":     {"vec": {"surprise": 0.5, "fear": 0.2}},
    "embarrassed":  {"vec": {"fear": 0.35, "happy": 0.3}, "speed": 1.03},
    "flustered":    {"vec": {"surprise": 0.5, "fear": 0.35}, "speed": 1.1},
    "tired":        {"vec": {"melancholy": 0.4, "calm": 0.3}, "speed": 0.87},
    "determined":   {"vec": {"anger": 0.3, "calm": 0.4}},
    "cold":         {"vec": {"calm": 0.5, "anger": 0.2}, "speed": 0.95},
    "whisper":      {"vec": {"calm": 0.4}, "speed": 0.85, "gain_db": -4.0},
}

EMOTION_DIMS = ["happy", "anger", "sad", "fear", "disgust", "melancholy", "surprise", "calm"]
EMOTION_VECTOR_SUM_CAP = 1.5
EMOTION_SLIDER_CAP = 1.2


# --------------------------------------------------------------------------
# delivery note -> pacing and punctuation (tts.delivery_rules)
# --------------------------------------------------------------------------
# The writer's free-text note ("trails off", "too quick", "flat") is the only
# per-line direction the voice gets beyond the emotion enum, and IndexTTS-2.5
# has no markup for it: no SSML, no word stress, and English text is lowercased
# before tokenising, so caps do nothing. What it does follow is punctuation and
# duration -- so each rule is some mix of:
#
#   speed      multiplies the emotion map's speed (the model's duration_factor)
#   intensity  multiplies the line's intensity before the vector is scaled
#   text       an edit to the spoken text only, never the say node's text:
#              hesitate  first comma -> "…", else "…" after the first word
#              exclaim   a closing "." (or none) -> "!"
#              trail     closing punctuation -> "…"
#              cut       closing punctuation -> "—", the line stops dead
#
# Phrases match whole words, case-insensitively. Several rules can fire; their
# multipliers compound and the ending edits apply in the order above, so the
# last one wins. Bare "quick" / "slow" / "sharp" are left out on purpose: the
# writer uses them for glances and smiles as often as for speech.

DELIVERY_RULES: list[dict[str, Any]] = [
    {"name": "trailing", "text": "trail", "speed": 0.95,
     "match": ["trails off", "trailing off", "trailing", "trails", "fading",
               "fades", "fades out", "drifts off", "tapering off"]},
    {"name": "hesitant", "text": "hesitate", "speed": 0.93,
     "match": ["hesitant", "hesitantly", "hesitates", "hesitating", "halting",
               "haltingly", "tentative", "tentatively", "uncertain", "unsure",
               "stammering", "stammers", "stuttering", "stutters"]},
    {"name": "cut off", "text": "cut",
     "match": ["cut off", "cuts off", "cuts herself off", "cuts himself off",
               "cuts themself off", "breaks off", "interrupted", "cut short"]},
    {"name": "rushed", "speed": 1.1,
     "match": ["too quick", "too quickly", "quickly", "rushed", "rushing",
               "hurried", "hurriedly", "rapid", "rapidly", "rapid-fire", "fast",
               "blurts", "blurted", "blurting", "all at once", "breathless",
               "breathlessly", "talking over", "in a rush"]},
    {"name": "slow", "speed": 0.9,
     "match": ["slowly", "too slowly", "deliberate", "deliberately", "measured",
               "drawn out", "drawling", "drawls", "savoring", "lingering"]},
    {"name": "weary", "speed": 0.93,
     "match": ["sighs", "sighing", "with a sigh", "wearily", "weary", "exhausted"]},
    {"name": "flat", "intensity": 0.5,
     "match": ["flat", "flatly", "deadpan", "monotone", "toneless", "tonelessly",
               "emotionless", "dryly", "matter-of-fact", "matter-of-factly"]},
    {"name": "hushed", "intensity": 0.75,
     "match": ["under her breath", "under his breath", "under their breath",
               "muttering", "mutters", "muttered", "murmuring", "murmurs",
               "mumbling", "mumbles", "quietly", "softly", "barely audible",
               "to herself", "to himself", "to themself"]},
    {"name": "emphatic", "intensity": 1.2,
     "match": ["firmly", "emphatic", "emphatically", "insistent", "insisting",
               "pointedly", "sharply", "clipped", "through gritted teeth",
               "through her teeth", "through his teeth"]},
    {"name": "loud", "text": "exclaim", "speed": 1.05, "intensity": 1.3,
     "match": ["shouting", "shouts", "shouted", "yelling", "yells", "yelled",
               "screaming", "screams", "bellowing", "raising her voice",
               "raising his voice", "raising their voice", "raised voice"]},
]


# --------------------------------------------------------------------------
# character wizard vocabulary
# --------------------------------------------------------------------------
# Curated dropdowns of tag-mapped options; every field also takes free text
# that is stored as a raw tag.

TAG_VOCAB: dict[str, list[dict[str, str]]] = {
    "hair_color": [
        {"label": "black", "tag": "black hair"},
        {"label": "dark brown", "tag": "brown hair"},
        {"label": "platinum blonde", "tag": "platinum blonde hair"},
        {"label": "blonde", "tag": "blonde hair"},
        {"label": "silver", "tag": "silver hair"},
        {"label": "white", "tag": "white hair"},
        {"label": "red", "tag": "red hair"},
        {"label": "pink", "tag": "pink hair"},
        {"label": "blue", "tag": "blue hair"},
        {"label": "green", "tag": "green hair"},
        {"label": "purple", "tag": "purple hair"},
        {"label": "two-tone", "tag": "multicolored hair, two-tone hair"},
    ],
    "hair_style": [
        {"label": "long and straight", "tag": "long hair, straight hair"},
        {"label": "hime cut", "tag": "long hair, hime cut, straight hair"},
        {"label": "wolf cut", "tag": "wolf cut"},
        {"label": "short bob", "tag": "short hair, bob cut"},
        {"label": "ponytail", "tag": "ponytail, long hair"},
        {"label": "twintails", "tag": "twintails"},
        {"label": "braided", "tag": "braid, single braid"},
        {"label": "messy short", "tag": "short hair, messy hair"},
        {"label": "wavy long", "tag": "long hair, wavy hair"},
        {"label": "buzz cut", "tag": "very short hair, buzz cut"},
        {"label": "undercut", "tag": "undercut, short hair"},
    ],
    "eye_color": [
        {"label": "pale grey", "tag": "grey eyes"},
        {"label": "brown", "tag": "brown eyes"},
        {"label": "blue", "tag": "blue eyes"},
        {"label": "green", "tag": "green eyes"},
        {"label": "amber", "tag": "orange eyes"},
        {"label": "red", "tag": "red eyes"},
        {"label": "violet", "tag": "purple eyes"},
        {"label": "heterochromia", "tag": "heterochromia"},
    ],
    "eye_quality": [
        {"label": "sharp, guarded", "tag": "tsurime, half-closed eyes"},
        {"label": "wide, open", "tag": "tareme"},
        {"label": "sleepy", "tag": "half-closed eyes, sleepy"},
        {"label": "intense", "tag": "sharp eyes"},
        {"label": "gentle", "tag": "gentle eyes"},
        {"label": "glasses", "tag": "glasses"},
    ],
    "face": [
        {"label": "cute, youthful", "tag": "baby face"},
        {"label": "round", "tag": "round face"},
        {"label": "soft, gentle", "tag": "soft features"},
        {"label": "mature", "tag": "mature female"},
        {"label": "elegant, refined", "tag": "refined features"},
        {"label": "sharp, angular", "tag": "angular face"},
        {"label": "long, narrow", "tag": "long face"},
        {"label": "wide, square jaw", "tag": "square jaw"},
    ],
    "skin_tone": [
        {"label": "pale", "tag": "pale skin"},
        {"label": "fair", "tag": "light skin"},
        {"label": "olive", "tag": "tan skin"},
        {"label": "brown", "tag": "dark skin"},
        {"label": "deep brown", "tag": "very dark skin"},
    ],
    "height": [
        {"label": "short (under 155cm)", "tag": "short"},
        {"label": "average (160-170cm)", "tag": ""},
        {"label": "tall (175cm+)", "tag": "tall"},
        {"label": "very tall (185cm+)", "tag": "very tall"},
    ],
    "build": [
        {"label": "lean, athletic", "tag": "slender, toned"},
        {"label": "slight", "tag": "petite, slender"},
        {"label": "average", "tag": "average build"},
        {"label": "curvy", "tag": "curvy, wide hips"},
        {"label": "muscular", "tag": "muscular"},
        {"label": "plump", "tag": "plump"},
    ],
    "bust": [
        {"label": "flat", "tag": "flat chest"},
        {"label": "small", "tag": "small breasts, perky breasts"},
        {"label": "medium", "tag": "medium breasts, perky breasts"},
        {"label": "large", "tag": "large breasts"},
        {"label": "huge", "tag": "huge breasts"},
        {"label": "gigantic", "tag": "gigantic breasts"},
        {"label": "sagging", "tag": "large breasts, sagging breasts"},
    ],
    "butt": [
        {"label": "flat", "tag": "flat ass"},
        {"label": "cute", "tag": "cute ass, small ass"},
        {"label": "average", "tag": ""},
        {"label": "round", "tag": "round ass, bubble butt"},
        {"label": "huge", "tag": "huge ass, massive butt"},
    ],
    "rest_expression": [
        {"label": "flat, unreadable", "tag": "expressionless"},
        {"label": "faint smile", "tag": "light smile"},
        {"label": "tired", "tag": "tired eyes"},
        {"label": "guarded", "tag": "serious"},
        {"label": "open, warm", "tag": "smile"},
        {"label": "sullen", "tag": "frown"},
    ],
    "posture": [
        {"label": "upright, formal", "tag": "standing straight"},
        {"label": "slouched", "tag": "slouching"},
        {"label": "arms crossed", "tag": "crossed arms"},
        {"label": "hands in pockets", "tag": "hand in pocket"},
        {"label": "restless", "tag": "fidgeting"},
    ],
    # Which of these apply per outfit is set per-character on the Outfits
    # card (Outfit.hidden_marks / .hidden_accessories), not here.
    "accessories": [
        {"label": "glasses", "tag": "glasses"},
        {"label": "earrings", "tag": "earrings"},
        {"label": "choker", "tag": "choker"},
        {"label": "necklace", "tag": "necklace"},
        {"label": "hair ribbon", "tag": "hair ribbon"},
        {"label": "headphones", "tag": "headphones around neck"},
        {"label": "wristwatch", "tag": "watch"},
        {"label": "scarf", "tag": "scarf"},
        {"label": "nose piercing", "tag": "nose piercing"},
        {"label": "tongue piercing", "tag": "tongue piercing"},
        {"label": "lip piercing", "tag": "lip piercing"},
        {"label": "nipple piercings", "tag": "nipple piercings"},
        {"label": "navel piercing", "tag": "navel piercing"},
        {"label": "belly chain", "tag": "belly chain"},
        {"label": "arm bracelet", "tag": "bracelet"},
        {"label": "anklet", "tag": "anklet"},
        {"label": "toe ring", "tag": "toe ring"},
        {"label": "thigh garter", "tag": "garter straps"},
    ],
    "distinguishing": [
        {"label": "mole under eye", "tag": "mole under eye"},
        {"label": "mole under mouth", "tag": "mole under mouth"},
        {"label": "cat ears", "tag": "cat ears"},
        {"label": "freckles", "tag": "freckles"},
        {"label": "scar on cheek", "tag": "scar on cheek"},
        {"label": "eye scar", "tag": "eye scar"},
        {"label": "birthmark on chest", "tag": "birthmark on chest"},
        {"label": "neck tattoo", "tag": "neck tattoo"},
        {"label": "chest tattoo", "tag": "chest tattoo"},
        {"label": "back tattoo", "tag": "back tattoo"},
        {"label": "thigh tattoo", "tag": "thigh tattoo"},
        {"label": "stomach tattoo", "tag": "stomach tattoo"},
        {"label": "arm tattoo", "tag": "arm tattoo"},
        {"label": "lower back tattoo", "tag": "lower back tattoo, tramp stamp"},
        {"label": "freckles across shoulders", "tag": "shoulder freckles"},
        {"label": "bandaged arm", "tag": "bandaged arm"},
        {"label": "small penis", "tag": "small penis"},
        {"label": "medium penis", "tag": "medium penis"},
        {"label": "large penis", "tag": "large penis"},
        {"label": "huge penis", "tag": "huge penis, veiny penis"},
        {"label": "cleft of venus", "tag": "cleft of venus"},
        {"label": "large areolae", "tag": "large areolae"},
        {"label": "public stubble", "tag": "public stubble"},
        {"label": "public hair", "tag": "public hair"},
        {"label": "excess public hair", "tag": "excessive public hair"},
        {"label": "stretch marks", "tag": "stretch marks"},
        {"label": "cellulite", "tag": "cellulite"},
        {"label": "thick thighs", "tag": "thick thighs"},
        {"label": "gluteal fold", "tag": "gluteal fold"},
        {"label": "milf", "tag": "milf"},
        {"label": "tanlines", "tag": "tanlines"},
        {"label": "cleavage", "tag": "cleavage"},
    ],
    "negatives": [
        {"label": "public hair", "tag": "public hair"},
        {"label": "fat", "tag": "fat"},
        {"label": "old", "tag": "old"},
    ],
    "speech_register": [
        {"label": "formal / polite", "tag": ""},
        {"label": "casual", "tag": ""},
        {"label": "blunt / clipped", "tag": ""},
        {"label": "archaic / literary", "tag": ""},
        {"label": "regional dialect", "tag": ""},
        {"label": "slangy", "tag": ""},
    ],
}

SHOT_VOCAB = [
    "close-up", "upper body", "cowboy shot", "full body", "from below",
    "from above", "from side", "dutch angle", "over-the-shoulder", "wide shot",
]

# --------------------------------------------------------------------------
# CG director vocabulary
# --------------------------------------------------------------------------
# Illustrious/NoobAI are Danbooru-tag-trained, and SDXL's CLIP encoders bind a
# bag of concepts rather than a sentence -- so prose in the prompt is not just
# unhelpful, it spends the attention budget on syntax the encoder discards.
# The fix is not a different base model (an SDXL LoRA runs on nothing but
# SDXL); it is to make the director emit tags the checkpoint actually saw in
# training. Danbooru happens to have a rich vocabulary for exactly what a VN
# CG needs -- camera, POV, contact, gaze -- so this is the list the director
# is given, and it is told to prefer it over anything it invents.
#
# Everything here is a real Danbooru tag. If you add to it, check the tag
# exists on danbooru first; an invented tag is noise in the conditioning.
CG_VOCAB: dict[str, list[str]] = {
    "framing": [
        "close-up", "portrait", "upper body", "lower body", "cowboy shot",
        "full body", "wide shot", "face focus", "solo focus", "profile",
        "hand focus", "foot focus", "back focus", "ass focus",
        "feet out of frame", "head out of frame", "cropped",
    ],
    "angle": [
        "from above", "from below", "from side", "from behind", "from front",
        "three-quarter view", "dutch angle", "straight-on", "foreshortening",
        "upside-down", "fisheye", "perspective",
    ],
    "pov": [
        # The player is the camera in a first/second-person VN: these are the
        # tags that make the shot read as *seen from where the player stands*.
        "pov", "pov hands", "looking at viewer", "eye contact",
        "reaching towards viewer", "incoming hug", "imminent kiss",
        "leaning forward", "close to viewer", "pointing at viewer",
        "hand on viewer", "straddling viewer",
        # The player's body may be in frame; their face may not. These are how
        # Danbooru says that, and the checkpoint has seen a great many of them
        # -- a POV embrace is one of the most heavily tagged compositions
        # there is. Without one the second body in the frame has no tags of
        # its own and gets built out of the heroine's, which is how the player
        # ended up wearing her uniform.
        "faceless", "faceless male", "faceless female",
    ],
    # Body parts as *composition*, never as description. Everything here is
    # something the camera can be pointed at; nothing here says what a body
    # looks like. That line matters more than it seems -- `_CG_ALLOWED` lets
    # this vocabulary override the identity ban in `clean_cg_tags`, so a size
    # or shape word added to this list would hand the director a way to
    # contradict the character record ("wide hips", "medium breasts" and
    # "round ass" already come from Natalie's own appearance fields). Parts,
    # yes; adjectives about parts, never.
    "body": [
        "collarbone", "nape", "shoulders", "neck", "armpits", "navel",
        "stomach", "back", "spine", "thighs", "legs", "knees", "kneepits",
        "feet", "soles", "toes", "barefoot", "hands", "palms", "fingers",
        "lips", "tongue", "teeth", "wrist", "ankle", "jawline",
        # Skin an outfit leaves uncovered. Ordinary tags -- a sundress shows
        # bare arms -- so they belong here rather than in the adult palette,
        # where the register screen would have withheld them from every SFW
        # scene that legitimately wanted one.
        "bare back", "bare arms", "bare legs",
    ],
    "contact": [
        "hug", "hug from behind", "waist hug", "arm hug", "group hug",
        "holding hands", "interlocked fingers", "locked arms",
        "hand on another's shoulder", "hand on another's cheek",
        "hand on another's head", "hand on another's chest", "headpat",
        "arm grab", "wrist grab", "shoulder grab", "chin grab",
        "leaning on person", "back-to-back", "facing another", "carrying",
        "princess carry", "piggyback", "shoulder carry", "over the knee",
        "cheek-to-cheek", "forehead-to-forehead", "noses touching",
        "heads together", "head on chest", "head on another's shoulder",
        "shoulder-to-shoulder", "face-to-face", "lap pillow", "sitting on lap",
        "sitting on person", "straddling", "leg lock", "kiss", "kiss on forehead",
        "kiss on cheek", "hand holding", "pinky swear", "high five", "fist bump",
    ],
    "gaze": [
        "looking at viewer", "looking away", "looking down", "looking up",
        "looking to the side", "looking back", "looking at another",
        "closed eyes", "half-closed eyes", "staring", "averting eyes",
        "sideways glance", "looking afar", "downcast eyes",
    ],
    "hands": [
        "outstretched hand", "own hands together", "own hands clasped",
        "hand on own chest", "hand up", "covering mouth", "adjusting hair",
        "clenched hand", "crossed arms", "arms behind back", "hands in pockets",
        "holding", "open hand", "spread fingers", "curled fingers",
        "steepled fingers", "cupping hands", "reaching", "pointing",
        "pointing at self", "waving", "facepalm", "shushing", "beckoning",
        "thumbs up", "raised fist", "stroking own chin", "finger to mouth",
        "arm support", "head rest", "outstretched arms", "spread arms",
        "arms at sides", "arm behind head", "shrugging", "hand over eyes",
    ],
    "posture": [
        "standing", "sitting", "kneeling", "on one knee", "lying", "on back",
        "on side", "on stomach", "squatting", "leaning forward", "leaning back",
        "against wall", "walking", "running", "turning around", "bent over",
        "arched back", "all fours", "crawling", "fetal position", "crossed legs",
        "crossed ankles", "seiza", "reclining", "hugging own legs", "knees to chest",
        "knee up", "knees up", "legs apart", "spread legs", "leg up", "tiptoes",
        "stretching", "slouching", "twisted torso", "head tilt", "head down",
        "head back", "jumping", "falling", "midair", "standing on one leg",
        "legs crossed", "sitting on floor", "sitting on chair", "cowering",
    ],
    "light": [
        "dramatic lighting", "backlighting", "rim lighting", "dim lighting",
        "soft lighting", "harsh shadow", "cinematic lighting", "god rays",
        "lens flare", "silhouette", "depth of field", "blurry background",
        "motion blur", "wind", "floating hair", "dappled sunlight",
        "window light", "chiaroscuro", "light rays", "backlit", "vignetting",
        "bloom", "caustics", "candlelight", "neon lights", "firelight",
    ],
    # The visceral half. A CG goes flat when the prompt names a mood but no
    # physical thing the eye can land on, so the director is asked for one of
    # these on every shot (see "The bar" in system.cg_director).
    "detail": [
        "sweat", "tears", "teardrop", "blush", "trembling", "clenched teeth",
        "wet hair", "wet clothes", "steam", "breath", "goosebumps",
        "veins", "scar", "bandaged", "dirty face", "blood on face",
        "messy hair", "hair between eyes", "disheveled", "sunlight through hair",
        "falling petals", "dust particles", "light particles", "rain",
        "snowflakes", "embers", "reflection", "shadow over eyes",
        "heavy breathing", "parted lips", "biting lip", "saliva", "drooling",
        "flushed", "goose bumps", "wet skin", "smoke", "condensation",
        "torn clothes", "wrinkled clothes", "loose clothing", "wind lift",
    ],
}

# The adult half of the vocabulary, shown to the director ONLY for a scene the
# author marked NSFW (`cg_vocab_block(nsfw=True)`, fed from the owning scene's
# flag in `services.context.assemble_cg`).
#
# Gated rather than merged for the same reason the prose prompt gates its adult
# register: an explicit tag offered on every shot is an explicit tag that turns
# up in a scene that never asked for one, and "ignore this unless" is not an
# instruction a 7-12B holds reliably. A project with no NSFW scene never has
# these in a prompt at all.
#
# Anatomy is deliberately absent here too. The character record already carries
# it (Natalie's own tags include "shaved vagina" and "large areolas"), so these
# groups describe only what is *happening* -- acts, positions and transient
# state -- which is the half the director is qualified to decide.
CG_VOCAB_NSFW: dict[str, list[str]] = {
    "position": [
        "missionary", "cowgirl position", "reverse cowgirl position",
        "doggystyle", "sex from behind", "prone bone", "standing sex",
        "spooning", "girl on top", "boy on top", "upright straddle",
        "reverse upright straddle", "mating press", "folded", "legs up",
        "knees to chest", "legs over head", "suspended congress", "69",
        "amazon position", "squatting cowgirl position", "standing missionary",
        "top-down bottom-up", "mounting", "against wall", "on lap",
    ],
    "act": [
        "sex", "vaginal", "anal", "oral", "fellatio", "cunnilingus",
        "handjob", "fingering", "paizuri", "grinding", "frottage",
        "kissing", "french kiss", "deep kiss", "tongue out", "licking",
        "groping", "breast grab", "ass grab", "thigh grab", "hair grab",
        "undressing", "clothed sex", "clothes lift", "straddling",
        "nipple stimulation", "masturbation", "penetration", "insertion",
        "hug from behind", "pinned down", "held down", "restrained",
    ],
    # The half that was missing, and the reason an adult scene came back
    # describing what was happening but never what was visible.
    #
    # Two separate mechanisms conspired. The director was never *offered* an
    # anatomy tag for an adult beat -- the groups above are positions, acts and
    # states, and none of them names a body part. And any it reached for anyway
    # was eaten on the way back: `BANNED_TAG` screens anatomy regardless of
    # register, because in an ordinary scene that all comes from the character
    # record, and its only exemption is the curated vocabulary. So `nipples`,
    # `cleavage`, `pubic hair` and `ass` were dropped from every render,
    # including in a scene explicitly marked 18+.
    #
    # The same rule as CG_VOCAB["body"] applies and matters more here, because
    # this list is what overrides the identity ban: **parts and visible states,
    # never sizes or shapes**. `nipples` is something a frame either shows or
    # does not; `large breasts` is a fact about the character that her record
    # already supplies, and putting it here would let the director contradict
    # it. Which is why `breasts`, `flat chest` and `thigh gap` are deliberately
    # absent despite being real tags.
    # Named `body` rather than `anatomy` so it lands in an existing tag
    # category (`tags.CATEGORIES`) -- a new group name becomes a new category,
    # and one nothing else knows about has no weight and no bucket in the UI.
    "body": [
        "nipples", "areolae", "nipple slip", "cleavage", "underboob", "sideboob",
        "pubic hair", "groin", "crotch", "ass", "butt crack", "anus",
        "penis", "testicles", "pussy", "clitoris",
    ],
    "state": [
        "nude", "topless", "bottomless", "partially undressed", "naked shirt",
        "clothing aside", "open clothes", "bare shoulders", "exposed",
        "orgasm", "afterglow", "ahegao", "rolling eyes", "half-closed eyes",
        "heavy breathing", "moaning", "open mouth", "saliva trail",
        "sweat", "sweatdrop", "trembling", "arched back", "curled toes",
        # "embarrassed" and "shy" used to be here and are deliberately gone:
        # both are values of the node's own expression enum, so offering them
        # to the director meant two instructions about one face.
        "clenched hands", "covering breasts",
        "covering crotch", "steam", "wet",
    ],
}


# Tags the director is not allowed to emit, however it phrases them. Identity
# comes from the character record every time, unchanged -- a director-written
# hair or eye tag either duplicates it (wasted attention) or contradicts it
# (two people in one body), and both are worse than saying nothing. The
# instruction is in the template; this is the enforcement, because a 7-12B
# following a "never" is not something to bet a render on.
BANNED_TAG = re.compile(
    r"\b("
    # -- how many people, which the group count computes instead ------------
    r"\d+\s*(girls?|boys?)|solo|"
    # -- hair: colour, length, and the cut names that never say "hair" ------
    r"hair|bangs|ponytail|twintails|braid|sidelocks|ahoge|updo|"
    r"bob cut|pixie cut|hime cut|wolf cut|undercut|afro|dreadlocks|"
    r"blonde|blond|brunette|redhead|"
    # -- eyes and face -----------------------------------------------------
    r"eyes|eyelashes|eyebrows|heterochromia|tareme|tsurime|"
    r"face|features|jaw|jawline|chin|cheekbones|"
    # -- skin and its marks. `skinned` is separate because \bskin\b does not
    #    match "dark-skinned", which is how that preset is actually written.
    r"skin|skinned|freckles|mole|scar|birthmark|tanlines|stretch marks|"
    r"cellulite|stubble|tattoo|piercing|"
    # -- shape and proportions. All of this comes from the appearance fields
    #    (build / bust / butt / height), and the director writing any of it
    #    either repeats the record or argues with it. This was the gap: of 142
    #    preset identity tags, 65 used to survive, and "wide hips", "round
    #    ass" and "bubble butt" are on Natalie's own record.
    r"hips|ass|buttocks|butt|waist|bust|breasts?|chest|cleavage|areolae?|"
    # `thighs?` singular too, for the shape words the plural misses: `thigh
    # gap` is a claim about a body and `\bthighs\b` does not touch it. `thighs`
    # itself is curated as composition and comes back via the rescue list.
    r"nipples?|thighs?|abs|abdomen|physique|build|figure|ears|"
    r"curvy|slender|petite|plump|chubby|fat|obese|muscular|toned|slim|thin|"
    r"gluteal|cleft of venus|milf|"
    r"penis|testicles|vagina|pussy|"
    # -- height and the age a body reads as --------------------------------
    r"short|tall|young|old|mature|teen|adult|"
    # -- the face's expression, which the *node* owns. `expression` on a say
    #    or narration node picks one of images.EXPRESSION_TAGS and that lands
    #    in its own prompt slot, so a director-written "smile" does not
    #    replace it -- both go in, and the render gets two instructions about
    #    one face. 29 of the 34 tags EXPRESSION_TAGS can emit used to be
    #    reachable this way.
    #
    #    The transient physical half stays available, via the usual vocabulary
    #    override: `blush`, `tears` and `open mouth` are in CG_VOCAB because
    #    they add to a face rather than deciding it.
    r"expression|expressionless|gentle expression|"
    r"smile|smiling|grin|grinning|laughing|smirk|smirking|"
    r"frown|frowning|scowl|angry|glaring|glare|furrowed|"
    r"pout|pouting|crying|tears?|tearing|teary|sad|"
    r"surprised|startled|wide-eyed|blush|blushing|flushed|embarrassed|"
    r"sleepy|worried|concerned|thinking|serious|closed mouth|open mouth|"
    # -- worn things, which the outfit and the accessories list own ---------
    r"glasses|earrings|necklace|choker|bracelet|anklet|ring|watch|scarf|"
    r"belt|headphones|garter|chain|"
    r"uniform|dress|skirt|shirt|blouse|jacket|coat|sweater|hoodie|"
    r"swimsuit|bikini|kimono|yukata|apron|pantyhose|thighhighs|socks|shoes|hat"
    r")\b",
    re.IGNORECASE,
)

# The vocabulary always wins over the ban list: several legitimate camera tags
# contain a banned word ("closed eyes", "floating hair", "averting eyes"), and
# dropping those would quietly delete the very gaze direction that was asked
# for.
#
# Both halves, including the adult one. This is a rescue list, not a filter --
# what an SFW scene's director is allowed to *reach for* is decided by which
# groups `cg_vocab_block` shows it, and a tag it was never offered is one it
# has no reason to write. Leaving the adult tags out here would not gate
# anything; it would only mean that on a scene that legitimately asked for
# them, "hair grab" got silently eaten by the identity ban.
CG_ALLOWED = {t.lower()
               for vocab in (CG_VOCAB, CG_VOCAB_NSFW)
               for group in vocab.values()
               for t in group}

# The SFW half on its own, which is the half that outranks the adult screen.
#
# `all fours`, `spread legs`, `straddling` and `straddling viewer` are curated
# SFW posture and POV tags -- a character on all fours reaching for a dropped
# book is an ordinary shot -- and they are also substrings of how explicit
# material is described, so a blunt adult regex eats them. Hand-picking them
# into CG_VOCAB *is* the judgement that they are safe, so that judgement wins;
# the adult screen only ever gets to decide about vocabulary nobody vetted,
# plus CG_VOCAB_NSFW, which is exactly the set an SFW scene must not reach.
CG_ALLOWED_SFW = {t.lower()
                  for group in CG_VOCAB.values()
                  for t in group}


# Explicit vocabulary, screened out of the palette an SFW scene is offered and
# out of its director's output.
#
# The enum used to give this for free: an SFW beat was handed a grammar built
# from the SFW groups only, so an adult tag was unreachable rather than
# discouraged. Free-text fields give that up, and the real dictionary is full
# of tags no scene asked for -- `sex`, `vaginal` and `groping` are all in the
# top of the `action` category by post count, because Danbooru is Danbooru.
# So the guarantee moves here: same promise, enforced on the way in (which
# tags are offered) and on the way out (services.generation.clean_cg_tags),
# rather than by the shape of the grammar.
#
# Deliberately blunt. A false positive costs one tag on an adult scene, where
# the curated CG_VOCAB_NSFW list is offered anyway and wins via CG_ALLOWED; a
# false negative puts an explicit tag in a scene that did not ask for one.
ADULT_TAG = re.compile(
    r"\b("
    r"sex|sexual|intercourse|penetration|penetrated|vaginal|anal|oral|"
    r"fellatio|cunnilingus|paizuri|handjob|footjob|masturbation|"
    r"orgasm|ejaculation|cum|semen|precum|"
    r"nude|nudity|naked|topless|bottomless|undressing|undressed|"
    r"penis|testicles|vagina|pussy|clitoris|labia|anus|"
    r"nipples?|areolae?|breasts out|nipple slip|"
    # Anatomy the identity ban does not catch, because these are not words a
    # character record ever writes -- so without them here an adult-palette
    # tag passed both screens and reached an ordinary scene.
    r"boobs?|underboob|sideboob|groin|crotch|"
    r"groping|molest[a-z]*|rape|bondage|bdsm|"
    r"erection|aroused|arousal|horny|lewd|ahegao|"
    r"panties|underwear|lingerie|bra|thong|garter belt|"
    r"spread legs|legs up|presenting|all fours|doggystyle|cowgirl position|"
    r"missionary|straddling|grinding|humping|"
    r"saliva trail|body fluids|sweaty body"
    r")\b",
    re.IGNORECASE,
)


def cg_vocab_block(nsfw: bool = False) -> str:
    """CG_VOCAB rendered for a prompt -- one line per group.

    `nsfw` appends CG_VOCAB_NSFW under a heading that says why it is there.
    The heading is not decoration: without it the adult tags read as just more
    camera vocabulary, and the director reaches for them to make an ordinary
    scene look interesting.
    """
    lines = [f"- {group}: {', '.join(tags)}" for group, tags in CG_VOCAB.items()]
    if nsfw:
        lines.append("")
        lines.append("This scene is marked adult (18+). These are available too, "
                     "and only when the moment in the script is actually one of "
                     "them -- an explicit tag on a beat that is not explicit is "
                     "the same mistake as a flat one:")
        lines += [f"- {group}: {', '.join(tags)}"
                  for group, tags in CG_VOCAB_NSFW.items()]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CG LoRA training set vocabulary
# --------------------------------------------------------------------------
# What the CG training set varies, and the sheet deliberately does not
# (lora-training.md S4 point 3: "a dataset where every image is a front-facing
# full-body shot on white produces a LoRA that quietly insists on front-facing
# full-body shots on white"). All four lists are sampled per image rather than
# multiplied out -- the run is a fixed count, not a grid.
SHEET_FRAMINGS = ["full body", "cowboy shot", "upper body", "close-up", "portrait"]

SHEET_SCENES = [
    "simple background, white background",
    "indoors, blurry background, depth of field",
    "outdoors, day, blurry background",
    "indoors, window light, soft lighting",
    "outdoors, evening, dim lighting",
    "simple background, grey background",
    "indoors, dramatic lighting",
]

# Who else is in frame. An event CG is shot from the player's point of view,
# so a CG LoRA trained only on solo studio shots fights every two-hander and
# every close POV moment it is later asked for. The dataset has to contain
# them.
#
# The second figure is faceless, always. A second real face in the set is a
# second identity for the LoRA to learn and then leak into its subject -- and
# it is also a lie about the game, where the other body in frame is the player
# and the camera sits behind their eyes. `faceless male` / `faceless female`
# is what `system.cg_director` now asks for by name on any shot that brings the
# player's head into frame, so these are the images that teach the LoRA to
# accept it instead of putting a face there anyway.
#
# Both genders, because which one a project needs depends on its player
# character and the dataset has no way to know.
#
# No count tags here: the frame's count is computed from the subject's own
# gender plus these (images.company_count). An entry carrying `1girl` would be
# wrong for a male subject, and could never say `2girls` at all.
#
# Solo still dominates at six of nine. The company images exist to stop the
# LoRA refusing a second body, which is a much smaller job than teaching the
# subject's face, and every frame spent on a partner is a frame not spent on
# the identity this LoRA is for.
#
# No camera angles here either. `cg_set_plan` samples every axis independently,
# so a `from behind` in this list lands on top of whatever the angle axis drew
# and the two contradict outright -- where a gaze or contact tag merely layers.
CG_SET_COMPANY = [
    "solo",                              # weighted: most CGs are still one figure
    "solo",
    "solo",
    "solo, pov, looking at viewer",
    "solo, pov, pov hands, looking at viewer",
    "solo, pov, close to viewer, eye contact",
    "pov, faceless male, hug",
    "pov, faceless female, hand on another's shoulder",
    "pov, faceless male, leaning on person",
]

# What the character is *doing*. The sheet trains angles and expressions; a CG
# prompt is mostly context and action, so the CG dataset has to have taught
# the LoRA that its subject's body moves.
CG_SET_ACTIONS = [
    "standing",
    "sitting",
    "walking",
    "leaning forward",
    "leaning back",
    "against wall",
    "arms crossed",
    "hand on own chest",
    "outstretched hand, reaching towards viewer",
    "covering mouth",
    "adjusting hair",
    "looking back over shoulder",
    "turning around",
    "kneeling",
    "lying, on back",
    "bent over",
    "hands on hips",
    "holding, both hands",
    "hugging own knees",
    "stretching",
]

TIME_OF_DAY = ["dawn", "morning", "day", "afternoon", "dusk", "evening", "night", "late night"]

TIME_TAGS = {
    "dawn": "dawn, soft golden light, long shadows",
    "morning": "morning, bright natural light",
    "day": "daylight, clear sky",
    "afternoon": "afternoon light, warm tone",
    "dusk": "sunset, orange sky, golden hour",
    "evening": "evening, dim light",
    "night": "night, moonlight, dark",
    "late night": "late night, deep blue tones, sparse lighting",
}

WEATHER_TAGS = {
    "clear": "clear sky",
    "cloudy": "cloudy, overcast",
    "rain": "rain, wet ground, reflections",
    "storm": "storm, heavy rain, lightning",
    "snow": "snow, snowing",
    "fog": "fog, mist",
    "wind": "wind, blowing leaves",
}
