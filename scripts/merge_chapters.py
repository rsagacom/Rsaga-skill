#!/usr/bin/env python3
"""合并 1-9 章漫画 PDF 为单个文件"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import B5
from reportlab.pdfgen import canvas

def merge_pdfs(chapter_pdfs, output_path):
    """合并多个单章 PDF 为一个"""
    # 简单方案：将所有 PNG 页面收集后重新生成一个 PDF
    all_pages = []
    for ch_name, pdf_path in chapter_pdfs:
        pages_dir = Path(pdf_path).parent
        pngs = sorted(pages_dir.glob("page_*.png"))
        for png in pngs:
            all_pages.append((ch_name, png))

    if not all_pages:
        print("No pages found!")
        return

    c = canvas.Canvas(str(output_path), pagesize=B5)
    w, h = B5

    for ch_name, png_path in all_pages:
        # 每章首页可加书签（简化版直接画图）
        c.drawImage(str(png_path), 0, 0, width=w, height=h)
        c.showPage()

    c.save()
    return len(all_pages)

if __name__ == "__main__":
    desktop = Path.home() / "Desktop"
    chapters = []
    for ch in range(1, 10):
        ch_dir = desktop / f"comic-chapter{ch}-v0.3.0"
        pdf = ch_dir / "pages" / f"桥底的溃烂神明_第{ch}章_v0.3.0.pdf"
        if pdf.exists():
            chapters.append((f"第{ch}章", pdf))
        else:
            # try old v7 format
            old_dir = desktop / f"comic-chapter{ch}-v7-final"
            old_pdf = list(old_dir.glob("pages/*.pdf")) if old_dir.exists() else []
            if old_pdf:
                chapters.append((f"第{ch}章(v7)", old_pdf[0]))

    if not chapters:
        print("No chapter PDFs found!")
        sys.exit(1)

    print(f"Found {len(chapters)} chapters:")
    for name, path in chapters:
        print(f"  {name}: {path}")

    output = desktop / "桥底的溃烂神明_全九章.pdf"
    pages = merge_pdfs(chapters, output)
    print(f"\nMerged {pages} pages → {output} ({output.stat().st_size//1024//1024}MB)")
