#!/usr/bin/env python3
"""Build a 15-second Motion Context chain for Destiny Model chapter 1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.make_h3_pruned_lora_motion_context_15s import make_workflow


PROMPTS = [
    """integrated_multimodal_description: [Destiny Model chapter 1, segment 1] Premium Chinese 3D CG animation, stylized-realistic cinematic game cutscene, physically based materials and warm late-afternoon global illumination. Qiyuan Siyuan, a Chinese man aged twenty-five, slim build, short black hair with a natural fringe, thin metal glasses, tired eyes, beige casual jacket over a deep blue V-neck shirt, dark trousers and an old black backpack, stands beside a condemned urban district and an abandoned stone bridge. Broken brick, exposed rebar, dry weeds and dust fill the scene. In the deep shadow beneath the bridge, a woman in a dark trench coat is seen only from behind; she takes one step into the shadow and disappears without a cut. Qiyuan slowly turns his head toward the empty shadow. Keep a stable wide-to-medium move, one continuous shot, readable facial geometry and the same bridge. He whispers in Mandarin: “刚才那里，明明有人。” Natural restrained mouth motion, no subtitles. Preserve face, glasses, fringe, jacket, blue shirt, backpack, bridge geometry, light direction and acoustic space. No live action, no flat 2D anime, no cuts, no text, no watermark, no duplicate person, no extra limbs, no melted face, no warped glasses, no sudden zoom. overall_soundscape: dry weeds, distant demolition machinery, low city hum, a faint footstep and restrained electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: [Destiny Model chapter 1, segment 2] Continue from the exact previous ending through Motion Context. Same Qiyuan Siyuan, same thin metal glasses, hair, beige jacket, deep blue shirt, black backpack and abandoned stone bridge. The woman is gone; do not introduce a new visible character. Qiyuan takes one cautious step toward the shadow and the stone wall. A few blue-green circuit traces and ancient talisman markings wake up on the wall, reflected softly in his glasses. Hold a controlled medium shot and keep face geometry, eye spacing, nose, lips, jawline and costume consistent. He says in Mandarin: “她去哪了？” Natural restrained speech, continuous ambience, no cut and no scene change. No face replacement, no extra limbs, no deformed hands, no flicker, no text or watermark. overall_soundscape: continue the same wind, demolition machinery, city hum and faint electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: [Destiny Model chapter 1, segment 3] Continue from the exact previous latent and audio context. Same Qiyuan, same bridge, same light direction and same blue-green markings. Ease into a medium close-up as the markings pulse once across the old stone. Qiyuan looks toward the dark arch; the camera remains calm and the face remains readable. A deep metallic male voice comes from the shadow, with no visible body, saying in Mandarin: “租客。” Qiyuan does not change identity or age. Preserve thin glasses, fringe, eye shape, nose bridge, lips, jawline and jacket topology. Keep the voice and electrical ambience continuous. No new person, no mouth distortion, no melted architecture, no subtitles, no text or watermark. overall_soundscape: the same wind and machinery under a restrained electrical resonance; voice is diegetic and centered in the bridge shadow. non_diegetic_music: N/A""",
    """integrated_multimodal_description: [Destiny Model chapter 1, segment 4] Continue from the exact previous ending through Motion Context. Same Qiyuan Siyuan on the same abandoned bridge. Hold a controlled three-quarter close-up with shoulders visible; keep the face, glasses and clothing stable. He raises his left hand slightly and sees a faint darkening along the forearm, subtle and readable rather than grotesque. The metallic voice remains in the shadow: “想要答案，就付出代价。” Qiyuan steadies his breath and answers in Mandarin: “什么代价？” Maintain natural restrained mouth motion, continuous audio, fixed light direction and no hard cut. No gore, no face replacement, no extra limbs, no flicker, no text or watermark. overall_soundscape: same wind, distant demolition machinery, low city hum and restrained electrical pulse. non_diegetic_music: N/A""",
]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument(
        "--context-dir",
        default="h3_destiny_ch01_motion_context_15s_640x384",
        help="Unique remote output directory for Save/LoadLatent files.",
    )
    args = parser.parse_args()
    if args.steps < 1 or args.steps > 20:
        parser.error("--steps must be between 1 and 20")
    args.output.mkdir(parents=True, exist_ok=True)
    for index, prompt in enumerate(PROMPTS):
        workflow = make_workflow(index, args.steps, args.width, args.height, args.context_dir)
        workflow["6"]["inputs"]["prompt"] = prompt
        workflow["15"]["inputs"]["filename_prefix"] = f"h3_destiny_ch01_motion_context_15s_seg{index + 1}_{args.width}x{args.height}_{{{{ASSET_ID}}}}"
        # SaveLatent 与后续 LoadLatent 必须共享同一个新目录；不能复用
        # 其他测试批次的 latent，否则长视频会出现“看似接力、实际串片”。
        workflow["16"]["inputs"]["filename_prefix"] = f"{args.context_dir}/clip"
        if "17" in workflow:
            workflow["17"]["inputs"]["latent_path"] = args.context_dir
        path = args.output / f"workflow_destiny_ch01_motion_context_15s_seg{index + 1}_{args.width}x{args.height}_{args.steps}steps.json"
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
