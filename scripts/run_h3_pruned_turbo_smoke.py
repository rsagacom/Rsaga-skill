#!/usr/bin/env python3
"""Smoke-test the published T8/DualClock workflow against pruned H3 bases.

This is intentionally a small A/B gate: one 3DCG medium shot at 4 and 8
steps per base. It checks whether the T8-convert LoRA is structurally usable
with a pruned base before expanding to the full style matrix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
RUN_ROOT = ROOT / "test-runs/2026-08-17-h3-pruned-turbo-modelonly"
TEMPLATE = ROOT / "projects/destiny-model/workflow_h3_t8_dualclock_int8_lora_1mp_5s_20steps.json"
BASE_URL = "http://192.168.1.6:8190"
MODEL_FILES = {
    "int4": "minimax_h3_fl2va_pruned_int4_convrot.safetensors",
    "int8": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
}
LORA = "minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors"

STYLE_TEXT = {
    "realistic": "cinematic live-action realism, natural human skin and fabric, grounded practical lighting, not animation",
    "3dcg": "premium Chinese 3D CG animated feature, stylized-realistic clean facial geometry, physically based materials, cinematic global illumination, not live action and not flat 2D",
    "ink": "Chinese ink-wash CG animation, controlled ink edges, layered paper texture, restrained watercolor shading, coherent stylized face, not photorealistic",
    "anime2d": "high-quality 2D anime/cel animation, clean line art, stable cel shading, coherent eyes and hair silhouette, not 3D and not live action",
}
SHOT_TEXT = {
    "medium": "stable medium shot from the waist up, character and bridge wall clearly visible",
    "face_strong": "locked extreme close-up portrait: the face fills 60 to 70 percent of the frame, head and shoulders only, eyes, nose, mouth and cheeks are the main subject, no full body, no wide environment; one tiny natural head turn",
}


def make_prompt(style: str, shot: str) -> str:
    return f"""integrated_multimodal_description: [Shot 1] {STYLE_TEXT[style]}. {SHOT_TEXT[shot]}. Qiyuan Siyuan, a Chinese man in his early thirties with short black hair, a charcoal-gray jacket, pale shirt and worn black backpack, stands in a ruined stone bridge at sunset. Blue-green circuit and ancient talisman markings glow on the wall behind him. Keep the same face, hair, costume, backpack, bridge geometry and light direction in every frame. Smooth controlled animation, coherent hands and anatomy, one continuous shot, no cuts, no text, no watermark, no duplicate character, no extra limbs, no face melting, no flickering facial features.

overall_soundscape: distant demolition machinery, dry weeds, a low city hum and a restrained electrical heartbeat-like pulse.

non_diegetic_music: N/A"""


def http_json(url: str, method: str = "GET", payload: object | None = None, timeout: int = 60) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_media(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str) and str(value["filename"]).lower().endswith((".mp4", ".webm", ".mov")):
            return value
        for child in value.values():
            found = find_media(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_media(child)
            if found:
                return found
    return None


def make_workflow(case: dict[str, object]) -> dict[str, object]:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["1"]["inputs"]["unet_name"] = MODEL_FILES[str(case["model"])]
    data["6"]["inputs"].update({
        "prompt": make_prompt(str(case["style"]), str(case["shot"])),
        "width": 640,
        "height": 384,
        "length": 124,
        "task_type": "T2VA",
        "audio_mode": "native",
        "audio_denoise_strength": 1.0,
        "add_source_as_reference": False,
        "prompt_primary_audio_ordinal": 0,
        "strict_prompt_tags": True,
        "ref_image_size": "match",
        "reference_video_policy": "official_2_to_15s",
    })
    data["5"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["1", 0],
            "lora_name": LORA,
            "strength_model": 1.0,
        },
    }
    data["7"]["inputs"]["model"] = ["5", 0]
    data["7"]["inputs"]["steps"] = int(case["steps"])
    data["8"]["inputs"]["noise_seed"] = int(case["seed"])
    data["13"]["inputs"]["filename_prefix"] = f"h3_pruned_turbo_{case['model']}_{case['style']}_{case['shot']}_{case['steps']}steps_r360_5s"
    return data


def verify_media(path: Path) -> dict[str, object]:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels", "-of", "json", str(path)], capture_output=True, text=True, check=False)
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    frames = RUN_ROOT / "frames" / path.stem
    frames.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "select='eq(n,0)+eq(n,61)+eq(n,123)'", "-vsync", "0", str(frames / "frame_%02d.png")], capture_output=True, text=True, check=False)
    return {
        "status": "passed" if probe.returncode == 0 and decode.returncode == 0 else "failed",
        "ffprobe": json.loads(probe.stdout) if probe.returncode == 0 and probe.stdout else {"stderr": probe.stderr.strip()},
        "decode_stderr": decode.stderr.strip(),
        "frames": [str(p) for p in sorted(frames.glob("frame_*.png"))],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def run_case(case: dict[str, object], poll: int) -> dict[str, object]:
    start = time.time()
    row = {**case, "status": "submitted", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        submitted = http_json(f"{BASE_URL}/prompt", method="POST", payload={"prompt": make_workflow(case)})
        prompt_id = str(submitted["prompt_id"])
        row["prompt_id"] = prompt_id
        print(f"[{case['id']}] submitted {prompt_id}", flush=True)
        while True:
            item = http_json(f"{BASE_URL}/history/{prompt_id}").get(prompt_id)
            if item:
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    row.update({"status": "error", "error_messages": status.get("messages", [])[-12:]})
                    break
                if status.get("completed") or status.get("status_str") == "success":
                    media = find_media(item.get("outputs", {}))
                    if not media:
                        row["status"] = "no_media"
                        break
                    target = RUN_ROOT / "outputs" / f"{case['id']}.mp4"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    query = urlencode({"filename": str(media["filename"]), "subfolder": str(media.get("subfolder", "")), "type": str(media.get("type", "output"))})
                    with urlopen(Request(f"{BASE_URL}/view?{query}"), timeout=180) as response, target.open("wb") as handle:
                        while chunk := response.read(1024 * 1024):
                            handle.write(chunk)
                    row.update({"status": "success", "output": str(target), "remote_media": media, "verification": verify_media(target)})
                    break
            if time.time() - start > 8 * 3600:
                row["status"] = "timeout"
                break
            time.sleep(poll)
    except Exception as exc:
        row.update({"status": "client_error", "error": f"{type(exc).__name__}: {exc}"})
    row["elapsed_seconds"] = round(time.time() - start, 2)
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{case['id']}] {row['status']} elapsed={row['elapsed_seconds']}s", flush=True)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="int4,int8")
    parser.add_argument("--steps", default="4,8")
    parser.add_argument("--styles", default="3dcg")
    parser.add_argument("--shots", default="medium")
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()
    cases = []
    for model in [x.strip() for x in args.models.split(",") if x.strip()]:
        for style in [x.strip() for x in args.styles.split(",") if x.strip()]:
            for shot in [x.strip() for x in args.shots.split(",") if x.strip()]:
                for steps in [int(x.strip()) for x in args.steps.split(",") if x.strip()]:
                    cases.append({"id": f"{model}_{style}_{shot}_{steps}steps_r360_5s", "model": model, "style": style, "shot": shot, "steps": steps, "seed": 20260830 + len(cases)})
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "workflows").mkdir(parents=True, exist_ok=True)
    results = RUN_ROOT / "results.jsonl"
    done = {}
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if item.get("status") == "success":
                    done[str(item["id"])] = item
    pending = [case for case in cases if str(case["id"]) not in done]
    for case in cases:
        (RUN_ROOT / "workflows" / f"workflow_{case['id']}.json").write_text(json.dumps(make_workflow(case), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": len(cases), "already_success": len(done), "pending": len(pending)}, ensure_ascii=False), flush=True)
    for case in pending:
        item = run_case(case, args.poll_seconds)
        with results.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
