#!/usr/bin/env python3
"""Extract the downloaded H3 Director package with safe Unicode filenames."""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path


def safe_name(name: str) -> str:
    name = name.replace("\\", "/")
    parts = []
    for part in name.split("/"):
        part = part.strip() or "unnamed"
        part = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", part)
        parts.append(part)
    return "/".join(parts)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: extract_h3_director_package.py ZIP DEST", file=sys.stderr)
        return 2
    archive = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            relative = safe_name(info.filename)
            target = destination / relative
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            entries.append({"archive_name": info.filename, "extracted": str(target), "size": info.file_size})
    (destination / "_extraction_manifest.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(entries), "destination": str(destination), "manifest": str(destination / '_extraction_manifest.json')}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
