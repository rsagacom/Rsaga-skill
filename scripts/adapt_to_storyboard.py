#!/usr/bin/env python3
"""DS v4 Pro 紧凑改编层 → 完整分镜脚本（含英文 prompt）"""
import sys, re
from pathlib import Path

# v7 角色外貌（<200 字符，留空间给场景描述+画风后缀）
QI = (
    'Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs, '
    'beige casual blazer, dark blue V-neck shirt, old black backpack, '
    'tired hollow eyes with dark circles, pale skin'
)
# prompt 内嵌的完整版（加一致性锚点，拼接用）
QI_FULL = QI + ', exact same character, same short black hair bangs, same thin glasses, same beige blazer'
W = 'Woman with very long straight black hair flowing down past waist, dark trench coat, seen from behind only, NO face visible'
L = 'Shadowy humanoid form, NO facial features, NO mouth NO nose, ONLY empty glowing cyan eye sockets, semi-transparent shadowy body, edges dissolving into smoke'
S = 'Manhua ink wash comic art style, black white grayscale, dramatic lighting, G-pen linework, manga illustration, cel shaded, 2D comic art, NOT photograph, NOT photorealistic, NOT 3D render, NOT realistic face'

CHAR_MAP = {'祁思远': 'Q', '房东': 'L', '租客': 'L', '女人': 'W', 'narration': 'narration'}

def generate_prompt(role, emotion, keywords, atype):
    """v7 风格：角色外貌嵌入，<400 字符"""
    role_en = CHAR_MAP.get(role, 'narration')
    kw = keywords.replace('/', ' ').replace('_', ' ')
    # 精简版 QI（<250 char）+ 场景 + 后缀 = 总共 <400 char
    qi_short = f'{QI}, {kw}, {emotion}. {S}'
    qi_char = f'{QI}, exact same character, {kw}, {emotion}. {S}'
    li_p = f'{L}, {kw}, {emotion}. {S}'
    wi_p = f'{W}, exactly same woman, seen from behind, NO face, {kw}, {emotion}. {S}'

    if atype == 'character':
        if role_en == 'Q' or role_en == 'narration':
            return qi_char
        elif role_en == 'L':
            return li_p
        elif role_en == 'W':
            return wi_p
        return kw + ', ' + emotion + '. ' + S

    elif atype == 'supernatural':
        if role_en == 'L':
            return li_p
        return kw + ', ' + emotion + ', supernatural atmosphere. ' + S

    elif atype == 'hand':
        return 'Close-up ' + QI + ' hand, ' + kw + ', simple normal hand shape five fingers, ' + emotion + '. ' + S

    elif atype == 'abstract':
        if role_en == 'Q' or role_en == 'narration':
            return 'Abstract composition, ' + qi_short + ', NO double image, NO overlapping, single figure only'
        return 'Abstract composition: ' + kw + ', ' + emotion + '. ' + S

    elif atype == 'scene':
        if role_en == 'Q' or role_en == 'narration':
            return 'Wide shot ' + qi_short
        return 'Wide shot ' + kw + ', ' + emotion + '. NO children unless specified. ' + S

    return kw + ', ' + emotion + '. ' + S

def parse_adaptation(filepath):
    """解析紧凑改编层"""
    panels = []
    bubbles = {}

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('格号') or line.startswith('#') or line.startswith('=') or line.startswith('---'):
                continue
            parts = line.split('|')
            if len(parts) < 6:
                continue

            num = parts[0].strip()
            role = parts[1].strip()
            text = parts[2].strip().strip('"').strip("'")
            emotion = parts[3].strip()
            atype = parts[4].strip()
            keywords = parts[5].strip() if len(parts) > 5 else ''

            # 生成格名
            # 截取文本前6字作为格名
            short_name = text[:6].replace('"', '').replace("'", '').replace(' ', '').replace('\n', '')
            if not short_name:
                short_name = keywords[:6].replace('/', '')
            name = f'P{num}_{short_name}'

            # 生成英文 prompt
            prompt = generate_prompt(role, emotion, keywords, atype)

            panels.append((name, prompt, atype))

            # BUBBLES
            if role == 'narration':
                bubble_role = 'narration'
                pos = 'bottom'
            elif role in ('房东', '租客'):
                bubble_role = 'supernatural'
                pos = 'center'
            else:
                bubble_role = role
                pos = 'bottom'

            if text:
                bubbles[name] = [(bubble_role, text, pos)]

    return panels, bubbles

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 adapt_to_storyboard.py <adaptation.txt> [output.py]")
        sys.exit(1)

    adapt_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else adapt_file.replace('.txt', '_storyboard.py')

    panels, bubbles = parse_adaptation(adapt_file)

    with open(output_file, 'w') as f:
        f.write('#!/usr/bin/env python3\n')
        f.write(f'"""DS v4 Pro 改编 — {len(panels)} 格"""\n')
        # v7 风格：角色变量在文件顶部分配，prompt 里用字符串拼接
        f.write(f'QI="{QI}"\n')
        f.write(f'W="{W}"\n')
        f.write(f'L="{L}"\n')
        f.write(f'S="{S}"\n\n')
        f.write('PANELS = [\n')
        for name, prompt, atype in panels:
            f.write(f'    ("{name}", {prompt!r}, "{atype}"),\n')
        f.write(']\n\n')
        f.write('BUBBLES = {\n')
        for name, entries in bubbles.items():
            f.write(f'    "{name}": {entries!r},\n')
        f.write('}\n')

    print(f'{len(panels)} panels → {output_file}')

if __name__ == '__main__':
    main()
