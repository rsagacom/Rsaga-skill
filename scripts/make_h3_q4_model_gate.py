#!/usr/bin/env python3
"""Create a minimal MiniMax H3 FL2VA GGUF model-gate workflow.

The gate keeps the validated prune-LoRA prompt, latent, sampler and decoder
chain, replacing only the diffusion/text-encoder loaders with the installed
GGUF loaders.  It is intentionally a 5-second, 640x384, 4-step probe: model
selection happens before any Director/Motion Context long-video test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diffusion", default="MiniMax-H3-FL2VA-Q4_K_M.gguf")
    parser.add_argument("--native-diffusion", action="store_true")
    parser.add_argument(
        "--native-diffusion-name",
        default="minimax_h3_fl2va_pruned_int4_convrot.safetensors",
    )
    parser.add_argument("--text-encoder", default="qwen3vl-32B-MiniMax-H3-Q4_K_M.gguf")
    parser.add_argument("--native-text-encoder", action="store_true")
    parser.add_argument("--dynamic-text-encoder", action="store_true")
    parser.add_argument(
        "--native-text-encoder-name",
        default="qwen3vl_32b_minimax_h3_int4_convrot.safetensors",
    )
    parser.add_argument("--prefix", default="h3_q4km_drbaph_rawkeys_4steps_640x384")
    parser.add_argument(
        "--lora",
        default="minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors",
    )
    parser.add_argument("--low-vram-lora", action="store_true")
    args = parser.parse_args()

    workflow = json.loads(args.base.read_text())
    if args.native_diffusion:
        workflow["1"] = {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": args.native_diffusion_name,
                "weight_dtype": "default",
            },
        }
    else:
        workflow["1"] = {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": args.diffusion},
        }
    if args.native_text_encoder and args.dynamic_text_encoder:
        parser.error("--native-text-encoder and --dynamic-text-encoder are mutually exclusive")
    if args.native_text_encoder:
        workflow["2"] = {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": args.native_text_encoder_name,
                "type": "minimax",
                "device": "cpu",
            },
        }
    elif args.dynamic_text_encoder:
        workflow["2"] = {
            "class_type": "CLIPLoaderGGUFDynamicVRAM",
            "inputs": {"clip_name": args.text_encoder, "type": "minimax"},
        }
    else:
        workflow["2"] = {
            "class_type": "CLIPLoaderGGUF",
            "inputs": {"clip_name": args.text_encoder, "type": "minimax"},
        }
    workflow["5"]["inputs"]["lora_name"] = args.lora
    workflow["5"]["inputs"]["low_vram"] = args.low_vram_lora
    workflow["15"]["inputs"]["filename_prefix"] = args.prefix
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
