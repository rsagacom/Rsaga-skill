#!/usr/bin/env python3
"""Control run: pruned H3 + T8 dual-clock sampler with no LoRA.

This isolates whether the T8 sampler path itself accepts the pruned model.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
RUN_ROOT = ROOT / "test-runs/2026-08-17-h3-pruned-turbo-smoke"
TEMPLATE = ROOT / "projects/destiny-model/workflow_h3_t8_dualclock_int8_lora_1mp_5s_20steps.json"
BASE_URL = "http://192.168.1.6:8190"
MODEL = "minimax_h3_fl2va_pruned_int4_convrot.safetensors"
PROMPT = """integrated_multimodal_description: [Shot 1] Premium Chinese 3D CG animated feature, stylized-realistic clean facial geometry, physically based materials, cinematic global illumination, not live action and not flat 2D. A Chinese man in his early thirties with short black hair and a charcoal-gray jacket stands in a ruined stone bridge at sunset. Stable medium shot from the waist up, one restrained turn toward the wall, same face, hair, costume, bridge geometry and light direction in every frame. Smooth controlled animation, coherent hands and anatomy, one continuous shot, no cuts, no text, no watermark, no extra limbs, no face melting, no flickering facial features.

overall_soundscape: distant demolition machinery, dry weeds, a low city hum and a restrained electrical pulse.

non_diegetic_music: N/A"""


def api(url: str, method: str = "GET", payload: object | None = None) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=60) as r:
        return json.loads(r.read().decode())


def media(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str) and str(value["filename"]).lower().endswith(".mp4"):
            return value
        for child in value.values():
            found = media(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = media(child)
            if found:
                return found
    return None


def workflow() -> dict[str, object]:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["1"]["inputs"]["unet_name"] = MODEL
    data.pop("5", None)
    data["7"]["inputs"]["model"] = ["1", 0]
    data["6"]["inputs"].update({
        "prompt": PROMPT,
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
    data["7"]["inputs"].update({
        "steps": 4,
        "shift_video": 12.0,
        "shift_audio": 3.0,
        "sampler_name": "dual_clock_euler",
        "scheduler": "native_flow",
    })
    data["8"]["inputs"]["noise_seed"] = 20260841
    data["13"]["inputs"]["filename_prefix"] = "h3_pruned_t8_control_int4_3dcg_medium_4steps_r360_5s"
    return data


def main() -> int:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "workflows").mkdir(parents=True, exist_ok=True)
    wf = workflow()
    (RUN_ROOT / "workflows" / "workflow_pruned_t8_control_int4_4steps.json").write_text(
        json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    started = time.time()
    submitted = api(BASE_URL + "/prompt", "POST", {"prompt": wf})
    prompt_id = str(submitted["prompt_id"])
    print(f"submitted {prompt_id}", flush=True)
    while True:
        item = api(BASE_URL + "/history/" + prompt_id).get(prompt_id)
        if item:
            status = item.get("status", {})
            if status.get("status_str") == "error":
                row = {"status": "error", "prompt_id": prompt_id, "elapsed_seconds": round(time.time() - started, 2), "messages": status.get("messages", [])[-3:]}
                (RUN_ROOT / "t8_control_result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(row, ensure_ascii=False)[:5000], flush=True)
                return 1
            if status.get("completed") or status.get("status_str") == "success":
                found = media(item.get("outputs", {}))
                if not found:
                    print("success_without_media", flush=True)
                    return 2
                target = RUN_ROOT / "outputs" / "h3_pruned_t8_control_int4_3dcg_medium_4steps_r360_5s.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                query = urlencode({"filename": str(found["filename"]), "subfolder": str(found.get("subfolder", "")), "type": str(found.get("type", "output"))})
                with urlopen(Request(BASE_URL + "/view?" + query), timeout=180) as r, target.open("wb") as f:
                    while chunk := r.read(1024 * 1024):
                        f.write(chunk)
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels", "-of", "json", str(target)], capture_output=True, text=True)
                decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(target), "-f", "null", "-"], capture_output=True, text=True)
                row = {"status": "success", "prompt_id": prompt_id, "output": str(target), "elapsed_seconds": round(time.time() - started, 2), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "ffprobe": json.loads(probe.stdout), "decode_returncode": decode.returncode}
                (RUN_ROOT / "t8_control_result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(row, ensure_ascii=False), flush=True)
                return 0
        if time.time() - started > 8 * 3600:
            print("timeout", flush=True)
            return 3
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
