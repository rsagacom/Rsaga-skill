#!/usr/bin/env python3
"""Write the reproducible two-clip H3 T8 + Motion Context API workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
OUT = ROOT / "test-runs/2026-08-13-h3-motion-context"

PROMPT_1 = """integrated_multimodal_description: [Shot 1] Live-action photorealistic cinematic drama, a young Chinese woman in her early thirties with shoulder-length black hair, a dark waterproof field coat and a small canvas satchel, walking alone through a rain-soaked old city lane at blue hour. Wet stone pavement reflects warm window light, fine rain and distant steam create depth. The camera makes one slow stable dolly forward from waist height, then gently reveals a warm doorway at the end of the lane. One continuous shot, stable face, hair, coat and body proportions, no cuts, no duplicate person, no extra limbs, no deformed hands.

overall_soundscape: soft rain on stone, distant footsteps, a low city hum and a quiet breath.

non_diegetic_music: N/A"""

PROMPT_2 = """integrated_multimodal_description: Continue the exact same continuous shot from the previous clip. Begin with the same young Chinese woman, same face, wet black hair, dark waterproof field coat and canvas satchel, still in the same rain-soaked old city lane and facing the same warm doorway; hold the previous ending composition for a brief natural beat while she shifts her weight and takes one quiet breath. Then she walks two steps toward the doorway and turns her head toward the light as the camera makes a slow, gentle side reveal. Preserve the same street geometry, wet reflections, rain direction, wardrobe and body proportions. No cut, no new person, no sudden location change, no duplicate person, no extra limbs, no deformed hands.

overall_soundscape: continue the same rain on stone, footsteps, low city hum and breath without a hard audio cut; the doorway gives a soft wooden creak as she approaches.

non_diegetic_music: N/A"""


def base_nodes(prompt: str, prefix: str, seed: int) -> dict[str, dict[str, object]]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int4_convrot.safetensors", "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "LoraLoaderBypassModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors", "strength_model": 1.0}},
        "6": {"class_type": "MiniMaxH3AudioConditioningT8", "inputs": {"clip": ["2", 0], "video_vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt, "width": 832, "height": 480, "length": 124, "task_type": "T2VA", "audio_mode": "native", "audio_denoise_strength": 1.0, "add_source_as_reference": False, "prompt_primary_audio_ordinal": 0, "strict_prompt_tags": True, "ref_image_size": "match", "reference_video_policy": "official_2_to_15s"}},
        "7": {"class_type": "MiniMaxH3DualClockSamplerT8", "inputs": {"model": ["5", 0], "av_latent": ["6", 1], "steps": 8, "shift_video": 12.0, "shift_audio": 3.0, "sampler_name": "dual_clock_euler", "scheduler": "native_flow"}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "9": {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": ["6", 0]}},
        "10": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["9", 0], "sampler": ["7", 1], "sigmas": ["7", 2], "latent_image": ["6", 1]}},
        "11": {"class_type": "MiniMaxH3AVDecodeT8", "inputs": {"av_latent": ["10", 0], "video_vae": ["3", 0], "audio_vae": ["4", 0]}},
        "12": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "audio": ["11", 1], "fps": 24.0, "bit_depth": 8}},
        "13": {"class_type": "SaveVideo", "inputs": {"video": ["12", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
    }


def make_clip1() -> dict[str, dict[str, object]]:
    nodes = base_nodes(PROMPT_1, "h3_motion_context_t8_clip1_832x480_5s", 20260813)
    nodes["14"] = {"class_type": "MiniMaxH3MotionContextSaveLatent", "inputs": {"latent": ["10", 0], "filename_prefix": "h3_context_t8/clip", "clip_index": 1}}
    return nodes


def make_clip2() -> dict[str, dict[str, object]]:
    nodes = base_nodes(PROMPT_2, "h3_motion_context_t8_clip2_trimmed_832x480_5s", 20260814)
    nodes["14"] = {"class_type": "MiniMaxH3MotionContextLoadLatent", "inputs": {"latent_path": "h3_context_t8", "clip_index": 1}}
    nodes["15"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {"conditioning": ["6", 0], "vae": ["3", 0], "latent": ["6", 1], "context_length": "22", "audio_context_length": 24, "context_latent": ["14", 0]}}
    nodes["9"]["inputs"]["conditioning"] = ["15", 0]
    nodes["16"] = {"class_type": "MiniMaxH3MotionContextSaveLatent", "inputs": {"latent": ["10", 0], "filename_prefix": "h3_context_t8/clip", "clip_index": 2}}
    nodes["17"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {"images": ["11", 0], "trim_frames": ["15", 1], "audio": ["11", 1], "fps": 24.0, "match_tail": True}}
    nodes["12"]["inputs"]["images"] = ["17", 0]
    nodes["12"]["inputs"]["audio"] = ["17", 1]
    return nodes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT / "workflows")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    files = {
        "workflow_clip1_t8_lora_832x480_5s.json": make_clip1(),
        "workflow_clip2_t8_lora_motion_context_832x480_5s.json": make_clip2(),
    }
    for name, workflow in files.items():
        path = args.output / name
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
