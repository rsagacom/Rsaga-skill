#!/usr/bin/env python3
"""
批量生图 — 基于 storyboard_chXX.py 生成画格
模型：Step Image Edit 2（通过本地 mm-gateway 调用）
"""
import os, json, time, sys, pathlib, importlib.util, base64, argparse, urllib.request, re

GATEWAY = "http://127.0.0.1:11939/image/generate"
SIZE = "1024x1024"
RETRY_DELAY = 10
MAX_RETRIES = 5


def load_storyboard(path):
    """动态导入 storyboard 文件，获取 PANELS 列表，替换 {Q} 和 {S}"""
    spec = importlib.util.spec_from_file_location("storyboard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    panels = []
    for p in mod.PANELS:
        pid, prompt = p[0], p[1]
        atype = p[2] if len(p) > 2 else "unknown"
        if hasattr(mod, 'Q') and '{Q}' in prompt:
            prompt = prompt.replace('{Q}', mod.Q)
        if hasattr(mod, 'S') and '{S}' in prompt:
            prompt = prompt.replace('{S}', mod.S)
        panels.append((pid, prompt, atype))
    return panels


def _has_chinese_face_word(text):
    """检测中文prompt中是否包含人脸/五官词"""
    chinese_face = ["脸", "鼻", "嘴", "牙齿", "笑容", "皮肤", "眉毛", "眼珠", "光头"]
    for word in chinese_face:
        if word in text:
            return True
    return False

# ─── 逻辑检测规则库 ───────────────────────────────────────
LOGIC_RULES = [
    # 医疗场景：主角不应戴氧气面罩/呼吸机/病号服
    ("氧气面罩|氧氣面罩|呼吸机|呼吸機|氧氣罩", [
        ("{Q}|主角|男人|男子|青年", "主角自己戴面罩", "主角不应戴氧气面罩，是病人在戴。应写：老人戴氧气面罩，主角站在旁边"),
    ]),
    # 回忆场景：成年人不该在童年场景
    ("7岁|8岁|童年|小时候|小孩|沙坑|滑梯|楼道|灌沙|灌沙子|被欺负", [
        ("25yo|adult|man|beige|blazer|beard|肌肉|皱纹", "成年人出现在童年场景", "童年场景应使用 young boy version of {Q}, age 7-8"),
    ]),
    # 女性角色：避免男性特征
    ("女人|女性|女子|长发|背影|风衣", [
        ("trench coat|silhouette alone|figure|dark silhouette", "女性剪影缺少女特征", "应加 female silhouette, long black hair, seen from behind, NO face visible"),
    ]),
    # 超自然实体：不应有五官（只检测中文人脸关键词，不检测英文否定词）
    ("超自然|租客|房东|发光眼窝|幽绿|影子|虚影|黑影", [
        ("__CHINESE_FACE__", "超自然实体有五官/人类形象", "超自然实体只能用剪影/虚影/发光眼窝，中文写：无五官，无面部，无嘴无鼻"),
    ]),
    # 医院场景：主角不是病人
    ("医院|ICU|重症|病床|病房|老人|抢救|急救", [
        ("gown|patient|bedridden|wheelchair|病床|躺|输液|病号服", "主角被描述为病人", "主角是访客站在床边，不是病人"),
    ]),
    # 尸体/死亡：不应活过来
    ("尸体|猝死|死亡|死去|栽倒|倒在地上", [
        ("stand up|walk|open eyes|breathe|move|爬起来|站起来|睁眼", "尸体在动", "尸体应保持静止不动"),
    ]),
]


def check_prompt_logic(pid, prompt, atype):
    """对单条 prompt 进行逻辑检测，返回问题列表"""
    issues = []
    # 长度检测（Step API 400字节限制，每个中文字3字节）
    byte_len = len(prompt.encode('utf-8'))
    if byte_len > 400:
        issues.append(f"prompt超400字节（{byte_len}字节，约{len(prompt)}字），Step API会返回HTTP 400")
    elif byte_len > 350:
        issues.append(f"prompt接近400字节（{byte_len}字节），注意可能触发限制")
    prompt_lower = prompt.lower()
    for pattern, checks in LOGIC_RULES:
        if not re.search(pattern, prompt_lower):
            continue
        for keyword, label, suggestion in checks:
            if keyword == "__CHINESE_FACE__":
                if _has_chinese_face_word(prompt):
                    issues.append(f"{label} → {suggestion}")
            elif re.search(keyword, prompt_lower):
                issues.append(f"{label} → {suggestion}")
    return issues


def validate_all_panels(panels):
    """检测所有 prompt 的逻辑问题，严重问题阻止生图"""
    all_issues = []
    for pid, prompt, atype in panels:
        issues = check_prompt_logic(pid, prompt, atype)
        if issues:
            for iss in issues:
                print(f"  ⚠️  [{pid}] {iss}")
                all_issues.append((pid, iss))
    return all_issues


def generate_panel(panel_id, prompt, out_dir, force=False):
    """通过 gateway 生成单个画格"""
    safe_name = panel_id.replace("/", "_").replace(" ", "_")
    out_path = out_dir / f"{safe_name}.png"

    if out_path.exists() and not force:
        return "skip"

    print(f"  🎨 {safe_name}", flush=True)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            payload = json.dumps({"prompt": prompt, "size": SIZE, "steps": 8}).encode()
            req = urllib.request.Request(
                GATEWAY,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                r = json.loads(resp.read())

            if "error" in r:
                print(f"    ⚠️  API error: {r['error']}", flush=True)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                return "error"

            img_b64 = r.get("image", "")
            if not img_b64:
                return "error"

            img_data = base64.b64decode(img_b64)
            with open(out_path, "wb") as f:
                f.write(img_data)
            return "ok"

        except Exception as e:
            print(f"    ⚠️  {attempt}/{MAX_RETRIES}: {e}", flush=True)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                return "error"


def main():
    parser = argparse.ArgumentParser(description="批量生图（通过本地 gateway）")
    parser.add_argument("storyboard", help="storyboard_chXX.py 路径")
    parser.add_argument("--out", "-o", help="输出目录")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有画格")
    parser.add_argument("--dry-run", action="store_true", help="只预览不生成")
    parser.add_argument("--concurrent", "-c", type=int, default=1, help="并发数（默认1）")
    args = parser.parse_args()

    print(f"📖 加载分镜脚本: {args.storyboard}")
    panels = load_storyboard(args.storyboard)

    # 🔥 逻辑检测：生图前拦截不合逻辑的 prompt
    print(f"\n🔍 逻辑检测中...")
    issues = validate_all_panels(panels)
    if issues:
        print(f"\n❌ 发现 {len(issues)} 个逻辑问题，请修正后重试：")
        for pid, iss in issues:
            print(f"    {pid}: {iss}")
        sys.exit(1)
    print(f"  ✅ 全部通过\n")

    if args.out:
        out_dir = pathlib.Path(args.out)
    else:
        parts = pathlib.Path(args.storyboard).parts
        if "projects" in parts:
            idx = parts.index("projects")
            project_name = parts[idx + 1]
            chapter = parts[idx + 2] if len(parts) > idx + 2 else "chapter"
            out_dir = pathlib.Path.home() / "Desktop" / f"{project_name}-{chapter}" / "panels"
        else:
            out_dir = pathlib.Path.home() / "Desktop" / "comic-output" / "panels"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 输出: {out_dir}")

    review_types = {}
    for p in panels:
        rt = p[2] if len(p) > 2 else "unknown"
        review_types[rt] = review_types.get(rt, 0) + 1
    print(f"📊 共 {len(panels)} 个画格: {review_types}")

    if args.dry_run:
        for i, p in enumerate(panels):
            print(f"  [{i+1}/{len(panels)}] {p[0]}")
        return

    import concurrent.futures
    results = {"ok": 0, "skip": 0, "error": 0}

    if args.concurrent > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {}
            for i, panel in enumerate(panels):
                panel_id, prompt = panel[0], panel[1]
                fut = pool.submit(generate_panel, panel_id, prompt, out_dir, args.force)
                futures[fut] = (i + 1, panel_id)

            for fut in concurrent.futures.as_completed(futures):
                idx, pid = futures[fut]
                result = fut.result()
                results[result] = results.get(result, 0) + 1
                icon = {"ok": "✅", "skip": "⏭️", "error": "❌"}.get(result, "?")
                print(f"[{idx}/{len(panels)}] {pid} {icon}", flush=True)
    else:
        for i, panel in enumerate(panels):
            panel_id, prompt = panel[0], panel[1]
            print(f"\n[{i+1}/{len(panels)}] ", end="", flush=True)
            result = generate_panel(panel_id, prompt, out_dir, args.force)
            results[result] = results.get(result, 0) + 1
            icon = {"ok": "✅", "skip": "⏭️", "error": "❌"}.get(result, "?")
            print(f"  {icon}", flush=True)

    print(f"\n{'='*40}")
    print(f"✅ 成功: {results.get('ok', 0)}")
    print(f"⏭️  跳过: {results.get('skip', 0)}")
    print(f"❌ 失败: {results.get('error', 0)}")
    print(f"📁 {out_dir}")


if __name__ == "__main__":
    main()
