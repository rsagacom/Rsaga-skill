#!/usr/bin/env python3
"""Create pruned-INT8 standalone Motion Context chains for non-3DCG style tests.

The first segment is native H3 T2VA (no reference image). Later segments load
the previous AV latent through Motion Context. This intentionally keeps style
conversion separate from the 3DCG three-view reference test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
DEFAULT_OUT = ROOT / "test-runs/2026-08-17-h3-pruned-style-long/workflows"
MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
WIDTH, HEIGHT, FPS, LENGTH = 640, 384, 24.0, 124

STYLE_PREFIX = {
    "realistic": (
        "cinematic live-action realism, natural human skin and fabric, "
        "grounded practical lighting, not animation"
    ),
    "ink": (
        "Chinese ink-wash CG animation, controlled ink edges, layered paper "
        "texture, restrained watercolor shading, coherent stylized face, "
        "not photorealistic"
    ),
    "anime2d": (
        "high-quality 2D anime/cel animation, clean line art, stable cel "
        "shading, coherent eyes and hair silhouette, not 3D and not live action"
    ),
}

SHOT_DIRECTIONS = [
    "Begin in a stable wide establishing shot: the character is full-body and relatively small inside the abandoned stone bridge at rust-red sunset. Lock the camera for the first beat, then make only a very slow gentle push in.",
    "Continue from the exact previous ending. Move to a medium-long shot as the same character takes one restrained step toward the luminous bridge wall. Keep the same lens axis, horizon and bridge geometry.",
    "Continue without a cut into a medium shot. Ease closer until the upper torso and face are readable. The character breathes naturally and turns only slightly toward the wall. Preserve facial geometry and costume.",
    "Continue into a controlled three-quarter close-up and slight profile. Turn only a few degrees toward the blue-green markings. Keep the same eye shape, nose, lips and jawline.",
    "Continue by easing back to a medium-long shot, revealing the same bridge opening and broken masonry. Preserve the charcoal jacket, pale shirt and worn black backpack.",
    "Continue in a stable medium shot matching the previous motion. The character settles and takes one small natural breath. No face replacement or sudden background change.",
    "Finish in a stable medium close-up matching the previous motion. Hold the face readable through the final frame and complete the quiet line.",
]


def base_prompt(style: str) -> str:
    return f"""integrated_multimodal_description: [Shot 1] {STYLE_PREFIX[style]}. a stable medium shot from the waist up, character and luminous bridge wall clearly visible. Qiyuan Siyuan, a Chinese man in his early thirties, short black hair, charcoal-gray jacket over a pale shirt, dark trousers and a worn black backpack, stands inside an abandoned stone bridge in a condemned old urban district at sunset. Through the bridge opening are broken bricks, exposed rebar, dry weeds and rust-red evening light. Dense organic markings combine circuit-board traces and ancient talisman patterns on the bridge wall; they emit a faint blue-green pulse and cast soft light across the same face and costume. Keep one continuous shot, the same character identity, hair, clothing, bridge geometry, light direction, eye shape, nose, mouth and facial proportions. Camera motion is slow and stable and the action is only a restrained turn toward the wall. Preserve anatomy, hands and facial structure across all frames. No cuts, no text, no watermark, no duplicate character, no extra limbs, no melted architecture, no flickering markings, no face melting, no sudden zoom.

overall_soundscape: distant demolition machinery, dry weeds moving in the wind, low city hum, faint electrical resonance and a restrained heartbeat-like pulse.

non_diegetic_music: N/A"""


def prompt_for(style: str, index: int) -> str:
    return base_prompt(style) + "\n\nshot_direction:\n" + SHOT_DIRECTIONS[index]


def nodes(style: str, index: int, context_dir: str, width: int, height: int, steps: int) -> dict[str, dict[str, object]]:
    prefix = f"h3_pruned_int8_{style}_motion_context_seg{index + 1}_{width}x{height}_{steps}steps"
    data: dict[str, dict[str, object]] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "PathchSageAttentionKJ", "inputs": {"model": ["1", 0], "sage_attention": "auto", "allow_compile": False}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["5", 0]}},
        "8": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "prompt": prompt_for(style, index),
                "width": width,
                "height": height,
                "length": LENGTH,
                "task_type": "T2VA",
                "audio_mode": "native",
                "add_source_as_reference": False,
            },
        },
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "BasicScheduler", "inputs": {"model": ["6", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": 20260817090 + index}},
        "12": {"class_type": "BasicGuider", "inputs": {"model": ["6", 0], "conditioning": ["18", 0] if index > 0 else ["8", 0]}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["12", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["8", 1]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["13", 0], "vae": ["4", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {"images": ["20", 0] if index > 0 else ["14", 0], "audio": ["20", 1] if index > 0 else ["15", 0], "fps": FPS, "bit_depth": 8}},
        "17": {"class_type": "SaveVideo", "inputs": {"video": ["16", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
        "19": {"class_type": "MiniMaxH3MotionContextSaveLatent", "inputs": {"latent": ["13", 0], "filename_prefix": f"{context_dir}/clip", "clip_index": index + 1}},
    }
    if index > 0:
        data["18"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {"conditioning": ["8", 0], "vae": ["3", 0], "latent": ["8", 1], "context_length": "22", "audio_context_length": 24, "context_latent": ["25", 0]}}
        data["20"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {"images": ["14", 0], "audio": ["15", 0], "trim_frames": ["18", 1], "fps": FPS, "match_tail": True}}
        data["25"] = {"class_type": "MiniMaxH3MotionContextLoadLatent", "inputs": {"latent_path": context_dir, "clip_index": index}}
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", choices=sorted(STYLE_PREFIX), required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--segments", type=int, default=7)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.segments <= len(SHOT_DIRECTIONS):
        raise SystemExit(f"segments must be 1..{len(SHOT_DIRECTIONS)}")
    args.output.mkdir(parents=True, exist_ok=True)
    context_dir = f"h3_context_pruned_int8_{args.style}_motion_{args.width}x{args.height}"
    for index in range(args.segments):
        path = args.output / f"workflow_pruned_int8_{args.style}_motion_context_seg{index + 1}_{args.width}x{args.height}_{args.steps}steps.json"
        path.write_text(json.dumps(nodes(args.style, index, context_dir, args.width, args.height, args.steps), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
