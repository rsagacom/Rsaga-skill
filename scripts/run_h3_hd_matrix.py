#!/usr/bin/env python3
"""Run a resumable MiniMax H3 high-definition multi-style matrix.

The model, VAE, T8 LoRA, dual-clock sampler, seed and audio path stay fixed.
Only style, resolution and (in the optional second phase) steps change.
Cases are ordered from low to high resolution so the run itself is a useful
capacity record for the RTX 3060 12GB machine.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
WORKFLOW_ROOT = ROOT / "projects/destiny-model"
RUN_ROOT = ROOT / "test-runs/2026-08-13-h3-hd-matrix"
TEMPLATE = WORKFLOW_ROOT / "workflow_h3_t8_dualclock_int8_lora_1mp_5s_20steps.json"


STYLE_PROMPTS = {
    "live_action": (
        "integrated_multimodal_description: [Shot 1] Live-action photorealistic cinematic drama, "
        "a young Chinese woman in her early thirties with natural facial proportions, shoulder-length "
        "black hair, a dark waterproof field coat and a small canvas satchel, walking alone through a "
        "rain-soaked old city lane at blue hour. Wet stone pavement reflects warm window light, fine "
        "rain and distant steam create depth, realistic skin, hair and fabric texture, restrained natural "
        "performance. The camera makes one slow stable dolly forward from waist height, then a gentle side "
        "reveal as she looks toward a warm doorway. One continuous shot, grounded human motion, consistent "
        "face, hair, coat and body proportions, no cuts, no readable text, no watermark, no duplicate person, "
        "no extra limbs, no deformed hands.\n\n"
        "overall_soundscape: soft rain on stone, distant footsteps, a low city hum and a quiet breath.\n\n"
        "non_diegetic_music: N/A"
    ),
    "3d_guofeng": (
        "integrated_multimodal_description: [Shot 1] High-end 3D CG Chinese fantasy film, physically "
        "based rendered materials with elegant stylized character design. A red-robed young swordswoman "
        "with a jade hairpin stands on a single ancient stone bridge above a misty mountain stream; "
        "terraced cliffs, pine trees, distant tiled pavilions, floating lanterns and soft sunrise haze "
        "form a layered Chinese landscape. Rich silk folds, carved stone, wet moss and polished metal are "
        "sharply rendered. The camera performs one slow cinematic crane-forward move along the bridge and "
        "gently arcs to reveal the valley, maintaining the same character, bridge and lantern layout. One "
        "continuous shot, coherent 3D geometry, stable anatomy and costume, no cuts, no readable text, no "
        "watermark, no duplicated character, no extra limbs, no melting architecture.\n\n"
        "overall_soundscape: mountain wind, a clear stream, light bell tones from distant lanterns and birds.\n\n"
        "non_diegetic_music: N/A"
    ),
    "3d_xianxia": (
        "integrated_multimodal_description: [Shot 1] Premium 3D CG xianxia animation with cinematic "
        "lighting and detailed physically based materials. A young male cultivator in a pale ivory robe and "
        "dark teal sash walks across a vast suspended jade platform above clouds; a red sun, distant floating "
        "mountain monasteries, bronze bells and slow circling cranes establish depth. The camera makes a slow "
        "forward crane move and a restrained orbit around the hero while the robe and hair respond naturally to "
        "wind. Preserve the platform edges, sun direction, monastery silhouettes and character proportions in "
        "one continuous shot. No cuts, no readable text, no watermark, no duplicate character, no extra limbs, "
        "no melted buildings, no flickering geometry.\n\n"
        "overall_soundscape: high mountain wind, distant bronze bells, soft cloth movement and faraway cranes.\n\n"
        "non_diegetic_music: N/A"
    ),
    "anime_cel": (
        "integrated_multimodal_description: [Shot 1] High-detail cinematic 2D anime film with clean cel "
        "shading, carefully drawn line art and controlled painterly backgrounds. A teenage girl with short "
        "black hair, a yellow raincoat and a red umbrella walks beside a canal in a quiet old Chinese town at "
        "twilight. Reflected lanterns ripple in the water, bicycles and tiled roofs form a stable background, "
        "and a few paper charms sway from one eave. The camera tracks gently beside her then pushes toward a "
        "small bridge, preserving the same face, umbrella, canal and architecture. Fluid restrained animation, "
        "one continuous shot, no cuts, no readable text, no watermark, no duplicate person, no extra limbs, no "
        "line-art melting or background flicker.\n\n"
        "overall_soundscape: light rain, bicycle chain clicks, canal water and a distant evening chime.\n\n"
        "non_diegetic_music: N/A"
    ),
    "ink_cg": (
        "integrated_multimodal_description: [Shot 1] 3D Chinese ink-wash animation with refined guochao "
        "art direction, volumetric mist, layered rice-paper watercolor textures, controlled cel shading and a "
        "deep indigo, cinnabar and jade palette. A young scholar in a pale blue robe walks beside a white "
        "crane through a bamboo valley toward a small arched footbridge; ink-like mountains recede in the "
        "background, red maple leaves drift across the foreground, and the terrain has clear modeled 3D depth "
        "beneath the painted surface. The camera makes one slow lateral tracking move followed by a gentle "
        "push toward the bridge, preserving the scholar, crane and bamboo positions. One continuous shot, "
        "deliberate animation, stable silhouette and costume, no cuts, no readable text, no watermark, no "
        "duplicate characters, no extra limbs, no flickering geometry.\n\n"
        "overall_soundscape: bamboo leaves in a light breeze, a distant stream, crane wings and soft wooden chimes.\n\n"
        "non_diegetic_music: N/A"
    ),
}


# All dimensions are multiples of 32 and preserve a 16:9-ish H3 landscape.
RESOLUTIONS = {
    "r360": (640, 384),
    "r480": (832, 480),
    "r540": (960, 544),
    "r660": (1088, 608),
    "r800": (1216, 672),
    "r1mp": (1344, 768),
}


def make_cases(include_20_steps: bool) -> list[dict[str, object]]:
    cases = [
        {
            "id": f"{res}_{style}_8s",
            "style": style,
            "res": res,
            "steps": 8,
            "seed": 20260813,
        }
        for res in RESOLUTIONS
        for style in STYLE_PROMPTS
    ]
    if include_20_steps:
        cases.extend(
            {
                "id": f"r1mp_{style}_20s",
                "style": style,
                "res": "r1mp",
                "steps": 20,
                "seed": 20260813,
            }
            for style in STYLE_PROMPTS
        )
    return cases


def http_json(url: str, method: str = "GET", payload: object | None = None, timeout: int = 60) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def make_workflow(case: dict[str, object]) -> dict[str, object]:
    workflow = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    width, height = RESOLUTIONS[str(case["res"])]
    workflow["6"]["inputs"].update(
        {"prompt": STYLE_PROMPTS[str(case["style"])], "width": width, "height": height}
    )
    workflow["6"]["inputs"]["length"] = 124
    workflow["7"]["inputs"]["steps"] = int(case["steps"])
    workflow["8"]["inputs"]["noise_seed"] = int(case["seed"])
    workflow["13"]["inputs"]["filename_prefix"] = f"h3_hd_{case['id']}"
    return workflow


def write_workflows(cases: list[dict[str, object]]) -> None:
    workflow_dir = RUN_ROOT / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        path = workflow_dir / f"workflow_{case['id']}.json"
        path.write_text(json.dumps(make_workflow(case), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        width, height = RESOLUTIONS[str(case["res"])]
        rows.append({**case, "workflow": str(path), "width": width, "height": height})
    (RUN_ROOT / "matrix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    with urlopen(Request(f"{base_url}/view?{query}", headers={"Accept": "video/mp4,video/*,*/*"}), timeout=180) as response:
        with target.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)


def run_case(base_url: str, row: dict[str, object], poll_seconds: int) -> dict[str, object]:
    started = time.time()
    result: dict[str, object] = {**row, "status": "submitted", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        submitted = http_json(f"{base_url}/prompt", method="POST", payload={"prompt": make_workflow(row)})
        prompt_id = str(submitted["prompt_id"])
        result["prompt_id"] = prompt_id
        print(f"[{row['id']}] submitted {prompt_id}", flush=True)
        while True:
            item = http_json(f"{base_url}/history/{prompt_id}").get(prompt_id)
            if item:
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    result["status"] = "error"
                    result["error_messages"] = status.get("messages", [])[-5:]
                    break
                if status.get("completed") or status.get("status_str") == "success":
                    media = find_media(item.get("outputs", {}))
                    if media:
                        target = RUN_ROOT / "outputs" / f"{row['id']}.mp4"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        download_media(base_url, media, target)
                        result.update({"status": "success", "output": str(target), "remote_media": media})
                    else:
                        result["status"] = "no_media"
                    break
            if time.time() - started > 8 * 3600:
                result["status"] = "timeout"
                break
            time.sleep(poll_seconds)
    except (HTTPError, URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        result.update({"status": "client_error", "error": f"{type(exc).__name__}: {exc}"})
    result["elapsed_seconds"] = round(time.time() - started, 2)
    print(f"[{row['id']}] {result['status']} elapsed={result['elapsed_seconds']}s", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("H3_BASE_URL", "http://192.168.1.6:8189"))
    parser.add_argument("--include-20-steps", action="store_true")
    parser.add_argument("--write-only", action="store_true")
    parser.add_argument("--cases", help="comma-separated case IDs")
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()
    cases = make_cases(args.include_20_steps)
    write_workflows(cases)
    if args.write_only:
        print(f"wrote {len(cases)} workflows under {RUN_ROOT / 'workflows'}")
        return 0
    selected = {item.strip() for item in args.cases.split(",")} if args.cases else None
    selected_cases = [case for case in cases if selected is None or case["id"] in selected]
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    results_path = RUN_ROOT / "results.jsonl"
    completed = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if item.get("status") == "success":
                    completed[str(item["id"])] = item
    pending = [case for case in selected_cases if str(case["id"]) not in completed]
    print(json.dumps({"total": len(selected_cases), "already_success": len(completed), "pending": len(pending)}, ensure_ascii=False), flush=True)
    for row in pending:
        result = run_case(args.base_url.rstrip("/"), row, args.poll_seconds)
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"completed {len(pending)} new cases; results={results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
