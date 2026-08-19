#!/usr/bin/env python3
"""Run the official-style MiniMax H3 GGUF Q4_K_M T2VA/R2VA baselines.

This deliberately uses the GGUF loaders and the native H3 conditioning nodes;
it does not attach safetensors Turbo/Ref2V LoRAs.  The script expects an
isolated ComfyUI endpoint with ComfyUI-GGUF-MiniMax-H3 enabled.
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
RUN_ROOT = ROOT / "test-runs/2026-08-17-h3-gguf-q4-baseline"
BASE_URL = "http://192.168.1.6:8192"
REFERENCE = "character_front_reference.png"


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


def prompt(task: str, reference_match: bool = False) -> str:
    if task == "t2v":
        return """integrated_multimodal_description: [Shot 1] premium Chinese 3D CG animated feature, stylized-realistic clean facial geometry, physically based materials, cinematic global illumination, not live action and not flat 2D. Stable medium shot from the waist up of Qiyuan Siyuan, a Chinese man in his early thirties with short black hair, charcoal-gray jacket, pale shirt and worn black backpack, standing on a ruined stone bridge at sunset. Blue-green circuit and ancient talisman markings glow on the wall. Keep one face, hair, clothing, backpack, bridge geometry and light direction in every frame. One continuous restrained head turn, coherent hands and anatomy, no cuts, no text, no watermark, no duplicate character, no extra limbs, no face melting, no flickering facial features.

overall_soundscape: distant demolition machinery, dry weeds moving in the wind, low city hum and a restrained electrical pulse.

non_diegetic_music: N/A"""
    if reference_match:
        return """subject_definitions:
<Picture 1> is the front reference for one adult Chinese woman, authoritative for face, hair, teal-blue robe, cream inner garment, waist sash, shoulder bag and hairpin. Use it only as an identity and costume reference; never render the reference panel or its background.

summary:
Premium Chinese 3D CG animated feature, one continuous medium shot on a ruined stone bridge at sunset. The same woman turns her head slightly toward the glowing blue-green talisman markings while speaking one short Mandarin line. Preserve the female identity, face shape, eye spacing, nose, mouth, hair silhouette, teal-blue robe, cream inner garment, waist sash, shoulder bag, hairpin, bridge geometry and light direction.

spoken_dialogue:
The woman says in Mandarin exactly: “命运不是答案，它只是下一道门。” Keep natural restrained mouth motion.

overall_soundscape:
Dry weeds in the wind, distant demolition machinery, low city hum, faint electrical resonance and restrained breath.

negative_constraints:
No gender change, no live-action conversion, no anime cel shading, no face replacement, no costume change, no duplicate person, no extra limbs, no facial melting, no warped eyes, no text, no logo, no watermark, no scene cut.

non_diegetic_music:
N/A

shot_direction:
Stable medium shot, slow controlled push in, no sudden zoom."""
    return """subject_definitions:
<Picture 1> is the front reference for one adult Chinese man, authoritative for face, hair, charcoal-gray jacket, pale shirt and worn black backpack. Use it only as an identity reference; never render the reference panel or its background.

summary:
Premium Chinese 3D CG animated feature, one continuous medium shot on a ruined stone bridge at sunset. The same man turns his head slightly toward the glowing blue-green talisman markings while speaking one short Mandarin line. Preserve face shape, eye spacing, nose, mouth, hair silhouette, jacket, backpack, bridge geometry and light direction.

spoken_dialogue:
The man says in Mandarin exactly: “命运不是答案，它只是下一道门。” Keep natural restrained mouth motion.

overall_soundscape:
Dry weeds in the wind, distant demolition machinery, low city hum, faint electrical resonance and restrained breath.

negative_constraints:
No live-action conversion, no anime cel shading, no face replacement, no costume change, no duplicate person, no extra limbs, no facial melting, no warped eyes, no text, no logo, no watermark, no scene cut.

non_diegetic_music:
N/A

shot_direction:
Stable medium shot, slow controlled push in, no sudden zoom."""


def make_workflow(task: str, steps: int, seed: int, prefix: str, reference_match: bool = False) -> dict[str, dict[str, object]]:
    d: dict[str, dict[str, object]] = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "MiniMax-H3-FL2VA-Q4_K_M.gguf" if task == "t2v" else "MiniMax-H3-REF2VA-Q4_K_M.gguf"}},
        "2": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": "qwen3vl-32B-MiniMax-H3-Q4_K_M.gguf", "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "6": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "8": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["10", 0]}},
        "9": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["8", 0], "sampler": ["5", 0], "sigmas": ["6", 0], "latent_image": ["10", 1]}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "audio": ["12", 0], "fps": 24.0, "bit_depth": 8}},
        "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}},
    }
    if task == "t2v":
        d["10"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt(task, reference_match), "width": 640, "height": 384, "length": 124}}
    else:
        d["15"] = {"class_type": "LoadImage", "inputs": {"image": REFERENCE}}
        d["10"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt(task, reference_match), "width": 640, "height": 384, "length": 124, "ref_image_size": "match", "ref_images.ref_image_0": ["15", 0]}}
    return d


def host_sample() -> dict[str, object]:
    cmd = "nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu --format=csv,noheader; free -b | awk '/Mem:/ {print \"ram_total=\"$2,\"ram_used=\"$3,\"ram_free=\"$4}'"
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "cachyos-ai", "/bin/bash", "-s"], input=cmd, capture_output=True, text=True, timeout=12, check=False)
    return {"ok": p.returncode == 0, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


def verify_media(path: Path) -> dict[str, object]:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels", "-of", "json", str(path)], capture_output=True, text=True, check=False)
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    frames = RUN_ROOT / "frames" / path.stem
    frames.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "select='eq(n,0)+eq(n,61)+eq(n,123)'", "-vsync", "0", str(frames / "frame_%02d.png")], capture_output=True, text=True, check=False)
    return {"status": "passed" if probe.returncode == 0 and decode.returncode == 0 else "failed", "ffprobe": json.loads(probe.stdout) if probe.returncode == 0 else {"stderr": probe.stderr.strip()}, "decode_stderr": decode.stderr.strip(), "frames": [str(p) for p in sorted(frames.glob("frame_*.png"))], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def run_case(task: str, steps: int, seed: int, poll: int, reference_match: bool = False) -> dict[str, object]:
    suffix = "_refmatch" if reference_match else ""
    case_id = f"gguf_q4_{task}{suffix}_{steps}steps_640x384_5s"
    started = time.time()
    row: dict[str, object] = {"id": case_id, "task": task, "steps": steps, "seed": seed, "status": "submitted", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host_start": host_sample()}
    workflow = make_workflow(task, steps, seed, f"h3_gguf_q4_{task}{suffix}_{steps}steps_640x384_5s", reference_match)
    (RUN_ROOT / "workflows").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "workflows" / f"workflow_{case_id}.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        submitted = http_json(f"{BASE_URL}/prompt", method="POST", payload={"prompt": workflow})
        prompt_id = str(submitted["prompt_id"])
        row["prompt_id"] = prompt_id
        print(f"[{case_id}] submitted {prompt_id}", flush=True)
        while True:
            item = http_json(f"{BASE_URL}/history/{prompt_id}")
            item = item.get(prompt_id) if isinstance(item, dict) else None
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
                    target = RUN_ROOT / "outputs" / f"{case_id}.mp4"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    q = urlencode({"filename": media["filename"], "subfolder": media.get("subfolder", ""), "type": media.get("type", "output")})
                    with urlopen(Request(f"{BASE_URL}/view?{q}"), timeout=300) as response, target.open("wb") as handle:
                        while chunk := response.read(1024 * 1024): handle.write(chunk)
                    row.update({"status": "success", "output": str(target), "remote_media": media, "verification": verify_media(target), "host_end": host_sample()})
                    break
            if time.time() - started > 12 * 3600:
                row["status"] = "timeout"
                break
            time.sleep(poll)
    except Exception as exc:
        row.update({"status": "client_error", "error": f"{type(exc).__name__}: {exc}"})
    row["elapsed_seconds"] = round(time.time() - started, 2)
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with (RUN_ROOT / "results.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[{case_id}] {row['status']} elapsed={row['elapsed_seconds']}s", flush=True)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="t2v,r2v")
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--force", action="store_true", help="rerun cases already present in results.jsonl")
    parser.add_argument("--r2v-reference-match", action="store_true", help="use a female prompt matching character_front_reference.png for R2V")
    args = parser.parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    existing = {json.loads(line).get("id") for line in (RUN_ROOT / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()} if (RUN_ROOT / "results.jsonl").exists() else set()
    for i, task in enumerate(x.strip() for x in args.tasks.split(",") if x.strip()):
        suffix = "_refmatch" if args.r2v_reference_match and task == "r2v" else ""
        case_id = f"gguf_q4_{task}{suffix}_{args.steps}steps_640x384_5s"
        if case_id in existing and not args.force:
            print(f"[{case_id}] already recorded", flush=True)
            continue
        run_case(task, args.steps, 20260817 + i, args.poll_seconds, args.r2v_reference_match and task == "r2v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
