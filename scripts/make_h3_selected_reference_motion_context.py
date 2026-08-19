#!/usr/bin/env python3
"""Build a selected-base H3 reference-to-video + Motion Context chain.

The first clip uses the four-image 3DCG identity pack. Later clips use only
the previous AV latent through Motion Context; this deliberately tests whether
latent continuity can keep the identity without repeatedly exposing the
reference sheet to the model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors"
WIDTH, HEIGHT, FPS, LENGTH = 640, 384, 24.0, 124
DEFAULT_CONTEXT_DIR = "h3_reference_motion_context_selected_15s_640x384"

PROMPT_PREFIX = """subject_definitions:
<Picture 1> is the front 3D CG reference of one adult Chinese woman, authoritative for face, proportions and vermilion-red gold-embroidered hanfu.
<Picture 2> is the strict side-profile reference of the same woman, confirming profile, hair silhouette and scabbard placement.
<Picture 3> is the back-view reference of the same woman, confirming tied hair, jade hairpin, robe silhouette, sash and scabbard placement.
<Picture 4> is the high-detail face close-up reference of the same woman, authoritative for eye spacing, nose bridge, lips, jawline, skin shading and hairline.
<Subject 1> is one single adult 3D CG Chinese woman defined by the identity sources. Never render the reference panels or studio background.

identity_lock:
Keep one refined oval face, almond-shaped dark eyes, black half-up hair, green jade hairpin, vermilion-red embroidered hanfu, ivory collar, dark teal sash and black lacquer sword scabbard. Do not convert to live action or 2D cel shading. Later clips must inherit the previous clip through Motion Context rather than recreating a reference sheet.

negative_constraints:
No reference-sheet panels, no studio backdrop, no readable text, no subtitles, no captions, no logo, no watermark, no face replacement, no age change, no hairstyle change, no costume change, no duplicate person, no extra limbs, no facial melting, no warped eyes, no asymmetrical mouth, no plastic mask, no hard cut.

summary:
High-end cinematic Chinese 3D CG game render on one old stone bridge at warm sunset. Keep physically based materials, restrained mountain mist, warm lanterns and distant mountains in one stable spatial layout. This is a silent visual identity and continuity test: natural breathing and small controlled head or hand movement only, no speech and no exaggerated mouth motion.

overall_soundscape:
Soft continuous wind through mountain pines, distant bronze bell resonance, faint lantern-chain movement and restrained cloth rustle. No voice, no music, no readable text.
"""

SHOT_DIRECTIONS = [
    "Begin with a stable wide-to-medium-long shot that keeps the full woman readable on the bridge. Hold the lens axis, then make only a very slow push in.",
    "Continue from the exact previous ending through Motion Context. Keep the same bridge, light, face and costume for a beat, then let her take one restrained step toward the lantern and turn slightly.",
    "Continue without a cut through Motion Context into a stable medium shot. Keep the eyes, nose bridge, lips and jade hairpin readable while she makes one small natural breath and looks toward the lantern.",
    "Continue without a cut through Motion Context into a controlled three-quarter medium close-up and slight profile. Hold the same face geometry, hair, robe, sash and scabbard through the final frame.",
]


def prompt_for(index: int) -> str:
    return PROMPT_PREFIX + "\nshot_direction:\n" + SHOT_DIRECTIONS[index]


def make_workflow(index: int, steps: int, context_dir: str) -> dict[str, dict[str, object]]:
    prefix = f"h3_selected_reference_motion_context_15s_seg{index + 1}_{WIDTH}x{HEIGHT}_{steps}steps"
    nodes: dict[str, dict[str, object]] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {"model": ["1", 0], "lora_name": LORA, "strength": 1.0, "low_vram": False}},
        "8": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt_for(index), "width": WIDTH, "height": HEIGHT, "length": LENGTH, "ref_image_size": "match"}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": 20260817061 + index}},
        "12": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["8", 0]}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["12", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["8", 1]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["13", 0], "vae": ["4", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {"images": ["14", 0], "audio": ["15", 0], "fps": FPS, "bit_depth": 8}},
        "17": {"class_type": "SaveVideo", "inputs": {"video": ["16", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
        "19": {"class_type": "MiniMaxH3MotionContextSaveLatent", "inputs": {"latent": ["13", 0], "filename_prefix": f"{context_dir}/clip", "clip_index": index + 1}},
    }
    if index == 0:
        for node_id, image in (("21", "h3_3dcg_character_front.png"), ("22", "h3_3dcg_character_side.png"), ("23", "h3_3dcg_character_back.png"), ("24", "h3_3dcg_character_face_closeup.png")):
            nodes[node_id] = {"class_type": "LoadImage", "inputs": {"image": image}}
        for slot, node_id in enumerate(("21", "22", "23", "24")):
            nodes["8"]["inputs"][f"ref_images.ref_image_{slot}"] = [node_id, 0]
    else:
        nodes["18"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {"conditioning": ["8", 0], "vae": ["3", 0], "latent": ["8", 1], "context_length": "22", "audio_context_length": 24, "context_latent": ["25", 0]}}
        nodes["25"] = {"class_type": "MiniMaxH3MotionContextLoadLatent", "inputs": {"latent_path": context_dir, "clip_index": index}}
        nodes["12"]["inputs"]["conditioning"] = ["18", 0]
        nodes["20"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {"images": ["14", 0], "audio": ["15", 0], "trim_frames": ["18", 1], "fps": FPS, "match_tail": True}}
        nodes["16"]["inputs"]["images"] = ["20", 0]
        nodes["16"]["inputs"]["audio"] = ["20", 1]
    return nodes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--segments", type=int, default=4)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--context-dir", default=DEFAULT_CONTEXT_DIR)
    args = parser.parse_args()
    if not 1 <= args.segments <= len(SHOT_DIRECTIONS):
        parser.error(f"segments must be between 1 and {len(SHOT_DIRECTIONS)}")
    args.output.mkdir(parents=True, exist_ok=True)
    for index in range(args.segments):
        path = args.output / f"workflow_selected_reference_motion_context_15s_seg{index + 1}_{WIDTH}x{HEIGHT}_{args.steps}steps.json"
        path.write_text(json.dumps(make_workflow(index, args.steps, args.context_dir), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
