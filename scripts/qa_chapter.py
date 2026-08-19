#!/usr/bin/env python3
"""章节排版 QA：检查 PAGES / BUBBLE_CONFIG / panels 的基础质量门槛。"""
import argparse
import importlib.util
import sys
from pathlib import Path


ALLOWED_LAYOUTS = {"full", "spotlight", "2x2", "1x2", "2x1x2", "hero", "triple-row"}


def load_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_bubbles(mod):
    if hasattr(mod, "BUBBLE_CONFIG"):
        return getattr(mod, "BUBBLE_CONFIG")
    if hasattr(mod, "BUBBLES"):
        return getattr(mod, "BUBBLES")
    return {}


def text_len(text):
    return len(str(text).strip())


def main():
    parser = argparse.ArgumentParser(description="章节排版 QA")
    parser.add_argument("pages_config", help="pages_config_chXX.py 路径")
    parser.add_argument("--panels", default=None, help="画格目录，默认 pages_config 同目录 panels/")
    parser.add_argument("--max-2x2-ratio", type=float, default=0.75, help="2x2 页面占比上限")
    args = parser.parse_args()

    cfg_path = Path(args.pages_config)
    mod = load_module(cfg_path)
    pages = list(getattr(mod, "PAGES", []))
    bubbles = get_bubbles(mod)
    panels_dir = Path(args.panels) if args.panels else cfg_path.parent / "panels"

    has_bubble_config = hasattr(mod, "BUBBLE_CONFIG")
    has_bubbles_legacy = hasattr(mod, "BUBBLES")

    failures = []
    warnings = []
    flat = []

    if not pages:
        failures.append("PAGES 为空")
    if not isinstance(bubbles, dict) or not bubbles:
        failures.append("BUBBLE_CONFIG 为空或不存在")
    else:
        # BUBBLE_CONFIG 必须是最终变量：legacy BUBBLES 只是中间数据
        if not has_bubble_config and has_bubbles_legacy:
            failures.append("只有 legacy BUBBLES 没有 BUBBLE_CONFIG → 排版只认 BUBBLE_CONFIG，请整理为 BUBBLE_CONFIG")
        elif has_bubble_config and has_bubbles_legacy:
            warnings.append("同时存在 BUBBLE_CONFIG 与 legacy BUBBLES，请确认 BUBBLE_CONFIG 为最终变量")

    for page_idx, item in enumerate(pages, 1):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            failures.append(f"第 {page_idx} 页格式错误，应为 (layout, [panel_ids])")
            continue
        layout, ids = item
        if layout not in ALLOWED_LAYOUTS:
            failures.append(f"第 {page_idx} 页布局类型未知: {layout}")
        if not isinstance(ids, (list, tuple)) or not ids:
            failures.append(f"第 {page_idx} 页没有 panel id")
            continue
        if len(ids) > 4 and layout != "2x1x2":
            warnings.append(f"第 {page_idx} 页包含 {len(ids)} 格，需人工确认阅读顺序")
        for pid in ids:
            if not pid:
                continue
            flat.append(pid)

    duplicates = sorted({pid for pid in flat if flat.count(pid) > 1})
    if duplicates:
        failures.append("PAGES 内重复引用 panel: " + ", ".join(duplicates))

    # PAGES 正文引用顺序：除扉页/标题页外，panel 编号应递增，防止图文顺序错位
    import re as _re
    def _pid_num(pid):
        # 只取 P 前缀编号(P22_12月上旬 → 22)，避免中文标签里的数字污染
        m = _re.match(r'^[A-Za-z]*(\d+)', pid)
        if m:
            return int(m.group(1))
        return int(''.join(filter(str.isdigit, pid)) or '0')

    def _is_titlepage(pid):
        return any(k in pid for k in ("扉页", "title", "cover"))

    prev_num = None
    out_of_order = []
    for pid in flat:
        if _is_titlepage(pid):
            continue
        n = _pid_num(pid)
        if prev_num is not None and n < prev_num:
            out_of_order.append(f"{pid}(#{n} < 前格#{prev_num})")
        prev_num = n
    if out_of_order:
        warnings.append("PAGES 正文引用顺序非递增(可能图文错位): " + ", ".join(out_of_order[:8]))

    missing_images = []
    for pid in flat:
        if not (panels_dir / f"{pid}.png").exists():
            missing_images.append(pid)
    if missing_images:
        failures.append("缺少画格图片: " + ", ".join(missing_images[:12]) + (" ..." if len(missing_images) > 12 else ""))

    missing_bubbles = [pid for pid in flat if pid not in bubbles]
    if missing_bubbles:
        failures.append("缺少配文 BUBBLE_CONFIG: " + ", ".join(missing_bubbles[:12]) + (" ..." if len(missing_bubbles) > 12 else ""))

    extra_bubbles = sorted(set(bubbles) - set(flat))
    if extra_bubbles:
        warnings.append("BUBBLE_CONFIG 中存在未使用 panel: " + ", ".join(extra_bubbles[:12]) + (" ..." if len(extra_bubbles) > 12 else ""))

    if pages:
        two_by_two = sum(1 for layout, _ids in pages if layout == "2x2")
        ratio = two_by_two / len(pages)
        if ratio > args.max_2x2_ratio:
            failures.append(f"2x2 页面占比过高: {two_by_two}/{len(pages)} = {ratio:.0%}，需要加入 full/hero/triple-row 等节奏页")

    for pid, entries in bubbles.items():
        if not isinstance(entries, (list, tuple)):
            failures.append(f"{pid} 的 BUBBLE_CONFIG 不是列表")
            continue
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                failures.append(f"{pid} 存在格式错误的气泡项: {entry!r}")
                continue
            role, text, pos = entry[:3]
            n = text_len(text)
            if not text:
                warnings.append(f"{pid} 存在空配文")
            if role in ("title", "sfx") and pos != "center":
                warnings.append(f"{pid} {role} 未居中({pos})，角落放置有贴边风险，建议 center")
            if role == "narration" and pos != "bottom":
                warnings.append(f"{pid} 旁白建议使用 bottom 横排: {text}")
            if role != "narration" and pos == "center" and n > 28:
                warnings.append(f"{pid} 居中竖排对白过长({n}字)，建议拆成两列/拆条/改位置")
            if role == "narration" and n > 52:
                warnings.append(f"{pid} 旁白偏长({n}字)，建议拆短")

    print(f"章节 QA: {cfg_path}")
    print(f"画格目录: {panels_dir}")
    print(f"PAGES: {len(pages)} 页 | panels引用: {len(flat)} | bubbles: {len(bubbles)}")
    print(f"FAIL: {len(failures)} | WARN: {len(warnings)}")

    if failures:
        print("\n失败项:")
        for item in failures:
            print(f"- {item}")
    if warnings:
        print("\n警告项:")
        for item in warnings[:40]:
            print(f"- {item}")
        if len(warnings) > 40:
            print(f"- ... 另有 {len(warnings) - 40} 条")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
