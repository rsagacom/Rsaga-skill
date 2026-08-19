#!/usr/bin/env python3
"""Build the selected-model 15-second AIMixer Director gate.

The source workflow already contains the officially exercised Director
timeline and media export chain.  This generator changes only the model path:
the validated pruned INT8 base is passed through the dedicated H3 Turbo LoRA
node before Director.  It deliberately keeps Director's 8-step res_multistep
settings so this is a continuity-plugin test, not a disguised 4-step Turbo
sampler benchmark.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROMPTS = [
    """integrated_multimodal_description: [Shot 1] Premium Chinese 3D CG animation, stylized-realistic game cinematic, physically based materials and warm sunset global illumination. Qiyuan Siyuan, a Chinese man in his early thirties with short black hair, charcoal-gray jacket, pale shirt, dark trousers and worn black backpack, stands on an abandoned stone bridge in a condemned urban district. Blue-green circuit and ancient talisman markings glow on the bridge wall. Begin with a stable wide shot, then make a very slow push in. He says in Mandarin with restrained natural diction: “命运模型启动了。” Keep the same face, hair, clothing, bridge geometry and light direction. No cuts, no text, no watermark, no duplicate person, no extra limbs, no face melting. overall_soundscape: distant demolition machinery, dry weeds, low city hum and restrained electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: Continue the exact same shot from the previous ending. Premium Chinese 3D CG animation, same Qiyuan Siyuan, same charcoal jacket, pale shirt, backpack, abandoned stone bridge and blue-green markings. Move from the previous wide framing into a stable medium-long shot as he takes one restrained step toward the glowing wall and turns slightly. Preserve identity, facial proportions, eye spacing, nose, lips, jawline, costume, bridge layout and sunset direction. He continues the Mandarin line: “它记得我，也记得这座城。” Keep natural mouth motion, continuous soundscape and no hard audio cut. No scene change, no face replacement, no extra limbs, no deformed hands, no flicker, no text or watermark. overall_soundscape: continue the same machinery, wind, city hum and electrical resonance. non_diegetic_music: N/A""",
    """integrated_multimodal_description: Continue without a cut into a controlled medium close-up and slight profile of the same Qiyuan Siyuan on the same abandoned stone bridge. Premium Chinese 3D CG game cinematic, stable facial geometry, same hair and clothing, same wall markings and warm sunset. He turns only a few degrees toward the markings and finishes the quiet Mandarin line: “但它没有告诉我，代价是什么。” Keep eye shape, nose bridge, lips and jawline readable through the final frame, with restrained natural mouth motion and continuous ambience. No new person, no location jump, no melted face, no asymmetrical eyes, no extra limbs, no text or watermark. overall_soundscape: the same dry weeds, distant machinery, low city hum and faint electrical pulse. non_diegetic_music: N/A""",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    workflow = json.loads(args.base.read_text())
    workflow.pop("6", None)
    workflow["5"] = {
        "class_type": "MiniMaxH3TurboLoRA",
        "inputs": {
            "model": ["1", 0],
            "lora_name": "minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors",
            "strength": 1.0,
            "low_vram": False,
        },
    }
    workflow["8"]["inputs"]["model"] = ["5", 0]
    timeline = json.loads(workflow["8"]["inputs"]["timeline_data"])
    for segment, prompt in zip(timeline["segments"], PROMPTS):
        segment["prompt"] = prompt
    workflow["8"]["inputs"]["timeline_data"] = json.dumps(
        timeline, ensure_ascii=False, separators=(",", ":")
    )
    workflow["10"]["inputs"]["filename_prefix"] = (
        "h3_pruned_int8_drbaph_director_15s_640x384"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
