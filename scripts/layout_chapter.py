#!/usr/bin/env python3
"""排版输出 PDF

用法:
    python3 scripts/layout_chapter.py projects/example/pages_config_ch3.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comic_engine.config import load_config
from comic_engine.layout import ComicLayoutEngine
from comic_engine.utils import load_storyboard


def load_python_module(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_bubble_config(mod):
    """Layout uses BUBBLE_CONFIG; accept legacy BUBBLES as a fallback."""
    if hasattr(mod, "BUBBLE_CONFIG"):
        return getattr(mod, "BUBBLE_CONFIG")
    if hasattr(mod, "BUBBLES"):
        print("提示: 未找到 BUBBLE_CONFIG，使用旧变量 BUBBLES 作为配文配置")
        return getattr(mod, "BUBBLES")
    return {}


def main():
    parser = argparse.ArgumentParser(description="漫画排版输出 PDF")
    parser.add_argument("layout_config", help="排版配置或分镜脚本路径")
    parser.add_argument("--panels", "-p", default=None, help="画格输入目录")
    parser.add_argument("--output", "-o", default=None, help="页面输出目录")
    parser.add_argument("--config", "-c", default=None, help="引擎配置文件路径")
    parser.add_argument("--auto", "-a", action="store_true", help="自动布局模式（根据画格类型推荐布局）")
    args = parser.parse_args()

    config = load_config(args.config)
    layout_cfg_path = Path(args.layout_config)

    if args.auto:
        # 自动布局模式：从分镜脚本自动生成排版配置
        panels = load_storyboard(str(layout_cfg_path))
        panel_types = {}
        bubble_config = {}
        for pid, prompt, atype in panels:
            panel_types[pid] = atype
            bubble_config[pid] = [("normal", "", "bottom")]

        engine = ComicLayoutEngine(config)
        pages_config = engine.suggest_auto_layout(panels_list=panels)
        storyboard_mod = load_python_module(layout_cfg_path)
        authored_bubbles = get_bubble_config(storyboard_mod)
        if authored_bubbles:
            bubble_config = authored_bubbles

        panels_dir = Path(args.panels) if args.panels else layout_cfg_path.parent / "panels"
        output_dir = Path(args.output) if args.output else layout_cfg_path.parent / "pages"

        print(f"自动排版 {len(pages_config)} 页（{len(panels)} 格）")
        for pi, (lt, names) in enumerate(pages_config, 1):
            names_str = [n for n in names if n]
            print(f"  第{pi:02d}页: {lt:12s} {names_str}")
        print(f"\n画格目录: {panels_dir}")
        print(f"输出目录: {output_dir}\n")

        page_images = engine.render_pages(pages_config, bubble_config, panels_dir, output_dir)
        pdf_path = output_dir / f"{layout_cfg_path.stem.replace('storyboard_', 'ch')}.pdf"
        engine.export_pdf(page_images, pdf_path)
        print(f"\n✓ PDF 已导出: {pdf_path}")
        return

    # 动态加载排版配置
    sys.path.insert(0, str(layout_cfg_path.parent))
    mod_name = layout_cfg_path.stem
    mod = load_python_module(layout_cfg_path)

    pages_config = getattr(mod, "PAGES", [])
    bubble_config = get_bubble_config(mod)

    panels_dir = Path(args.panels) if args.panels else layout_cfg_path.parent / "panels"
    output_dir = Path(args.output) if args.output else layout_cfg_path.parent / "pages"

    print(f"排版 {len(pages_config)} 页")
    print(f"画格目录: {panels_dir}")
    print(f"输出目录: {output_dir}\n")

    engine = ComicLayoutEngine(config)
    page_images = engine.render_pages(pages_config, bubble_config, panels_dir, output_dir)

    pdf_path = output_dir / f"{mod_name.replace('pages_config_', '')}.pdf"
    engine.export_pdf(page_images, pdf_path)
    print(f"\n✓ PDF 已导出: {pdf_path}")


if __name__ == "__main__":
    main()
