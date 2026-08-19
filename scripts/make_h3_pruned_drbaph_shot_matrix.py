#!/usr/bin/env python3
"""Create valid pruned-INT8 + drbaph raw-key LoRA shot-test workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors"

SHOTS = {
    "wide": "Begin in a stable wide establishing shot. The character is full-body and relatively small inside the abandoned stone bridge at rust-red sunset. Lock the camera for the first beat, then make only a very slow gentle push in.",
    "medium": "Hold a stable medium-long shot from knees to head. The same character takes one restrained step toward the luminous bridge wall. Keep the lens axis, horizon, bridge geometry and backpack unchanged.",
    "close": "Hold a controlled three-quarter close-up from chest to head. The same character turns only slightly toward the blue-green markings. Keep the eye shape, nose, lips, jawline, hair silhouette and costume stable.",
    "face": "Hold a stable facial close-up with shoulders partly visible. The same character makes one subtle breath and a small eye movement toward the glowing wall. Preserve exact eye shape, nose, lips, jawline and skin or CG facial topology; no face melting.",
}

STYLE = "Premium Chinese 3D CG animation, stylized-realistic character design, clean facial geometry, physically based materials, cinematic global illumination, not live action and not flat 2D anime."


def prompt(shot: str) -> str:
    return f"""integrated_multimodal_description: [Shot 1] {STYLE} Qiyuan Siyuan, a Chinese man in his early thirties with short black hair, charcoal-gray jacket over a pale shirt, dark trousers and a worn black backpack, stands inside an abandoned stone bridge in a condemned old urban district at sunset. Through the bridge opening are broken bricks, exposed rebar, dry weeds and rust-red evening light. Dense organic markings combine circuit-board traces and ancient talisman patterns on the bridge wall; they emit a faint blue-green pulse and cast soft light across the same face and costume. {SHOTS[shot]} Keep one continuous shot, the same character identity, hair, clothing, bridge geometry, light direction, eye shape, nose, mouth and facial proportions. Preserve anatomy, hands and facial structure across all frames. No cuts, no text, no watermark, no duplicate character, no extra limbs, no melted architecture, no flickering markings, no face melting, no sudden zoom.\n\noverall_soundscape: distant demolition machinery, dry weeds moving in the wind, low city hum, faint electrical resonance and a restrained heartbeat-like pulse.\n\nnon_diegetic_music: N/A"""


def workflow(shot: str, width: int, height: int, steps: int, seed: int) -> dict[str, dict[str, object]]:
    prefix = f"h3_pruned_drbaph_{shot}_{width}x{height}_{steps}steps"
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {"model": ["1", 0], "lora_name": LORA, "strength": 1.0, "low_vram": False}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt(shot), "width": width, "height": height, "length": 124, "task_type": "T2VA", "audio_mode": "native", "add_source_as_reference": False}},
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--steps", type=int, default=4)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for index, shot in enumerate(SHOTS, start=1):
        path = args.output / f"workflow_pruned_drbaph_{shot}_{args.width}x{args.height}_{args.steps}steps.json"
        path.write_text(json.dumps(workflow(shot, args.width, args.height, args.steps, 20260817100 + index), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
