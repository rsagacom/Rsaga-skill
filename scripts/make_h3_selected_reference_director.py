#!/usr/bin/env python3
"""Build selected-model Director R2V workflows for the 3DCG reference gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REFERENCE_IMAGES = [
    "h3_3dcg_character_front.png",
    "h3_3dcg_character_side.png",
    "h3_3dcg_character_back.png",
    "h3_3dcg_character_face_closeup.png",
]

IDENTITY = (
    "subject_definitions:\n"
    "<Picture 1> is the 3D CG front full-body reference of one adult Chinese woman, authoritative for face, proportions, vermilion-red gold-embroidered hanfu, ivory inner robe, dark teal sash and black lacquer sword scabbard.\n"
    "<Picture 2> is the strict 3D CG side-profile reference of the same woman, confirming facial profile, hair silhouette, robe layers and scabbard placement.\n"
    "<Picture 3> is the 3D CG back-view reference of the same woman, confirming tied hair, jade hairpin, robe silhouette, sash and scabbard placement.\n"
    "<Picture 4> is the high-detail 3D CG face close-up of the same woman, authoritative for facial geometry, eye spacing, nose bridge, lips, jawline, skin shading and hairline.\n"
    "<Subject 1> is one single adult 3D CG Chinese woman defined by the four pictures. Never render the reference panels or their studio background.\n\n"
    "identity_lock:\n"
    "The reference pack is an identity constraint, not a scene to copy. Preserve one refined oval face, almond-shaped dark eyes, eye spacing, nose bridge, lips, jawline, black half-up hair, green jade hairpin, vermilion-red embroidered hanfu, ivory collar, dark teal sash and black lacquer sword scabbard. Do not convert to live action or 2D cel shading. Do not create a second person.\n\n"
    "negative_constraints:\n"
    "No reference-sheet panels, no studio backdrop, no readable text, no subtitles, no captions, no logo, no watermark, no face replacement, no age change, no hairstyle change, no costume change, no duplicate person, no extra limbs, no facial melting, no warped eyes, no asymmetrical mouth, no plastic mask, no hard cut.\n"
)

PROMPTS = [
    IDENTITY
    + "summary:\nHigh-end cinematic 3D CG Chinese fantasy game render on one old stone bridge at warm sunset. Keep physically based materials, restrained volumetric mist, warm lanterns and distant mountains in one stable spatial layout. This is a silent visual performance: the woman stands calmly, breathes naturally and makes one small controlled head movement. Begin with a stable wide-to-medium-long composition that keeps the full character readable. No speech and no mouth exaggeration; preserve a clean face for identity evaluation.\n\n"
    "overall_soundscape:\nSoft continuous wind through mountain pines, distant bronze bell resonance, faint lantern-chain movement and restrained cloth rustle. No voice, no music, no readable text.",
    IDENTITY
    + "summary:\nContinue from the exact previous ending through the same rendered stone bridge scene. Move gradually into a stable medium shot as the same woman takes one restrained step toward the warm lantern and turns slightly. Preserve the same lens axis, bridge geometry, mist, lighting, face, hair, jade hairpin, red robe, teal sash and sword scabbard. This is silent acting with a small natural breath and no exaggerated mouth movement. No cut, no camera jump, no new person and no readable text.\n\n"
    "overall_soundscape:\nContinue the same wind, distant bell, lantern-chain movement and cloth rustle with no voice or music.",
    IDENTITY
    + "summary:\nContinue without a cut into a controlled three-quarter medium close-up and slight profile of the same woman on the same bridge. Hold the eyes, nose bridge, lips, jawline, hairline and jade hairpin readable through the final frame. The woman makes one small natural breath and looks toward the glowing lantern; keep the red embroidered robe, ivory collar, teal sash and black scabbard unchanged. Silent visual performance, no speech, no captions, no readable text, no face replacement and no sudden zoom.\n\n"
    "overall_soundscape:\nThe same continuous wind, bell resonance, lantern-chain movement and cloth rustle; no voice and no music.",
]


def group_node(prompt: str, first: bool, image_ids: list[str]) -> dict[str, object]:
    inputs: dict[str, object] = {"prompt": prompt, "duration_sec": 5.0}
    if first:
        for slot, node_id in enumerate(image_ids):
            inputs[f"ref_images.ref_image_{slot}"] = [node_id, 0]
    return {"class_type": "MiniMaxH3DirectorGroupReferenceToVideo", "inputs": inputs}


def build(source: Path, output: Path, seconds: int, seed: int) -> None:
    if seconds not in (5, 15):
        raise ValueError("seconds must be 5 or 15")
    workflow = json.loads(source.read_text())
    segment_count = 1 if seconds == 5 else 3
    frame_count = 124 * segment_count
    director_inputs = workflow["8"]["inputs"]
    director_inputs.update(
        {
            "task_type": "r2v — 参考主体生视频(Reference to Video)",
            "global_prompt": "",
            "seed": seed,
            "width": 640,
            "height": 384,
            "ref_max_size": 640,
            "total_frames": frame_count,
            "steps": 8,
            "sampler": "res_multistep",
            "scheduler": "simple",
            "shift_video": 12.0,
            "shift_audio": 3.0,
            "clear_vram_between_segments": True,
            "export_source_images": False,
        }
    )
    timeline = {
        "version": 4,
        "editMode": "segment",
        "timelineMode": "prompt_batch",
        "totalFrames": frame_count,
        "frameRate": 24.0,
        "width": 640,
        "height": 384,
        "refMaxSize": 640,
        "output": {
            "mode": "fixed",
            "longEdge": 640,
            "width": 640,
            "height": 384,
            "maxExportFrames": 0,
            "exportMode": "all",
            "audioMode": "generate",
            "continuityEnabled": segment_count > 1,
            "continuityOverlapFrames": 22,
        },
        "videoClips": [],
        "video": {"fileName": "", "videoFile": "", "subfolder": "", "type": "input", "frames": [], "frameMap": []},
        "global": {"taskType": "r2v — 参考主体生视频(Reference to Video)", "prompt": "", "refs": [], "referenceVideo": {}, "continuousReference": False, "genImage": {"imageFile": ""}},
        "segments": [],
        "gen": {"defaultFrameCount": 124},
        "runSelectEnabled": False,
        "runSelection": [],
    }
    for index in range(segment_count):
        timeline["segments"].append(
            {
                "id": f"selected-ref-{seconds}s-s{index + 1}",
                "start": 124 * index,
                "length": 124,
                "frameCount": 124,
                "durationSec": 124 / 24.0,
                "prompt": PROMPTS[index],
                "taskType": "r2v — 参考主体生视频(Reference to Video)",
                "refs": [],
                "referenceVideo": {},
                "genImage": {"imageFile": ""},
                "negativePrompt": "",
                "continuityFromPrev": index > 0,
            }
        )
    director_inputs["timeline_data"] = json.dumps(timeline, ensure_ascii=False, separators=(",", ":"))

    image_ids = ["11", "12", "13", "14"]
    for node_id, image_name in zip(image_ids, REFERENCE_IMAGES):
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    group_ids = [str(20 + index) for index in range(segment_count)]
    for index, group_id in enumerate(group_ids):
        workflow[group_id] = group_node(PROMPTS[index], index == 0, image_ids)
    workflow["30"] = {"class_type": "MiniMaxH3DirectorGroupsCombine", "inputs": {f"groups.group_{index}": [group_id, 0] for index, group_id in enumerate(group_ids)}}
    director_inputs["r2v_groups"] = ["30", 0]
    workflow["10"]["inputs"]["filename_prefix"] = f"h3_selected_3dcg_reference_facepack_director_{seconds}s_640x384"
    workflow["10"]["inputs"]["format"] = "mp4"
    workflow["10"]["inputs"]["codec"] = "auto"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, choices=(5, 15), required=True)
    parser.add_argument("--seed", type=int, default=20260817051)
    args = parser.parse_args()
    build(args.source, args.output, args.seconds, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
