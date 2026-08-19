#!/usr/bin/env python3
"""导出 API 项目为可继续编辑的文件优先目录。

用法：.venv/bin/python scripts/export_project.py PROJECT_ID --runtime-dir runtime --output projects/exported
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio_api.service import DEFAULT_USER_ID, StudioService
from studio_api.store import StudioStore


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 AI 漫剧项目交换包")
    parser.add_argument("project_id")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--output", required=True)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    args = parser.parse_args()
    runtime = Path(args.runtime_dir)
    service = StudioService(StudioStore(runtime / "studio.sqlite3"), runtime / "assets")
    output = service.write_project_bundle(args.project_id, args.output, args.user_id)
    print(f"项目交换包已导出: {output}")


if __name__ == "__main__":
    main()
