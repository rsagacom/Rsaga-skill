#!/usr/bin/env python3
"""扫描所有画格实际画面内容"""
import urllib.request, json, time, os, sys

GATEWAY = "http://127.0.0.1:11939/vision/step"
PANELS_DIR = "/Users/rsaga/Documents/Playground/projects/桥底的溃烂神明/chapter1/panels"
OUTPUT = "/Users/rsaga/Documents/Playground/projects/桥底的溃烂神明/chapter1/panel_descriptions.json"

results = {}
for i in range(1, 119):
    pid = f"P{i:03d}"
    path = f"{PANELS_DIR}/{pid}.png"
    if not os.path.exists(path):
        results[pid] = "NOT_FOUND"
        continue
    payload = json.dumps({
        "image": path,
        "prompt": "一句话描述画面：谁/什么在做什么？构图类型（特写/中景/远景/抽象）？不超过20字。"
    }).encode()
    req = urllib.request.Request(GATEWAY, data=payload, headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            desc = r.get("content", "").replace("\n", " ").strip()[:80]
            results[pid] = desc
            print(f"{pid}: {desc}")
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                results[pid] = f"ERROR: {str(e)[:40]}"
                print(f"{pid}: ERROR")
    time.sleep(2)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n✅ 已保存: {OUTPUT}")
