#!/usr/bin/env python3
"""Submit one ComfyUI API workflow and record/download its media output."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get_json(url: str) -> object:
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: object) -> object:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with urlopen(Request(url, data=data, method="POST", headers={"Content-Type": "application/json"}), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def find_media(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str) and str(value["filename"]).lower().endswith((".mp4", ".webm", ".mov")):
            return value
        for child in value.values():
            media = find_media(child)
            if media:
                return media
    elif isinstance(value, list):
        for child in value:
            media = find_media(child)
            if media:
                return media
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-url", default="http://192.168.1.6:8190")
    parser.add_argument("--poll-seconds", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=14400)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
    started = time.time()
    submitted = post_json(f"{base}/prompt", {"prompt": workflow})
    prompt_id = str(submitted["prompt_id"])
    print(json.dumps({"prompt_id": prompt_id, "workflow": str(args.workflow), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False), flush=True)
    while True:
        history = get_json(f"{base}/history/{prompt_id}")
        item = history.get(prompt_id) if isinstance(history, dict) else None
        if item:
            status = item.get("status", {})
            if status.get("status_str") == "error" or status.get("messages") and not status.get("completed"):
                print(json.dumps({"status": "error", "messages": status.get("messages", [])[-10:], "elapsed_seconds": round(time.time() - started, 2)}, ensure_ascii=False), flush=True)
                return 2
            if status.get("completed") or status.get("status_str") == "success":
                media = find_media(item.get("outputs", {}))
                if not media:
                    print(json.dumps({"status": "no_media", "outputs": item.get("outputs", {}), "elapsed_seconds": round(time.time() - started, 2)}, ensure_ascii=False), flush=True)
                    return 3
                args.output.parent.mkdir(parents=True, exist_ok=True)
                query = urlencode({"filename": media["filename"], "subfolder": media.get("subfolder", ""), "type": media.get("type", "output")})
                with urlopen(Request(f"{base}/view?{query}", headers={"Accept": "video/mp4,video/*,*/*"}), timeout=180) as response, args.output.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
                print(json.dumps({"status": "success", "prompt_id": prompt_id, "media": media, "output": str(args.output), "elapsed_seconds": round(time.time() - started, 2)}, ensure_ascii=False), flush=True)
                return 0
        if time.time() - started > args.timeout_seconds:
            print(json.dumps({"status": "timeout", "prompt_id": prompt_id, "elapsed_seconds": round(time.time() - started, 2)}, ensure_ascii=False), flush=True)
            return 4
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
