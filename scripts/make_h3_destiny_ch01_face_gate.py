#!/usr/bin/env python3
"""Build a first-chapter front-face dialogue gate for the selected H3 base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROMPT = """integrated_multimodal_description: [Destiny Model chapter 1, front-face dialogue gate] Premium Chinese 3D CG animation, stylized-realistic cinematic game cutscene, physically based materials and warm late-afternoon global illumination. Qiyuan Siyuan, a Chinese man aged twenty-five, slim build, short black hair with a natural fringe, thin metal glasses, tired but intelligent eyes, beige casual jacket over a deep blue V-neck shirt, dark trousers and an old black backpack. Hold a stable frontal medium close-up from upper chest to just above the head, with the abandoned stone bridge and its dark arch softly visible behind him. A faint blue-green circuit and talisman glow from the wall should illuminate the same face without changing the light direction. He looks toward the camera axis, takes one small breath, then says in Mandarin with restrained natural lip motion: “刚才那里，明明有人。” Keep the frontal eye spacing, glasses shape, nose bridge, lips, jawline, hair fringe, skin or CG facial topology and clothing stable in every frame. One continuous locked shot with only a subtle head turn of a few degrees; no zoom, no cut, no second person, no face replacement, no asymmetrical eyes, no warped glasses, no melted face, no extra limbs, no text, no subtitles, no watermark. overall_soundscape: dry weeds, distant demolition machinery, low city hum, one restrained footstep and faint electrical resonance. non_diegetic_music: N/A"""


def make_workflow(width: int, height: int, steps: int, seed: int) -> dict[str, dict[str, object]]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int4_convrot.safetensors", "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors", "strength": 1.0, "low_vram": False}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPT, "width": width, "height": height, "length": 124, "task_type": "T2VA", "audio_mode": "native", "add_source_as_reference": False}},
        "7": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["10", 0], "sampler": ["7", 0], "sigmas": ["8", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24.0, "bit_depth": 8}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": f"h3_destiny_ch01_face_gate_{width}x{height}_{steps}steps", "format": "mp4", "codec": "auto"}},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(make_workflow(args.width, args.height, args.steps, 20260817061), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
