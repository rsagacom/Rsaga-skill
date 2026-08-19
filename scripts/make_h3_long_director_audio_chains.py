#!/usr/bin/env python3
"""Create reproducible MiniMax H3 Director long-chain T2VA workflows.

The Director node owns the timeline and uses its integrated H3 Motion Context
continuity path between segments.  This deliberately does not inject the
standalone Motion Context runtime patch into the same ComfyUI process.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
DEFAULT_OUT = ROOT / "test-runs/2026-08-14-h3-long-director-audio"
WIDTH = 704
HEIGHT = 416
FPS = 24.0
SEGMENT_FRAMES = 362  # 15.083333s; H3 17n+5 aligned maximum on this GPU
OVERLAP_FRAMES = 22

MONOLOGUES = [
    "这条旧巷子还记得我吗？我离开之后，风一直替我守着这里。",
    "师父说过，真正的归途不是回到某个地方，而是敢于面对当年的自己。",
    "灯影落在青石上，像一封没有寄出的信，提醒我不要再错过今晚。",
    "如果命运真的有答案，它一定藏在每一次选择之后，而不是等待之中。",
    "我听见远处的钟声，也听见自己的心跳；这一次，我不会再转身离开。",
    "石桥那边就是旧宅，门还在，故事也还在，只是我已经不是从前的我。",
    "雨会洗去脚印，却洗不掉记忆；我必须走完这条路，才能知道真相。",
    "天快亮了。无论门后是什么，我都要亲手推开它，替所有沉默的人问一句为什么。",
]

MOTIONS = [
    "The camera makes a slow forward dolly through the rain-soaked stone lane.",
    "The camera follows beside her at walking pace as lantern reflections move across the wet stones.",
    "She passes a carved wooden gate while the camera makes a restrained lateral reveal toward the old courtyard.",
    "The camera arcs gently around her shoulder as she studies a faded paper talisman on the wall.",
    "She crosses a narrow covered bridge; the camera tracks backward while keeping her face and body proportions stable.",
    "The camera settles into a slow push toward the ancestral doorway as she raises one hand toward the latch.",
    "The door opens a fraction; incense haze and rain mist drift through the same stable courtyard geometry.",
    "At first light she steps across the threshold; the camera lifts slightly to reveal the quiet tiled roofs beyond.",
]


def prompt(index: int) -> str:
    line = MONOLOGUES[index % len(MONOLOGUES)]
    motion = MOTIONS[index % len(MOTIONS)]
    return f"""integrated_multimodal_description: [Shot {index + 1}] Cinematic Chinese guofeng fantasy drama, a young Chinese woman in her early thirties with the same oval face, long black hair tied with a dark jade hairpin, a deep teal hanfu coat over an ivory inner robe, and a small weathered satchel. Keep the exact same person, hairstyle, costume colors, body proportions, ancient rain-soaked Jiangnan stone lane, tiled roofs, red lanterns, carved wooden doors and blue-hour-to-dawn lighting continuity from the previous shot. {motion} One continuous shot, restrained cinematic movement, stable face and hands, no cuts, no duplicate person, no extra limbs, no deformed hands, no modern objects, no text, no logo.

spoken_dialogue: In Mandarin Chinese, the woman speaks a quiet first-person inner monologue with clear natural diction and restrained emotion. Say exactly this line and no other words: “{line}” Finish the line naturally before the final two seconds, then remain silent while the environment continues. Her mouth movement should be subtle and consistent with the spoken line.

overall_soundscape: continuous light rain on stone, soft wind through bamboo, distant wooden eaves dripping, cloth movement, measured footsteps, subtle breath, and a single realistic lantern chain rattle when she passes. Preserve the same acoustic space and do not hard-cut the ambience or dialogue between segments.

non_diegetic_music: very low restrained guqin and xiao texture, no percussion, ducked under the spoken voice, fading naturally at the end."""


def timeline(segment_count: int) -> str:
    total = SEGMENT_FRAMES * segment_count
    segments = []
    for i in range(segment_count):
        segments.append(
            {
                "id": f"s{i}",
                "start": i * SEGMENT_FRAMES,
                "length": SEGMENT_FRAMES,
                "frameCount": SEGMENT_FRAMES,
                "durationSec": SEGMENT_FRAMES / FPS,
                "prompt": prompt(i),
                "taskType": "",
                "refs": [],
                "referenceVideo": {},
                "genImage": {"imageFile": ""},
                "negativePrompt": "",
                "continuityFromPrev": i > 0,
            }
        )
    data = {
        "version": 4,
        "editMode": "segment",
        "timelineMode": "prompt_batch",
        "totalFrames": total,
        "frameRate": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "refMaxSize": WIDTH,
        "output": {
            "mode": "fixed",
            "longEdge": WIDTH,
            "width": WIDTH,
            "height": HEIGHT,
            "maxExportFrames": 0,
            "exportMode": "all",
            "audioMode": "generate",
            "continuityEnabled": True,
            "continuityOverlapFrames": OVERLAP_FRAMES,
        },
        "videoClips": [],
        "video": {"fileName": "", "videoFile": "", "subfolder": "", "type": "input", "frames": [], "frameMap": []},
        "global": {"taskType": "t2v — 文生视频(Text to Video)", "prompt": "", "refs": [], "referenceVideo": {}, "continuousReference": False, "genImage": {"imageFile": ""}},
        "segments": segments,
        "gen": {"defaultFrameCount": SEGMENT_FRAMES},
        "runSelectEnabled": False,
        "runSelection": [],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def workflow(segment_count: int, duration_label: str, seed: int) -> dict[str, dict[str, object]]:
    total = SEGMENT_FRAMES * segment_count
    prefix = f"h3_director_guofeng_monologue_{duration_label}_704x416"
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int4_convrot.safetensors", "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "PathchSageAttentionKJ", "inputs": {"model": ["1", 0], "sage_attention": "auto", "allow_compile": False}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["5", 0]}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["6", 0], "lora_name": "minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors", "strength_model": 1.0}},
        "8": {"class_type": "MiniMaxH3Director", "inputs": {"model": ["7", 0], "video_vae": ["3", 0], "audio_vae": ["4", 0], "clip": ["2", 0], "task_type": "t2v — 文生视频(Text to Video)", "global_prompt": "", "bd_grp_sample": "采样设置", "cfg": 1.0, "seed": seed, "frame_rate": FPS, "width": WIDTH, "height": HEIGHT, "ref_max_size": WIDTH, "total_frames": total, "timeline_data": timeline(segment_count), "bd_grp_advanced": "高级采样", "steps": 8, "sampler": "res_multistep", "scheduler": "simple", "shift_video": 12.0, "shift_audio": 3.0, "bd_grp_perf": "性能", "clear_vram_between_segments": True, "export_source_images": False}},
        "9": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "audio": ["8", 1], "fps": ["8", 2], "bit_depth": 8}},
        "10": {"class_type": "SaveVideo", "inputs": {"video": ["9", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--durations", nargs="+", choices=["30s", "60s", "120s"], default=["30s", "60s", "120s"])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    counts = {"30s": 2, "60s": 4, "120s": 8}
    seeds = {"30s": 20260814_3001, "60s": 20260814_6001, "120s": 20260814_12001}
    for label in args.durations:
        out = args.output_dir / f"workflow_{label}_director_t8_guofeng_monologue_704x416.json"
        out.write_text(json.dumps(workflow(counts[label], label, seeds[label]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(out)


if __name__ == "__main__":
    main()
