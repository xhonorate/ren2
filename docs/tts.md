# Voice (phase 2 notes)

Decisions from the design conversation; the runner implements these.

## Engines
- **Per-character fine-tune, not zero-shot.** GPT-SoVITS is the default
  (good anime-style voices, few minutes of audio, emotion via reference clip);
  F5-TTS fine-tune is the fallback. Zero-shot (XTTS, Chatterbox) was the old
  project's weak point. Fine-tunes batch overnight like LoRAs.
- IndexTTS2 stays an option specifically because it disentangles speaker
  from emotion (speaker clip + separate emotion clip).

## Delivery
- The writer tags each line with a `delivery` from the vocab. The runner maps
  it to `{speed, emotion reference clip}` from `voice.yaml`.
- **Emotion mining** (no reference clips needed up front): render a neutral
  line many times per delivery tag with high variability / an emotion prompt;
  Claude or the human picks the best; the pick becomes the reference clip for
  that tag. Re-mine if a tag keeps scoring badly.

## Text normalisation (per engine, tested once, stored in the runner)
- Stutters `H-h-hello`: engines differ; some read the hyphens as pauses, some
  as letters. Rule per engine: keep as written / collapse to `H- hello` /
  collapse to `hello` and let the emotion clip carry it.
- Cut-offs `I just—`: keep the dash; if the engine runs on, trim trailing
  silence and apply a short fade.
- Interjections / sighs: written in the text only when they should be heard;
  otherwise they belong in `delivery: sigh`.
- Pronunciation dictionary (`voice.yaml: pronunciation`) is substituted
  before synthesis.

## Scoring (best of N)
For each line render N (default 4) takes and score:
1. **Word error rate**: Whisper transcript vs. script, after normalising both
   (drop stutter hyphens, dashes, punctuation, case; map numerals).
2. **Speaker similarity**: cosine similarity of a speaker embedding (ECAPA /
   Resemblyzer) between the take and the character's canon clip.
3. **Duration sanity**: seconds per character within a per-character band.
4. **Audio sanity**: no clipping, no long internal silence, no truncation.
Weighted sum; keep the best; flag the line if the best is under threshold so
the human (or a re-render with another seed/take count) can handle it.

## Hardware
TTS, Whisper and the scorer run on the 3070 (8 GB) while the 5080 does
images; the fine-tune jobs take whichever card is free overnight.
