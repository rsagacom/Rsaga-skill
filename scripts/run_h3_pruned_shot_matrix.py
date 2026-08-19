#!/usr/bin/env python3
"""Run staged H3 pruned-base shot/style tests through the isolated 8190 ComfyUI.

The first gate is deliberately small: 5.17s native-audio T2VA clips at a
safe 640x384 resolution.  It separates base-model/shot/style failures before
we spend hours on 10/15/30s Director + Motion Context runs.
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
RUN_ROOT = ROOT / "test-runs/2026-08-16-h3-pruned-shot-matrix"
TEMPLATE = ROOT / "projects/destiny-model/workflow_h3_s04a_official_int4_audio_480p_5s.json"
BASE_URL = "http://192.168.1.6:8190"
SEED = 20260816
RESOLUTION = (640, 384)

MODEL_FILES = {
    "int4": "minimax_h3_fl2va_pruned_int4_convrot.safetensors",
    "int8": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
}

SHOT_TEXT = {
    "wide": "a stable extreme wide establishing shot, the full bridge, abandoned district and character visible",
    "medium": "a stable medium shot from the waist up, character and luminous bridge wall clearly visible",
    "close": "a stable chest-up close shot, controlled slow push toward the character",
    "face": "an extreme head-and-shoulders facial close-up: the face fills about 60 percent of the frame, only the head, shoulders and a small part of the backpack are visible, no full body and no wide establishing composition; eyes and facial proportions remain clean while the character turns slightly",
    "face_strong": "a locked extreme close-up portrait: the face fills 60 to 70 percent of the frame, head and shoulders only, eyes, nose, mouth and cheeks are the main subject, no full body, no wide environment, no long shot; the character makes one tiny natural head turn while facial proportions stay fixed",
}

STYLE_TEXT = {
    "realistic": "cinematic live-action realism, natural human skin and fabric, grounded practical lighting, not animation",
    "3dcg": "premium Chinese 3D CG animated feature, stylized-realistic clean facial geometry, physically based materials, cinematic global illumination, not live action and not flat 2D",
    "ink": "Chinese ink-wash CG animation, controlled ink edges, layered paper texture, restrained watercolor shading, coherent stylized face, not photorealistic",
    "anime2d": "high-quality 2D anime/cel animation, clean line art, stable cel shading, coherent eyes and hair silhouette, not 3D and not live action",
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
        "--format=csv,noheader; free -b | awk '/Mem:/ {print \"ram_total=\"$2,\"ram_used=\"$3,\"ram_free=\"$4}'"
    )
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "cachyos-ai", "/bin/bash", "-lc", command],
            capture_output=True, text=True, timeout=12, check=False,
        )
        return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except Exception as exc:
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


def prompt(style: str, shot: str) -> str:
    return f"""integrated_multimodal_description: [Shot 1] {STYLE_TEXT[style]}. {SHOT_TEXT[shot]}. Qiyuan Siyuan, a Chinese man in his early thirties, short black hair, charcoal-gray jacket over a pale shirt, dark trousers and a worn black backpack, stands inside an abandoned stone bridge in a condemned old urban district at sunset. Through the bridge opening are broken bricks, exposed rebar, dry weeds and rust-red evening light. Dense organic markings combine circuit-board traces and ancient talisman patterns on the bridge wall; they emit a faint blue-green pulse and cast soft light across the same face and costume. Keep one continuous shot, the same character identity, hair, clothing, bridge geometry, light direction, eye shape, nose, mouth and facial proportions. Camera motion is slow and stable and the action is only a restrained turn toward the wall. Preserve anatomy, hands and facial structure across all frames. No cuts, no text, no watermark, no duplicate character, no extra limbs, no melted architecture, no flickering markings, no face melting, no sudden zoom.

overall_soundscape: distant demolition machinery, dry weeds moving in the wind, low city hum, faint electrical resonance and a restrained heartbeat-like pulse.

non_diegetic_music: N/A"""


def make_workflow(case: dict[str, object]) -> dict[str, object]:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["1"]["inputs"]["unet_name"] = MODEL_FILES[str(case["model"])]
    data["4"]["inputs"].update({
        "prompt": prompt(str(case["style"]), str(case["shot"])),
        "width": RESOLUTION[0], "height": RESOLUTION[1], "length": 124,
        "task_type": "T2VA", "audio_mode": "native", "add_source_as_reference": False,
    })
    data["6"]["inputs"]["steps"] = int(case["steps"])
    data["7"]["inputs"]["noise_seed"] = int(case["seed"])
    data["12"]["inputs"]["filename_prefix"] = f"h3_pruned_{case['model']}_{case['style']}_{case['shot']}_{case['steps']}s"
    return data


def make_cases(model: str, styles: list[str], shots: list[str], steps: int) -> list[dict[str, object]]:
    rows = []
    for style in styles:
        for shot in shots:
            rows.append({
                "id": f"{model}_{style}_{shot}_{steps}steps_r360_5s",
                "model": model, "style": style, "shot": shot, "steps": steps,
                "seed": SEED + len(rows),
            })
    return rows


def media_verify(path: Path) -> dict[str, object]:
    ffprobe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    frames_dir = RUN_ROOT / "frames" / path.stem
    frames_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "select='eq(n,0)+eq(n,61)+eq(n,123)'", "-vsync", "0", str(frames_dir / "frame_%02d.png")], capture_output=True, text=True, check=False)
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
        submitted = http_json(f"{BASE_URL}/prompt", method="POST", payload={"prompt": make_workflow(case)})
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
            time.sleep(poll_seconds)
    except (HTTPError, URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        row.update({"status": "client_error", "error": f"{type(exc).__name__}: {exc}"})
    row["elapsed_seconds"] = round(time.time() - started, 2)
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{case['id']}] {row['status']} elapsed={row['elapsed_seconds']}s", flush=True)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODEL_FILES), default="int4")
    parser.add_argument("--styles", default="3dcg")
    parser.add_argument("--shots", default="wide,medium,close,face")
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--cases")
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()
    styles = [x.strip() for x in args.styles.split(",") if x.strip()]
    shots = [x.strip() for x in args.shots.split(",") if x.strip()]
    cases = make_cases(args.model, styles, shots, args.steps)
    if args.cases:
        wanted = set(x.strip() for x in args.cases.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "workflows").mkdir(parents=True, exist_ok=True)
    for case in cases:
        (RUN_ROOT / "workflows" / f"workflow_{case['id']}.json").write_text(json.dumps(make_workflow(case), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
