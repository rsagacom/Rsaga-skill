#!/usr/bin/env python3
"""导入 AI 漫剧项目交换包为一个新项目。

用法：.venv/bin/python scripts/import_project.py project-export.json --runtime-dir runtime
      .venv/bin/python scripts/import_project.py project-bundle.zip --runtime-dir runtime
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
    parser = argparse.ArgumentParser(description="导入 AI 漫剧项目交换包")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    args = parser.parse_args()
    runtime = Path(args.runtime_dir)
    service = StudioService(StudioStore(runtime / "studio.sqlite3"), runtime / "assets")
    if args.bundle.suffix.lower() == ".zip":
        project = service.import_project_archive(args.bundle, args.user_id)
    else:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        project = service.import_project_bundle(bundle, args.user_id)
    result = {"project_id": project["id"], "title": project["title"]}
    if project.get("archive_import"):
        result["archive_import"] = project["archive_import"]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
