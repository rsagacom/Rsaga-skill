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


def main():
    parser = argparse.ArgumentParser(description="漫画排版输出 PDF")
    parser.add_argument("layout_config", help="排版配置文件路径（Python 模块，需包含 PAGES 和 BUBBLE_CONFIG）")
    parser.add_argument("--panels", "-p", default=None, help="画格输入目录")
    parser.add_argument("--output", "-o", default=None, help="页面输出目录")
    parser.add_argument("--config", "-c", default=None, help="引擎配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)
    layout_cfg_path = Path(args.layout_config)

    # 动态加载排版配置
    sys.path.insert(0, str(layout_cfg_path.parent))
    mod_name = layout_cfg_path.stem
    import importlib.util
    spec = importlib.util.spec_from_file_location(mod_name, layout_cfg_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    pages_config = getattr(mod, "PAGES", [])
    bubble_config = getattr(mod, "BUBBLE_CONFIG", {})

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
