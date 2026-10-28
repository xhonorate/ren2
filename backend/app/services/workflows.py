"""ComfyUI workflow templates.

A workflow *is* a JSON graph, which is the whole reason ComfyUI is the runner
(doc S4). Templates carry {{placeholder}} tokens; the image and TTS services
inject prompt/seed/LoRA/dimensions per job.

These are stored in settings, so the config page can replace any of them --
which is how you adapt to whatever custom nodes your ComfyUI install has.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

TOKEN = re.compile(r"^\{\{\s*([a-z_0-9]+)\s*\}\}$")
INLINE = re.compile(r"\{\{\s*([a-z_0-9]+)\s*\}\}")


def fill(template: Any, values: dict[str, Any]) -> Any:
    """Substitute {{tokens}} through a JSON structure.

    A string that is exactly one token becomes the raw value (so ints stay
    ints); a token inside a longer string is interpolated as text.
    """
    if isinstance(template, dict):
        return {k: fill(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, values) for v in template]
    if isinstance(template, str):
        whole = TOKEN.match(template.strip())
        if whole:
            return values.get(whole.group(1), "")
        return INLINE.sub(lambda m: str(values.get(m.group(1), "")), template)
    return template


def prune_disabled(workflow: dict[str, Any]) -> dict[str, Any]:
    """Drop nodes whose inputs point at nodes that were removed."""
    wf = copy.deepcopy(workflow)
    present = set(wf)
    changed = True
    while changed:
        changed = False
        for nid in list(wf):
            for value in (wf[nid].get("inputs") or {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    if value[0] not in present:
                        wf.pop(nid, None)
                        present.discard(nid)
                        changed = True
                        break
    return wf


# --------------------------------------------------------------------------
# SDXL text-to-image (Illustrious family)
# --------------------------------------------------------------------------
# NoobAI-XL V-Pred needs euler + cfg 4-5 + ~28 steps and NO karras scheduler
# (artifacts in v-pred mode), which is why sampler/scheduler are parameters.

TXT2IMG = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "{{checkpoint}}"},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "{{lora_name}}",
            "strength_model": "{{lora_weight}}",
            "strength_clip": "{{lora_weight}}",
            "model": ["1", 0],
            "clip": ["1", 1],
        },
    },
    "3": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "{{positive}}", "clip": ["2", 1]},
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "{{negative}}", "clip": ["2", 1]},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": "{{width}}", "height": "{{height}}",
                   "batch_size": "{{batch_size}}"},
    },
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": "{{seed}}",
            "steps": "{{steps}}",
            "cfg": "{{cfg}}",
            "sampler_name": "{{sampler}}",
            "scheduler": "{{scheduler}}",
            "denoise": 1.0,
            "model": ["2", 0],
            "positive": ["3", 0],
            "negative": ["4", 0],
            "latent_image": ["5", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "vn/{{prefix}}",
                                                "images": ["7", 0]}},
}

# Same graph without the LoRA loader, for backgrounds and un-LoRA'd characters.
TXT2IMG_NO_LORA = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{checkpoint}}"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}", "clip": ["1", 1]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}", "clip": ["1", 1]}},
    "5": {"class_type": "EmptyLatentImage",
          "inputs": {"width": "{{width}}", "height": "{{height}}",
                     "batch_size": "{{batch_size}}"}},
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": "{{seed}}", "steps": "{{steps}}", "cfg": "{{cfg}}",
            "sampler_name": "{{sampler}}", "scheduler": "{{scheduler}}", "denoise": 1.0,
            "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["5", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "vn/{{prefix}}", "images": ["7", 0]}},
}

# --------------------------------------------------------------------------
# Image to image: refine a take that is nearly right
# --------------------------------------------------------------------------
# The same sampler as txt2img, started from an existing image instead of from
# noise. `denoise` is the whole control: at 0.2 the composition, pose and
# colours all survive and it is a polish pass; at 0.6 the subject stays and
# most of the detail is rewritten; at 1.0 the source may as well not be there.
# The UI calls the inverse of it "adherence", which is how an author thinks
# about the knob -- the wire format stays `denoise` because that is what
# KSampler calls it.
#
# There is no EmptyLatentImage: the size comes from the source, so a refinement
# always lands at the original's dimensions rather than at whatever the CG
# settings currently say.
#
# The mask branch (11-13) is what makes this a region edit. `SetLatentNoiseMask`
# confines the denoise to the painted area, so everything outside it is carried
# through the sampler untouched -- which is the difference between "fix her
# hand" and "re-roll the picture and hope the hand improves". Unmasked runs
# drop those three nodes and point the sampler straight at the batch node
# (`images.build_img2img_workflow`).
#
# Deliberately `SetLatentNoiseMask` rather than `VAEEncodeForInpaint`: the
# latter blanks the masked region for a true inpainting checkpoint, which
# throws away the very thing a partial-denoise edit is built on -- what is
# already there.
IMG2IMG = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{checkpoint}}"}},
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "{{lora_name}}", "strength_model": "{{lora_weight}}",
            "strength_clip": "{{lora_weight}}", "model": ["1", 0], "clip": ["1", 1],
        },
    },
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}", "clip": ["2", 1]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}", "clip": ["2", 1]}},
    "9": {"class_type": "LoadImage", "inputs": {"image": "{{source_image}}"}},
    "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["1", 2]}},
    # Candidates from one source, the same trade the CG batch makes: the cost
    # is the round trip, not the sampling.
    "14": {"class_type": "RepeatLatentBatch",
           "inputs": {"samples": ["10", 0], "amount": "{{batch_size}}"}},
    # The mask arrives as its own black-and-white PNG rather than as the source
    # image's alpha, because the source is a finished render with no alpha to
    # spare and the author paints the region separately.
    "11": {"class_type": "LoadImage", "inputs": {"image": "{{mask_image}}"}},
    "12": {"class_type": "ImageToMask", "inputs": {"image": ["11", 0], "channel": "red"}},
    "13": {"class_type": "SetLatentNoiseMask",
           "inputs": {"samples": ["14", 0], "mask": ["12", 0]}},
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": "{{seed}}", "steps": "{{steps}}", "cfg": "{{cfg}}",
            "sampler_name": "{{sampler}}", "scheduler": "{{scheduler}}",
            "denoise": "{{denoise}}",
            "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["13", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "vn/{{prefix}}", "images": ["7", 0]}},
}

# Same graph with no LoRA loader -- a CG whose character has none, or a
# background. A LoraLoader with an empty lora_name is a hard ComfyUI error.
IMG2IMG_NO_LORA = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{checkpoint}}"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}", "clip": ["1", 1]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}", "clip": ["1", 1]}},
    "9": {"class_type": "LoadImage", "inputs": {"image": "{{source_image}}"}},
    "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["1", 2]}},
    "14": {"class_type": "RepeatLatentBatch",
           "inputs": {"samples": ["10", 0], "amount": "{{batch_size}}"}},
    "11": {"class_type": "LoadImage", "inputs": {"image": "{{mask_image}}"}},
    "12": {"class_type": "ImageToMask", "inputs": {"image": ["11", 0], "channel": "red"}},
    "13": {"class_type": "SetLatentNoiseMask",
           "inputs": {"samples": ["14", 0], "mask": ["12", 0]}},
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": "{{seed}}", "steps": "{{steps}}", "cfg": "{{cfg}}",
            "sampler_name": "{{sampler}}", "scheduler": "{{scheduler}}",
            "denoise": "{{denoise}}",
            "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["13", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "vn/{{prefix}}", "images": ["7", 0]}},
}


# Sprite: same as txt2img, then background removal to transparent PNG.
# The removal node class differs per install (BiRefNet, rembg, essentials), so
# it is a setting; if the running ComfyUI does not have it, the service falls
# back to the plain workflow and says so.
#
# ComfyUI_essentials' ImageRemoveBackground+ takes a pre-built rembg session
# object rather than removing background in one step, so node 19 builds that
# session (RemBGSession+ by default) and node 20 consumes it. If your install
# uses a different remover that takes a plain image with no session node,
# delete node 19 and change node 20's inputs back to a single image input --
# do this from Config -> workflows, not here.
#
# Getting an actually-transparent PNG out takes two more nodes. The remover
# returns (IMAGE, MASK) where the IMAGE is RGB only -- the cut-out background
# is *black pixels*, not transparency -- and the alpha lives in the separate
# MASK output (1 = subject). SaveImage writes RGBA only if it is handed a
# 4-channel image, so node 23 joins the mask back on as alpha first.
# JoinImageWithAlpha inverts what it is given (alpha = 1.0 - mask), so node 22
# inverts the mask first and the two cancel out. Drop nodes 22/23 and point
# SaveImage back at ["20", 0] if your remover already returns RGBA.
SPRITE_SUFFIX = {
    "19": {
        "class_type": "{{rembg_session_class}}",
        "inputs": {"model": "{{rembg_model}}", "providers": "{{rembg_provider}}"},
    },
    "20": {
        "class_type": "{{rembg_class}}",
        "inputs": {"rembg_session": ["19", 0], "image": ["7", 0]},
    },
    "22": {
        "class_type": "{{rembg_invert_class}}",
        "inputs": {"mask": ["20", 1]},
    },
    "23": {
        "class_type": "{{rembg_join_class}}",
        "inputs": {"image": ["20", 0], "alpha": ["22", 0]},
    },
    "21": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "vn/{{prefix}}", "images": ["23", 0]},
    },
}

# Event CG: a face-detail pass appended to txt2img.
#
# This is the answer to the tension between pose and likeness. A character
# LoRA trained on the sheet has only ever seen front-facing standing shots, so
# running it at sprite strength drags every CG back to that composition; run it
# weak (or not at all) and the pose is free but the face stops being hers.
# FaceDetailer resolves it by re-rendering only the detected face crop, so the
# main pass owns the composition and this pass owns the likeness -- and it
# loads the LoRA on its OWN branch, chained from the checkpoint (node "1")
# rather than the main pass's LoraLoader, which is what lets the two run at
# different weights in one graph.
#
# Needs ComfyUI-Impact-Pack (FaceDetailer + UltralyticsDetectorProvider) and a
# face bbox model in models/ultralytics/bbox/. If either is missing the service
# checks object_info first and falls back to the plain CG, saying so -- same
# contract as the sprite background remover.
CG_FACE_SUFFIX = {
    "30": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "{{face_lora_name}}",
            "strength_model": "{{face_lora_weight}}",
            "strength_clip": "{{face_lora_weight}}",
            "model": ["1", 0],
            "clip": ["1", 1],
        },
    },
    "31": {"class_type": "CLIPTextEncode",
           "inputs": {"text": "{{face_positive}}", "clip": ["30", 1]}},
    "32": {"class_type": "CLIPTextEncode",
           "inputs": {"text": "{{face_negative}}", "clip": ["30", 1]}},
    "33": {"class_type": "{{detector_provider_class}}",
           "inputs": {"model_name": "{{face_detector_model}}"}},
    "34": {
        "class_type": "{{facedetailer_class}}",
        "inputs": {
            "image": ["7", 0],
            "model": ["30", 0],
            "clip": ["30", 1],
            "vae": ["1", 2],
            "positive": ["31", 0],
            "negative": ["32", 0],
            "bbox_detector": ["33", 0],
            "guide_size": 512,
            "guide_size_for": True,
            "max_size": 1024,
            "seed": "{{face_seed}}",
            "steps": "{{steps}}",
            "cfg": "{{cfg}}",
            "sampler_name": "{{sampler}}",
            "scheduler": "{{scheduler}}",
            # The one number worth tuning. Too low and the LoRA never lands;
            # too high and the detailer redraws a face that no longer matches
            # the body's lighting or angle. 0.45 is the working middle.
            "denoise": "{{face_denoise}}",
            "feather": 5,
            "noise_mask": True,
            "force_inpaint": True,
            "bbox_threshold": 0.5,
            "bbox_dilation": 10,
            "bbox_crop_factor": 3.0,
            "sam_detection_hint": "center-1",
            "sam_dilation": 0,
            "sam_threshold": 0.93,
            "sam_bbox_expansion": 0,
            "sam_mask_hint_threshold": 0.7,
            "sam_mask_hint_use_negative": "False",
            "drop_size": 10,
            "wildcard": "",
            "cycle": 1,
        },
    },
    "35": {"class_type": "SaveImage",
           "inputs": {"filename_prefix": "vn/{{prefix}}", "images": ["34", 0]}},
}

# WD14 tagger, for auto-captioning a culled character sheet before LoRA
# training (doc S4, bootstrap loop step 4).
TAGGER = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "{{image}}"}},
    "2": {
        "class_type": "{{tagger_class}}",
        "inputs": {
            "image": ["1", 0],
            "model": "{{tagger_model}}",
            "threshold": 0.35,
            "character_threshold": 0.85,
            "exclude_tags": "",
            # Recent WD14Tagger|pysssss builds mark these required (no defaults),
            # so the workflow 400s without them. Both match the node's own
            # defaults: keep booru underscores (what Illustrious/NoobAI saw in
            # training), and no trailing comma since the handler already builds
            # "<trigger>, <tags>".
            "replace_underscore": False,
            "trailing_comma": False,
        },
    },
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}},
}

# IndexTTS-2.5 through TTS Audio Suite
# (github.com/diodiogod/TTS-Audio-Suite, 5.8.0+). 2.5's weights do not load in
# the older kaola pack -- UnifiedVoice changed shape -- so moving to 2.5 is a
# node-pack switch, not a model_dir change. The suite splits what the old
# wrapper did in one node: an engine node holds the model and sampler settings,
# an emotion node builds the 8-dim vector (doc S5), and the TTS Text node
# synthesises with the timbre clip on `opt_narrator`.
#
# What this project is built on is unchanged: timbre comes from a fixed
# reference clip, emotion arrives separately as the vector, so a character's
# voice does not drift when they get angry. The suite's sliders are capitalised
# and named for feelings (Afraid/Disgusted/Melancholic/...) where our
# EMOTION_DIMS use shorter names, so the tokens are mapped slot by slot here.
#
# Every input the engine marks required is written out, node defaults included:
# a pack that later drops a default would otherwise 400 the whole workflow. The
# three node classes and the model choice are settings, so another wrapper pack
# -- or IndexTTS-2 itself, via the `tts_indextts2` template below -- can be
# swapped in from Config -> workflows.
TTS = {
    "1": {"class_type": "LoadAudio", "inputs": {"audio": "{{reference_clip}}"}},
    "2": {
        "class_type": "{{tts_emotion_class}}",
        "inputs": {
            "Happy": "{{emo_happy}}",
            "Angry": "{{emo_anger}}",
            "Sad": "{{emo_sad}}",
            "Afraid": "{{emo_fear}}",
            "Disgusted": "{{emo_disgust}}",
            "Melancholic": "{{emo_melancholy}}",
            "Surprised": "{{emo_surprise}}",
            "Calm": "{{emo_calm}}",
        },
    },
    "3": {
        "class_type": "{{tts_engine_class}}",
        "inputs": {
            "model_path": "{{tts_model_path}}",
            "device": "auto",
            "language": "English",       # 2.5 is multilingual; the graph is not
            "emotion_control": ["2", 0],
            # 1.0 passes the vector through exactly as resolve_emotion built
            # it. The line's intensity and the character's emotion_scale have
            # already scaled it, which is why the tts_emo_alpha setting is not
            # wired here -- doing so would scale the same thing twice.
            "emotion_alpha": 1.0,
            # 2.5's own pacing control, and the inverse of the emotion map's
            # `speed`: >1.0 SLOWS speech down here. Filled per line from
            # 1 / speed (tts.build_workflow). It stretches the semantic
            # features before the vocoder runs, so the vocoder re-synthesises
            # at the new length -- where an ffmpeg atempo on the finished clip
            # smears it audibly at whisper/crying pace. Changing it does not
            # reload the model. Remove the token and tts.to_ogg falls back to
            # atempo, so a pasted-in template without it still gets paced.
            "duration_factor": "{{duration_factor}}",
            "use_random": "{{tts_use_random}}",
            "use_fp16": False,   # edit to true in Config -> workflows to save ~1 GB VRAM
            "use_deepspeed": False,
            "text_normalization": True,
            "max_text_tokens_per_segment": 120,
            "interval_silence": 200,
            "temperature": 0.8,
            "top_p": 0.8,
            "top_k": 30,
            "do_sample": True,
            "length_penalty": 0.0,
            # One beam, not the pack's 3. Beam search keeps the most likely
            # token sequence, and the most likely read of a line is the
            # flattest one -- the reason lines came back evenly stressed
            # whatever the emotion. One beam samples a livelier read that
            # occasionally slips; a re-take rolls several seeds to pick from.
            "num_beams": 1,
            "repetition_penalty": 10.0,
            "max_mel_tokens": 1500,
        },
    },
    "4": {
        "class_type": "{{tts_text_class}}",
        "inputs": {
            "TTS_engine": ["3", 0],
            "text": "{{text}}",
            # The frozen clip IS the voice, so it goes in directly rather than
            # through the suite's voice folders; `narrator_voice` is the
            # fallback those folders offer and stays off.
            "opt_narrator": ["1", 0],
            "narrator_voice": "none",
            "seed": "{{seed}}",
        },
    },
    "5": {
        "class_type": "SaveAudio",
        "inputs": {"filename_prefix": "vn/voice/{{prefix}}", "audio": ["4", 0]},
    },
}

# IndexTTS-2 through the "IndexTTS-2 Nodes" pack
# (github.com/kana112233/ComfyUI-kaola-IndexTTS2) -- the previous default, kept
# as its own template so going back is a copy-paste: paste this into `tts` in
# Config -> workflows. Its loader reads ComfyUI/models/<tts_model_dir>/ and
# cannot load 2.5 weights (it fails on UnifiedVoice's state_dict).
TTS_INDEXTTS2 = {
    "1": {"class_type": "LoadAudio", "inputs": {"audio": "{{reference_clip}}"}},
    "2": {
        "class_type": "{{tts_model_loader_class}}",
        "inputs": {
            "model_dir": "{{tts_model_dir}}",
            "device": "auto",
            "use_fp16": False,   # edit to true in Config -> workflows to save ~1 GB VRAM
            "use_cuda_kernel": False,
            "use_deepspeed": False,
        },
    },
    "3": {
        "class_type": "{{tts_class}}",
        "inputs": {
            "model": ["2", 0],
            "text": "{{text}}",
            "spk_audio_prompt": ["1", 0],
            "happy": "{{emo_happy}}",
            "angry": "{{emo_anger}}",
            "sad": "{{emo_sad}}",
            "afraid": "{{emo_fear}}",
            "disgusted": "{{emo_disgust}}",
            "melancholic": "{{emo_melancholy}}",
            "surprised": "{{emo_surprise}}",
            "calm": "{{emo_calm}}",
            "temperature": 1.0,
            "top_k": 0,
            "top_p": 1.0,
            "use_random": "{{tts_use_random}}",
        },
    },
    "4": {
        "class_type": "SaveAudio",
        "inputs": {"filename_prefix": "vn/voice/{{prefix}}", "audio": ["3", 0]},
    },
}


DEFAULT_WORKFLOWS: dict[str, Any] = {
    "txt2img": TXT2IMG,
    "txt2img_no_lora": TXT2IMG_NO_LORA,
    "img2img": IMG2IMG,
    "img2img_no_lora": IMG2IMG_NO_LORA,
    "sprite_suffix": SPRITE_SUFFIX,
    "cg_face_suffix": CG_FACE_SUFFIX,
    "tagger": TAGGER,
    "tts": TTS,
    "tts_indextts2": TTS_INDEXTTS2,
}

DEFAULT_NODE_CLASSES: dict[str, str] = {
    "rembg_class": "ImageRemoveBackground+",
    "rembg_session_class": "RemBGSession+",
    "rembg_invert_class": "InvertMask",
    "rembg_join_class": "JoinImageWithAlpha",
    "rembg_model": "isnet-anime: anime illustrations",
    "rembg_provider": "CPU",
    "tagger_class": "WD14Tagger|pysssss",
    "tagger_model": "wd-v1-4-moat-tagger-v2",
    # ComfyUI-Impact-Pack. face_yolov8m is the usual anime face bbox model and
    # lives in ComfyUI/models/ultralytics/bbox/.
    "facedetailer_class": "FaceDetailer",
    "detector_provider_class": "UltralyticsDetectorProvider",
    "face_detector_model": "bbox/face_yolov8m.pt",
    # IndexTTS-2.5 through TTS Audio Suite -- the `tts` template.
    "tts_engine_class": "IndexTTSEngineNode",
    "tts_emotion_class": "IndexTTSEmotionOptionsNode",
    "tts_text_class": "UnifiedTTSTextNode",
    "tts_model_path": "IndexTTS-2.5",
    # IndexTTS-2 through ComfyUI-kaola-IndexTTS2 -- only the `tts_indextts2`
    # template reads these. They stay so reverting is a paste, not an edit.
    "tts_model_loader_class": "IndexTTS2ModelLoader",
    "tts_class": "IndexTTS2EmotionVector",
    "tts_model_dir": "IndexTTS-2",
}


def as_json(workflow: dict[str, Any]) -> str:
    return json.dumps(workflow, indent=2)
