#!/usr/bin/env python3
"""批量生成画格入口脚本

用法:
    python3 scripts/generate_panels.py projects/example/storyboard_ch3.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comic_engine.config import load_config
from comic_engine.generator import generate_panels
from comic_engine.utils import load_storyboard


def main():
    parser = argparse.ArgumentParser(description="批量生成漫画画格")
    parser.add_argument("storyboard", help="分镜脚本文件路径（Python 模块，需包含 PANELS 列表）")
    parser.add_argument("--config", "-c", default=None, help="配置文件路径")
    parser.add_argument("--output", "-o", default=None, help="输出目录")
    args = parser.parse_args()

    config = load_config(args.config)
    panels = load_storyboard(args.storyboard)

    if args.output:
        output_dir = Path(args.output)
    else:
        storyboard_path = Path(args.storyboard)
        output_dir = storyboard_path.parent / "panels"

    print(f"生成 {len(panels)} 格画格")
    print(f"输出目录: {output_dir.resolve()}\n")

    results = generate_panels(panels, config, output_dir)

    ok = sum(1 for r in results if r["status"] == "ok")
    fail = len(results) - ok
    print(f"\n完成: {ok} 成功, {fail} 失败")

    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
