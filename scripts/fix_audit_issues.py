#!/usr/bin/env python3
"""修复审核问题画格 — 通过本地 gateway 重新生成"""
import json, urllib.request, time, sys, os, base64
from pathlib import Path

GATEWAY = "http://127.0.0.1:11939/image/generate"
SIZE = "1024x1024"
PANELS_DIR = Path("/Users/rsaga/Documents/Playground/projects/桥底的溃烂神明/chapter1/panels")
S = "East Asian B/W manhua, G-pen ink, high-contrast grayscale, 2D comic, NOT photo, NOT photorealistic, NOT realistic face."
Q = ("Young Chinese man 25yo, thin wire-rim glasses, short black hair with bangs "
     "covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, "
     "tired hollow eyes with dark circles, pale skin.")

# 修复后的 prompts
FIXES = {
    # 眼镜粗框 → 改用 wire-rim（细框更容易生成）
    "P009": (f"Library interior, laptop screen showing job application status read no reply, {Q} exhausted staring at screen, {S}", "character"),
    "P010": (f"Extreme close-up of {Q}'s face, eyes empty as stagnant dead water, hopeless gaze, thin wire-rim glasses, {S}", "character"),
    "P011": (f"{Q} by library window at sunset, orange light slanting across profile, weary tired expression, thin wire-rim glasses clearly visible, {S}", "character"),
    "P060": (f"Extreme close-up of {Q}'s lips, dry cracked and trembling like withered leaf, thin wire-rim glasses in lower frame, {S}", "character"),
    "P069": (f"Extreme close-up of {Q}'s eyes reflecting landlord's glowing eye sockets, weariness and emptiness visible, thin wire-rim glasses, {S}", "character"),
    "P087": (f"Extreme close-up of {Q}'s mouth, a single word about to be spoken, lips slightly parted, thin wire-rim glasses visible, {S}", "character"),
    "P094": (f"{Q} looking down at left hand screaming in agony, face twisted with horror, thin wire-rim glasses, dark blue V-neck shirt clearly visible, {S}", "character"),
    "P116": (f"{Q} looking down at left hand, perspective from his eyes moving toward hand, fearful anticipation, thin wire-rim glasses, dark blue V-neck shirt visible, {S}", "character"),

    # 服装不符 → 强化蓝色V领衬衫
    "P032": (f"{Q} looking around inside bridge tunnel, empty space, confused and frightened expression, dark blue V-neck shirt clearly visible under blazer, no white collar showing, {S}", "character"),

    # 超自然有脸 → 加硬约束
    "P067": ("Shadowy semi-transparent figure leaning forward, NO facial features, NO mouth, NO nose, NO teeth, ONLY empty glowing cyan eye sockets, threatening posture, edges dissolving into smoke, {S}".format(S=S), "supernatural"),
    "P074": ("Shadowy semi-transparent figure with NO mouth, NO nose, NO facial features, ONLY empty glowing cyan eye sockets, sound wave ripples spreading from darkness, mouthless entity, {S}".format(S=S), "supernatural"),

    # 手部画格模型没画出手 → 用更强的手部描述
    "P097": ("Close-up of left arm with black color climbing along veins, simple normal hand shape, five fingers, muscles withering, necrotic progression, {S}".format(S=S), "hand"),
    "P101": ("Close-up of left arm, black necrotic line reaching elbow, simple normal hand shape visible, five fingers, advancing death mark on arm, {S}".format(S=S), "hand"),
}


def generate(panel_id, prompt):
    safe = panel_id.replace("/", "_")
    path = PANELS_DIR / f"{safe}.png"
    # 删除旧文件
    if path.exists():
        path.unlink()
    for attempt in range(1, 4):
        try:
            payload = json.dumps({"prompt": prompt, "size": SIZE, "steps": 8}).encode()
            req = urllib.request.Request(GATEWAY, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                r = json.loads(resp.read())
            if "error" in r:
                print(f"  ⚠️  API error: {r['error'][:60]}")
                time.sleep(5)
                continue
            img_b64 = r.get("image", "")
            if not img_b64:
                continue
            with open(path, "wb") as f:
                f.write(base64.b64decode(img_b64))
            return True
        except Exception as e:
            print(f"  ⚠️  Attempt {attempt}: {str(e)[:60]}")
            time.sleep(5)
    return False


def main():
    print(f"修复 {len(FIXES)} 个画格\n")
    ok, fail = 0, 0
    for i, (pid, (prompt, atype)) in enumerate(FIXES.items(), 1):
        sys.stdout.write(f"  [{i}/{len(FIXES)}] {pid} ({atype}) ... ")
        sys.stdout.flush()
        if generate(pid, prompt):
            print("✅")
            ok += 1
        else:
            print("❌")
            fail += 1
        time.sleep(2)  # 避免限流

    print(f"\n完成: ✅ {ok} 成功, ❌ {fail} 失败")


if __name__ == "__main__":
    main()
