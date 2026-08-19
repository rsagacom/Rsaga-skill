---
name: novel-to-comic
description: Use when converting Chinese fiction chapters into manga/manhua adaptation layers, storyboards, image-generation panels, visual audits, lettering/layout configs, or debugging quality problems in the novel-to-comic pipeline.
---

# 小说转漫画引擎

这个 skill 的目标是把中文小说章节稳定转成漫画生产资产，而不是“压缩剧情”。核心原则：**不要替小说做取舍，让小说自己决定分镜数量。**

默认工作目录：

```bash
cd /Volumes/AJW-Data/Projects/novel-to-comic-engine
```

不要读取、展示或提交 `config.yaml`。API key 只能来自环境变量、本地网关或用户已经配置好的文件。

## 硬规则

1. **改编层不可跳过。** 先生成 `adaptation_chXX.md`，再生成 `storyboard_chXX.py`。
2. **粒度唯一标准：1 句原文 = 1 个改编条目 = 1 格画面。** 不使用“1-2句=1格”或“比例 >= 0.5”的旧规则。
3. **不可合并跨段落、跨情绪、跨动作、跨对白的句子。** 如果原文句子很短，也先保留一格；后续排版可以合页，但不能在改编层吞句子。
4. **生图前必须过分镜审核和 dry-run。** 没有通过前不要调用真实生图。
5. **生图输出目录必须显式指定。** 不依赖 `generate_panels.py` 默认输出到 Desktop；使用 `--out projects/<项目>/<章节>/panels` 并让排版读取同一路径。
6. **最终排版只认 `BUBBLE_CONFIG`。** `adapt_to_storyboard.py` 产出的 `BUBBLES` 是中间数据，不能当成 `layout_chapter.py` 会自动读取的最终配文。
7. **同一项目只能有一套角色描述。** 不要同时维护 `QI`、`Q`、storyboard 内不同版本主角外貌；先确定项目级角色表，再引用。
8. **禁止真人照片感。** 画风必须是东亚黑白漫画/2D ink/manhua，不得把 `realistic`、`photograph`、`photo-realistic`、`live-action`、`写实风格` 当正向画风词。需要写成 `NOT photograph, NOT photorealistic, NOT realistic face`。
9. **文字层只由排版处理。** prompt 不得要求生图模型画可读文字、帖子、报告正文、标题、门牌、SFX 字样；这些全部进入 `BUBBLE_CONFIG`。
10. **storyboard 必须与改编层 1:1 对齐，不得吞并。** 改编层 N 条 → storyboard 必须 N 格，每条改编条目对应独立一格。排版可以合页（一页放多格），但 storyboard 层绝不能把多句并一格。交付前必须跑 `qa_chapter.py` 并人工抽验：改编层条目数 == storyboard 格数 == PAGES 引用 panel 数。
11. **禁用摄影感词诱导写实漂移。** prompt 不得用「模糊虚化/景深/细腻光影/皮肤纹理/毛孔」等摄影感词（会诱导生图模型走真人写实），改用「块面化高对比明暗/G笔粗线条勾勒轮廓/平面色块」。面部特写格尤其要显式写「纯2D漫画线条无写实皮肤纹理无毛孔」。
12. **文字载体题材必须强化否定。** 屏幕/日历/时钟/报纸/海报/论坛/报告格，prompt 必须写「绝对不画任何汉字字母数字、纯灰色模糊矩形块」——光写「无可读文字」不够，Step Image Edit 2 对文字载体题材服从度低，需更强措辞 + 明确「纯模糊块占位」。



## 模型路由

| 环节 | 首选 | 说明 |
| --- | --- | --- |
| 改编层/编剧 | DS v4 Pro | 负责文本理解、逐句改编、旁白与对白连续性 |
| 分镜/画面 prompt | Kimi K2.7 | 负责画面感、镜头、构图、情绪递进 |
| 分镜结构审核 | GLM 5.2（火山 coding 端点） | 纯文本 JSON 审核。注意：`kimi-k2.7-code` 做**纯文本结构化 JSON 审核**会强制 thinking、输出常空，不用于此环节；但它的**识图能力**正常（见下行） |
| 生图 | Step Image Edit 2 | 通过本地 `mm-gateway` 或现有脚本调用 |
| 视觉审核（识图） | Step 3.7 Flash（首选）/ Kimi K2.7（火山方舟，备选） | 入口 `scripts/audit_panels.py`，内部走 `comic_engine/auditor.py` + `config.yaml` 的 `providers.vision`。默认 `step-3.7-flash`（stepfun 官方）。Kimi K2.7 走火山方舟 `kimi-k2.7-code`，**多模态可识图**，作为 Step 额度耗尽时的备选。看实际图片是否匹配，不用旧 `scan_all_panels.py` 作为主流程 |
| 排版配文 | Kimi K2.7 或人工校正 | 产出 `PAGES` + `BUBBLE_CONFIG`，再本地渲染 |

> **识图模型能力**：
> - **GLM 系列（5.1 / 5.2 / 火山 coding 端点）**：仅支持文本输入，不支持图片，传入图片返回 `400 Model only support text input`。不要用 GLM 识图。
> - **Kimi K2.7（火山方舟 `kimi-k2.7-code`）**：**多模态，支持识图**（已验证：三格漫画人物特征/场景/对视关系均识别准确）。走火山方舟 OpenAI 兼容端点 `https://ark.cn-beijing.volces.com/api/coding/v3`，Anthropic 兼容端点 `https://ark.cn-beijing.volces.com/api/coding`。注意 K2.7 做**纯文本结构化 JSON 审核**会思考失控输出空，但识图不受影响。
> - **Step 3.7 Flash（`step-1o-turbo-vision`）**：多模态识图，stepfun 官方端点，视觉审核首选。
> - 任何看图环节（画格审核、整页压脸抽查、OCR）走 Step 3.7 Flash 或 Kimi K2.7，不要用 CC 当前主模型直接读 PNG（若主模型是 GLM 会 400）。

分镜审核优先命令：

```bash
python3 scripts/audit_storyboard.py projects/<项目>/<章节>/storyboard_chXX.py --provider volc --volc-model glm-5.2
```

如果火山 key 未在当前 shell 中配置，先说明阻塞点；不要切到 `kimi-k2.7-code` 硬跑结构化审核。

## 产物契约

### `adaptation_chXX.md`

每条改编条目必须能追溯到原文句子：

```markdown
格号|角色|配文|情绪|审核类型|画面关键词|来源
001|narration|失业第三个月，他学会扮演正常人。|麻木|character|地铁早高峰/疲惫眼神|L1
002|祁思远|今天也要装得像个人。|伪装|character|手扶栏杆/低头|L2
```

字段要求：

- `格号` 连续递增，推荐 `001` 起。
- `角色` 用 `narration`、主角名、具体角色名或超自然角色名。
- `配文` 是最终可能进入气泡/旁白的文字，避免塞入过长解释。
- `情绪` 必须体现相邻格渐变。
- `审核类型` 只能用 `character`、`supernatural`、`hand`、`scene`、`abstract`。
- `画面关键词` 写具体可见元素，避免“黑暗扩张”“命运感”这种难画抽象词。
- `来源` 写原文行号或句号编号。

自检：

- 改编条目数 = 原文句子数。
- 每句原文都有独立格号。
- 每条只覆盖 1 句原文。
- 情绪单元有起因、发展、结果。
- 旁白顺序朗读能听懂完整故事。

### `storyboard_chXX.py`

必须是可导入 Python 文件，定义 `Q`、`S`、`PANELS`：

```python
Q = "东亚青年，25岁，细金属框眼镜，黑色短发齐刘海，米色休闲西装，深蓝色V领衬衫，旧黑色双肩背包，疲惫空洞的眼神，黑眼圈，苍白皮肤。"
S = "东亚黑白漫画，G笔线条，高对比灰度，2D漫画，非照片，非写实人脸。"

PANELS = [
    ("P001", f"{Q}站在地铁车厢里，低头抓紧扶手，眼神麻木，冷白顶灯压在脸上，{S}", "character"),
]
```

要求：

- `PANELS` 元组必须是 `(panel_id, prompt, audit_type)` 三元素。
- `panel_id` 与 `PAGES`、`BUBBLE_CONFIG`、图片文件名完全一致。
- prompt 优先中文，目标 120 字以内（不含 `Q/S` 展开），接近 400 字节要主动压缩。
- prompt 必须保留 `{S}` 或等价的东亚漫画风格约束；压缩 prompt 时不能先砍画风后缀。
- 回忆/童年场景不得直接使用成年版 `{Q}`，必须写清当时年龄。
- 超自然角色必须包含“无五官、无嘴、无鼻、无牙齿、只有发光眼窝/剪影/烟雾边缘”等否定约束。
- 模糊反派、房东、租客、无名路人优先剪影/背影/虚影；不要幻想具体五官。

### `pages_config_chXX.py`

最终排版文件只需要：

```python
PAGES = [
    ("full", ["P001"]),
    ("2x2", ["P002", "P003", "P004", "P005"]),
]

BUBBLE_CONFIG = {
    "P001": [("narration", "失业第三个月，他学会扮演正常人。", "bottom")],
    "P002": [("normal", "今天也要装得像个人。", "center")],
}
```

注意：

- 不要输出 `BUBBLES` 当最终排版变量。
- 气泡位置用 `bottom` 时会走横排旁白条；其他位置会走竖排对话框。
- 每个 `PAGES` 引用的 panel 都必须存在图片。

## 推荐流程

### 1. 初始化或进入项目

```bash
python3 scripts/init_project.py <项目名>
```

章节目录推荐使用：

```text
projects/<项目名>/chXX/
```

### 2. 生成改编层

读取 `projects/<项目名>/<章节>/chapter_XX.md`，按“1句=1格”生成 `adaptation_chXX.md`。生成后先做条目数、来源、情绪连续性检查。

如果使用转换脚本：

```bash
python3 scripts/adapt_to_storyboard.py projects/<项目名>/<章节>/adaptation_chXX.md projects/<项目名>/<章节>/storyboard_chXX.py
```

脚本会生成 `BUBBLES`，后续仍要整理成 `pages_config_chXX.py` 的 `BUBBLE_CONFIG`。

### 3. 审核分镜

先确认 Python 可导入：

```bash
python3 -m py_compile projects/<项目名>/<章节>/storyboard_chXX.py
```

再做模型审核：

```bash
python3 scripts/audit_storyboard.py projects/<项目名>/<章节>/storyboard_chXX.py --provider volc --volc-model glm-5.2
```

如果生成了 `_fixes.json`，先应用或手动合并修正，再进入 dry-run。

### 4. Dry-run，不生图

```bash
python3 scripts/generate_panels.py projects/<项目名>/<章节>/storyboard_chXX.py --dry-run --out projects/<项目名>/<章节>/panels
```

只有 dry-run 通过，才真实生成。

### 5. 批量生图

```bash
python3 scripts/generate_panels.py projects/<项目名>/<章节>/storyboard_chXX.py --out projects/<项目名>/<章节>/panels
```

已有图片默认跳过；需要覆盖时才加 `--force`。

### 6. 视觉审核与修复

```bash
python3 scripts/audit_panels.py projects/<项目名>/<章节>/panels
```

发现画面不匹配时，优先修 prompt 或重生单格：

```bash
python3 scripts/fix_panel.py projects/<项目名>/<章节>/panels/P001.png "修正后的 prompt"
```

不要为了迁就错误图片而硬改剧情配文。

### 7. 排版输出

创建 `pages_config_chXX.py`，然后：

```bash
python3 scripts/layout_chapter.py projects/<项目名>/<章节>/pages_config_chXX.py --panels projects/<项目名>/<章节>/panels --output projects/<项目名>/<章节>/pages
```

排版后先跑成片 QA：

```bash
python3 scripts/qa_chapter.py projects/<项目名>/<章节>/pages_config_chXX.py --panels projects/<项目名>/<章节>/panels
```

## 常见失败点

| 症状 | 根因 | 处理 |
| --- | --- | --- |
| 剧情被压缩/吞并 | storyboard 层把多句并一格（改编层1句1条但分镜吞了） | 对齐改编层条目数==storyboard格数，每条独立一格，参考 ch03 v3 补格流程 |
| 审核全 PASS 但成片有问题 | `auditor.py` 旧判定 `or "通过" in content` 让"通过"二字一票否决所有 issues | 已修复：过滤纯结论行+否式行后 issues 非空即 FAIL；勿回退此逻辑 |
| K2.7 识图偶发空响应 | 思考型模型 content 偶尔为空 | 简短描述型 prompt + 重试；auditor.py 已标 UNKNOWN 不当 PASS |
| 配文没有进入画面 | 写了 `BUBBLES` 但 layout 读 `BUBBLE_CONFIG` | 生成或转换 `pages_config_chXX.py` |
| 图片生成在 Desktop，排版找不到 | 未显式传 `--out` | 生图与排版都使用项目内同一 panels 目录 |
| 主角外貌漂移 | 多套 Q/QI 描述并存 | 保留单一项目级 `Q` |
| 超自然角色长人脸 | prompt 否定约束不够 | 加“无五官/无嘴鼻牙/剪影/发光眼窝” |
| 画面混入真人照片脸 | 正向使用 `realistic/写实风格` 或摄影感词（虚化/景深/细腻光影） | 删除正向写实词+摄影感词，面部特写格显式写「纯2D漫画线条无写实皮肤纹理无毛孔」 |
| 图内出现乱码字/伪标题 | 让生图模型画文字 | prompt 写“无可读文字”，真实文字进 `BUBBLE_CONFIG` |
| 屏幕/日历/时钟/论坛格仍有可读字 | 光写「无可读文字」不够，Step 对文字载体服从度低 | 强化「绝对不画任何汉字字母数字、纯灰色模糊矩形块」+ 明确载体用模糊块占位 |
| 抽象画面乱飞 | prompt 太虚 | 改成具体可见构图、物体、光线和镜头 |
| 识图报 `400 Model only support text input` | 用了 GLM 5.x / CC 主模型（GLM 5.2）直接读 PNG | 改走 `audit_panels.py`（Step 3.7 Flash 或 Kimi K2.7），GLM 仅文本不识图 |
| K2.7 CC 实例报 `effort got xhigh` | `settings.kimi-k27-code.json` 里 `effortLevel: max` 被 CC 映射成 `xhigh`，火山方舟不认 | 把 `effortLevel` 改为 `high` |
| 识图脚本崩 `unterminated string literal` | `scripts/step-vision.sh` 双引号 here-string + 单引号 python + `$` 插值冲突 | 不要用该脚本；走 `audit_panels.py` 或在 python 里用 `os.environ` 读 key |
| 识图/生图报 `SSL: UNEXPECTED_EOF` 或 `IncompleteRead` | python `urllib` 默认不读 `HTTPS_PROXY`，代理环境 TLS 断连 | `generator.py`/`auditor.py` 已加 `ProxyHandler`；偶发失败重试即可 |
| 生图 401 Incorrect API key | config 里 step key 失效 | 用 `STEP_API_KEY` 环境变量传有效 key（`fix_panel.py`/`generate_panels.py` 已支持环境变量优先） |

## 结束前检查

完成任意章节后至少汇报：

- 改编条目数、原文句子数、是否 1:1。
- **storyboard 格数 == 改编条目数**（剧情完整性硬指标，storyboard 不得吞并改编条目）。
- `storyboard_chXX.py` 是否通过 `py_compile`。
- `audit_storyboard.py` 是否跑过，使用了哪个 provider/model。
- `generate_panels.py --dry-run` 是否通过。
- 实际生成画格数量、审核问题数量。
- `pages_config_chXX.py` 是否含 `PAGES` 和 `BUBBLE_CONFIG`。
- `qa_chapter.py` 是否通过；如果未通过，说明是缺图、漏配文、图文顺序或排版节奏问题，不要直接交付 PDF。
- **跑 `audit_panels.py --provider volc-k27` 抽验成片**（旧宽松审核会误判全 PASS，K2.7 严格审核才能抓真人脸/图内文字/压脸真问题）。

最后更新：2026-06-22
