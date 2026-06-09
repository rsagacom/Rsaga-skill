#!/usr/bin/env python3
"""批量生成画格入口脚本

用法:
    python3 scripts/generate_panels.py projects/example/storyboard_ch3.py
    python3 scripts/generate_panels.py storyboard.py --dry-run
    python3 scripts/generate_panels.py storyboard.py --resume
    python3 scripts/generate_panels.py storyboard.py --force
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comic_engine.config import load_config
from comic_engine.generator import generate_panels, estimate_resources
from comic_engine.utils import load_storyboard


def main():
    parser = argparse.ArgumentParser(description="批量生成漫画画格")
    parser.add_argument("storyboard", help="分镜脚本文件路径（Python 模块，需包含 PANELS 列表）")
    parser.add_argument("--config", "-c", default=None, help="配置文件路径")
    parser.add_argument("--output", "-o", default=None, help="输出目录（默认 storyboard 同目录 panels/）")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="断点续传：跳过已生成的画格（默认启用）")
    parser.add_argument("--no-resume", action="store_false", dest="resume",
                        help="禁用断点续传，从头生成")
    parser.add_argument("--force", "-f", action="store_true", default=False,
                        help="强制覆盖已有画格")
    parser.add_argument("--dry-run", "-n", action="store_true", default=False,
                        help="仅估算，不实际生成")
    parser.add_argument("--sleep", "-s", type=float, default=6.0,
                        help="每格间隔秒数（默认 6.0）")
    args = parser.parse_args()

    panels = load_storyboard(args.storyboard)

    if args.output:
        output_dir = Path(args.output)
    else:
        storyboard_path = Path(args.storyboard)
        output_dir = storyboard_path.parent / "panels"

    # Dry-run 模式（不需要 config.yaml）
    if args.dry_run:
        est = estimate_resources(panels, args.sleep)
        print(f"=== DRY RUN ===")
        print(f"画格数:    {est['panel_count']}")
        print(f"间隔:      {est['sleep_seconds']} 秒/格")
        print(f"预估耗时:  {est['estimated_minutes']} 分钟 ({est['estimated_seconds']} 秒)")
        print(f"费用参考:  {est['cost_note']}")
        print(f"输出目录:  {output_dir.resolve()}")
        return

    config = load_config(args.config)

    print(f"生成 {len(panels)} 格画格")
    print(f"输出目录: {output_dir.resolve()}")

    if args.force:
        print("模式: 强制覆盖")
    elif args.resume:
        print("模式: 断点续传 (--no-resume 禁用)")
    print()

    results = generate_panels(
        panels, config, output_dir,
        sleep_seconds=args.sleep,
        resume=args.resume,
        force=args.force,
    )

    ok = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    fail = len(results) - ok - skipped

    print(f"\n完成: {ok} 成功, {skipped} 跳过, {fail} 失败")

    # 分类展示失败
    bad_prompts = [r for r in results if r["status"].startswith("bad_prompt")]
    if bad_prompts:
        print(f"\n⚠️  {len(bad_prompts)} 个画格的 prompt 有问题（HTTP 400），请修改后重试：")
        for r in bad_prompts:
            print(f"    - {r['name']}: {r['status']}")

    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
