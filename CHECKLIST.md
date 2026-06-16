# 漫画引擎 v0.5 — 修正总结与优化工作流

## 本轮发现的问题

### 1. 生图画格和分镜脚本描述不一致
**根因**：模型不是精确的渲染器。即使 prompt 写"眼睛特写"，模型可能生成半身像。旧流程直接按分镜脚本配文，导致**画面和旁白对不上**。

**典型例子**：
| 画格 | 分镜脚本描述 | 模型实际生成 |
|------|-------------|-------------|
| P056 | 发光眼窝戏谑 | 一只猫 |
| P063 | 绝对黑暗凝固 | 女孩眼睛发光 |
| P071 | 巨大汉字"容器" | 两个人共享一个身体 |
| P077 | 巨石砸落"杀人" | 书法"爆"字 |

### 2. 眼镜框型漂移
**根因**：`thin black-frame glasses` → Step 模型理解为**粗黑框**。改用 `thin wire-rim glasses`（金属细框）后准确度大幅提升。

### 3. 超自然实体出现完整人脸
**根因**：prompt 中虽然有"glowing eye sockets"但缺少硬性否定约束。需要显式加 `NO facial features, NO mouth, NO nose, NO teeth`。

### 4. 场景中出现无关元素（猫、女孩、爆炸字等）
**根因**：抽象/超自然 prompt 不够具体，模型自由发挥。需要极度具体的视觉描述。

---

## 修正后工作流（v0.5）

```
小说原文
    │
    ▼
Step 1: 改编层 (adaptation_chXX.md)
    │   ├── 按情绪单元组织
    │   ├── 1-2句 = 1格粒度控制
    │   └── 标注审核类型
    │
    ▼
Step 2: 生分镜脚本 (storyboard_chXX.py)
    │   ├── Kimi K2.7 生成（擅长画面描述）
    │   └── 或手动编写（Kimi 限流时）
    │
    ▼
Step 3: 批量生图 (generate_panels.py)
    │   ├── 通过 mm-gateway → Step Image Edit 2
    │   ├── 并发3路加速
    │   └── 断点续传（已有画格自动跳过）
    │
    ▼
Step 4: 视觉扫描 ← 🔥 新增
    │   ├── Step 识图逐格扫描实际画面内容
    │   ├── 输出 panel_descriptions.json
    │   └── 对比分镜脚本预期，标记不匹配
    │
    ▼
Step 5: 修复不匹配画格 ← 🔥 新增
    │   ├── 画面完全不对 → 修改 prompt 重生成
    │   └── 画面大致对但细节偏 → 调整 prompt 约束
    │
    ▼
Step 6: 配文 → 排版 (pages_config + layout_chapter.py)
    │   ├── 根据实际画面内容匹配旁白/对白
    │   └── 生成 BUBBLE_CONFIG
    │
    ▼
Step 7: 输出 PDF
        ├── 35页/8页 PNG
        └── B5判 chXX.pdf
```

---

## 修复效果统计（第一章）

| 轮次 | 修复画格数 | 问题类型 |
|------|-----------|---------|
| 第1轮 | 13格 | 眼镜粗框、服装不符、超自然有脸 |
| 第2轮 | 2格 | 画面不匹配（P069、P072） |
| 第3轮 | 5格 | 画面完全不匹配（P042猫/P056猫/P063女孩/P071双身/P077爆字） |
| **总计** | **20格** | |

---

## Prompt 优化经验（记录到自检清单）

```
❌ thin black-frame glasses   → 模型理解为粗框
✅ thin wire-rim glasses      → 生成细框

❌ glowing eye sockets        → 可能长出完整人脸
✅ ONLY empty glowing cyan eye sockets, NO facial features, NO mouth, NO nose

❌ 抽象/隐喻描述              → 模型自由发挥
✅ 极度具体的视觉构图描述      → 减少随机性
```
