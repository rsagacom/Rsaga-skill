#!/usr/bin/env python3
"""Create reproducible pruned-INT8 + standalone Motion Context workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
DEFAULT_OUT = ROOT / "test-runs/2026-08-17-h3-pruned-long-continuity/motion_workflows"
MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
WIDTH, HEIGHT, FPS, LENGTH = 640, 384, 24.0, 124
CONTEXT_DIR = "h3_context_pruned_int8_motion_640x384"

PROMPT_PREFIX = """subject_definitions:
<Picture 1> is the front 3D CG reference of one Chinese woman, authoritative for face, proportions and costume.
<Picture 2> is the strict side-profile 3D CG reference of that same woman.
<Picture 3> is the back-view 3D CG reference of that same woman.
<Subject 1> is one single adult 3D CG Chinese woman defined by these pictures. Never render the reference panels or their studio background.

summary:
High-end cinematic 3D CG Chinese fantasy game render, one continuous shot in a misty Jiangnan mountain setting at warm sunset. Preserve one single character across every distance: refined oval face, almond-shaped dark eyes, black half-up hair, green jade hairpin, vermilion-red embroidered hanfu, ivory collar, dark teal sash and black lacquer sword scabbard. Keep the stone bridge, warm lanterns, volumetric mist and distant mountains in one stable spatial layout.

identity_lock:
The character identity is more important than spectacle. Keep eye spacing, nose bridge, lips, jawline, hair silhouette, jade hairpin, robe embroidery and sash position consistent. Later segments inherit the previous segment through Motion Context latent, not by re-rendering a reference sheet.

spoken_dialogue:
In Mandarin Chinese, the woman speaks exactly this quiet line with clear natural diction: “山门之外，风还记得我吗？今日我终于回来了。” Keep restrained, anatomically plausible mouth motion.

overall_soundscape:
Continuous soft wind through mountain pines, distant bronze bell resonance, faint lantern-chain movement, cloth rustle and restrained breath. Keep the acoustic space continuous across every segment.

negative_constraints:
No live-action conversion, no anime cel shading, no face replacement, no age change, no hairstyle change, no costume change, no duplicate person, no extra limbs, no facial melting, no warped eyes, no asymmetrical mouth, no plastic mask, no reference-sheet panels, no studio backdrop, no text, no logo, no watermark, no scene cut.

non_diegetic_music:
Very low restrained guqin and xiao texture, ducked under the spoken voice, no percussion.
"""

SHOT_DIRECTIONS = [
    "Begin in a stable wide establishing shot: the woman is full-body and relatively small on a misty Jiangnan stone bridge at warm sunset. The camera is locked for the first beat, then makes only a very slow gentle push in.",
    "Continue from the exact previous ending. Move to a medium-long shot as the same woman takes one restrained step along the bridge and turns slightly toward the warm lantern. Keep the same lens axis, horizon and background layout.",
    "Continue without a cut into a medium shot. The camera eases closer until her upper torso and face are readable. She breathes naturally and speaks softly, with only a small head turn. Preserve facial geometry and costume.",
    "Continue into a controlled three-quarter close-up and slight profile. The woman turns only a few degrees toward the lantern. Keep the same rendered materials, eye spacing, nose, lips and jawline.",
    "Continue by easing back to a medium-long shot, revealing the same stone bridge and lantern position. Preserve the red robe, ivory collar, teal sash and black sword scabbard.",
    "Continue in a stable medium shot matching the previous motion. The woman settles and gives one small natural breath. No face replacement or sudden background change.",
    "Finish in a stable medium close-up matching the previous motion. Hold the face readable through the final frame and complete the quiet Mandarin line.",
]


def prompt_for(index: int) -> str:
    refs = "<Picture 1>, <Picture 2> and <Picture 3>"
    return PROMPT_PREFIX.replace("Never render the reference panels or their studio background.", f"The first segment uses {refs} as identity sources. Never render the reference panels or their studio background.") + "\n\nshot_direction:\n" + SHOT_DIRECTIONS[index]


def motion_nodes(index: int) -> dict[str, dict[str, object]]:
    nodes: dict[str, dict[str, object]] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "PathchSageAttentionKJ", "inputs": {"model": ["1", 0], "sage_attention": "auto", "allow_compile": False}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["5", 0]}},
        "8": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt_for(index), "width": WIDTH, "height": HEIGHT, "length": LENGTH, "ref_image_size": "match"}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "BasicScheduler", "inputs": {"model": ["6", 0], "scheduler": "simple", "steps": 8, "denoise": 1.0}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": 20260817081 + index}},
        "12": {"class_type": "BasicGuider", "inputs": {"model": ["6", 0], "conditioning": ["18", 0] if index > 0 else ["8", 0]}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["12", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["8", 1]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["13", 0], "vae": ["4", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {"images": ["20", 0] if index > 0 else ["14", 0], "audio": ["20", 1] if index > 0 else ["15", 0], "fps": FPS, "bit_depth": 8}},
        "17": {"class_type": "SaveVideo", "inputs": {"video": ["16", 0], "filename_prefix": f"h3_pruned_int8_motion_context_seg{index + 1}_640x384", "format": "mp4", "codec": "auto"}},
        "19": {"class_type": "MiniMaxH3MotionContextSaveLatent", "inputs": {"latent": ["13", 0], "filename_prefix": f"{CONTEXT_DIR}/clip", "clip_index": index + 1}},
    }
    if index == 0:
        nodes["8"]["inputs"].update({
            "ref_images.ref_image_0": ["21", 0],
            "ref_images.ref_image_1": ["22", 0],
            "ref_images.ref_image_2": ["23", 0],
        })
        nodes["21"] = {"class_type": "LoadImage", "inputs": {"image": "h3_3dcg_character_front.png"}}
        nodes["22"] = {"class_type": "LoadImage", "inputs": {"image": "h3_3dcg_character_side.png"}}
        nodes["23"] = {"class_type": "LoadImage", "inputs": {"image": "h3_3dcg_character_back.png"}}
    else:
        nodes["18"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {"conditioning": ["8", 0], "vae": ["3", 0], "latent": ["8", 1], "context_length": "22", "audio_context_length": 24, "context_latent": ["25", 0]}}
        nodes["20"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {"images": ["14", 0], "audio": ["15", 0], "trim_frames": ["18", 1], "fps": FPS, "match_tail": True}}
        nodes["25"] = {"class_type": "MiniMaxH3MotionContextLoadLatent", "inputs": {"latent_path": CONTEXT_DIR, "clip_index": index}}
    return nodes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--segments", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.segments <= len(SHOT_DIRECTIONS):
        raise SystemExit(f"segments must be 1..{len(SHOT_DIRECTIONS)}")
    args.output.mkdir(parents=True, exist_ok=True)
    for index in range(args.segments):
        path = args.output / f"workflow_pruned_int8_motion_context_seg{index + 1}_640x384.json"
        path.write_text(json.dumps(motion_nodes(index), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
