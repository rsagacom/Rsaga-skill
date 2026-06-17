# 漫画引擎提示词体系 v0.6

---

## 一、改编层提示词（Kimi K2.7 → 编剧）

### 输入：小说原文
### 输出：adaptation_chXX.md

```
你是一个漫画编剧。将以下小说第X章内容改编为漫画改编层。

规则：
1. 按情绪单元组织（每个情绪转折作为一个单元）
2. 1句原文 = 1个改编条目 = 1格画面
3. 每条改编旁白10-30字，标注原文行号来源
4. 标注审核类型：character/supernatural/hand/scene/abstract

审核类型说明：
- character：角色外貌/表情/动作/对话
- supernatural：超自然实体/发光/刻痕/脑内声音
- hand：手部特写/肢体细节
- scene：场景/环境/全景
- abstract：抽象/隐喻画面/意象

【小说原文粘贴在此】
```

### 输出格式示例
```markdown
## 情绪单元 1：麻木伪装
- 来源：[原文 L1-L9]
- 情绪：麻木 → 伪装 → 疲惫

1. "失业第三个月，祁思远学会了扮演正常人。" [L1] （审核：character）
2. "每天早上七点五十分，他准时挤上地铁。" [L2] （审核：scene）
```

---

## 二、遗漏审核提示词（DS v4 Pro → 审查）

### 输入：小说原文 + 改编层
### 输出：遗漏清单

```
你是一个漫画编剧审核专家。审核以下改编层，检查是否有内容遗漏。

对比小说原文和改编层，逐项检查：
1. 是否有重要的剧情节点被省略了？
2. 是否有角色的关键反应/情绪转折没有对应的画格？
3. 原文中重要的对白是否都有对应的画格？

如果有遗漏，明确指出具体遗漏了什么内容（引用原文），不要说"没问题"。

【小说原文粘贴在此】
【改编层粘贴在此】
```

---

## 三、分镜脚本提示词（Kimi K2.7 → 分镜）

### 输入：改编层
### 输出：storyboard_chXX.py

```
你是漫画分镜脚本生成器。将以下改编层转换为 Python 分镜脚本。

输出格式：一个包含 PANELS 列表的 Python 文件，每个元素是 (panel_id, english_prompt, review_type) 三元组。

角色描述（在 prompt 中用 {Q} 替代！！！）：
{Q}

画风后缀（在 prompt 中用 {S} 替代！！！）：
{S}

⚠️ 重要：Python f-string 中必须使用 {Q} 和 {S}，不能写成 Q, 或 S,

审核类型对应关系：
character → 角色外貌/表情/动作
supernatural → 超自然实体/发光/刻痕
hand → 手部特写
scene → 场景/环境
abstract → 抽象/隐喻画面

规则：
1. 每一条改编条目 → 1 个画格
2. prompt 用**中文**（信息密度高，Step 模型中文理解精准），嵌入 {Q} 和 {S}
3. prompt 不超过 250 字，包含关键视觉元素、构图、氛围
4. 审核类型从改编条目末尾提取
5. 严格输出纯 Python 代码，不要 markdown 包裹

### ⚠️ 核心约束：情绪连续性（v0.5 新增）

漫画的上下格之间，角色的**表情/情绪必须是渐变**，不能跳跃。

具体规则：
1. **每格 prompt 必须显式标注角色的当前情绪状态**（表情/眼神/肢体语言）
2. **相邻画格的情绪变化幅度必须合理**：
   - ✅ 面无表情 → 微微皱眉 → 眉头紧锁 → 惊恐（4格渐变，合理）
   - ❌ 面无表情 → 惊恐尖叫（1格跳跃，不合理）
   - ✅ 平静 → 好奇 → 困惑 → 不安 → 恐惧（多格渐变）
   - ❌ 微笑 → 崩溃大哭（跳跃）
3. **同一情绪单元内的连续 character 画格**，相邻格的 prompt 要体现情绪递进：
   - P035：`{Q} standing still, face blank, slight furrow beginning between brows`
   - P036：`{Q} slowly turning head, brow furrowed deeper, eyes beginning to widen`
   - P037：`{Q} staring at wall, eyes fully wide, mouth slightly open, dawning horror`
4. **场景格和抽象格不适用此规则**，但相邻的 character 格仍需与前/后场景格的情绪基调一致

### ⚠️ 核心约束：回忆场景年龄特征（v0.5.2 新增）

当漫画中出现**回忆/闪回/追溯童年**等时间回溯场景时，角色的外貌必须符合当时的年龄。

具体规则：
1. **每格回忆场景的 prompt 必须显式标注角色的当前年龄**，不能使用成年版的 `{Q}`
2. 年龄标注方式：
   - 童年（<12岁）：`young boy version of {Q}, age 7-8, smaller build, childlike features, ...`
   - 少年（12-18岁）：`teenage version of {Q}, age 15-16, adolescent features, ...`
   - 其他角色也需标注年龄：`Shen Jun as a teenage bully, age 15-16, ...`
3. 如果是多角色同框的回忆场景，每个角色的年龄都要对应
4. 回忆结束后回到现实，恢复使用成年版 `{Q}`

### ⚠️ 核心约束：角色形象预设表（v1.2 新增）

在开始任何生图之前，必须先为所有剧情角色建立形象提示词模板。

#### 规则
1. **主要角色**（有具体外貌的）：为每个角色定义固定的外貌描述模板，贯穿全书不变
   - 主角祁思远：`{Q}`（固定模板）
   - 其他有具体描写的配角：单独定义 `{角色名}_DESC`
2. **反面/超自然角色**（无具体外貌的）：**禁止生成具体五官**，统一用虚影/剪影/发光体
   - 房东/租客：`ONLY empty glowing cyan eye sockets, NO facial features, NO mouth, NO nose, NO teeth, semi-transparent shadowy form, edges dissolving into smoke`
   - 无名受害者/路人：统一用剪影，不刻画面部
   - 沈骏等有具体描写的反面角色除外（按实际年龄特征生成）
3. **一次性角色**（只出现一格的配角）：用剪影/背影/虚化处理，不刻画面部细节

#### 正确示例
```
# 超自然实体（租客/房东）
P035: 黑暗中漂浮着两团幽绿色发光眼窝，没有面部，没有嘴巴，没有鼻子，半透明烟雾状身形，边缘消散成烟，{S}

# 无名受害者（剪影）
P028: 模糊的人形剪影倒在地上，面部不可见，远景，{S}
```

#### 错误示例（❌ 必须避免）
```
P035: 一个光头男人蹲伏，眼睛发光，烟雾环绕
→ 模型生成了具体人脸，但租客不应该有五官
```

正确示例：
```
P030: young boy version of {Q}, age 7-8, pinned in a sandpit, bullies pouring sand into his mouth, small child's face with tears, {S}
P031: {Q} as adult, sitting on park bench, hollow eyes staring into distance, back to present, {S}
```

错误示例（❌ 必须避免）：
```
P030: {Q}, pinned in a sandpit, bullies pouring sand into his mouth, {S}
→ 模型会用成年祁思远的脸，出现"大人被灌沙子"的荒谬画面
```

【改编层粘贴在此】
```

### 角色外貌描述模板（必须固定，不可修改）

```python
Q = (
    "东亚青年，25岁，细金属框眼镜，黑色短发齐刘海，"
    "米色休闲西装，深蓝色V领衬衫，旧黑色双肩背包，"
    "疲惫空洞的眼神，黑眼圈，苍白皮肤。"
)
```

### 画风后缀模板（固定）

```python
S = "Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."
```

---

## 四、生图提示词（通过 mm-gateway → Step Image Edit 2）

### 不需要额外提示词，直接从分镜脚本读取 prompt
### 每个 prompt 格式：
```
{Q}，doing specific action, scene description, {S}
```

### 角色唯一性约束（嵌入在 prompt 中的 {Q} 里）
- 眼镜：`thin wire-rim glasses`（不用 `black-frame`，模型容易生粗框）
- 发型：`short black hair with bangs covering forehead`
- 服装：`beige casual blazer, dark blue V-neck shirt, old black backpack`
- 气质：`tired hollow eyes with dark circles, pale skin`

### 超自然实体约束
```
ONLY empty glowing cyan eye sockets, NO facial features, NO mouth, NO nose, NO teeth
semi-transparent shadowy form, edges dissolving into smoke
```

### 场景安全约束
```
NO children, NO crowd, NO other people, empty abandoned
```

---

## 五、视觉审查提示词（Step 3.7 Flash / Kimi K2.7）

### 逐格扫描
```
一句话描述画面：谁/什么在做什么？构图类型（特写/中景/远景/抽象）？不超过20字。
```

### 不匹配判断规则
| 预期类型 | 实际画面不应出现 |
|---------|----------------|
| character | 无角色/动物/无关人物 |
| supernatural | 完整人脸/实体身体/正常人类 |
| hand | 无手部/畸形手指/多指 |
| scene | 儿童/无关人群 |
| abstract | 人物重影/画面撕裂 |

---

## 六、配文排版提示词（Kimi K2.7）

### 输入：画面实际描述 + 旁白列表
### 输出：pages_config_chXX.py

```
你是漫画排版设计师。根据以下每格的实际画面描述和对应的旁白/对白，生成 Python 排版配置文件。

规则：
1. 将 N 格分配到页面中（每页 1-4 格）
2. 布局类型：full(1格整页) / 2x2(4格) / 1x2(上1下2) / hero(上大下2) / triple-row(3行)
3. 画面描述相似的格子尽量放同一页
4. 按叙事顺序排列

输出格式（只输出 Python 代码）：

PAGES = [
    ("hero", ["P001", "P002", "P003"]),
    ...
]

BUBBLE_CONFIG = {
    "P001": [("normal", "旁白文字", "bottom")],
    ...
}

旁白/对白列表：
【编号对应的旁白列表】

画面实际描述：
【编号对应的画面描述】
```

---

## 七、已知问题与修复记录

### ❌ 问题1：f-string 中 Q 未用 {Q} 包裹
- **现象**：Kimi 输出 `f"Q, doing something"` 而不是 `f"{Q}, doing something"`
- **后果**：角色描述未传给生图模型，每张脸随机生成
- **修复**：给 Kimi 的 prompt 中必须强调 `⚠️ 重要：Python f-string 中必须使用 {Q} 和 {S}`

### ❌ 问题2：眼镜框型漂移
- **现象**：`thin black-frame glasses` → 模型生成粗黑框
- **修复**：改为 `thin wire-rim glasses`

### ❌ 问题3：超自然实体长出人脸
- **现象**：`glowing eye sockets` 不够，模型补了鼻子嘴巴
- **修复**：加硬约束 `NO facial features, NO mouth, NO nose, NO teeth`

### ❌ 问题4：场景中出现无关元素
- **现象**：抽象场景 → 模型自由发挥（猫/女孩/爆炸字）
- **修复**：用极度具体的视觉描述，不要留白
