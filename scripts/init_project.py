#!/usr/bin/env python3
"""初始化新漫画项目

用法:
    python3 scripts/init_project.py <项目名>
    python3 scripts/init_project.py my-novel --chapters 3
"""
import argparse
import sys
from pathlib import Path


STORYBOARD_TEMPLATE = '''#!/usr/bin/env python3
"""{chapter_title} 分镜脚本"""

# 格式: (画格名称, 英文 prompt)
# 或带审核类型: (画格名称, 英文 prompt, 审核类型)
# 审核类型: character, supernatural, hand, scene, abstract
PANELS = [
    ("P01_扉页", "Wide shot abstract dark background, misty atmosphere, ... Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."),
    # ("P02_画格1_场景名", "Close-up <角色描述>... Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."),
    # 更多画格...
]
'''

PAGES_CONFIG_TEMPLATE = '''#!/usr/bin/env python3
"""{chapter_title} 排版配置"""

from pathlib import Path

# 路径配置
INPUT_DIR = Path("projects/{project_name}/panels")
OUTPUT_DIR = Path("projects/{project_name}/pages")
DPI = 150
# B5判 182mm × 257mm @ 150dpi
PAGE_W = 1363
PAGE_H = 1920
MARGIN = 71
GUTTER = 30

# 布局类型: full / 2x2 / 1x2 / 2x1x2
PAGES = [
    ("full", ["P01_扉页"]),
    # ("2x2", ["P02_画格1", "P02_画格2", "P02_画格3", "P02_画格4"]),
    # ("1x2", ["P03_画格1", "P03_画格2", "P03_画格3"]),
]

BUBBLE_CONFIG = {{
    "P01_扉页": [("title", "{chapter_title}", "center")],
    # "P02_画格1": [("narration", "旁白文字", "bottom")],
    # "P02_画格2": [("supernatural", "角色：对白", "center")],
    # "P02_画格3": [("sfx", "轰隆——", "center")],
}}
'''

AUDIT_RULES_TEMPLATE = '''# 项目审核规则
# 覆盖默认规则（character, supernatural, hand, scene, abstract）
# 添加自定义审核类型

# character:
#   description: "祁思远外貌一致性"
#   keywords: ["祁思远", "主角", "青年", "眼镜"]
#   prompt: |
#     审核要求：检查图中男子是否符合角色设定。
#     角色设定：Young Chinese man 25yo, thin black-frame glasses, short black hair...
#     请逐一检查：
#     1. 发型是否为黑色短发带刘海？
#     2. 是否佩戴细黑框眼镜？
#     3. 服装是否为米色休闲西装+深蓝色V领衬衫？
#     4. 画面是否出现重影/叠加/双重人像？
#     只输出发现的问题，每条一行。如无问题，输出「通过」。
'''

CHAPTER_TEMPLATE = '''# {chapter_title}

（在此粘贴小说原文）

<!--
使用流程：
1. 在 Claude Code 中说「读取 projects/{project_name}/chapter_01.md，生成改编层和分镜脚本」
2. CC 会先生成 adaptation_ch01.md，再生成 storyboard_ch01.py
3. 检查 storyboard 后说「开始生成第一章画格」
4. 生成完毕后说「审核第一章画格」
5. 修复问题画格后说「排版第一章」
-->
'''


def main():
    parser = argparse.ArgumentParser(description="初始化新漫画项目")
    parser.add_argument("name", help="项目名称（kebab-case，如 my-novel）")
    parser.add_argument("--chapters", "-n", type=int, default=1, help="章节数量（默认 1）")
    parser.add_argument("--first-title", default="第一章", help="首章标题")
    args = parser.parse_args()

    # 验证项目名
    name = args.name.strip().lower().replace(" ", "-")
    if not name:
        print("错误：项目名不能为空")
        sys.exit(1)

    project_dir = Path(__file__).parent.parent / "projects" / name

    if project_dir.exists():
        print(f"错误：项目目录已存在: {project_dir}")
        sys.exit(1)

    project_dir.mkdir(parents=True)

    # 生成章节模板
    for i in range(1, args.chapters + 1):
        ch_num = f"{i:02d}"
        chapter_title = args.first_title if i == 1 else f"第{i}章"
        var_name = f"ch{ch_num}"

        # chapter_XX.md
        ch_path = project_dir / f"chapter_{ch_num}.md"
        ch_path.write_text(CHAPTER_TEMPLATE.format(
            chapter_title=chapter_title,
            project_name=name
        ), encoding="utf-8")

        # storyboard_chXX.py
        sb_path = project_dir / f"storyboard_ch{ch_num}.py"
        sb_path.write_text(STORYBOARD_TEMPLATE.format(
            chapter_title=chapter_title
        ), encoding="utf-8")

        # pages_config_chXX.py
        pc_path = project_dir / f"pages_config_ch{ch_num}.py"
        pc_path.write_text(PAGES_CONFIG_TEMPLATE.format(
            chapter_title=chapter_title,
            project_name=name
        ), encoding="utf-8")

    # audit_rules.yaml
    audit_path = project_dir / "audit_rules.yaml"
    audit_path.write_text(AUDIT_RULES_TEMPLATE, encoding="utf-8")

    # 输出目录
    (project_dir / "panels").mkdir(exist_ok=True)
    (project_dir / "pages").mkdir(exist_ok=True)

    print(f"✓ 项目已创建: {project_dir}")
    print(f"  章节数: {args.chapters}")
    print()
    print("下一步:")
    print(f"  1. 编辑 projects/{name}/chapter_01.md — 粘贴小说原文")
    print(f"  2. 在 Claude Code 中说「读取 projects/{name}/chapter_01.md，生成改编层和分镜脚本」")
    print(f"  3. 检查 storyboard_ch01.py 后说「开始生成画格」")


if __name__ == "__main__":
    main()
