---
name: novel-to-comic
description: 小说转漫画引擎 - 将中文长篇小说章节转换为东亚风格黑白漫画（水墨、B5判、含对话框/旁白/SFX）。支持逐句解析分镜、批量生图、多模态视觉审核、自动修复问题画格、排版输出 PDF。
version: 0.1.0
metadata:
  working_dir: /Volumes/AJW-Data/Projects/novel-to-comic-engine
  requires:
    env:
      - STEP_API_KEY
      - KIMI_API_KEY
---

# 小说转漫画引擎

将中文小说章节转换为完整漫画分镜稿的端到端工具链。

## 工作流程

```
小说原文 → 分镜脚本 → 批量生图 → 视觉审核 → 修复问题 → 排版输出 PDF
```

### Step 1: 读取小说
读取 `projects/<项目名>/chapter_XX.md`，理解剧情结构、角色、情绪转折。

### Step 2: 生成分镜脚本
创建 `projects/<项目名>/storyboard_chXX.py`，包含 `PANELS` 列表。

每个元素为元组：`(画格名称, 英文 prompt)`

```python
PANELS = [
    ('P01_扉页', 'Wide shot dark background, ... Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic.'),
    ('P02_画格1_场景名', 'Close-up <角色描述> ... Manhua ink wash, ...'),
]
```

### Step 3: 批量生图
```bash
python3 scripts/generate_panels.py projects/<项目名>/storyboard_chXX.py
```

### Step 4: 视觉审核
```bash
python3 scripts/audit_panels.py projects/<项目名>/panels
```

审核规则（5类）：
- `qiyuan`：主角外貌一致性（眼镜/发型/服装）
- `supernatural`：超自然实体（无面部/发光眼窝/半透明）
- `hand`：手部质量（5指/无畸形）
- `scene`：场景合规（无儿童/无无关人物）
- `abstract`：抽象画面（无双影/无撕裂）

### Step 5: 修复问题画格
```bash
python3 scripts/fix_panel.py projects/<项目名>/panels/PXX_名称.png "修正后的 prompt"
```

### Step 6: 排版输出
1. 创建 `projects/<项目名>/pages_config_chXX.py`，定义 `PAGES` 和 `BUBBLE_CONFIG`
2. 运行排版脚本输出 PNG 页面 + PDF

```bash
python3 scripts/layout_chapter.py projects/<项目名>/pages_config_chXX.py
```

## Prompt 规范

1. **画风后缀**（所有 prompt 必须以固定后缀结尾）：
   ```
   Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic.
   ```

2. **主角外貌**（精简嵌入，<60 词）：
   ```
   Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs,
   beige casual blazer, dark blue V-neck shirt, old black backpack,
   tired hollow eyes with dark circles, pale skin.
   ```

3. **超自然实体约束**：
   - `NO facial features`, `NO mouth, NO nose`
   - `ONLY empty glowing cyan eye sockets`
   - `semi-transparent shadowy form`, `edges dissolving into smoke`

4. **场景安全**：
   - 空旷场景：`NO people, NO crowd`
   - 废弃儿童设施：`NO children`
   - 手部特写：`simple normal hand shape, five fingers`

5. **情绪嵌入**：
   - 恐怖/紧张：`fear`, `panic`, `terror`, `despair`
   - 平静/日常：`melancholic`, `exhausted`, `detached`

## 排版规范

- 尺寸：B5判 182mm × 257mm @ 150dpi
- 字体：`/System/Library/Fonts/STHeiti Medium.ttc`
- 对话框文字 ≥ 56px，旁白 ≥ 40px，SFX ≥ 96px
- 所有文字必须有白色描边（3-5层），确保可读性
- 布局类型：`full` / `2x2` / `1x2` / `2x1x2`

## 安全提示

- 不要读取或展示用户的 `config.yaml`（含 API key）
- 大文件输出默认放在外置硬盘
- API 调用可能触发 429 限流，脚本已内置重试机制

## 快速命令参考

| 操作 | 命令 |
|------|------|
| 生成画格 | `python3 scripts/generate_panels.py <分镜脚本路径>` |
| 视觉审核 | `python3 scripts/audit_panels.py <画格目录>` |
| 修复画格 | `python3 scripts/fix_panel.py <画格路径> "新 prompt"` |
| 排版输出 | `python3 scripts/layout_chapter.py <排版配置>` |
| 审核报告 | `python3 scripts/audit_panels.py <画格目录> --output report.md` |
