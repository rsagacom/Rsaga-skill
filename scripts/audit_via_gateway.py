#!/usr/bin/env python3
"""通过本地 mm-gateway 用 Kimi 批量审核画格"""
import json, urllib.request, time, sys
from pathlib import Path

GATEWAY = "http://127.0.0.1:11939"
PANELS_DIR = "/Users/rsaga/Documents/Playground/projects/桥底的溃烂神明/chapter1/panels"

AUDIT_PROMPTS = {
    "character": """审核要求：检查图中角色是否符合描述。
请逐一检查：
1. 发型、发色是否与描述一致？角色应为短发有刘海，戴黑色细框眼镜
2. 是否佩戴指定眼镜？
3. 服装是否与描述一致？应为米色休闲西装/蓝色衬衫/背包
4. 画面是否出现重影/叠加/双重人像？

只输出「通过」或问题描述。""",

    "supernatural": """审核要求：检查超自然实体。
1. 是否只有发光眼窝，无完整人脸？
2. 是否有嘴巴/鼻子等面部细节？
3. 身形是否半透明/烟雾质感？
4. 是否出现重影/叠加？

只输出「通过」或问题描述。""",

    "hand": """审核要求：检查手部绘制质量。
1. 手指数量是否为5根？
2. 手掌轮廓是否正常？
3. 是否过度写实到不自然？

只输出「通过」或问题描述。""",

    "scene": """审核要求：检查场景合规。
1. 是否有不应出现的儿童？
2. 空旷场景是否有无关人群？
3. 是否出现与剧情时代不符的元素？

只输出「通过」或问题描述。""",

    "abstract": """审核要求：检查抽象/隐喻画面。
1. 是否出现人物重影/叠加？
2. 画面是否有撕裂/扭曲？
3. 超自然实体是否出现完整人脸？

只输出「通过」或问题描述。""",

    "title": """审核要求：检查标题/扉页画面。
1. 画面是否干净清晰？
2. 是否有文字重叠或模糊？

只输出「通过」或问题描述。""",
}

# 从 storyboard 加载审核类型
STORYBOARD = "/Users/rsaga/Documents/Playground/projects/桥底的溃烂神明/chapter1/storyboard_ch01.py"

def load_panel_types():
    """从 storyboard 提取 (panel_name, audit_type) 映射"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("storyboard", STORYBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    types = {}
    for p in mod.PANELS:
        pid = p[0]
        atype = p[2] if len(p) > 2 else "scene"
        types[pid] = atype
    return types


def kimi_vision(image_path, prompt):
    """通过 gateway 调用 Kimi 识图"""
    payload = json.dumps({
        "image": image_path,
        "prompt": prompt,
        "max_tokens": 400,
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/vision/kimi",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    panel_types = load_panel_types()
    panels_dir = Path(PANELS_DIR)
    panels = sorted([p for p in panels_dir.iterdir() if p.suffix == ".png"])

    print(f"视觉审核开始 — 共 {len(panels)} 格 (Kimi K2.7)\n")

    results = []
    for i, panel_path in enumerate(panels, 1):
        name = panel_path.stem
        atype = panel_types.get(name, "scene")
        prompt = AUDIT_PROMPTS.get(atype, AUDIT_PROMPTS["scene"])
        sys.stdout.write(f"  [{i:3d}/{len(panels)}] {name:35s} ({atype:12s}) ... ")
        sys.stdout.flush()

        try:
            resp = kimi_vision(str(panel_path), prompt)
            if "error" in resp:
                print(f"⚠️  {resp['error'][:60]}")
                results.append((name, atype, "ERROR", resp["error"]))
                continue

            content = resp.get("content", "")
            has_pass = "通过" in content
            issues = [l.strip() for l in content.split("\n") if l.strip() and "通过" not in l]

            if has_pass and not issues:
                print("✅")
                results.append((name, atype, "PASS", []))
            else:
                print(f"❌ {len(issues)} 个问题")
                for iss in issues[:3]:
                    print(f"      → {iss}")
                results.append((name, atype, "FAIL", issues))

        except Exception as e:
            print(f"⚠️  ERROR: {str(e)[:50]}")
            results.append((name, atype, "ERROR", str(e)))

        time.sleep(1.5)

    # 总结
    passed = sum(1 for r in results if r[2] == "PASS")
    failed = sum(1 for r in results if r[2] == "FAIL")
    errors = sum(1 for r in results if r[2] == "ERROR")

    print(f"\n{'='*60}")
    print(f"审核完成！总计 {len(results)} 格 | ✅ 通过 {passed} | ❌ 问题 {failed} | ⚠️ 错误 {errors}")
    print(f"{'='*60}")

    if failed:
        print("\n问题画格：")
        for name, atype, status, issues in results:
            if status == "FAIL":
                print(f"  ❌ {name} ({atype})")
                for iss in issues[:2]:
                    print(f"      {iss}")

    if errors:
        print(f"\n错误画格：")
        for name, atype, status, iss in results:
            if status == "ERROR":
                print(f"  ⚠️  {name}: {str(iss)[:80]}")

    # 保存报告
    report = {"total": len(results), "passed": passed, "failed": failed, "errors": errors,
              "details": [{"name": r[0], "type": r[1], "status": r[2], "issues": r[3]} for r in results]}
    report_path = "/Users/rsaga/Documents/Playground/projects/桥底的溃烂神明/chapter1/audit_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
