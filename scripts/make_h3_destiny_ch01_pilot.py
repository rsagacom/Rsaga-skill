#!/usr/bin/env python3
"""Build the first 5-second 3DCG pilot for Destiny Model chapter 1.

This is intentionally a single-shot T2VA workflow.  It tests the selected
production base on the actual novel opening before spending time on a 15-second
Motion Context chain.  It does not require a first frame.
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

PROMPT = """integrated_multimodal_description: [Destiny Model chapter 1 opening] Premium Chinese 3D CG animation, stylized-realistic cinematic game cutscene, physically based materials, restrained facial topology and warm late-afternoon global illumination. Qiyuan Siyuan, a Chinese man aged twenty-five, slim build, short black hair with a natural fringe, thin metal glasses, tired eyes, beige casual jacket over a deep blue V-neck shirt, dark trousers and an old black backpack, stands at the edge of a condemned urban district beside an abandoned stone bridge. Broken brick, exposed rebar, dry weeds and dust fill the scene. In the deep shadow beneath the bridge, a woman in a dark trench coat is seen only from behind; she takes one step into the shadow and disappears without a cut. Qiyuan remains the same person and turns his head slightly toward the empty shadow. Keep the camera in a stable wide-to-medium establishing move, with one continuous shot and readable face geometry at the end. Blue-green circuit traces and ancient talisman markings begin to glow on the bridge wall, casting a soft reflection in his glasses. He whispers in Mandarin: “刚才那里，明明有人。” Natural restrained mouth motion, quiet realistic timing, no subtitles.

Preserve the same face, eye spacing, nose bridge, lips, jawline, glasses, fringe, beige jacket, blue shirt, backpack, bridge geometry, shadow direction and light direction across every frame. No live action, no flat 2D anime, no cuts, no text, no watermark, no duplicate person, no extra limbs, no melted face, no asymmetrical eyes, no warped glasses, no deformed hands, no flickering architecture, no sudden zoom.

overall_soundscape: dry weeds in wind, distant demolition machinery, low city hum, a faint footstep under the bridge, restrained electrical resonance as the markings activate.

non_diegetic_music: N/A"""


def workflow(width: int, height: int, steps: int, seed: int) -> dict[str, dict[str, object]]:
    # Provider 模式用 ASSET_ID 隔离每次输出；ComfyUI 直接导入时也只是普通
    # filename_prefix 模板，项目的 provider 会在提交前替换它。
    prefix = f"h3_destiny_ch01_pilot_{width}x{height}_{steps}steps_{{{{ASSET_ID}}}}"
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {"model": ["1", 0], "lora_name": LORA, "strength": 1.0, "low_vram": False}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPT, "width": width, "height": height, "length": 124, "task_type": "T2VA", "audio_mode": "native", "add_source_as_reference": False}},
        "7": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["10", 0], "sampler": ["7", 0], "sigmas": ["8", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24.0, "bit_depth": 8}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260817051)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(workflow(args.width, args.height, args.steps, args.seed), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
