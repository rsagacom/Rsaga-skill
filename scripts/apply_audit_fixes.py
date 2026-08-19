#!/usr/bin/env python3
"""把审核修正建议(_fixes.json)应用回 storyboard，生成 v2 分镜脚本

用法:
    python3 scripts/apply_audit_fixes.py projects/bridge-god/ch03/storyboard_ch03.py

读取同目录的 storyboard_chXX_fixes.json，对每个 pid 用修正 prompt 替换原 PANELS，
输出 storyboard_chXX_v2.py。

规范化：
- 若修正 prompt 未用 {Q}，但展开了完整角色描述 → 尝试替换回 {Q} 占位符(保证一致性)
- 若未用 {S} 且末尾含画风描述 → 替换回 {S}
- 超过 400 字节的标记警告(不强制截断，由生图逻辑检测拦截)
"""
import json, sys, re, importlib.util
from pathlib import Path

# 角色描述的常见展开形态(用于回替为 {Q}) —— 中英文都覆盖
Q_EXPANSIONS = [
    # 英文完整版
    "Young Chinese man 25yo, thin wire-rim glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin",
    "Young Chinese man 25yo, thin wire-rim glasses, short black hair with bangs, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin",
    "Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin",
    # 中文完整版(对应 Q_V2_ZH)
    "25岁东亚青年，细金属框眼镜，黑色短发齐刘海，米色休闲西装，深蓝色V领衬衫，旧黑色双肩背包，疲惫空洞的眼神，黑眼圈，苍白皮肤",
    # ch01 cn 版的写法
    "25岁中国男性，戴细黑框眼镜，黑色短发碎刘海，米色休闲西装，深蓝色V领衫，黑色旧背包，面色苍白，眼神空洞疲惫，黑眼圈重",
]
# 中文 Q 定义(v2 统一使用，眼镜用细金属框)
Q_V2_ZH = "25岁东亚青年，细金属框眼镜，黑色短发齐刘海，米色休闲西装，深蓝色V领衬衫，旧黑色双肩背包，疲惫空洞的眼神，黑眼圈，苍白皮肤"
# 画风后缀展开(中英文)
S_EXPANSION = "East Asian B/W manhua, G-pen ink, high-contrast grayscale, 2D comic, NOT photo, NOT photorealistic, NOT realistic face."
S_EXPANSION_VARIANTS = [
    S_EXPANSION,
    "East Asian black-and-white manhua, G-pen linework, high-contrast grayscale, stylized 2D comic art, NOT photograph, NOT photorealistic, NOT live-action, NOT realistic face",
    "East Asian B/W manhua, G-pen ink, high-contrast grayscale, 2D comic, NOT photo, NOT photorealistic, NOT realistic face",
    "Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic",
    "东亚漫画风格，黑白水墨，G笔线条，高对比度灰度，写实风格。",
    "东亚漫画，黑白水墨，G笔线条，高对比度灰度，写实风格。",
    "Manhua ink wash comic art style, black white grayscale, dramatic lighting, G-pen linework, manga illustration, cel shaded, 2D comic art, NOT photograph, NOT photorealistic, NOT 3D render, NOT realistic face",
]


def normalize_prompt(prompt, q_val, s_val):
    """把展开的角色/画风描述回替为 {Q}/{S} 占位符"""
    out = prompt
    # 回替 Q：先长后短，避免部分匹配
    for exp in sorted(Q_EXPANSIONS, key=len, reverse=True):
        if exp in out and "{Q}" not in out:
            out = out.replace(exp, "{Q}")
    # 容错：部分展开(只有前半段)也回替
    # 英文：含 "thin wire-rim glasses" 但没用 {Q}
    if "{Q}" not in out and "thin wire-rim glasses" in out:
        m = re.search(r'Young Chinese man[^,]*,?\s*thin wire-rim glasses[^.]*?pale skin', out)
        if m:
            out = out[:m.start()] + "{Q}" + out[m.end():]
    # 中文：含 "细金属框眼镜" 或 "25岁" 角色描述但没用 {Q}
    if "{Q}" not in out and ("细金属框眼镜" in out or "细黑框眼镜" in out):
        m = re.search(r'25岁[^，。]*?(东亚青年|中国男性)[^。]*?(黑眼圈|苍白)[^，。]*', out)
        if m:
            out = out[:m.start()] + "{Q}" + out[m.end():]

    # 回替 S
    for exp in sorted(S_EXPANSION_VARIANTS, key=len, reverse=True):
        if exp in out and "{S}" not in out:
            out = out.replace(exp, "{S}")
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 apply_audit_fixes.py <storyboard.py>")
        sys.exit(1)

    sb_path = Path(sys.argv[1])
    fixes_path = sb_path.with_name(sb_path.stem + "_fixes.json")
    out_path = sb_path.with_name(sb_path.stem.replace("storyboard", "storyboard") + "_v2.py")

    if not fixes_path.exists():
        print(f"❌ 找不到修正文件: {fixes_path}")
        print("   先运行: python3 scripts/audit_storyboard.py " + str(sb_path))
        sys.exit(1)

    # 加载原 storyboard(取 Q/S/PANELS 结构)
    spec = importlib.util.spec_from_file_location("sb", sb_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    q_val = getattr(mod, "Q", "")
    s_val = getattr(mod, "S", "")
    orig_panels = getattr(mod, "PANELS", [])

    fixes = json.loads(fixes_path.read_text(encoding="utf-8"))

    # v2 统一用中文 Q 定义(眼镜细金属框)，与中文 prompt 配套
    # 若原 Q 是英文则替换为中文版；若原 Q 已是中文则只修眼镜
    if any(c in q_val for c in ("Young", "Chinese man", "wire-rim", "black-frame")):
        q_v2 = Q_V2_ZH  # 英文 Q → 中文
    else:
        q_v2 = q_val.replace("细黑框眼镜", "细金属框眼镜").replace("黑框眼镜", "细金属框眼镜")
    # S 统一为安全漫画后缀，禁止正向 realistic/写实风格。
    s_v2 = S_EXPANSION

    # 构建新 PANELS：按原顺序，优先用修正 prompt
    new_panels = []
    applied = 0
    normalized = 0
    over_limit = []
    for item in orig_panels:
        pid = item[0]
        atype = item[2] if len(item) > 2 else "scene"
        if pid in fixes and fixes[pid].strip():
            prompt = fixes[pid].strip()
            # 规范化：回替占位符
            normed = normalize_prompt(prompt, q_v2, s_val)
            if normed != prompt:
                normalized += 1
            prompt = normed
            applied += 1
        else:
            # 没有修正建议的格，沿用原 prompt(但把 {Q} 引用的描述保持)
            prompt = item[1]
        # 字节检查
        blen = len(prompt.encode("utf-8"))
        if blen > 400:
            over_limit.append((pid, blen))
        new_panels.append((pid, prompt, atype))

    # 写 v2 文件
    def panel_line(pid, prompt, atype):
        """生成一行 PANELS 元组。占位符用 f-string, 否则普通字符串; 转义引号"""
        needs_f = "{Q}" in prompt or "{S}" in prompt
        # prompt 内部双引号转义为 \"
        esc = prompt.replace('\\', '\\\\').replace('"', '\\"')
        if needs_f:
            return f'    ({pid!r}, f"{esc}", {atype!r}),\n'
        else:
            return f'    ({pid!r}, "{esc}", {atype!r}),\n'

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env python3\n")
        f.write(f'"""{sb_path.stem} v2 — 经 glm 分镜审核修正后版本\n')
        f.write(f"原版: {sb_path.name}\n")
        f.write(f"修正来源: {fixes_path.name}\n")
        f.write(f"应用修正: {applied}/{len(orig_panels)} 格, 规范化回替占位符: {normalized} 格\n")
        f.write(f'Q 统一中文 + 细金属框眼镜\n"""\n\n')
        f.write(f'Q = {q_v2!r}\n')
        f.write(f'S = {s_v2!r}\n\n')
        f.write("PANELS = [\n")
        for pid, prompt, atype in new_panels:
            f.write(panel_line(pid, prompt, atype))
        f.write("]\n")

    print(f"✓ v2 分镜已生成: {out_path}")
    print(f"  应用修正: {applied}/{len(orig_panels)} 格")
    print(f"  规范化回替 {{Q}}/{{S}}: {normalized} 格")
    print(f"  Q 定义: black-frame → wire-rim")
    if over_limit:
        print(f"\n⚠️  仍有 {len(over_limit)} 格 prompt 超 400 字节(生图逻辑检测会拦截):")
        for pid, blen in over_limit:
            print(f"    {pid}: {blen} 字节")
    print(f"\n下一步: python3 scripts/generate_panels.py {out_path} --dry-run")


if __name__ == "__main__":
    main()
