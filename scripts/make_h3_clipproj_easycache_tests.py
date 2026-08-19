#!/usr/bin/env python3
"""Build controlled MiniMax H3 ClipProj/EasyCache A/B workflows.

The three cases keep the selected pruned INT8 H3 base, LoRA, prompt, seed,
resolution and native audio fixed.  Only the text encoder/projection and the
optional native EasyCache wrapper vary.  The output is ComfyUI API-format
JSON, ready for the isolated 8192 service.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
FULL_CLIP = "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
SMALL_CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
CLIP_CONFIGS = {
    "4b": (SMALL_CLIP, "krea2"),
    "8b-nvfp4": ("qwen3vl_8b_nvfp4.safetensors", "boogu"),
    "8b-fp8": ("qwen3vl_8b_fp8_scaled.safetensors", "boogu"),
}
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors"
MATRIX = "mmh3-4b-ClipProj-v3.1-mlp.safetensors"

PROMPT = """integrated_multimodal_description: [ClipProj EasyCache controlled gate] Premium Chinese 3D CG animation, stylized-realistic cinematic game cutscene, physically based materials and stable facial topology. Qiyuan Siyuan, a Chinese man aged twenty-five, slim build, short black hair with a natural fringe, thin metal glasses, tired eyes, beige casual jacket over a deep blue V-neck shirt, dark trousers and an old black backpack, stands beside an abandoned stone bridge in a condemned urban district. Broken brick, exposed rebar, dry weeds and dust fill the scene. The camera makes a restrained wide-to-medium move while he turns his head slightly toward a blue-green circuit trace glowing on the bridge wall. He whispers in Mandarin: “刚才那里，明明有人。” Natural restrained mouth motion, quiet realistic timing, no subtitles.

Preserve the same face, eye spacing, nose bridge, lips, jawline, glasses, fringe, beige jacket, blue shirt, backpack, bridge geometry, shadow direction and light direction across every frame. One continuous shot, no cuts, no text, no watermark, no duplicate person, no extra limbs, no melted face, no asymmetrical eyes, no warped glasses, no deformed hands, no flickering architecture, no sudden zoom.

overall_soundscape: dry weeds in wind, distant demolition machinery, low city hum and a restrained electrical resonance.

non_diegetic_music: N/A"""


def workflow(
    case: str,
    *,
    matrix: str = MATRIX,
    clip: str = "4b",
) -> dict[str, dict[str, object]]:
    is_clipproj = clip != "full"
    use_cache = "easycache" in case
    steps = 20 if use_cache else 8
    if is_clipproj:
        try:
            clip_name, clip_type = CLIP_CONFIGS[clip]
        except KeyError as exc:
            raise ValueError(f"unsupported ClipProj encoder: {clip}") from exc
    else:
        clip_name, clip_type = FULL_CLIP, "minimax"
    prefix = f"h3_clipproj_gate_{clip}_{case}_640x384_{steps}steps_{{{{ASSET_ID}}}}"

    nodes: dict[str, dict[str, object]] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip_name, "type": clip_type, "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {"model": ["1", 0], "lora_name": LORA, "strength": 1.0, "low_vram": False}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPT, "width": 640, "height": 384, "length": 124, "task_type": "T2VA", "audio_mode": "native", "add_source_as_reference": False}},
        "7": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": 20260818001}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["10", 0], "sampler": ["7", 0], "sigmas": ["8", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24.0, "bit_depth": 8}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
    }

    if is_clipproj:
        nodes["16"] = {"class_type": "ClipProjApply", "inputs": {"clip": ["2", 0], "projection": matrix}}
        nodes["6"]["inputs"]["clip"] = ["16", 0]

    if use_cache:
        threshold = 0.20 if "t020" in case else 0.05
        nodes["17"] = {"class_type": "EasyCache", "inputs": {"model": ["5", 0], "reuse_threshold": threshold, "start_percent": 0.15, "end_percent": 0.80, "verbose": True}}
        nodes["8"]["inputs"]["model"] = ["17", 0]
        nodes["10"]["inputs"]["model"] = ["17", 0]
    return nodes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--matrix", default=MATRIX)
    parser.add_argument("--clip", choices=("full", *CLIP_CONFIGS), default="4b")
    parser.add_argument(
        "--cases",
        default="fullclip_8steps,clipproj_4b_8steps,clipproj_4b_20steps_easycache,clipproj_4b_20steps_easycache_t020",
        help="comma-separated case names; 'easycache' in a name selects 20 steps",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = tuple(x.strip() for x in args.cases.split(",") if x.strip())
    for case in cases:
        path = args.output_dir / f"workflow_{case}.json"
        path.write_text(json.dumps(workflow(case, matrix=args.matrix, clip=args.clip), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
