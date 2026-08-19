#!/usr/bin/env python3
"""Official DiffSynth-Studio MiniMax-H3 Pruned-NF4 FL2VA smoke test.

This script intentionally uses the isolated DiffSynth pipeline and local
weights. It is not a ComfyUI workflow and does not load the existing H3 LoRA
or SageAttention chain. The first run is a 5-second T2VA baseline so the
quantized model can be measured independently.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from pathlib import Path


os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# The shared ComfyUI venv contains SageAttention. DiffSynth auto-selects it
# when importable, but its temporary int8 tensors exceed the 12GB smoke-test
# budget. Keep this standalone NF4 baseline on PyTorch SDPA; SageAttention is
# tested separately in the ComfyUI chain.
os.environ.setdefault("DIFFSYNTH_ATTENTION_IMPLEMENTATION", "torch")


ROOT = Path(os.environ.get("H3_NF4_ROOT", "/mnt/gaosu_sata/MiniMax-H3-NF4"))
RUN_ROOT = Path(
    os.environ.get(
        "H3_NF4_RUN_ROOT",
        "/mnt/gaosu_sata/MiniMax-H3-diffsynth/runs/nf4-pruned-fl2va-smoke",
    )
)
WIDTH = int(os.environ.get("H3_NF4_WIDTH", "832"))
HEIGHT = int(os.environ.get("H3_NF4_HEIGHT", "480"))
NUM_FRAMES = int(os.environ.get("H3_NF4_NUM_FRAMES", "124"))
OUTPUT_PATH = RUN_ROOT / os.environ.get("H3_NF4_OUTPUT_NAME", "h3_nf4_pruned_fl2va_t2va_5s.mp4")
METADATA_PATH = RUN_ROOT / "run_metadata.json"
PROCESSOR_PATH = Path(
    os.environ.get(
        "H3_NF4_PROCESSOR_ROOT",
        "/mnt/gaosu_sata/MiniMax-H3-diffsynth/models/MiniMax/MiniMax-H3/FL2VA/processor",
    )
)


def gpu_snapshot() -> str:
    try:
        return subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except Exception as exc:  # pragma: no cover - diagnostics only
        return f"unavailable: {exc}"


def main() -> int:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    metadata: dict[str, object] = {
        "profile": "official_diffsynth_pruned_nf4_fl2va",
        "mode": "T2VA",
        "model_root": str(ROOT),
        "processor_root": str(PROCESSOR_PATH),
        "output": str(OUTPUT_PATH),
        "started_at_epoch": started_at,
        "gpu_before": gpu_snapshot(),
        "parameters": {
            "width": WIDTH,
            "height": HEIGHT,
            "num_frames": NUM_FRAMES,
            "fps": 24,
            "num_inference_steps": 50,
            "seed": 20260816,
            "flow_shift": 12.0,
            "audio_flow_shift": 3.0,
            "cfg_scale": 1.0,
            "lora": None,
            "sage_attention": None,
            "attention_implementation": os.environ["DIFFSYNTH_ATTENTION_IMPLEMENTATION"],
            "first_frame": None,
        },
        "weights": {
            "dit": str(ROOT / "minimax-h3-fl2va-pruned-nf4.safetensors"),
            "text_encoder": str(ROOT / "minimax-h3-text-encoder-nf4.safetensors"),
            "video_vae": str(ROOT / "video_vae_nf4.safetensors"),
            "audio_vae": str(ROOT / "audio_vae_nf4.safetensors"),
        },
    }

    try:
        import torch
        from diffsynth.core import ModelConfig
        from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline
        from diffsynth.utils.data.audio_video import write_video_audio

        free_vram, total_vram = torch.cuda.mem_get_info("cuda")
        metadata["cuda_before_pipeline"] = {
            "free_bytes": free_vram,
            "total_bytes": total_vram,
            "free_gib": free_vram / 1024**3,
            "total_gib": total_vram / 1024**3,
        }

        # This is the official Pruned-NF4 low-VRAM profile. Disk offload is
        # deliberate: the shared machine has 32GB RAM but only 12GB VRAM,
        # and the existing ComfyUI process must remain untouched.
        vram_config = {
            "offload_dtype": "disk",
            "offload_device": "disk",
            "onload_dtype": "disk",
            "onload_device": "disk",
            "preparing_dtype": torch.bfloat16,
            "preparing_device": "cuda",
            "computation_dtype": torch.bfloat16,
            "computation_device": "cuda",
        }

        configured_vram_limit = float(os.environ.get("H3_NF4_VRAM_LIMIT_GIB", "0"))
        vram_limit = (
            configured_vram_limit
            if configured_vram_limit > 0
            else max(torch.cuda.mem_get_info("cuda")[1] / 1024**3 - 2.0, 1.0)
        )
        metadata["vram_limit_gib"] = vram_limit

        pipe = MiniMaxH3Pipeline.from_pretrained(
            torch_dtype=torch.bfloat16,
            device="cuda",
            model_configs=[
                ModelConfig(path=str(ROOT / "minimax-h3-fl2va-pruned-nf4.safetensors"), **vram_config),
                ModelConfig(path=str(ROOT / "minimax-h3-text-encoder-nf4.safetensors"), **vram_config),
                ModelConfig(path=str(ROOT / "video_vae_nf4.safetensors"), **vram_config),
                ModelConfig(path=str(ROOT / "audio_vae_nf4.safetensors"), **vram_config),
            ],
            processor_config=ModelConfig(path=str(PROCESSOR_PATH)),
            vram_limit=vram_limit,
        )

        prompt = (
            "中国古风真人短剧，雨夜旧城巷口，一名穿深青色长衫的年轻男子站在檐下，"
            "镜头缓慢推近，雨丝、远处脚步声和低沉风声形成自然环境音。"
            "他压低声音对镜头独白：‘这条路既然走到这里，我就不会回头。’"
            "表演克制、面部自然、动作连续，真实电影光影，无字幕、无水印、无画面文字。"
        )
        video, audio = pipe(
            prompt=prompt,
            height=HEIGHT,
            width=WIDTH,
            num_frames=NUM_FRAMES,
            num_inference_steps=50,
            seed=20260816,
        )
        write_video_audio(
            video=video,
            audio=audio,
            output_path=str(OUTPUT_PATH),
            fps=24,
            audio_sample_rate=32000,
        )

        metadata["status"] = "success"
        metadata["ended_at_epoch"] = time.time()
        metadata["elapsed_seconds"] = metadata["ended_at_epoch"] - started_at
        metadata["gpu_after"] = gpu_snapshot()
        metadata["cuda_after_pipeline"] = {
            "free_bytes": torch.cuda.mem_get_info("cuda")[0],
            "total_bytes": torch.cuda.mem_get_info("cuda")[1],
        }
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["ended_at_epoch"] = time.time()
        metadata["elapsed_seconds"] = metadata["ended_at_epoch"] - started_at
        metadata["gpu_after"] = gpu_snapshot()
        metadata["error"] = repr(exc)
        metadata["traceback"] = traceback.format_exc()
        METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"OUTPUT={OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
