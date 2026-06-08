#!/usr/bin/env python3
"""视觉审核入口脚本

用法:
    python3 scripts/audit_panels.py projects/example/panels
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comic_engine.auditor import audit_panels
from comic_engine.config import load_config


def main():
    parser = argparse.ArgumentParser(description="视觉审核漫画画格")
    parser.add_argument("panels_dir", help="画格目录路径")
    parser.add_argument("--config", "-c", default=None, help="配置文件路径")
    parser.add_argument("--output", "-o", default=None, help="审核报告输出路径（JSON/Markdown）")
    args = parser.parse_args()

    config = load_config(args.config)
    results = audit_panels(args.panels_dir, config)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix == ".json":
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        else:
            lines = ["# 视觉审核报告\n"]
            for r in results:
                lines.append(f"## {r['panel']} ({r['type']})")
                lines.append(f"- 状态: {r['status']}")
                if r["issues"]:
                    for issue in r["issues"]:
                        lines.append(f"- {issue}")
                lines.append("")
            out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n报告已保存: {out_path}")

    failed = [r for r in results if r["status"] == "FAIL"]
    if failed:
        print(f"\n发现 {len(failed)} 个问题画格，请运行 scripts/fix_panel.py 修复")
        sys.exit(1)


if __name__ == "__main__":
    main()
