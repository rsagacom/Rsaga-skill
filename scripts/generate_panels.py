#!/usr/bin/env python3
"""
批量生图 — 基于 storyboard_chXX.py 生成画格
模型：Step Image Edit 2（通过本地 mm-gateway 调用）
"""
import os, json, time, sys, pathlib, importlib.util, base64, argparse, urllib.request, re

GATEWAY = os.environ.get("IMAGE_GATEWAY_URL", "")
GATEWAY_KEY = os.environ.get("IMAGE_GATEWAY_API_KEY", "")
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


def _strip_negated_style_terms(text):
    """移除安全的否定式风格词，剩下的写实/照片词才算风险。

    覆盖裸 `NOT realistic`(不限于 realistic face) 与中文非写实/非真人等，
    避免把否定约束误判为正向写实词。
    """
    patterns = [
        r"\b(?:not|no)\s+(?:a\s+)?(?:photograph|photo|photorealistic|photo-realistic|live-action|realistic(?:\s+face)?|3d render)\b",
        r"非照片", r"不是照片", r"不要照片", r"禁止照片",
        r"非真人", r"不要真人", r"禁止真人",
        r"非写实", r"不要写实", r"禁止写实", r"不写实",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _strip_supernatural_negations(text):
    """移除无五官等否定词，避免把 NO mouth 误判为有嘴。"""
    patterns = [
        r"\b(?:no|not)\s+(?:facial features|face|human face|realistic face|mouth|nose|teeth|smile|grin)\b",
        r"无五官", r"无面部", r"没有面部", r"没有五官", r"无脸", r"无嘴", r"无鼻", r"无牙齿", r"无牙",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned


def check_style_safety(prompt):
    """正向写实/照片词一律报错(硬拦)。

    先 strip 掉 NOT/非 前缀的否定约束，剩余的 photograph/photo/realistic/
    live-action/写实/照片/真人 等都视为正向危险词。
    """
    cleaned = _strip_negated_style_terms(prompt.lower())
    issues = []
    # 裸 realistic(任意后缀)、photograph、独立 photo、photorealistic、live-action
    dangerous_en = r"\b(photograph|photo-realistic|photorealistic|live-action|realistic|photo)\b"
    dangerous_zh = r"(真人照片|照片感|真实照片|真人脸|真实人脸|写实风格|写实感|照片级|摄影感|真人感|影视感|电影感)"
    if re.search(dangerous_en, cleaned, flags=re.IGNORECASE) or re.search(dangerous_zh, cleaned):
        issues.append(
            "真人/写实风格正向词 → 删除该词，改为东亚黑白漫画/2D comic，保留 NOT photograph, NOT photorealistic, NOT realistic face"
        )
    return issues


# 否定画风约束：prompt(展开 {S} 后)必须含其中至少一个
NEGATIVE_STYLE_GUARDS = [
    "not photo", "not photograph", "not photorealistic", "not photo-realistic",
    "not realistic face", "not realistic", "not live-action",
    "非照片", "不是照片", "非真人", "非写实", "不写实",
]


def check_negative_style_guard(prompt):
    """没有 NOT photo/photorealistic/realistic face 等否定约束时给出警告。

    警告(不硬拦)：画风后缀可能被压缩掉，提示补回否定约束，避免退回真人照片脸。
    """
    lowered = prompt.lower()
    if any(g in lowered for g in NEGATIVE_STYLE_GUARDS):
        return []
    return ["缺少否定画风约束 → 建议补 NOT photo, NOT photorealistic, NOT realistic face，避免真人照片脸"]


# hard 触发词：明确要求生图模型画文字 → 无 safe 词时硬拦
TEXT_HARD_TRIGGERS = [
    "可读文字", "正文文字", "帖子正文", "报告正文", "标题字样", "门牌字样",
    "字幕", "字样", "巨大汉字", "大字标题",
    "readable text", "text on screen", "words on screen", "lettering",
    "写明", "写着字", "印有文字",
]
# soft 触发词：场景含文字载体(屏幕/论坛/报告/报纸) → 无 safe 词时警告
TEXT_SOFT_TRIGGERS = [
    "手机屏幕", "屏幕", "论坛", "报告", "报纸", "海报", "门牌", "标题",
    "帖子", "新闻配图", "网页", "界面", "文档", "简历",
]
TEXT_SAFE_TERMS = [
    "无可读文字", "无可读汉字", "无可读字母", "无可读字", "无任何文字", "无文字",
    "不生成文字", "不要生成文字", "文字交给排版", "模糊块",
    "no readable text", "without readable text", "text handled in layout",
    "blur", "blurred",
]


def check_text_layer_safety(prompt):
    """可读文字应进 BUBBLE_CONFIG，不应交给生图模型。

    返回 (issues, warnings):
    - hard 触发(明确要求画文字)且无 safe 词 → issue 硬拦
    - soft 触发(场景含文字载体)且无 safe 词 → warning 软警告
    """
    cleaned = prompt.lower()
    has_safe = any(term in prompt or term in cleaned for term in TEXT_SAFE_TERMS)
    if has_safe:
        return [], []

    issues = []
    warnings = []
    if any(term in prompt or term in cleaned for term in TEXT_HARD_TRIGGERS):
        issues.append("图内文字污染(明确要求画文字) → 生图只画模糊块/符号/空白载体，真实文字放入 BUBBLE_CONFIG")
    if any(term in prompt or term in cleaned for term in TEXT_SOFT_TRIGGERS):
        warnings.append("场景含文字载体(屏幕/论坛/报告等) → 建议补'无可读文字'，真实文字放入 BUBBLE_CONFIG")
    return issues, warnings


def check_supernatural_safety(prompt, atype):
    """剪影/超自然角色必须无五官，且不能写笑容/嘴鼻牙等触发词。"""
    prompt_lower = prompt.lower()
    is_supernatural = atype == "supernatural" or re.search(
        r"超自然|租客|房东|发光眼窝|幽绿|无五官|虚影|剪影|shadowy humanoid|glowing eye|silhouette",
        prompt_lower,
    )
    if not is_supernatural:
        return []

    # 去掉主角描述，避免主角的脸/皮肤触发剪影规则。
    test_text = re.sub(r'25岁[^，。]*?(东亚青年|中国男性)[^，。]*?(皮肤|黑眼圈)[^，。]*', '', prompt)
    test_text = re.sub(r'young chinese man[^.]*?pale skin', '', test_text, flags=re.IGNORECASE)
    stripped = _strip_supernatural_negations(test_text)
    # 移除非实体五官的复合词用法，避免误拦：
    # "刺鼻/鼻息/鼻尖"里的鼻是嗅觉/部位描写，不是超自然实体长鼻；
    # "面色/脸庞"等在主角描述语境下也不该硬拦（主角在场由 LOGIC_RULES 降级处理）。
    stripped = re.sub(r"刺鼻|鼻息|鼻尖|鼻孔", "", stripped)
    issues = []

    has_no_features = re.search(
        r"无五官|无面部|没有面部|没有五官|无脸|NO facial features|NO face|NO human face",
        prompt,
        flags=re.IGNORECASE,
    )
    if atype == "supernatural" and not has_no_features:
        issues.append("超自然/剪影角色缺少无五官约束 → 补无五官、无嘴、无鼻、无牙齿")

    if re.search(r"诡笑|笑容|微笑|嘴|鼻|牙齿|五官|完整脸|human face|\bface\b|\bmouth\b|\bnose\b|\bteeth\b|\bsmile\b|\bgrin\b", stripped, flags=re.IGNORECASE):
        issues.append("剪影/超自然角色出现五官触发词 → 改为剪影/虚影/发光眼窝，无嘴鼻牙")
    return issues

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
    """对单条 prompt 进行逻辑检测，返回 (issues, warnings)。

    issues   → 硬拦(真人写实正向词/图内文字硬触发/超自然五官/逻辑矛盾)
    warnings → 只打印不拦(缺否定画风约束/文字载体软触发/字节偏长)

    字节超长不再硬拦(改警告)：Q 定义展开后普遍超 400，且实测 Step API
    对英文 prompt 容忍度更高。

    超自然五官检测：剔除 {Q} 展开的主角描述后再检测，避免把主角的
    "苍白皮肤/脸"误判为超自然实体长五官。
    """
    issues = []
    warnings = []
    issues.extend(check_style_safety(prompt))
    ti, tw = check_text_layer_safety(prompt)
    issues.extend(ti)
    warnings.extend(tw)
    warnings.extend(check_negative_style_guard(prompt))
    issues.extend(check_supernatural_safety(prompt, atype))

    # 长度检测：仅警告，不进 issues(避免硬拦)
    byte_len = len(prompt.encode('utf-8'))
    if byte_len > 400:
        warnings.append(f"字节偏长({byte_len}字节)，可能触发Step API限制")
    elif byte_len > 350:
        warnings.append(f"接近400字节({byte_len}字节)")

    prompt_lower = prompt.lower()
    for pattern, checks in LOGIC_RULES:
        if not re.search(pattern, prompt_lower):
            continue
        for keyword, label, suggestion in checks:
            if keyword == "__CHINESE_FACE__":
                # 剔除 {Q} 展开的主角描述(标记之间的部分)再检测五官
                # 主角描述形如 "25岁东亚青年...苍白皮肤"，用正则去掉
                test_text = re.sub(r'25岁[^，。]*?(东亚青年|中国男性)[^，。]*?(皮肤|黑眼圈)[^，。]*', '', prompt)
                # 也剔除英文主角描述
                test_text = re.sub(r'young chinese man[^.]*?pale skin', '', test_text, flags=re.IGNORECASE)
                if _has_chinese_face_word(test_text):
                    # 超自然五官检测降级为警告：主角在场时"脸/皮肤"易误判，
                    # 真实问题交给生图后视觉审核判断，不在此硬拦
                    warnings.append("疑似超自然实体五官(可能误判主角描述)，生图后视觉审核复查")
            elif re.search(keyword, prompt_lower):
                issues.append(f"{label} → {suggestion}")
    return issues, warnings


def validate_all_panels(panels):
    """检测所有 prompt 的逻辑问题。

    返回 (all_issues, all_warnings)。
    issues 非空时由调用方硬拦(阻止生图)；warnings 只打印。
    """
    all_issues = []
    all_warnings = []
    for pid, prompt, atype in panels:
        issues, warnings = check_prompt_logic(pid, prompt, atype)
        for iss in issues:
            print(f"  ⚠️  [{pid}] {iss}")
            all_issues.append((pid, iss))
        for w in warnings:
            print(f"  💡 [{pid}] {w}")
            all_warnings.append((pid, w))
    return all_issues, all_warnings


def generate_panel(panel_id, prompt, out_dir, force=False):
    """通过 gateway 生成单个画格"""
    safe_name = panel_id.replace("/", "_").replace(" ", "_")
    out_path = out_dir / f"{safe_name}.png"

    if out_path.exists() and not force:
        return "skip"

    print(f"  🎨 {safe_name}", flush=True)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            payload = json.dumps({
                "model": "gpt-image-2",
                "prompt": prompt,
                "n": 1,
                "size": SIZE,
                "response_format": "b64_json"
            }).encode()
            req = urllib.request.Request(
                GATEWAY,
                data=payload,
                headers={
                    "Authorization": f"Bearer {GATEWAY_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                r = json.loads(resp.read())

            if "error" in r:
                print(f"    ⚠️  API error: {r['error']}", flush=True)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                return "error"

            img_b64 = r.get("data", [{}])[0].get("b64_json", "")
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
    issues, warnings = validate_all_panels(panels)
    if issues:
        print(f"\n❌ 发现 {len(issues)} 个硬性问题，请修正后重试：")
        for pid, iss in issues:
            print(f"    {pid}: {iss}")
        sys.exit(1)
    if warnings:
        print(f"\n💡 {len(warnings)} 条提示(不阻断): {len(set(p for p,_ in warnings))} 格")
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

    if not GATEWAY or not GATEWAY_KEY:
        raise SystemExit("未配置生图 provider：请设置 IMAGE_GATEWAY_URL 和 IMAGE_GATEWAY_API_KEY；dry-run 不需要 provider")

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
