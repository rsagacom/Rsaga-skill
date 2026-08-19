#!/usr/bin/env python3
"""Generate and/or execute a reproducible MiniMax H3 style/resolution matrix.

The matrix intentionally keeps the model, text encoder, T8 LoRA, audio path,
seed policy, sampler and prompt contract fixed. Only style, resolution and
steps vary, so the resulting videos can be reviewed as an A/B set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
WORKFLOW_ROOT = ROOT / "projects/destiny-model"
RUN_ROOT = ROOT / "test-runs/2026-08-13-h3-hq-matrix"
TEMPLATE = WORKFLOW_ROOT / "workflow_h3_t8_dualclock_int8_lora_1mp_5s_20steps.json"

STYLE_PROMPTS = {
    "live_action": (
        "integrated_multimodal_description: [Shot 1] Live-action photorealistic cinematic drama, "
        "a young Chinese woman in her early thirties with natural facial proportions, shoulder-length "
        "black hair, a dark waterproof field coat and a small canvas satchel, walking alone through a "
        "rain-soaked old city lane at blue hour. Wet stone pavement reflects warm window light, fine "
        "rain and distant steam create depth, realistic skin and fabric texture, restrained natural "
        "performance. The camera makes one slow stable dolly forward from waist height, then a gentle "
        "side reveal as she looks toward a warm doorway. One continuous shot, grounded human motion, "
        "consistent face, hair, coat and body proportions, no cuts, no readable text, no watermark, "
        "no duplicate person, no extra limbs, no deformed hands.\n\n"
        "overall_soundscape: soft rain on stone, distant footsteps, a low city hum and a quiet breath.\n\n"
        "non_diegetic_music: N/A"
    ),
    "3d_guofeng": (
        "integrated_multimodal_description: [Shot 1] High-end 3D CG Chinese fantasy film, physically "
        "based rendered materials with elegant stylized character design. A red-robed young swordswoman "
        "with a jade hairpin stands on a single ancient stone bridge above a misty mountain stream; "
        "terraced cliffs, pine trees, distant tiled pavilions, floating lanterns and soft sunrise haze "
        "form a layered Chinese landscape. Rich silk folds, carved stone, wet moss and polished metal "
        "are sharply rendered. The camera performs one slow cinematic crane-forward move along the bridge "
        "and gently arcs to reveal the valley, maintaining the same character, bridge and lantern layout. "
        "One continuous shot, coherent 3D geometry, stable anatomy and costume, no cuts, no readable text, "
        "no watermark, no duplicated character, no extra limbs, no melting architecture.\n\n"
        "overall_soundscape: mountain wind, a clear stream, light bell tones from distant lanterns and birds.\n\n"
        "non_diegetic_music: N/A"
    ),
    "ink_cg": (
        "integrated_multimodal_description: [Shot 1] 3D Chinese ink-wash animation with refined guochao "
        "art direction, volumetric mist, layered rice-paper watercolor textures, controlled cel shading "
        "and a deep indigo, cinnabar and jade palette. A young scholar in a pale blue robe walks beside "
        "a white crane through a bamboo valley toward a small arched footbridge; ink-like mountains recede "
        "in the background, red maple leaves drift across the foreground, and the terrain has clear modeled "
        "3D depth beneath the painted surface. The camera makes one slow lateral tracking move followed by "
        "a gentle push toward the bridge, preserving the scholar, crane and bamboo positions. One continuous "
        "shot, deliberate animation, stable silhouette and costume, no cuts, no readable text, no watermark, "
        "no duplicate characters, no extra limbs, no flickering geometry.\n\n"
        "overall_soundscape: bamboo leaves in a light breeze, a distant stream, crane wings and soft wooden chimes.\n\n"
        "non_diegetic_music: N/A"
    ),
}

RESOLUTIONS = {
    "r480": (832, 480),
    "r660": (1088, 608),
    "r1mp": (1344, 768),
}

CASES = [
    {"id": f"{style}_{res}_8s", "style": style, "res": res, "steps": 8, "seed": 20260813}
    for style in STYLE_PROMPTS
    for res in RESOLUTIONS
] + [
    {"id": f"{style}_r1mp_20s", "style": style, "res": "r1mp", "steps": 20, "seed": 20260813}
    for style in STYLE_PROMPTS
]


def http_json(url: str, method: str = "GET", payload: object | None = None, timeout: int = 30) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, method=method, headers=headers)
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def make_workflow(case: dict[str, object]) -> dict[str, object]:
    with TEMPLATE.open(encoding="utf-8") as handle:
        workflow = json.load(handle)
    width, height = RESOLUTIONS[str(case["res"])]
    conditioning = workflow["6"]["inputs"]
    conditioning["prompt"] = STYLE_PROMPTS[str(case["style"])]
    conditioning["width"] = width
    conditioning["height"] = height
    workflow["7"]["inputs"]["steps"] = int(case["steps"])
    workflow["8"]["inputs"]["noise_seed"] = int(case["seed"])
    workflow["13"]["inputs"]["filename_prefix"] = f"h3_hq_{case['id']}"
    return workflow


def write_workflows() -> list[dict[str, object]]:
    workflow_dir = RUN_ROOT / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in CASES:
        path = workflow_dir / f"workflow_{case['id']}.json"
        path.write_text(json.dumps(make_workflow(case), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows.append({**case, "workflow": str(path), "width": RESOLUTIONS[str(case["res"])][0], "height": RESOLUTIONS[str(case["res"])][1]})
    (RUN_ROOT / "matrix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


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


def download_media(base_url: str, media: dict[str, object], target: Path) -> None:
    query = urlencode({
        "filename": str(media["filename"]),
        "subfolder": str(media.get("subfolder", "")),
        "type": str(media.get("type", "output")),
    })
    req = Request(f"{base_url}/view?{query}", headers={"Accept": "video/mp4,video/*,*/*"})
    with urlopen(req, timeout=120) as response, target.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def run_case(base_url: str, row: dict[str, object], poll_seconds: int) -> dict[str, object]:
    workflow = make_workflow(row)
    started = time.time()
    result: dict[str, object] = {**row, "status": "submitted", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        submitted = http_json(f"{base_url}/prompt", method="POST", payload={"prompt": workflow}, timeout=60)
        prompt_id = str(submitted["prompt_id"])
        result["prompt_id"] = prompt_id
        print(f"[{row['id']}] submitted {prompt_id}", flush=True)
        while True:
            history = http_json(f"{base_url}/history/{prompt_id}", timeout=60)
            item = history.get(prompt_id) if isinstance(history, dict) else None
            if item:
                status = item.get("status", {})
                if status.get("completed") or status.get("status_str") == "success":
                    result["status"] = "success"
                    media = find_media(item.get("outputs", {}))
                    if media:
                        target_dir = RUN_ROOT / "outputs"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target = target_dir / f"{row['id']}.mp4"
                        download_media(base_url, media, target)
                        result["output"] = str(target)
                        result["remote_media"] = media
                    else:
                        result["status"] = "no_media"
                    break
                if status.get("status_str") == "error" or item.get("status", {}).get("messages"):
                    messages = item.get("status", {}).get("messages", [])
                    result["status"] = "error"
                    result["error_messages"] = messages[-5:]
                    break
            if time.time() - started > 8 * 3600:
                result["status"] = "timeout"
                break
            time.sleep(poll_seconds)
        result["elapsed_seconds"] = round(time.time() - started, 2)
    except (HTTPError, URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        result["status"] = "client_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["elapsed_seconds"] = round(time.time() - started, 2)
    print(f"[{row['id']}] {result['status']} elapsed={result.get('elapsed_seconds')}s output={result.get('output', '')}", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("H3_BASE_URL", "http://192.168.1.6:8189"))
    parser.add_argument("--write-only", action="store_true")
    parser.add_argument("--cases", help="comma-separated case IDs; default is all 12")
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()
    rows = write_workflows()
    if args.write_only:
        print(f"wrote {len(rows)} workflows under {RUN_ROOT / 'workflows'}")
        return 0
    selected = {item.strip() for item in args.cases.split(",")} if args.cases else None
    selected_rows = [row for row in rows if selected is None or row["id"] in selected]
    results_path = RUN_ROOT / "results.jsonl"
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    for row in selected_rows:
        result = run_case(args.base_url.rstrip("/"), row, args.poll_seconds)
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"completed {len(selected_rows)} cases; results={results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
