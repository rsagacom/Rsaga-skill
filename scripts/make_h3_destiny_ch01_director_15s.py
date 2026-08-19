#!/usr/bin/env python3
"""Make the selected-model Director 15s first-chapter dialogue workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROMPTS = [
    """integrated_multimodal_description: [Destiny Model chapter 1, Director segment 1] Premium Chinese 3D CG animation, stylized-realistic cinematic game cutscene, physically based materials and warm late-afternoon global illumination. Qiyuan Siyuan, a Chinese man aged twenty-five, slim build, short black hair with a natural fringe, thin metal glasses, tired eyes, beige casual jacket over a deep blue V-neck shirt, dark trousers and an old black backpack, stands beside a condemned urban district and an abandoned stone bridge. Broken brick, exposed rebar, dry weeds and dust fill the scene. Begin with a stable wide shot and a very slow push toward him. In the deep shadow beneath the bridge, a woman in a dark trench coat takes one step into the shadow and disappears without a cut. Qiyuan whispers in Mandarin: “刚才那里，明明有人。” Preserve face, glasses, fringe, jacket, blue shirt, backpack, bridge geometry, light direction and acoustic space. No live action, no flat 2D anime, no cuts, no text, no watermark, no duplicate person, no extra limbs, no melted face. overall_soundscape: dry weeds, distant demolition machinery, low city hum, a faint footstep and restrained electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: [Destiny Model chapter 1, Director segment 2] Continue exactly from the previous segment ending without a hard cut. Premium Chinese 3D CG animation, same Qiyuan Siyuan, same glasses, short black hair, beige jacket, blue shirt, backpack, abandoned stone bridge and late-afternoon light. Move into a stable medium shot as he turns a few degrees toward the empty bridge shadow and takes one restrained step. Keep eye spacing, nose bridge, lips, jawline, clothing, bridge arches, weeds and light direction consistent. He says in Mandarin: “可刚才，明明有人站在那里。” Natural restrained mouth motion, continuous soundscape, no new person, no location jump, no face replacement, no warped glasses, no text or watermark. overall_soundscape: continue the same machinery, wind, city hum, footstep and electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: [Destiny Model chapter 1, Director segment 3] Continue the same shot and location into a controlled frontal medium close-up of Qiyuan Siyuan. Premium Chinese 3D CG game cinematic, same twenty-five-year-old Chinese man, same face, thin metal glasses, hair fringe, beige jacket, deep blue V-neck and backpack straps, with the same bridge arch and faint blue-green circuit/talisman glow behind him. Hold the face large enough to inspect; allow only a small breath and a subtle eye movement toward the shadow. He finishes the quiet Mandarin line: “这座桥……在看着我。” Keep exact eye shape, nose, lips, jawline and facial topology readable through the final frame, with natural lip motion and continuous ambience. No cuts, no face melting, no asymmetrical eyes, no extra limbs, no text, no subtitles, no watermark. overall_soundscape: the same dry weeds, distant machinery, low city hum and restrained electrical pulse. non_diegetic_music: N/A""",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workflow = json.loads(args.base.read_text(encoding="utf-8"))
    timeline = json.loads(workflow["8"]["inputs"]["timeline_data"])
    for segment, prompt in zip(timeline["segments"], PROMPTS):
        segment["prompt"] = prompt
    workflow["8"]["inputs"]["timeline_data"] = json.dumps(timeline, ensure_ascii=False, separators=(",", ":"))
    workflow["10"]["inputs"]["filename_prefix"] = "h3_destiny_ch01_director_15s_640x384"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
