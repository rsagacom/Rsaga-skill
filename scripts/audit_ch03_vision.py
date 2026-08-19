#!/usr/bin/env python3
"""ch03 视觉审核 — 走本地 gateway /vision/step (step-1o-turbo-vision)

按 v2 分镜的 atype 分流审核 prompt，character 审核嵌入主角 Q 设定。
用法:
    python3 scripts/audit_ch03_vision.py projects/bridge-god/ch03/storyboard_ch03_v2.py
    python3 scripts/audit_ch03_vision.py <v2_storyboard> --limit 3   # 先试 N 格
"""
import argparse, json, sys, time, urllib.request, importlib.util, base64
from pathlib import Path

GATEWAY = "http://127.0.0.1:11939/vision/step"

# ─── 审核提示词（中文，Step 对中文精准）────────────────────
def audit_prompt(atype, Q):
    base = ("这是东亚风格黑白水墨漫画画格。逐项检查后，**最后一行必须**严格写成：\n"
            "VERDICT: PASS   或   VERDICT: FAIL\n"
            "(PASS=无实质问题；FAIL=有需修复的实质问题如角色重影/超自然实体长五官/"
            "明显畸变/时代不符/真人照片感/图内可读乱码文字。轻微近似如领口非严格V领算 PASS)\n"
            "全局硬性检查：1. 画面必须像东亚黑白漫画，不得像真人照片、影视剧截图、3D渲染或写实人脸；"
            "2. 图内不得有可读文字、乱码文字、水印、英文标识、论坛正文、报告正文、标题字样，文字应由排版层后加；"
            "3. 不得出现无关人群、无关儿童、无关动物、双重人像或明显畸形。\n"
            "前面可写分析，但最后一行必须是上面的 VERDICT 格式。")
    if atype == "character":
        return base + f"\n主角设定：{Q}。审核：1.发型是否黑色短发齐刘海 2.是否细金属框眼镜（非黑框）3.服装是否米色休闲西装+深蓝V领衫+旧黑背包 4.有无重影/双重人像/叠加 5.五官是否正常无畸变。"
    if atype == "supernatural":
        return base + "\n超自然实体审核：1.是否只有发光眼窝/虚影，无完整人脸 2.有无嘴/鼻/牙齿等五官 3.身形是否剪影/烟雾质感（非具象人体）4.有无重影叠加。"
    if atype == "scene":
        return base + "\n场景合规审核：1.是否出现不应有的儿童 2.空旷场景是否有无关人群 3.是否出现与剧情时代不符的元素 4.构图是否合理无撕裂 5.屏幕/报纸/报告是否只呈现模糊块而非可读文字。"
    if atype == "abstract":
        return base + "\n抽象/隐喻画面审核：1.是否出现人物重影/叠加 2.画面是否撕裂/扭曲异常 3.超自然实体是否出现完整人脸。"
    return base + "\n通用审核：1.有无重影叠加 2.画面是否完整无撕裂 3.有无明显畸变。"


def load_v2(path):
    spec = importlib.util.spec_from_file_location("sb", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    Q = getattr(m, "Q", "")
    return [{"id": p[0], "atype": p[2] if len(p) > 2 else "scene"} for p in m.PANELS], Q


def vision(image_path, prompt, max_tokens=400, timeout=120):
    payload = json.dumps({"image": str(Path(image_path).resolve()),
                          "prompt": prompt, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(GATEWAY, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def parse_issues(content, atype=""):
    if not content:
        return [], "PASS"
    cu = content.upper()
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    # 1) 优先找 VERDICT 标记
    verdict = ""
    for l in lines:
        if "VERDICT" in l.upper():
            verdict = l.upper()
            break
    if "FAIL" in verdict:
        issues = [l.strip(" -•*") for l in lines
                  if l.strip() and "VERDICT" not in l.upper()]
        return issues, "FAIL"
    if "PASS" in verdict:
        return [], "PASS"
    # 2) 无 VERDICT → 关键词兜底（只在明确违规语境判 FAIL）
    # 真人照片感 / 图内文字污染
    for kw in ["真人照片", "照片感", "写实人脸", "影视截图", "3D渲染", "可读文字", "乱码", "水印", "英文标识"]:
        if kw in content and f"无{kw}" not in content and f"没有{kw}" not in content and f"未见{kw}" not in content:
            return [f"疑似{kw}"], "FAIL"
    # 超自然实体长五官
    if atype == "supernatural":
        for kw in ["有嘴", "有牙齿", "露出牙", "笑容", "完整人脸", "有鼻", "五官"]:
            if kw in content and f"无{kw}" not in content and f"没有{kw}" not in content:
                return [f"超自然实体疑似含{kw}"], "FAIL"
    # 角色重影/叠加
    if atype == "character":
        for kw in ["重影", "双重人像", "叠加", "畸变"]:
            if kw in content and ("无" + kw not in content) and ("没有" + kw not in content):
                return [f"疑似{kw}"], "FAIL"
    # 默认 PASS（含通过/合规等正面词或无法判定）
    if any(w in content for w in ["通过", "合规", "符合要求", "无问题"]):
        return [], "PASS"
    return [], "PASS"


def main():
    ap = argparse.ArgumentParser(description="ch03 视觉审核 (gateway /vision/step)")
    ap.add_argument("storyboard", help="storyboard_chXX_v2.py 路径")
    ap.add_argument("--panels", default=None, help="画格目录（默认 storyboard 同目录 panels/）")
    ap.add_argument("--limit", type=int, default=0, help="只审前 N 格（0=全部）")
    ap.add_argument("--out", default=None, help="报告输出路径（.json/.md）")
    args = ap.parse_args()

    panels, Q = load_v2(args.storyboard)
    pdir = Path(args.panels) if args.panels else Path(args.storyboard).parent / "panels"
    if args.limit:
        panels = panels[:args.limit]

    print(f"📖 加载 {len(panels)} 格 | panels: {pdir}")
    print(f"   主角 Q: {Q[:40]}...")
    results = []
    for i, p in enumerate(panels, 1):
        img = pdir / f"{p['id']}.png"
        if not img.exists():
            print(f"[{i}/{len(panels)}] {p['id']} ❌ 缺图"); continue
        prompt = audit_prompt(p["atype"], Q)
        for attempt in range(1, 4):
            try:
                r = vision(img, prompt)
                if "error" in r:
                    raise RuntimeError(r["error"])
                content = r.get("content", "")
                issues, status = parse_issues(content, p["atype"])
                icon = "✅" if status == "PASS" else "⚠️"
                print(f"[{i}/{len(panels)}] {p['id']} ({p['atype']}) {icon}"
                      + ("" if status == "PASS" else f" {len(issues)}问题"))
                results.append({"panel": p["id"], "type": p["atype"], "status": status,
                                "issues": issues, "raw": content})
                break
            except Exception as e:
                print(f"    ⚠️ {attempt}/3: {e}")
                if attempt == 3:
                    results.append({"panel": p["id"], "type": p["atype"], "status": "ERROR",
                                    "issues": [str(e)], "raw": ""})
                time.sleep(5)
        time.sleep(1.0)

    # 汇总
    fail = [r for r in results if r["status"] == "FAIL"]
    err = [r for r in results if r["status"] == "ERROR"]
    print(f"\n{'='*40}")
    print(f"✅ 通过: {sum(1 for r in results if r['status']=='PASS')}")
    print(f"⚠️ 问题: {len(fail)}")
    print(f"❌ 错误: {len(err)}")
    for r in fail:
        print(f"\n  【{r['panel']}】({r['type']})")
        for iss in r["issues"]:
            print(f"    - {iss}")

    if args.out:
        op = Path(args.out); op.parent.mkdir(parents=True, exist_ok=True)
        # 始终存 json（含 raw）
        jp = op.with_suffix(".json")
        jp.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        # md 摘要
        lines = ["# ch03 视觉审核报告\n"]
        for r in results:
            lines.append(f"## {r['panel']} ({r['type']}) — {r['status']}")
            if r["issues"]:
                lines += [f"- {i}" for i in r["issues"]]
            lines.append("")
        op.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n报告: {op} / {jp}")

    if fail or err:
        sys.exit(1)


if __name__ == "__main__":
    main()
