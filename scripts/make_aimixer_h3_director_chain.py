#!/usr/bin/env python3
"""Create a small reproducible AIMixer MiniMax H3 Director continuity test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
OUT = ROOT / "test-runs/2026-08-13-h3-director-aimixer"

P1 = """integrated_multimodal_description: [Shot 1] Live-action photorealistic cinematic drama, a young Chinese woman in her early thirties with shoulder-length black hair, a dark waterproof field coat and a small canvas satchel, walking alone through a rain-soaked old city lane at blue hour. Wet stone pavement reflects warm window light, fine rain and distant steam create depth. The camera makes one slow stable dolly forward from waist height, then gently reveals a warm doorway at the end of the lane. One continuous shot, stable face, hair, coat and body proportions, no cuts, no duplicate person, no extra limbs, no deformed hands.

overall_soundscape: soft rain on stone, distant footsteps, a low city hum and a quiet breath.

non_diegetic_music: N/A"""

P2 = """integrated_multimodal_description: Continue the exact same continuous shot from the previous segment. Begin with the same young Chinese woman, same face, wet black hair, dark waterproof field coat and canvas satchel, still in the same rain-soaked old city lane and facing the same warm doorway; hold the previous ending composition for a brief natural beat while she shifts her weight and takes one quiet breath. Then she walks two steps toward the doorway and turns her head toward the light as the camera makes a slow, gentle side reveal. Preserve the same street geometry, wet reflections, rain direction, wardrobe and body proportions. No cut, no new person, no sudden location change, no duplicate person, no extra limbs, no deformed hands.

overall_soundscape: continue the same rain on stone, footsteps, low city hum and breath without a hard audio cut; the doorway gives a soft wooden creak as she approaches.

non_diegetic_music: N/A"""


def timeline() -> str:
    data = {
        "version": 4,
        "editMode": "segment",
        "timelineMode": "prompt_batch",
        "totalFrames": 248,
        "frameRate": 24.0,
        "width": 832,
        "height": 480,
        "refMaxSize": 832,
        "output": {
            "mode": "fixed",
            "longEdge": 832,
            "width": 832,
            "height": 480,
            "maxExportFrames": 0,
            "exportMode": "all",
            "audioMode": "generate",
            "continuityEnabled": True,
            "continuityOverlapFrames": 22,
        },
        "videoClips": [],
        "video": {"fileName": "", "videoFile": "", "subfolder": "", "type": "input", "frames": [], "frameMap": []},
        "global": {"taskType": "t2v — 文生视频(Text to Video)", "prompt": "", "refs": [], "referenceVideo": {}, "continuousReference": False, "genImage": {"imageFile": ""}},
        "segments": [
            {"id": "s0", "start": 0, "length": 124, "frameCount": 124, "durationSec": 5, "prompt": P1, "taskType": "", "refs": [], "referenceVideo": {}, "genImage": {"imageFile": ""}, "negativePrompt": ""},
            {"id": "s1", "start": 124, "length": 124, "frameCount": 124, "durationSec": 5, "prompt": P2, "taskType": "", "refs": [], "referenceVideo": {}, "genImage": {"imageFile": ""}, "negativePrompt": ""},
        ],
        "gen": {"defaultFrameCount": 124},
        "runSelectEnabled": False,
        "runSelection": [],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def workflow() -> dict[str, dict[str, object]]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int4_convrot.safetensors", "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "PathchSageAttentionKJ", "inputs": {"model": ["1", 0], "sage_attention": "auto", "allow_compile": False}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["5", 0]}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["6", 0], "lora_name": "minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors", "strength_model": 1.0}},
        "8": {"class_type": "MiniMaxH3Director", "inputs": {"model": ["7", 0], "video_vae": ["3", 0], "audio_vae": ["4", 0], "clip": ["2", 0], "task_type": "t2v — 文生视频(Text to Video)", "global_prompt": "", "bd_grp_sample": "采样设置", "cfg": 1.0, "seed": 20260813, "frame_rate": 24.0, "width": 832, "height": 480, "ref_max_size": 832, "total_frames": 248, "timeline_data": timeline(), "bd_grp_advanced": "高级采样", "steps": 8, "sampler": "res_multistep", "scheduler": "simple", "shift_video": 12.0, "shift_audio": 3.0, "bd_grp_perf": "性能", "clear_vram_between_segments": True, "export_source_images": False}},
        "9": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "audio": ["8", 1], "fps": ["8", 2], "bit_depth": 8}},
        "10": {"class_type": "SaveVideo", "inputs": {"video": ["9", 0], "filename_prefix": "h3_aimixer_director_t8_2segments_832x480", "format": "mp4", "codec": "auto"}},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT / "workflow_aimixer_director_t8_2segments_832x480.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(workflow(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
