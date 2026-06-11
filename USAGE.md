# 小说转漫画引擎 — 使用说明

> 项目路径：`novel-to-comic-engine/`（克隆后的本地路径）
> 适用小说：中篇/长篇小说的漫画化

---

## 一、环境要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥ 3.10 | 运行所有脚本 |
| Pillow (PIL) | ≥ 9.0 | 画格加载 + 排版 |
| ReportLab | ≥ 3.6 | PDF 导出 |
| PyYAML | 可选 | 配置文件解析（无则 fallback 简易解析） |

安装依赖：

```bash
pip3 install pillow reportlab pyyaml
```

### API Key 配置

复制配置模板并填入 API Key：

```bash
cd novel-to-comic-engine
cp config.yaml.example config.yaml
```

编辑 `config.yaml`，填入阶跃星辰 API Key：

```yaml
providers:
  image:
    default: step
    step:
      api_key: "你的STEP_API_KEY"       # 必填：生图用
      api_url: "https://api.stepfun.com/v1"
      model: "step-image-edit-2"

  vision:
    default: step
    step:
      api_key: "你的STEP_API_KEY"       # 必填：审核用
      api_url: "https://api.stepfun.com/v1"
      model: "step-3.7-flash"
    kimi:
      api_key: "你的KIMI_API_KEY"       # 可选：审核备选模型
      api_url: "https://api.moonshot.cn/v1"
      model: "kimi-k2.6"
```

> ⚠️ `config.yaml` 含 API Key，**不要提交到 Git**。已加入 `.gitignore`。

---

## 二、完整工作流程

```
小说原文（chapter_XX.md）
        ↓
Step 1  读取 + 理解剧情
        ↓
Step 2  生成分镜脚本（PANELS 列表）
        ↓
Step 3  批量生成画格（step-image-edit-2）
        ↓
Step 4  视觉审核（kimi-k2.6）
        ↓
Step 5  修复问题画格
        ↓
Step 6  排版输出（B5判 PDF）
```

---

## 三、各步骤详解

### Step 1：准备小说

将小说章节放入项目目录：

```bash
projects/<项目名>/chapter_1.md
projects/<项目名>/chapter_2.md
...
```

文件格式：纯 Markdown，用 `# 第X章` 作为标题分隔。

### Step 2：生成分镜脚本

分镜脚本是一个 Python 文件，包含 `PANELS` 列表。

**文件位置**：`projects/<项目名>/storyboard_chXX.py`

**格式**：

```python
#!/usr/bin/env python3
"""第三章分镜脚本"""

PANELS = [
    ('P01_扉页', 'Wide shot dark abstract background... Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic.'),
    ('P02_画格1_场景名', 'Close-up <角色描述>... Manhua ink wash, ...'),
    # 更多画格...
]
```

每个元素是 `(画格名称, 英文 prompt)` 的元组。

**主角外貌模板**（必须嵌入每个含主角的 prompt）：

```
Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead,
beige casual blazer, dark blue V-neck shirt, old black backpack,
tired hollow eyes with dark circles, pale skin.
```

**画风后缀**（所有 prompt 必须以这段结尾）：

```
Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic.
```

### Step 3：批量生成画格

```bash
python3 scripts/generate_panels.py projects/<项目名>/storyboard_chXX.py
```

**输出**：`projects/<项目名>/panels/P01_扉页.png` 等

**参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config` / `-c` | 配置文件路径 | 自动查找 `config.yaml` |
| `--output` / `-o` | 输出目录 | 分镜脚本同目录下的 `panels/` |

**行为**：逐格调用 Step API，每格间隔 6 秒，失败自动重试 3 次（429 限流等待 15 秒）。

### Step 4：视觉审核

```bash
python3 scripts/audit_panels.py projects/<项目名>/panels
```

**可选输出报告**：

```bash
python3 scripts/audit_panels.py projects/<项目名>/panels --output audit_report.md
```

**审核规则（5类）**：

| 类型 | 检查项 | 适用画格 |
|------|--------|----------|
| `qiyuan` | 主角外貌：细黑框眼镜、短黑发、米色西装 | 含主角的画格 |
| `supernatural` | 无面部、发光眼窝、半透明阴影 | 含超自然实体的画格 |
| `hand` | 5根手指、无畸形 | 手部特写 |
| `scene` | 无儿童、无无关人物 | 场景/背景 |
| `abstract` | 无双影、无画面撕裂 | 抽象/隐喻画面 |

**审核报告示例**：

```
✅ 通过: 166 格
❌ 问题: 4 格
⚠️ 错误: 10 格（429 限流）
```

### Step 5：修复问题画格

```bash
python3 scripts/fix_panel.py projects/<项目名>/panels/P06_画格4_名称.png "修正后的 prompt"
```

**流程**：删除旧图 → 用新 prompt 重新生成 → 保存同名文件

### Step 6：排版输出

#### 6.1 创建排版配置

新建 `projects/<项目名>/pages_config_chXX.py`，包含两个核心变量：

```python
#!/usr/bin/env python3
"""第三章排版配置"""

from pathlib import Path
from comic_engine.layout import ComicLayoutEngine

INPUT_DIR = Path("projects/桥底的溃烂神明/chapter3/panels")
OUTPUT_DIR = Path("projects/桥底的溃烂神明/chapter3/pages")
DPI = 150
PAGE_W = 1363   # 182mm @ 150dpi
PAGE_H = 1920   # 257mm @ 150dpi
MARGIN = 71     # 12mm
GUTTER = 30     # 5mm

PAGES = [
    ('full', ['P01_扉页']),                          # 1格全幅
    ('2x2', ['P02_画格1', 'P02_画格2', 'P02_画格3', 'P02_画格4']),  # 4格2×2
    ('1x2', ['P03_画格1', 'P03_画格2', 'P03_画格3']),               # 3格上1下2
    ('2x1x2', ['P04_画格1', 'P04_画格2', 'P04_画格3', 'P04_画格4', 'P04_画格5']),  # 5格特殊
]

BUBBLE_CONFIG = {
    'P01_扉页': [('title', '第三章\n标题', 'center')],
    'P02_画格1': [('narration', '旁白文字', 'bottom')],
    'P02_画格2': [('supernatural', '对白文字', 'center')],
    'P02_画格3': [('fear', '祁思远：台词', 'bottom')],
    'P02_画格4': [('sfx', '轰隆——', 'center')],
}
```

**布局类型**：

| 类型 | 说明 | 最大画格数 |
|------|------|-----------|
| `full` | 全幅单格 | 1 |
| `2x2` | 2×2 四格 | 4 |
| `1x2` | 上1下2 | 3 |
| `2x1x2` | 上2中1下2 | 5 |

**对白类型**：

| 类型 | 说明 |
|------|------|
| `title` | 章节标题（大字，黑字白描边） |
| `narration` | 旁白条（底部半透明黑条 + 白字） |
| `supernatural` | 超自然对白（黑色气泡 + 白字 + 尾巴） |
| `fear` / 其他 | 普通对话框（白底黑字圆角气泡） |
| `sfx` | 音效字（超大 + 多层白描边） |

#### 6.2 运行排版

```bash
python3 scripts/layout_chapter.py projects/<项目名>/pages_config_chXX.py
```

**输出**：`projects/<项目名>/pages/page_01.png` ~ `page_NN.png` + PDF

---

## 四、Prompt 编写规范

### 安全约束（必填）

| 场景 | 约束词 |
|------|--------|
| 空旷场景 | `NO people, NO crowd, NO queue` |
| 废弃儿童设施 | `NO children, no kids, empty abandoned` |
| 手部特写 | `simple normal hand shape, five fingers` |
| 女人背影 | `woman only, seen from behind, no other person visible` |
| 超自然实体 | `NO facial features, NO mouth, NO nose, ONLY empty glowing cyan eye sockets` |

### 高危模式（禁止直接使用）

```
# ❌ 滑梯场景 → 会生成小孩
Children slide tilted crooked...

# ✅ 正确
Abandoned playground slide tilted crooked, no people, no children...

# ❌ 手部描述 → 会生成畸形手
hand gripping tightly, knuckles white, fingernails showing

# ✅ 正确
hand gripping backpack strap, simple normal hand shape, five fingers

# ❌ 女人场景 → 可能混入主角
Woman long black hair + 环境描述时未声明 no other person

# ✅ 正确
Woman long black hair, no other person visible, woman only
```

### Prompt 长度控制

- 每个 prompt **< 400 字符**（step-image-edit-2 限制）
- 角色描述 **< 60 词**，去掉冗余形容词
- 出现 HTTP 400 时立即精简 prompt

---

## 五、排版硬性标准

| 元素 | 字号 | 颜色 | 描边 |
|------|------|------|------|
| 标题文字 | ≥ 80px | 黑字 | 白描边 4 层 |
| 对话框文字 | ≥ 56px | 黑字 | 白描边 3 层 |
| 旁白文字 | ≥ 40px | 白字 | 无（半透明黑底） |
| SFX 文字 | ≥ 96px | 黑字 | 白描边 5 层 |

- 所有文字必须有白色描边，确保在任何背景上可读
- 旁白条高度 ≥ 文字高度的 1.8 倍
- 旁白禁止遮挡画面主体

---

## 六、目录结构

```
novel-to-comic-engine/
├── README.md                     # 项目说明
├── USAGE.md                      # 本文件
├── config.yaml.example           # 配置模板
├── config.yaml                   # 你的 API Key（勿提交 Git）
├── comic_engine/                 # 核心引擎库
│   ├── config.py                 # 配置加载
│   ├── generator.py              # 画格生成
│   ├── auditor.py                # 视觉审核
│   ├── layout.py                 # 排版引擎
│   └── utils.py                  # 工具函数
├── scripts/                      # CLI 入口
│   ├── generate_panels.py        # 批量生图
│   ├── audit_panels.py           # 视觉审核
│   ├── fix_panel.py              # 修复画格
│   ├── layout_chapter.py         # 排版输出
│   └── kimi-vision.sh            # Kimi 识图脚本
├── templates/
│   └── system-prompt.md          # Claude Code Skill 系统提示词
└── skills/
    └── claude-code/
        └── skill.json            # Skill 注册文件
```

---

## 七、常见问题

**Q: 生成画格时遇到 429 限流怎么办？**
A: 脚本已内置重试（等 15 秒再试，最多 3 次）。如果批量生成仍频繁 429，可以适当调大脚本中的 `time.sleep(6)` 间隔。

**Q: 审核时遇到 429 怎么办？**
A: 审核脚本间隔 1.5 秒，通常不会触发。如果触发，等待后自动重试。

**Q: 角色不一致怎么办？**
A: 检查 prompt 中是否包含完整主角外貌描述（<60 词），且没有出现冲突描述（如发型、服装漂移）。

**Q: 手部畸形怎么办？**
A: 在 prompt 中加入 `simple normal hand shape, five fingers`，避免详细描述手指关节、指甲等。

**Q: 排版时画格数量不匹配怎么办？**
A: 检查 `PAGES` 中每个 layout 引用的画格名称是否与 `panels/` 目录中的文件名一致。

**Q: PDF 太大怎么办？**
A: PNG 页面默认不压缩。可以在排版脚本中将 `page.save()` 的 `dpi` 从 150 降到 120，或将 PNG 转为 JPEG 后嵌入 PDF。

---

## 八、快速命令速查

```bash
# 生成画格
python3 scripts/generate_panels.py projects/xxx/storyboard_ch3.py

# 视觉审核
python3 scripts/audit_panels.py projects/xxx/panels
python3 scripts/audit_panels.py projects/xxx/panels --output report.md

# 修复画格
python3 scripts/fix_panel.py projects/xxx/panels/P06_名称.png "新 prompt"

# 排版输出
python3 scripts/layout_chapter.py projects/xxx/pages_config_ch3.py
```

---

*Last updated: 2026-06-11*
