#!/usr/bin/env python3
"""为本地 SQLite 项目抽取并输出故事资产草稿。

用法：.venv/bin/python scripts/generate_story_bible.py PROJECT_ID --runtime-dir runtime
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio_api.service import DEFAULT_USER_ID, StudioService
from studio_api.store import StudioStore


def main() -> None:
    parser = argparse.ArgumentParser(description="抽取 AI 漫剧项目的地点、道具和关系草稿")
    parser.add_argument("project_id")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    args = parser.parse_args()
    runtime = Path(args.runtime_dir)
    service = StudioService(StudioStore(runtime / "studio.sqlite3"), runtime / "assets")
    result = service.generate_story_bible(args.project_id, args.user_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
