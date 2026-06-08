# 小说转漫画引擎

一款基于 Claude Code skill 的小说转漫画工具。支持小说逐句解析、AI 分镜生成、多模态视觉审核、自动排版输出 PDF。

**适用场景**：将中文长篇小说章节转换为东亚漫画风格（黑白、水墨、B5判）的分镜稿。

---

## 特性

- **小说逐句解析**：不预设分镜数量，每个视觉/情绪转折独立成格
- **角色一致性控制**：精简角色描述嵌入每个 prompt
- **多模态视觉审核**：Step 3.7 Flash + Kimi K2.6 双模型审核
- **自动修复问题画格**：根据审核结果自动重新生成
- **漫画排版输出**：B5判 @ 150dpi，手绘边框、对白气泡、旁白条
- **Claude Code Skill 集成**：通过自然语言交互完成分镜规划和修改

---

## 目录结构

```
novel-to-comic-engine/
├── README.md
├── config.yaml.example          # 配置模板
├── comic_engine/                # 核心引擎库
│   ├── config.py
│   ├── generator.py             # 分镜生成
│   ├── auditor.py               # 视觉审核
│   ├── layout.py                # 排版引擎
│   └── utils.py
├── scripts/                     # CLI 脚本入口
│   ├── generate_panels.py
│   ├── audit_panels.py
│   ├── fix_panel.py
│   ├── layout_chapter.py
│   └── kimi-vision.sh
├── skills/claude-code/          # Claude Code skill 注册
│   └── skill.json
├── templates/                   # LLM 提示词模板
│   └── system-prompt.md
└── projects/example/            # 示例项目输出目录
```

---

## 快速开始

### 1. 克隆项目

```bash
git clone <repo-url> novel-to-comic-engine
cd novel-to-comic-engine
```

### 2. 配置 API Key

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入你的 API key
```

支持的模型：
- **生图**：阶跃星辰 Step Image Edit 2（推荐）
- **审核主模型**：阶跃星辰 Step 3.7 Flash
- **审核备用模型**：Kimi K2.6

### 3. 安装 Claude Code skill

在 `~/.claude/skills/` 下创建软链接：

```bash
ln -s /path/to/novel-to-comic-engine ~/.claude/skills/novel-to-comic
```

（Windows 用户用 mklink，macOS/Linux 用 ln -s）

CC 启动时会自动识别 `~/.claude/skills/` 下的 SKILL.md。

### 4. 使用流程

在 Claude Code 中：

```
用户：读取 projects/example/chapter_3.md，生成分镜脚本
Claude：生成 PANELS 列表并保存到 projects/example/storyboard_ch3.py

用户：开始生成第三章画格
Claude：调用 scripts/generate_panels.py 批量生图

用户：审核第三章
Claude：调用 scripts/audit_panels.py 全量审核

用户：修复问题画格 P06
Claude：调用 scripts/fix_panel.py 重新生成

用户：排版第三章
Claude：调用 scripts/layout_chapter.py 输出 PDF
```

---

## 配置说明

编辑 `config.yaml`：

```yaml
providers:
  image:
    default: step
    step:
      api_key: "${STEP_API_KEY}"      # 从环境变量读取
      api_url: "https://api.stepfun.com/v1"
      model: "step-image-edit-2"
      size: "1024x1024"

  vision:
    default: step
    step:
      api_key: "${STEP_API_KEY}"
      api_url: "https://api.stepfun.com/v1"
      model: "step-3.7-flash"
      max_tokens: 400
    kimi:
      api_key: "${KIMI_API_KEY}"
      api_url: "https://api.moonshot.cn/v1"
      model: "kimi-k2.6"
      max_tokens: 2000

project:
  output_dir: "./projects/example/output"
  font_title: "/System/Library/Fonts/STHeiti Medium.ttc"
  font_body: "/System/Library/Fonts/STHeiti Medium.ttc"

style:
  prompt_suffix: "Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."
  page_width_mm: 182
  page_height_mm: 257
  dpi: 150
  margin_mm: 12
  gutter_mm: 5
```

---

## 核心流程

```
小说原文
  ↓
[Claude Code] 读取 + 理解 + 生成 PANELS 脚本
  ↓
[scripts/generate_panels.py] 调用 Step API 批量生图
  ↓
[scripts/audit_panels.py] Step 3.7 Flash / Kimi 视觉审核
  ↓
[scripts/fix_panel.py] 修复问题画格
  ↓
[scripts/layout_chapter.py] PIL + reportlab 排版 → PDF
```

---

## 注意事项

1. **本工具为不通用的 Claude Code skill 版本**，面向个人创作者，不保证跨平台/跨模型兼容
2. API 调用会产生费用，请合理控制批量生成数量
3. 视觉审核可能触发 429 限流或 451 内容过滤，需耐心重试
4. 大文件输出默认建议放在外置硬盘，避免占用系统盘空间
5. 请勿将真实 API key 提交到 git，始终使用 `config.yaml` 并确保它在 `.gitignore` 中

---

## License

MIT
