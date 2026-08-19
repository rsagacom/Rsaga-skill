"""漫画排版引擎"""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import B5
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

from .utils import detect_platform_fonts, load_storyboard


def _resolve_font(config_key, default_path):
    """解析字体路径：如果配置的路径不存在，使用平台 fallback。"""
    if Path(default_path).exists():
        return default_path
    detected = detect_platform_fonts()
    fallback = detected.get(config_key, detected.get("body", default_path))
    if Path(fallback).exists():
        return fallback
    return default_path  # 最后的 fallback，会让 PIL 报明确错误


class ComicLayoutEngine:
    def __init__(self, config):
        self.cfg = config
        style = config.get("style", {})
        project = config.get("project", {})

        self.dpi = style.get("dpi", 150)
        mm_to_px = self.dpi / 25.4
        self.page_w = int(style.get("page_width_mm", 182) * mm_to_px)
        self.page_h = int(style.get("page_height_mm", 257) * mm_to_px)
        self.margin = int(style.get("margin_mm", 12) * mm_to_px)
        self.gutter = int(style.get("gutter_mm", 5) * mm_to_px)

        font_title = _resolve_font("title", project.get("font_title",
            "/System/Library/Fonts/STHeiti Medium.ttc"))
        font_body = _resolve_font("body", project.get("font_body",
            "/System/Library/Fonts/STHeiti Medium.ttc"))
        font_light = _resolve_font("light", project.get("font_light",
            "/System/Library/Fonts/STHeiti Light.ttc"))

        self.font_title_path = font_title
        self.font_body_path = font_body
        self.font_light_path = font_light
        self.font_title = ImageFont.truetype(font_title, 64)
        self.font_bubble = ImageFont.truetype(font_body, 40)
        self.font_small = ImageFont.truetype(font_body, 32)
        self.font_sfx = ImageFont.truetype(font_body, 88)
        self.font_page = ImageFont.truetype(font_light, 14)

        self.c_black = (20, 20, 20)
        self.c_white = (255, 255, 255)
        self.c_gray = (140, 140, 140)
        self.c_red = (200, 40, 40)

        random.seed(42)

    def draw_text_outline(self, draw, x, y, text, font, fill=None, outline=None, width=3):
        if fill is None:
            fill = self.c_black
        if outline is None:
            outline = self.c_white
        for r in range(width, 0, -1):
            for dx in [-r, 0, r]:
                for dy in [-r, 0, r]:
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), text, fill=outline, font=font)
        draw.text((x, y), text, fill=fill, font=font)

    def draw_vertical_text(self, draw, x, y, text, font, fill=None, outline=None, width=3, col_gap=10, max_h=600):
        """古籍竖排：从上往下读，从右往左列"""
        if fill is None:
            fill = self.c_black
        if outline is None:
            outline = self.c_white
        ch_h = font.getbbox("国")[3] - font.getbbox("国")[1] if font.getbbox("国") else font.size
        ch_w = font.getbbox("国")[2] - font.getbbox("国")[0] if font.getbbox("国") else font.size
        col_h = max_h
        max_chars_per_col = max(1, col_h // (ch_h + 2))

        chars = list(text)
        cols = [chars[i:i+max_chars_per_col] for i in range(0, len(chars), max_chars_per_col)]

        cx = x
        for col in cols:
            cy = y
            for ch in col:
                self.draw_text_outline(draw, cx, cy, ch, font, fill, outline, width)
                cy += ch_h + 2
            cx -= ch_w + col_gap  # 从右往左

    def wrap_text(self, text, font, max_width):
        lines = []
        for para in text.split("\n"):
            line = ""
            for ch in para:
                test = line + ch
                bbox = font.getbbox(test)
                tw = bbox[2] - bbox[0] if bbox else len(test) * 28
                if tw > max_width and line:
                    lines.append(line)
                    line = ch
                else:
                    line = test
            if line:
                lines.append(line)
        return lines

    def calc_text_size(self, lines, font):
        line_h = font.getbbox("国")[3] - font.getbbox("国")[1] if font.getbbox("国") else 32
        max_w = 0
        for line in lines:
            bbox = font.getbbox(line)
            w = bbox[2] - bbox[0] if bbox else len(line) * 28
            max_w = max(max_w, w)
        return max_w, len(lines) * line_h, line_h

    def draw_panel_border(self, draw, box, w=3):
        x1, y1, x2, y2 = box
        segs = 5
        edges = [
            [(x1 + (x2 - x1) * i / segs, y1 + random.randint(-1, 1)) for i in range(segs + 1)],
            [(x1 + (x2 - x1) * i / segs, y2 + random.randint(-1, 1)) for i in range(segs + 1)],
            [(x1 + random.randint(-1, 1), y1 + (y2 - y1) * i / segs) for i in range(segs + 1)],
            [(x2 + random.randint(-1, 1), y1 + (y2 - y1) * i / segs) for i in range(segs + 1)],
        ]
        for edge in edges:
            for i in range(len(edge) - 1):
                draw.line([edge[i], edge[i + 1]], fill=self.c_black, width=w)
            draw.line([edge[-2], edge[-1]], fill=self.c_black, width=w + 1)

    def load_panel(self, name, input_dir, bw, bh):
        path = Path(input_dir) / f"{name}.png"
        if not path.exists():
            return None
        img = Image.open(path).convert("RGB")
        iw, ih = img.size
        tr = bw / bh
        ir = iw / ih
        if ir > tr:
            nw = int(ih * tr)
            left = (iw - nw) // 2
            img = img.crop((left, 0, left + nw, ih))
        else:
            nh = int(iw / tr)
            top = (ih - nh) // 2
            img = img.crop((0, top, iw, top + nh))
        return img.resize((bw, bh), Image.LANCZOS)

    def layout_full(self, page, draw, names, input_dir):
        w = self.page_w - self.margin * 2
        h = self.page_h - self.margin * 2
        box = (self.margin, self.margin, self.margin + w, self.margin + h)
        img = self.load_panel(names[0], input_dir, w, h)
        if img:
            page.paste(img, (self.margin, self.margin))
        draw.rectangle((0, 0, self.page_w - 1, self.page_h - 1), outline=self.c_black, width=3)
        return [box]

    def layout_2x2(self, page, draw, names, input_dir):
        w = (self.page_w - self.margin * 2 - self.gutter) // 2
        h = (self.page_h - self.margin * 2 - self.gutter) // 2
        boxes = [
            (self.margin, self.margin, self.margin + w, self.margin + h),
            (self.margin + w + self.gutter, self.margin, self.page_w - self.margin, self.margin + h),
            (self.margin, self.margin + h + self.gutter, self.margin + w, self.page_h - self.margin),
            (self.margin + w + self.gutter, self.margin + h + self.gutter, self.page_w - self.margin, self.page_h - self.margin),
        ]
        for i, name in enumerate(names[:4]):
            img = self.load_panel(name, input_dir, w, h)
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def layout_1x2(self, page, draw, names, input_dir):
        w_full = self.page_w - self.margin * 2
        h_top = (self.page_h - self.margin * 2 - self.gutter) // 3
        h_bot = (self.page_h - self.margin * 2 - self.gutter) * 2 // 3
        w_bot = (w_full - self.gutter) // 2
        boxes = [
            (self.margin, self.margin, self.margin + w_full, self.margin + h_top),
            (self.margin, self.margin + h_top + self.gutter, self.margin + w_bot, self.page_h - self.margin),
            (self.margin + w_bot + self.gutter, self.margin + h_top + self.gutter, self.page_w - self.margin, self.page_h - self.margin),
        ]
        for i, name in enumerate(names):
            img = self.load_panel(name, input_dir, boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1])
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def layout_2x1x2(self, page, draw, names, input_dir):
        w_half = (self.page_w - self.margin * 2 - self.gutter) // 2
        h_row = (self.page_h - self.margin * 2 - self.gutter * 2) // 3
        boxes = [
            (self.margin, self.margin, self.margin + w_half, self.margin + h_row),
            (self.margin + w_half + self.gutter, self.margin, self.page_w - self.margin, self.margin + h_row),
            (self.margin, self.margin + h_row + self.gutter, self.margin + w_half, self.margin + h_row * 2 + self.gutter),
            (self.margin + w_half + self.gutter, self.margin + h_row + self.gutter, self.page_w - self.margin, self.margin + h_row * 2 + self.gutter),
            (self.margin, self.margin + h_row * 2 + self.gutter * 2, self.page_w - self.margin, self.page_h - self.margin),
        ]
        for i, name in enumerate(names):
            img = self.load_panel(name, input_dir, boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1])
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def layout_hero(self, page, draw, names, input_dir):
        """上大下二：1 格占上 65% + 2 格占下 35%"""
        w_full = self.page_w - self.margin * 2
        h_top = int((self.page_h - self.margin * 2 - self.gutter) * 0.65)
        h_bot = self.page_h - self.margin * 2 - self.gutter - h_top
        w_bot = (w_full - self.gutter) // 2
        boxes = [
            (self.margin, self.margin, self.margin + w_full, self.margin + h_top),
            (self.margin, self.margin + h_top + self.gutter, self.margin + w_bot, self.page_h - self.margin),
            (self.margin + w_bot + self.gutter, self.margin + h_top + self.gutter, self.page_w - self.margin, self.page_h - self.margin),
        ]
        for i, name in enumerate(names[:3]):
            img = self.load_panel(name, input_dir, boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1])
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def layout_triple_row(self, page, draw, names, input_dir):
        """三等分行：3 格上下排列"""
        w_full = self.page_w - self.margin * 2
        h_each = (self.page_h - self.margin * 2 - self.gutter * 2) // 3
        boxes = []
        for i in range(3):
            y = self.margin + i * (h_each + self.gutter)
            boxes.append((self.margin, y, self.margin + w_full, y + h_each))
        for i, name in enumerate(names[:3]):
            img = self.load_panel(name, input_dir, boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1])
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def layout_1plus3(self, page, draw, names, input_dir):
        """1 宽幅 + 3 小格：上方一横条 + 下方三格"""
        w_full = self.page_w - self.margin * 2
        h_top = int((self.page_h - self.margin * 2 - self.gutter * 2) * 0.35)
        h_bot = self.page_h - self.margin * 2 - self.gutter * 2 - h_top
        w3 = (w_full - self.gutter * 2) // 3
        boxes = [(self.margin, self.margin, self.margin + w_full, self.margin + h_top)]
        for i in range(3):
            x = self.margin + i * (w3 + self.gutter)
            boxes.append((x, self.margin + h_top + self.gutter, x + w3, self.page_h - self.margin))
        for i, name in enumerate(names[:4]):
            img = self.load_panel(name, input_dir, boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1])
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def layout_big_top_2(self, page, draw, names, input_dir):
        """上大下二平分：1 格占上 60% + 2 格平分下 40%"""
        w_full = self.page_w - self.margin * 2
        h_top = int((self.page_h - self.margin * 2 - self.gutter) * 0.6)
        h_bot = self.page_h - self.margin * 2 - self.gutter - h_top
        w_bot = (w_full - self.gutter) // 2
        boxes = [
            (self.margin, self.margin, self.margin + w_full, self.margin + h_top),
            (self.margin, self.margin + h_top + self.gutter, self.margin + w_bot, self.page_h - self.margin),
            (self.margin + w_bot + self.gutter, self.margin + h_top + self.gutter, self.page_w - self.margin, self.page_h - self.margin),
        ]
        for i, name in enumerate(names[:3]):
            img = self.load_panel(name, input_dir, boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1])
            if img:
                page.paste(img, (boxes[i][0], boxes[i][1]))
            self.draw_panel_border(draw, boxes[i])
        return boxes[:len(names)]

    def _fit_font(self, font_path, start_size, min_size, fits):
        """Return the largest font size that satisfies the supplied predicate."""
        for size in range(start_size, min_size - 1, -2):
            font = ImageFont.truetype(font_path, size)
            if fits(font):
                return font
        return ImageFont.truetype(font_path, min_size)

    def _vertical_metrics(self, text, font, max_h, col_gap=10):
        bbox = font.getbbox("国")
        ch_w = bbox[2] - bbox[0] if bbox else font.size
        ch_h = bbox[3] - bbox[1] if bbox else font.size
        max_chars_per_col = max(1, max_h // (ch_h + 2))
        n_cols = max(1, (len(text) + max_chars_per_col - 1) // max_chars_per_col)
        return ch_w, ch_h, n_cols

    def _place_overlay_box(self, panel_box, box_w, box_h, position):
        x1, y1, x2, y2 = panel_box
        inset = 12
        position = (position or "auto").lower()
        positions = {
            "top-left": (x1 + inset, y1 + inset),
            "top-right": (x2 - box_w - inset, y1 + inset),
            "bottom-left": (x1 + inset, y2 - box_h - inset),
            "bottom-right": (x2 - box_w - inset, y2 - box_h - inset),
            "left": (x1 + inset, (y1 + y2 - box_h) // 2),
            "right": (x2 - box_w - inset, (y1 + y2 - box_h) // 2),
            "top": ((x1 + x2 - box_w) // 2, y1 + inset),
            "center": ((x1 + x2 - box_w) // 2, (y1 + y2 - box_h) // 2),
        }
        if position in positions:
            bx, by = positions[position]
        else:
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            corners = [
                (x1 + inset, y1 + inset),
                (x2 - box_w - inset, y1 + inset),
                (x1 + inset, y2 - box_h - inset),
                (x2 - box_w - inset, y2 - box_h - inset),
            ]
            bx, by = max(
                corners,
                key=lambda p: abs(p[0] + box_w // 2 - cx) + abs(p[1] + box_h // 2 - cy),
            )
        bx = max(x1 + inset, min(bx, x2 - box_w - inset))
        by = max(y1 + inset, min(by, y2 - box_h - inset))
        return bx, by

    def draw_center_label(self, draw, box, text, font_path, start_size, fill=None, outline=None, width=4):
        """Draw fitted horizontal title/SFX text centered in a panel."""
        if fill is None:
            fill = self.c_black
        if outline is None:
            outline = self.c_white
        x1, y1, x2, y2 = box
        max_w = int((x2 - x1) * 0.82)
        max_h = int((y2 - y1) * 0.35)

        def fits(font):
            lines = text.split("\n")
            widths = []
            total_h = 0
            for line in lines:
                bbox = font.getbbox(line)
                widths.append(bbox[2] - bbox[0])
                total_h += bbox[3] - bbox[1] + 4
            return max(widths or [0]) <= max_w and total_h <= max_h

        font = self._fit_font(font_path, start_size, 28, fits)
        lines = text.split("\n")
        line_boxes = [font.getbbox(line) for line in lines]
        line_heights = [(b[3] - b[1]) for b in line_boxes]
        total_h = sum(line_heights) + 4 * (len(lines) - 1)
        y = (y1 + y2 - total_h) // 2
        for line, bbox, lh in zip(lines, line_boxes, line_heights):
            lw = bbox[2] - bbox[0]
            x = (x1 + x2 - lw) // 2
            self.draw_text_outline(draw, x, y, line, font, fill=fill, outline=outline, width=width)
            y += lh + 4

    def draw_bubble(self, draw, box, style, text, position="auto", tail_dir="bottom"):
        """古籍竖排对话框：从上往下读，从右往左列"""
        x1, y1, x2, y2 = box
        position = position or "auto"

        if style in ("title", "sfx") and position == "center":
            font_path = self.font_title_path if style == "title" else self.font_body_path
            start_size = 72 if style == "title" else 88
            self.draw_center_label(draw, box, text, font_path, start_size)
            return
        # 普通/超自然对白的 center：尊重 BUBBLE_CONFIG 居中竖排。
        # 长文本 center 会在 render_pages 层提前转 bottom 横排条(避免压脸)，
        # 走到这里的是短文本，单列窄框居中不压脸。

        font_path = self.font_title_path if style == "title" else self.font_body_path
        start_size = 64 if style == "title" else 40
        if style == "sfx":
            start_size = 72

        panel_w = x2 - x1
        panel_h = y2 - y1
        max_box_w = int(panel_w * 0.78)
        max_box_h = int(panel_h * 0.92)

        def fits(font):
            pad = int(font.size * 0.6)
            max_h = max(40, max_box_h - pad * 2)
            ch_w, _ch_h, n_cols = self._vertical_metrics(text, font, max_h)
            needed_w = n_cols * (ch_w + 10) + pad * 2
            return needed_w <= max_box_w

        font = self._fit_font(font_path, start_size, 24, fits)
        pad = int(font.size * 0.6)
        col_h = max(40, max_box_h - pad * 2)
        ch_w, _ch_h, n_cols = self._vertical_metrics(text, font, col_h)
        box_w = min(n_cols * (ch_w + 10) + pad * 2, max_box_w)
        box_h = min(col_h + pad * 2, max_box_h)
        bx, by = self._place_overlay_box(box, box_w, box_h, position)

        # 竖排绘制文字（古籍风格：从上往下，从右往左）
        text_x = bx + box_w - pad
        text_y = by + pad
        max_h = box_h - pad * 2

        if style == "supernatural":
            draw.rounded_rectangle((bx, by, bx + box_w, by + box_h), radius=12, fill=None, outline=self.c_white, width=2)
            self.draw_vertical_text(draw, text_x, text_y, text, font, fill=self.c_white, outline=self.c_black, width=3, max_h=max_h)
        elif style == "sfx":
            draw.rounded_rectangle((bx, by, bx + box_w, by + box_h), radius=12, fill=None, outline=self.c_black, width=3)
            self.draw_vertical_text(draw, text_x, text_y, text, font, fill=self.c_black, outline=self.c_white, width=4, max_h=max_h)
        elif style == "title":
            draw.rounded_rectangle((bx, by, bx + box_w, by + box_h), radius=12, fill=None, outline=self.c_black, width=3)
            self.draw_vertical_text(draw, text_x, text_y, text, font, fill=self.c_black, outline=self.c_white, width=4, max_h=max_h)
        else:
            draw.rounded_rectangle((bx, by, bx + box_w, by + box_h), radius=12, fill=None, outline=self.c_black, width=3)
            self.draw_vertical_text(draw, text_x, text_y, text, font, fill=self.c_black, outline=self.c_white, width=3, max_h=max_h)

    def draw_narration(self, page, draw, box, text):
        x1, y1, x2, y2 = box
        lines = self.wrap_text(text, self.font_small, x2 - x1 - 30)
        tw, th, line_h = self.calc_text_size(lines, self.font_small)
        pad = 28
        bar_h = th + pad * 2
        bar_y = y2 - bar_h - 5
        overlay = Image.new("RGBA", (x2 - x1, bar_h), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        ov_draw.rectangle((0, 0, x2 - x1, bar_h), fill=(15, 15, 15, 220))
        page.paste(Image.blend(page.crop((x1, bar_y, x2, bar_y + bar_h)).convert("RGBA"), overlay, 0.9), (x1, bar_y))
        draw = ImageDraw.Draw(page)
        ty = bar_y + pad
        for line in lines:
            bbox = self.font_small.getbbox(line)
            lw = bbox[2] - bbox[0] if bbox else 0
            tx = x1 + (x2 - x1 - lw) // 2
            draw.text((tx, ty), line, fill=self.c_white, font=self.font_small)
            ty += line_h

    def suggest_auto_layout(self, storyboard_path=None, panels_list=None, panel_types=None):
        """根据画格类型和剧情节奏自动推荐排版布局

        核心规则：
        - 关键剧情时刻（supernatural/emotional peak）→ full 全页大格
        - 情绪递进序列（连续 character 同情绪单元）→ triple-row 或 hero
        - 生死/剧烈疼痛/恐惧/情绪波动大 → full 特写全页
        - 动作/死亡场景 → hero 上大下二
        - 对话/日常 → 2x2 标准四格
        - 超自然实体首次出现 → full 全页

        返回：PAGES 列表 [(layout_name, [panel_names]), ...]
        """
        if panel_types is None and storyboard_path:
            panels = load_storyboard(storyboard_path)
            panel_types = {}
            for pid, prompt, atype in panels:
                panel_types[pid] = atype

        if panel_types is None and panels_list:
            panel_types = {}
            for pid, prompt, atype in panels_list:
                panel_types[pid] = atype

        if not panel_types:
            return []

        # 按叙事顺序排列的 panel_id 列表
        sorted_pids = sorted(panel_types.keys(), key=lambda x: int(''.join(filter(str.isdigit, x)) or '0'))

        pages = []
        i = 0
        while i < len(sorted_pids):
            pid = sorted_pids[i]
            atype = panel_types.get(pid, "scene")
            pid_num = int(''.join(filter(str.isdigit, pid)) or '0')

            # 规则1：超自然实体首次出现 → full 全页
            if atype == "supernatural" and self._is_first_of_type(pid, "supernatural", sorted_pids, panel_types):
                pages.append(("full", [pid]))
                i += 1
                continue

            # 规则2：连续 character 情绪递进（同一情绪高潮）→ hero 或 triple-row
            next_pids = sorted_pids[i:i+5]
            char_count = sum(1 for p in next_pids if panel_types.get(p) == "character")
            if char_count >= 3 and atype == "character":
                # 取连续3个 character 格用 hero 布局
                batch = []
                for j in range(i, min(i+3, len(sorted_pids))):
                    batch.append(sorted_pids[j])
                pages.append(("hero", batch))
                i += len(batch)
                continue

            # 规则3：生死/剧烈疼痛/恐惧/情绪波动大 → full 特写全页
            high_emotion_keywords = ["死", "恐惧", "惨叫", "痛苦", "尖叫", "窒息",
                                      "恐慌", "僵", "断裂", "血", "崩溃", "绝望"]
            prompt_text = ""
            if panels_list and i < len(panels_list):
                prompt_text = panels_list[i][1]
            if atype in ("character", "abstract") and any(kw in prompt_text for kw in high_emotion_keywords):
                pages.append(("full", [pid]))
                i += 1
                continue

            # 规则4：abstract 关键画面 → full
            if atype == "abstract" and pid_num in [11, 26, 29, 37, 39, 60, 65, 89, 98, 111]:
                pages.append(("full", [pid]))
                i += 1
                continue

            # 规则4：剩余格按 2x2 打包
            batch = sorted_pids[i:min(i+4, len(sorted_pids))]
            if len(batch) == 1:
                pages.append(("full", batch))
            elif len(batch) == 2:
                pages.append(("1x2", batch))
            elif len(batch) == 3:
                pages.append(("hero", batch))
            else:
                pages.append(("2x2", batch))
            i += len(batch)

        return pages

    def _is_first_of_type(self, pid, atype, sorted_pids, panel_types):
        """检查该画格是否是某种类型的首次出现"""
        for p in sorted_pids:
            if panel_types.get(p) == atype:
                return p == pid
        return False

    def render_pages(self, pages_config, bubble_config, input_dir, output_dir):
        """渲染所有页面

        Args:
            pages_config: list of (layout_name, [panel_names])
            bubble_config: dict of panel_name -> [(style, text, position), ...]
            input_dir: panels directory
            output_dir: pages output directory
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        layouts = {
            "full": self.layout_full,
            "spotlight": self.layout_full,  # 特写单页，同 full 但语义不同
            "2x2": self.layout_2x2,
            "1x2": self.layout_1x2,
            "2x1x2": self.layout_2x1x2,
            "hero": self.layout_hero,
            "triple-row": self.layout_triple_row,
            "1+3": self.layout_1plus3,
            "big-top-2": self.layout_big_top_2,
        }

        page_images = []
        for page_num, (layout_name, panel_names) in enumerate(pages_config, 1):
            page = Image.new("RGB", (self.page_w, self.page_h), self.c_white)
            draw = ImageDraw.Draw(page)
            layout_fn = layouts[layout_name]
            boxes = layout_fn(page, draw, panel_names, input_dir)

            for i, name in enumerate(panel_names):
                if i >= len(boxes):
                    continue
                box = boxes[i]
                cfgs = bubble_config.get(name, [])
                for cfg in cfgs:
                    if len(cfg) == 3:
                        style, text, pos = cfg
                        # 尊重 BUBBLE_CONFIG 位置：
                        # - bottom → 横排旁白条
                        # - title/sfx 的 center → 居中横排大字
                        # - 其余 center 短文本 → 居中竖排窄框；长文本自动转 bottom(避免压脸)
                        if pos == "bottom":
                            self.draw_narration(page, draw, box, text)
                        elif pos == "center" and style not in ("title", "sfx") and len(str(text)) >= 10:
                            self.draw_narration(page, draw, box, text)
                        else:
                            self.draw_bubble(draw, box, style, text, pos)
                    else:
                        style, text = cfg
                        self.draw_bubble(draw, box, style, text)

            page_path = output_dir / f"page_{page_num:02d}.png"
            page.save(page_path, "PNG", dpi=(self.dpi, self.dpi))
            page_images.append(page_path)
            print(f"  第 {page_num:02d}/{len(pages_config)} 页 ({layout_name}, {len(panel_names)}格) ... ✓")

        return page_images

    def export_pdf(self, page_images, pdf_path):
        """导出 PDF"""
        c = canvas.Canvas(str(pdf_path), pagesize=B5)
        w, h = B5
        for img_path in page_images:
            c.drawImage(str(img_path), 0, 0, width=w, height=h)
            c.showPage()
        c.save()
