---
name: novel-to-comic
description: 小说转漫画引擎 - 将中文长篇小说章节转换为东亚风格黑白漫画（水墨、B5判、含对话框/旁白/SFX）。支持改编层（1-2句/格粒度控制）、逐句解析分镜、批量生图、多模态视觉审核、自动修复问题画格、排版输出 PDF。
version: 0.2.0
metadata:
  working_dir: .
  requires:
    env:
      - STEP_API_KEY
      - KIMI_API_KEY
---

# 小说转漫画引擎

将中文小说章节转换为完整漫画分镜稿的端到端工具链。

## 工作流程

```
小说原文 → 改编层 → 分镜脚本 → 批量生图 → 视觉审核 → 修复问题 → 排版输出 PDF
```

### Step 1: 读取小说
读取 `projects/<项目名>/chapter_XX.md`，理解剧情结构、角色、情绪转折。

### Step 1.5: 改编层（⚠️ 关键，不可跳过）

**这是漫画编剧步骤，不是简单的文本截取。**

在生成分镜脚本之前，必须先产出 `projects/<项目名>/adaptation_chXX.md`，包含：
- 选取的小说原文段落（标注来源行号）
- 改编后的漫画旁白/对白（每条独立成段）
- 情绪标注（恐惧/麻木/平静/爆发等）

#### 约束一：禁止随意截取

| 规则 | 说明 |
|------|------|
| **以"情绪单元"为单位截取** | 每个情绪转折点（如"恐惧→麻木→习惯"）作为一个单元，不拆散 |
| **保留完整因果链** | "因为 A 所以 B"必须同时出现，不可只保留 B |
| **对白保留完整语义** | 截取对白必须保留说话人的意图和情绪，不可只取半句 |
| **内心戏必须配画面** | "他感到恐惧"这类内心描写，必须有对应的视觉画格（如面部特写/抽象画面），不能只放旁白 |
| **标注来源** | 每条改编文本必须标注对应的小说原文位置（如 `[原文 L23-L28]`），方便回溯 |

#### 约束二：旁白连贯性（朗读不脱节）

漫画旁白不是孤立的，而是**一条贯穿全页的叙事线**。

| 规则 | 说明 |
|------|------|
| **顺序读取等于完整故事** | 把所有旁白按页面顺序连起来朗读，必须能听懂完整故事，不可跳跃、不可缺因果 |
| **每页旁白 ≤ 3 条** | 超过 3 条旁白的页面，必须检查是否信息过载；过多信息应拆到后续页面 |
| **对白→旁白的过渡要自然** | 上一格的对白结尾与下一格的旁白开头，必须在语义上有承接关系 |
| **避免"跳接"** | 如果两格之间时间跨度 > 1 小时，必须有过渡画面或旁白说明（如"三天后""第二天"） |
| **终幕旁白必须收束** | 最后一页的旁白必须呼应本章主题，不可突然截断或引入新信息 |
| **旁白长度均衡** | 单条旁白 10-30 字为佳，最多不超过 50 字。过长的旁白必须拆成多条 |

#### 改编层模板

```markdown
# 第三章 改编层

## 情绪单元 1：恐惧→麻木→习惯
- 来源：[原文 L3-L9]（3 句）
- 改编旁白（3 条 = 3 格，每句 1 格）：
  1. "第一次是恐惧，像冰冷的潮水。" [L3-L5]
  2. "第二次，心跳慢了一拍。" [L7]
  3. "第三次，只是在等一个结果。" [L9]
- 画面规划：3 格面部特写，从惊恐→呆滞→平静
- 情绪：恐惧 → 麻木 → 习惯

## 粒度自检
- 原文句子数：3
- 改编旁白条目数：3
- 比例：3/3 = 1.0 ≥ 0.5 ✓
- 无条目覆盖超过 2 句 ✓
```

#### 改编层自检清单

输出改编层后，逐项检查：

- [ ] 朗读所有旁白，能听懂完整故事吗？
- [ ] 每个情绪单元有明确的"起因→发展→结果"吗？
- [ ] 对白和旁白之间有语义承接吗？
- [ ] 时间跨度 > 1 小时的地方有过渡吗？
- [ ] 最后一页的旁白是否收束本章主题？
- [ ] 没有任何旁白是孤立信息（前后没有因果关联的句子）？
- [ ] **粒度检查**：旁白条目数 ≥ 原文句子数 ÷ 2？
- [ ] **粒度检查**：没有旁白条目覆盖超过 2 句原文？
- [ ] **粒度检查**：每一句话都有对应的画格？

#### 约束三：画格粒度（1-2 句 = 1 格）

这是最重要的节奏约束，防止剧情跳跃。

| 规则 | 说明 |
|------|------|
| **每 1-2 句小说原文 = 1 个改编条目 = 1 格画面** | 不允许将超过 2 句的原文合并到同一个旁白条目里；也不允许 1 句原文拆成 3 格 |
| **旁白条目数 ≥ 原文句子数 ÷ 2** | 改编后的旁白条目数，必须不少于原文句子总数的一半 |
| **一句话一个画面** | 如果某句话含有独立视觉信息（动作、表情、场景变化），必须独立成格，不能和后一句合并 |
| **对话逐句独立** | 每句对白（含说话人动作）独立成格，不允许把两句对白合并到同一个画面 |
| **情绪描写独立成格** | 包含情绪关键词（恐惧/麻木/愤怒/绝望等）的句子，必须独立成格 |
| **不合并跨段落的句子** | 不同段落之间的句子，即使语义相关，也不允许合并到同一个旁白条目 |

**示例**：

```
原文（3句）：
  他坐在靠窗的位置，涮着羊肉。
  店里热气腾腾，人声鼎沸。
  终于又像个正常人了。

改编旁白（3条 = 3格）：
  1. "他坐在靠窗的位置，涮着羊肉。" → 1格（人物动作）
  2. "店里热气腾腾，人声鼎沸，锅底咕嘟咕嘟地冒着泡。" → 1格（环境氛围）
  3. "终于又像个正常人了。" → 1格（内心/表情特写）
```

**反例（不允许）**：
```
原文（3句）合并成1条旁白：
  "他坐在靠窗涮羊肉，店里热气腾腾人声鼎沸，终于像个正常人。" 
  → 这是3句话的信息塞进1格，违反了 1-2句=1格 的规则
```

**改编层自检（新增粒度检查）**：
- 改编旁白条目数 / 原文句子数 ≥ 0.5 吗？
- 是否有任何旁白条目覆盖了超过 2 句原文？
- 每一句话都有对应的画格吗？（不允许一句原文没有画面）

### Step 2: 生成分镜脚本
基于改编层（`adaptation_chXX.md`）生成分镜脚本，创建 `projects/<项目名>/storyboard_chXX.py`。

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
