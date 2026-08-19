#!/usr/bin/env python3
"""Build a 15-second Motion Context chain for the selected H3 model.

Four native 5.1667-second segments are generated so the final product can be
trimmed to an exact 15 seconds after the latent/audio handoff.  The first
segment is T2VA; later segments receive the previous AV latent through Motion
Context.  The dedicated prune-compatible Turbo LoRA is applied before the
sampler, and the validated 4-step Euler schedule is used consistently.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
WIDTH, HEIGHT, FPS, LENGTH = 640, 384, 24.0, 124
CONTEXT_DIR = "h3_context_pruned_int8_drbaph_motion_15s_640x384"

PROMPTS = [
    """integrated_multimodal_description: [Shot 1] Premium Chinese 3D CG animation, stylized-realistic game cinematic, physically based materials and warm sunset global illumination. Qiyuan Siyuan, a Chinese man in his early thirties with short black hair, charcoal-gray jacket, pale shirt, dark trousers and worn black backpack, stands on an abandoned stone bridge in a condemned urban district. Blue-green circuit and ancient talisman markings glow on the bridge wall. Begin with a stable wide shot, then make only a very slow push in. He says in Mandarin with restrained natural diction: “命运模型启动了。” Preserve the same face, hair, clothing, bridge geometry and light direction. No cuts, no text, no watermark, no duplicate person, no extra limbs, no face melting. overall_soundscape: distant demolition machinery, dry weeds, low city hum and restrained electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: Continue from the exact previous ending through Motion Context. Same Qiyuan Siyuan, same charcoal jacket, pale shirt, backpack, abandoned stone bridge and blue-green markings. Move into a stable medium-long shot as he takes one restrained step toward the wall and turns slightly. Preserve identity, facial proportions, clothing, bridge layout, sunset and acoustic space. He continues in Mandarin: “它记得我，也记得这座城。” Natural restrained mouth motion, no hard audio cut, no scene change, no face replacement, no extra limbs, no flicker, no text or watermark. overall_soundscape: continue the same machinery, wind, city hum and electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: Continue without a cut from the previous latent. Same Qiyuan Siyuan on the same bridge, premium Chinese 3D CG game cinematic, stable facial geometry and materials. Ease into a readable medium shot; he turns only a few degrees toward the luminous markings and says: “但它没有告诉我，代价是什么。” Keep eye shape, nose bridge, lips, jawline, hair and costume consistent. Preserve continuous sound and background geometry. No location jump, no melted face, no asymmetrical eyes, no duplicate person, no text or watermark. overall_soundscape: the same dry weeds, distant machinery, city hum and faint electrical pulse. non_diegetic_music: N/A""",
    """integrated_multimodal_description: Continue from the exact previous ending through Motion Context. Same character and same bridge. Finish in a controlled medium close-up and slight profile; hold the face readable while the blue-green markings pulse once. He takes one quiet breath and completes the line: “我会自己找到答案。” Preserve facial geometry, eye spacing, nose, lips, jawline, hair silhouette, jacket and backpack, with continuous Mandarin speech and ambience. No face replacement, no age change, no costume change, no sudden zoom, no extra limbs, no flicker, no text or watermark. overall_soundscape: the same wind, demolition machinery, low city hum and restrained electrical resonance. non_diegetic_music: N/A""",
]


def make_workflow(
    index: int,
    steps: int,
    width: int = WIDTH,
    height: int = HEIGHT,
    context_dir: str = CONTEXT_DIR,
) -> dict[str, dict[str, object]]:
    prefix = f"h3_pruned_int8_drbaph_motion_context_15s_seg{index + 1}_{width}x{height}"
    data: dict[str, dict[str, object]] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors", "strength": 1.0, "low_vram": False}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPTS[index], "width": width, "height": height, "length": LENGTH, "task_type": "T2VA", "audio_mode": "native", "add_source_as_reference": False}},
        "7": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": 20260817041 + index}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["10", 0], "sampler": ["7", 0], "sigmas": ["8", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": FPS, "bit_depth": 8}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
        "16": {"class_type": "MiniMaxH3MotionContextSaveLatent", "inputs": {"latent": ["11", 0], "filename_prefix": f"{context_dir}/clip", "clip_index": index + 1}},
    }
    if index > 0:
        data["17"] = {"class_type": "MiniMaxH3MotionContextLoadLatent", "inputs": {"latent_path": context_dir, "clip_index": index}}
        data["18"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {"conditioning": ["6", 0], "vae": ["3", 0], "latent": ["6", 1], "context_length": "22", "audio_context_length": 24, "context_latent": ["17", 0]}}
        data["10"]["inputs"]["conditioning"] = ["18", 0]
        data["14"] = {"class_type": "CreateVideo", "inputs": {"images": ["19", 0], "audio": ["19", 1], "fps": FPS, "bit_depth": 8}}
        data["19"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {"images": ["12", 0], "audio": ["13", 0], "trim_frames": ["18", 1], "fps": FPS, "match_tail": True}}
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--segments", type=int, default=4)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--context-dir", default=CONTEXT_DIR)
    args = parser.parse_args()
    if not 1 <= args.segments <= len(PROMPTS):
        parser.error("segments must be between 1 and 4")
    args.output.mkdir(parents=True, exist_ok=True)
    for index in range(args.segments):
        path = args.output / f"workflow_pruned_int8_drbaph_motion_context_15s_seg{index + 1}_{args.width}x{args.height}_{args.steps}steps.json"
        path.write_text(json.dumps(make_workflow(index, args.steps, args.width, args.height, args.context_dir), ensure_ascii=False, indent=2) + "\n")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
