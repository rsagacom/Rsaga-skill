#!/usr/bin/env python3
"""Run the dedicated Destiny Model Chinese 3D-CG anime H3 matrix.

This runner keeps the verified INT8 + INT4 Qwen + T8 + DualClock T2VA chain,
changes only the art direction/camera case, and records media/host evidence.
It never stops unrelated services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
RUN_ROOT = ROOT / "test-runs/2026-08-16-h3-3dcg-anime"
TEMPLATE = ROOT / "projects/destiny-model/workflow_h3_t8_dualclock_int8_lora_1mp_5s_20steps.json"
BASE_URL = "http://192.168.1.6:8190"
SEED = 20260816

RESOLUTIONS = {
    "r360": (640, 384),
    "r480": (832, 480),
    "r540": (960, 544),
    "r660": (1088, 608),
    "r800": (1216, 672),
    "r1mp": (1344, 768),
}

PROMPTS = {
    "bridge_medium": """integrated_multimodal_description: [Shot 1] Premium Chinese 3D CG animated feature film in the polished style of contemporary Chinese 3D animation, stylized-realistic character design, clean appealing facial geometry, physically based materials, cinematic global illumination, refined environment modeling, expressive eyes, smooth controlled shading, not live action and not flat 2D anime. Qiyuan Siyuan, a Chinese man in his early thirties, slim and slightly tired, short black hair, charcoal-gray everyday jacket over a pale shirt, dark trousers and a worn black backpack, stands inside an abandoned stone bridge in a condemned old urban district at sunset. Through the bridge opening, a demolished park, broken bricks, exposed rebar, dry weeds and rust-red evening light are visible. The inner bridge walls are covered by dense organic markings combining circuit-board traces and distorted ancient talisman patterns; they emit a faint blue-green phosphorescent pulse like a slow heartbeat and cast soft light across his face and jacket. The camera makes one slow stable forward dolly from a medium-wide shot to a restrained medium close-up as Qiyuan turns toward the luminous wall, keeping the same character, bridge geometry, markings, backpack, costume and light direction. High-end Chinese 3D animation look, detailed but stylized hair and cloth, cinematic depth, coherent hard-surface stone, volumetric dust, restrained supernatural atmosphere, one continuous shot, stable face and anatomy, stable hands, no cuts, no text, no watermark, no duplicate character, no extra limbs, no melted architecture, no flickering markings, no photorealistic live-action skin, no flat 2D line art.

overall_soundscape: distant demolition machinery, dry weeds moving in the wind, a low city hum, faint electrical resonance and a restrained heartbeat-like pulse from the markings.

non_diegetic_music: N/A""",
    "bridge_close": """integrated_multimodal_description: [Shot 1] Premium Chinese 3D CG animated feature film, contemporary Chinese 3D animation character rendering, stylized-realistic clean face, smooth cinematic shading, expressive eyes, detailed but controlled hair and cloth, physically based stone and metal, not live action and not flat 2D anime. Qiyuan Siyuan, a Chinese man in his early thirties with short black hair, a charcoal-gray jacket and pale shirt, stands in the dark interior of an abandoned stone bridge. A faint blue-green pulse from organic circuit-and-talisman markings on the wall lights the left side of his face. His expression shifts from fear to stunned disbelief as he slowly turns his head toward the wall; his shoulders, backpack strap, hands and facial proportions remain consistent. The camera holds a stable chest-up three-quarter close-up with only a very small forward push, keeping the luminous wall texture and the same cool-to-warm sunset direction behind him. High-end Chinese 3D CG animation, clean facial topology, natural eye movement, restrained supernatural atmosphere, coherent geometry, one continuous shot, no cuts, no text, no watermark, no duplicate character, no extra limbs, no deformed hands, no face melting, no flickering facial features, no photorealistic live-action skin, no flat 2D line art.

overall_soundscape: low bridge-room resonance, distant city machinery, dry wind and a quiet heartbeat-like electrical pulse.

non_diegetic_music: N/A""",
}


def http_json(url: str, method: str = "GET", payload: object | None = None, timeout: int = 60) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def host_sample() -> dict[str, object]:
    command = (
        "nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu "
        "--format=csv,noheader; free -b | awk '/Mem:/ {print \"ram_total=\"$2,\"ram_used=\"$3,\"ram_free=\"$4}'; "
        "free -b | awk '/Swap:/ {print \"swap_total=\"$2,\"swap_used=\"$3,\"swap_free=\"$4}'"
    )
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "cachyos-ai", "/bin/bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except Exception as exc:  # pragma: no cover - evidence collection must not stop a media run
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


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


def workflow(case: dict[str, object]) -> dict[str, object]:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    width, height = RESOLUTIONS[str(case["res"])]
    data["6"]["inputs"].update({
        "prompt": PROMPTS[str(case["shot"])],
        "width": width,
        "height": height,
        "length": 124,
        "task_type": "T2VA",
        "audio_mode": "native",
        "add_source_as_reference": False,
    })
    data["7"]["inputs"]["steps"] = int(case["steps"])
    data["8"]["inputs"]["noise_seed"] = int(case["seed"])
    data["13"]["inputs"]["filename_prefix"] = f"h3_3dcg_anime_{case['id']}"
    return data


def make_cases(include_close: bool, include_20: bool) -> list[dict[str, object]]:
    cases = [
        {"id": f"bridge_medium_{res}_8s", "shot": "bridge_medium", "res": res, "steps": 8, "seed": SEED}
        for res in RESOLUTIONS
    ]
    if include_close:
        cases.extend(
            {"id": f"bridge_close_{res}_8s", "shot": "bridge_close", "res": res, "steps": 8, "seed": SEED + 1}
            for res in ("r480", "r660", "r1mp")
        )
    if include_20:
        cases.extend([
            {"id": "bridge_medium_r1mp_20s", "shot": "bridge_medium", "res": "r1mp", "steps": 20, "seed": SEED},
            {"id": "bridge_close_r1mp_20s", "shot": "bridge_close", "res": "r1mp", "steps": 20, "seed": SEED + 1},
        ])
    return cases


def media_verify(path: Path) -> dict[str, object]:
    ffprobe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    frames_dir = path.parent.parent / "frames" / path.stem
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "select='eq(n,0)+eq(n,61)+eq(n,123)'", "-vsync", "0", str(frames_dir / "frame_%02d.png")]
    subprocess.run(frame_cmd, capture_output=True, text=True, check=False)
    return {
        "status": "passed" if ffprobe.returncode == 0 and decode.returncode == 0 else "failed",
        "ffprobe": json.loads(ffprobe.stdout) if ffprobe.returncode == 0 and ffprobe.stdout else {"stderr": ffprobe.stderr.strip()},
        "decode_stderr": decode.stderr.strip(),
        "frames": [str(p) for p in sorted(frames_dir.glob("frame_*.png"))],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def run_case(case: dict[str, object], poll_seconds: int) -> dict[str, object]:
    started = time.time()
    row: dict[str, object] = {**case, "status": "submitted", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host_start": host_sample()}
    try:
        submitted = http_json(f"{BASE_URL}/prompt", method="POST", payload={"prompt": workflow(case)})
        prompt_id = str(submitted["prompt_id"])
        row["prompt_id"] = prompt_id
        print(f"[{case['id']}] submitted {prompt_id}", flush=True)
        while True:
            item = http_json(f"{BASE_URL}/history/{prompt_id}").get(prompt_id)
            if item:
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    row.update({"status": "error", "error_messages": status.get("messages", [])[-8:]})
                    break
                if status.get("completed") or status.get("status_str") == "success":
                    media = find_media(item.get("outputs", {}))
                    if not media:
                        row["status"] = "no_media"
                        break
                    target = RUN_ROOT / "outputs" / f"{case['id']}.mp4"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    query = urlencode({"filename": str(media["filename"]), "subfolder": str(media.get("subfolder", "")), "type": str(media.get("type", "output"))})
                    with urlopen(Request(f"{BASE_URL}/view?{query}", headers={"Accept": "video/mp4,video/*,*/*"}), timeout=180) as response, target.open("wb") as handle:
                        while chunk := response.read(1024 * 1024):
                            handle.write(chunk)
                    row.update({"status": "success", "output": str(target), "remote_media": media, "verification": media_verify(target)})
                    break
            if time.time() - started > 8 * 3600:
                row["status"] = "timeout"
                break
            if int(time.time() - started) % 60 < poll_seconds:
                row["host_sample"] = host_sample()
            time.sleep(poll_seconds)
    except (HTTPError, URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        row.update({"status": "client_error", "error": f"{type(exc).__name__}: {exc}"})
    row["elapsed_seconds"] = round(time.time() - started, 2)
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{case['id']}] {row['status']} elapsed={row['elapsed_seconds']}s", flush=True)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-close", action="store_true")
    parser.add_argument("--include-20", action="store_true")
    parser.add_argument("--cases", help="comma-separated IDs")
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "workflows").mkdir(parents=True, exist_ok=True)
    cases = make_cases(args.include_close, args.include_20)
    selected = {x.strip() for x in args.cases.split(",")} if args.cases else None
    cases = [x for x in cases if selected is None or x["id"] in selected]
    for case in cases:
        (RUN_ROOT / "workflows" / f"workflow_{case['id']}.json").write_text(json.dumps(workflow(case), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    results_path = RUN_ROOT / "results.jsonl"
    completed = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if item.get("status") == "success":
                    completed[str(item["id"])] = item
    pending = [case for case in cases if str(case["id"]) not in completed]
    print(json.dumps({"total": len(cases), "already_success": len(completed), "pending": len(pending), "base_url": BASE_URL}, ensure_ascii=False), flush=True)
    for case in pending:
        result = run_case(case, args.poll_seconds)
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
