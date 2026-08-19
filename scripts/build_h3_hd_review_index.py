#!/usr/bin/env python3
"""Build a human-reviewable index for the H3 HD matrix outputs."""

from __future__ import annotations

import json
from pathlib import Path


BASE = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix")
MANIFEST = BASE / "review_manifest.json"
INDEX = BASE / "review_index.md"

RES_LABELS = {
    "r360": "640×384",
    "r480": "832×480",
    "r540": "960×544",
    "r660": "1088×608",
    "r800": "1216×672",
    "r1mp": "1344×768（约 1MP）",
}
STYLE_LABELS = {
    "live_action": "真人写实",
    "3d_guofeng": "3D 国风",
    "3d_xianxia": "3D 仙侠",
    "anime_cel": "赛璐璐动画",
    "ink_cg": "水墨 CG",
}


def link(path: Path, label: str) -> str:
    return f"[{label}]({path})"


def main() -> None:
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows.sort(key=lambda row: (list(RES_LABELS).index(row["res"]), row["steps"], row["style"]))

    passed = sum(row["verification"]["status"] == "passed" for row in rows)
    elapsed = sum(row["elapsed_seconds"] for row in rows)
    lines = [
        "# MiniMax H3 高清多风格多分辨率测试审阅索引",
        "",
        "> 生成批次：2026-08-13～14；底座：非剪枝 INT8 FL2VA + INT4 Qwen 文本编码器 + T8 Turbo LoRA + Sage/DualClock；统一 seed `20260813`。",
        "> 本页只记录可复核产物和机器验收结果；艺术质量、闪烁、人物一致性和镜头可用性请以视频/抽帧人工审阅为准。",
        "",
        f"- 样本：**{len(rows)}**；完整解码通过：**{passed}/{len(rows)}**；累计 GPU 运行时间约 **{elapsed / 3600:.2f} 小时**。",
        "- 每个视频：5.167 秒、24fps、124 帧、H.264；T2VA 原生音频：AAC 32kHz 双声道。",
        f"- 原始验收清单：{link(MANIFEST, 'review_manifest.json')}",
        f"- 中帧风格横向接触表：{', '.join(link(BASE / 'contact-sheets' / name, name) for name in ['r360_mid.png', 'r660_mid.png', 'r1mp_8s_mid.png', 'r1mp_20s_mid.png'])}",
        "",
        "## 先看结论样本",
        "",
        "| 目的 | 建议先审阅 |",
        "| --- | --- |",
        "| 最低可运行基线 | `r360` 五种风格，8 steps |",
        "| 生产候选起点 | `r660` 五种风格，8 steps |",
        "| 画质上限候选 | `r1mp` 五种风格，8 steps |",
        "| 高步数对照 | `r1mp` 五种风格，20 steps |",
        "| 真人 / 3D 国风重点 | `r1mp_live_action_8s`、`r1mp_3d_guofeng_8s` |",
        "",
        "## 逐样本审阅",
        "",
    ]

    current_res = None
    for row in rows:
        if row["res"] != current_res:
            current_res = row["res"]
            lines.extend([f"### {current_res} · {RES_LABELS[current_res]}", "", "| 样本 | 风格 | steps | 耗时 | 机器验收 | 视频 | 抽帧 | 工作流 |", "| --- | --- | ---: | ---: | --- | --- | --- | --- |"])
        ver = row["verification"]
        media = ver["media"]
        frame_links = " / ".join(link(Path(p), f"F{i}") for i, p in enumerate(ver["frames"], 1))
        workflow = BASE / "workflows" / f"workflow_{row['id']}.json"
        lines.append(
            "| {id} | {style} | {steps} | {elapsed:.2f}s | {status} · {w}×{h} · {frames}帧 · {duration:.3f}s | {video} | {frames_links} | {workflow} |".format(
                id=row["id"],
                style=STYLE_LABELS.get(row["style"], row["style"]),
                steps=row["steps"],
                elapsed=row["elapsed_seconds"],
                status=ver["status"],
                w=media["video_width"],
                h=media["video_height"],
                frames=media["video_frames"],
                duration=media["duration_seconds"],
                video=link(Path(row["output"]), "MP4"),
                frames_links=frame_links,
                workflow=link(workflow, "JSON"),
            )
        )

    lines.extend([
        "",
        "## 审阅建议",
        "",
        "1. 先审 `r1mp` 的 8 steps 与 20 steps：比较细节、闪烁、面部/手部、运动稳定性；不要只按耗时判断。",
        "2. 再审同一风格从 `r360 → r480 → r540 → r660 → r800 → r1mp` 的画面细节增益，确认高分辨率是否值得付出时间。",
        "3. 重点查看每个样本的 F1/F2/F3：分别用于首帧、中段、尾帧的快速检查；完整视频用于检查运动和音画。",
        "4. 本批次是统一工作流与统一 seed 的工程基线，不代表每种风格的最终提示词已调优到最佳。",
        "",
    ])
    INDEX.write_text("\n".join(lines), encoding="utf-8")
    print(INDEX)


if __name__ == "__main__":
    main()
