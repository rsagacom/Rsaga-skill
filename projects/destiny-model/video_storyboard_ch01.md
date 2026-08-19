# 《命运模型》第一章：视频分镜蓝图 v0.1

> 来源：`/Users/rsaga/Desktop/历史备份/《命运模型》原创科幻小说（1-9 章）作者 Rsaga.rtf` 第一章。
> 已复用：`storyboard_ch01.py` 的角色设定和 75 格剧情拆解；本文件是视频版，不覆盖原漫画分镜。

## 1. 本章改编目标

- 形式：横屏科幻惊悚短剧，16:9，24 fps。
- 第一章目标成片：约 160 秒，拆成约 32 个 5 秒 H3 片段，再通过剪辑、旁白、对白和音效合成。
- 叙事压缩原则：保留“失业伪装 → 废墟桥洞 → 租客交易 → 左臂坏死 → 被迫答应”的完整因果链；日常求职段以蒙太奇压缩，桥洞对话和身体异变保留时长。
- 第一阶段只做 5 秒无对白画面测试，确认显存、输出、人物/场景锚点和运动稳定后再加音频、对白和 Turbo。

## 2. 固定连续性设定

### 祁思远（Q）

25 岁东亚男性，消瘦、苍白皮肤、黑色短发齐刘海、细金属框眼镜、黑眼圈、疲惫空洞眼神；米色休闲西装、深蓝色 V 领衬衫、旧黑色双肩背包。全章衣着、发型、眼镜和背包不变。

### 女人（W）

只出现一瞬间。暗色风衣、黑色长发、始终背对镜头；不露正脸，不安排对白，不补充未经原文确认的身份信息。

### 租客（R）

没有实体，只用男声和环境变化表现；不出现人脸、嘴、鼻、牙齿或可辨认人体。声音为空洞、冰冷、带金属刮擦质感，偶尔出现慵懒戏谑。

### 视觉规则

写实电影构图但避免照片级皮肤纹理；冷灰城市日常，拆迁区使用锈红夕阳，桥洞内部使用近黑空间和微弱磷光；发光刻痕始终是同一套“血管/根系/电路/古老符咒”纹理，不在镜头间变成另一种图案。

## 3. 第一章视频分镜

| 镜头 | 时长 | 来源格 | 画面与运动 | 旁白/对白/音效 | 生产备注 |
|---|---:|---|---|---|---|
| S01 伪装日常 | 12s | P001-P012 | 地铁冷白灯下 Q 抓扶手；旧背包和报废电脑插头特写；写字楼人潮中 Q 被吞没；图书馆手机反光，最后停在麻木眼神。以三个短推拉镜头完成蒙太奇。 | 旁白：“失业的第三个月，祁思远学会了如何在人群中扮演一个正常人。” 地铁轰鸣、空调声、手机提示音。 | 3 个 4s 片段；手机屏幕不生成可读文字，只用模糊光块。 |
| S02 厌倦破裂 | 8s | P011-P015 | 图书馆门打开，Q 走入傍晚逆光；招聘页面的光映在脸上，镜头从近景拉到拆迁废墟的锈红全景。 | 旁白：“他知道自己在腐烂，只是腐烂得很安静。” 远处施工余响逐渐变成低频轰鸣。 | 避免把“腐烂”做成血腥特效，用脸部阴影和环境压迫表达。 |
| S03 半开的门 | 10s | P016-P018 | Q 走到两堵危墙之间；锈铁门在风中轻晃半开；镜头绕到门缝，门内是黑色杂草和夕光，Q 的手迟疑后推门。 | 无对白；铁门摩擦声、风声、远处金属碰撞声。 | 门、危墙、杂草作为后续场景锚点，需生成一张通过审核的关键帧。 |
| S04-A 荒废公园路径与滑梯 | 5s | P019-P021 | 镜头沿裂开的石板路低位前进，经过一座歪斜的旧滑梯；锈红夕阳和荒草固定在同一空间轴上。 | 旁白起句：“最深处……” 风吹杂草、黑水轻响，低频环境底噪。 | 已完成 H3 T2VA；作为 S04-B 的 I2V 首帧来源。 |
| S04-B 石桥抬升露出 | 5s | P022-P023 | 承接 S04-A 末帧，镜头向后上方拉升，滑梯退出右下角，露出干涸河床上的完整单拱旧石桥；桥洞阴影形成张开的嘴。 | 旁白收句：“是一座石桥。” 风声延续，桥洞加入低频空气声。 | 已完成 H3 I2V；与 S04-A 拼接为首个 10 秒环境测试片段。 |
| S05 女人消失 | 8s | P024-P027 | Q 在远处停下；W 只露背影，暗色风衣进入桥洞；Q 眼部反射出背影，镜头快速推近，W 在阴影中消失。 | 无对白；脚步声由远到近后突然断掉，加入短促高频耳鸣。 | 2 个 4s 片段；W 的脸不得出现，不能把她生成成可识别主角。 |
| S06 桥洞与刻痕 | 15s | P028-P037 | Q 进入桥洞，前后出口各留一条夕光；镜头转向墙面，刻痕从石头内部“生长”出来，像血管、根系和电路；微光按心跳搏动。 | 旁白：“那些刻痕覆盖了整个桥洞的内壁。” 硫磺味用低沉气流、细碎电流和远处脉冲音表现。 | 3 个 5s 片段；三段都要使用同一面墙的参考帧。 |
| S07 声音入脑 | 12s | P038-P044 | Q 想跑但双腿不动，喉咙发不出声；无方向声波直接在头部周围出现；Q 踉跄后退撞上桥壁，发光刻痕距脸很近。 | 租客：“你在找什么？” 声音不从画面外某个方向传来；加入金属刮玻璃的短促声。 | 先无对白测试运动，再生成带声版本；不要出现租客实体。 |
| S08 租客试探 | 18s | P045-P051 | Q 背贴桥壁大口喘息；黑暗不显形，只让刻痕和空气产生轻微波动；Q 的眼神从恐惧变成被看穿；“容器”一词时做极短抽象刺入感。 | 租客：“别费劲了。这里是世界的夹缝。” Q：“你是谁？这是什么东西？” 租客：“你可以叫我……租客。” | 对白建议后期 TTS/配音，不依赖 H3 自动口型；R 只做音频和光影反馈。 |
| S09 交易提出 | 17s | P052-P059 | 黑暗中刻痕像呼吸；Q 警惕抬头；“杀人”落下时画面短暂失色，桥洞声场被抽空；随后用城市夜景和无形线索表现“锁定即死亡”，不展示具体杀人。 | 租客：“我能给你一个机会。” Q：“什么代价？” 租客：“杀人。” | 避免生成文字特效；“杀人”由声音、停顿和画面失色传达。 |
| S10 左臂坏死 | 20s | P060-P066 | Q 左臂突然剧痛；低头看见灰黑色坏死线沿血管向手肘爬升；镜头切 Q 的眼睛、左手、胸口起伏，刻痕光线越来越强。 | 租客：“现在只是手臂。接下来是内脏，最后是你的大脑。” 心跳逐步变慢，低频压迫增强。 | 4 个 5s 片段；手部是高风险镜头，先做局部动作和遮挡版本。 |
| S11 屈服与退潮 | 12s | P067-P069 | Q 崩溃喊叫；黑线停在手肘，像潮水退去；肤色恢复，Q 瘫软在桥洞地面，泪水和冷汗混在一起。 | Q：“我答应！我答应你！” 退潮声、喘息、心跳恢复。 | 3 个 4s 片段；不要把恢复做成瞬间闪烁，要求连续向下退回。 |
| S12 四小时通牒 | 12s | P070-P072 | 黑暗中的刻痕恢复平静；Q 站起踉跄跑出桥洞，穿过荒草和锈铁门，深橙夕阳压在背后；镜头跟拍至他跪倒。 | 租客：“很好。四小时。” 最后一句“去吧”贴近耳边，随后切断。 | 2 个 6s 片段；出口光方向必须和 S06 相反镜头保持一致。 |
| S13 左手与命运转折 | 7s | P073-P075 | 路灯尚未亮起，Q 跪在暮色中低头检查左手；手干净正常；镜头缓慢拉远，Q 被废墟和城市灯光夹在中间。 | 旁白：“但他知道，有什么东西已经不一样了。” 只留风声和极低心跳。 | 1 个 7s 片段；章节结尾保留 1 秒黑场，方便接第二章倒计时。 |

## 4. 分阶段生成与加码

### 阶段 A：5 秒环境基线

生成 S04-A 与 S04-B 两个各 5 秒镜头：先以低位路径/滑梯镜头建立空间，再用 S04-A 通过帧作为 I2V 首帧，向后上方拉升露出石桥。无对白、无人物、无 LoRA，480p、24 fps、20 steps、固定 seed。

验收：两个输出视频可播放；帧率为 24；没有明显闪烁；S04-B 能稳定露出完整石桥且不俯冲成地面洞口；显存没有 OOM；记录首帧、末帧和耗时。

### 阶段 B：5 秒人物运动

在阶段 A 通过后生成 S05 的前 5 秒：Q 在远处停下，W 只露背影并走入桥洞。先不加对白，保留脚步声后期处理。

验收：Q 的服装、眼镜、背包稳定；W 不露脸；运动方向与桥洞入口一致；不会凭空增加第三个人。

### 阶段 C：5 秒超自然纹理

生成 S06 的前 5 秒：Q 站在桥洞中央，墙面刻痕微光搏动。先做静态构图和低幅度呼吸式光效，再尝试明显搏动。

验收：刻痕仍是同一面墙；光效不溢出成大面积霓虹；人物没有多手指、多眼睛和衣着跳变；连续两次固定 seed 的差异可解释。

### 阶段 D：15 秒连续段

将 A/B/C 中通过审核的片段按时间线拼接，补 S04→S05→S06 的过渡；暂不启用 Turbo LoRA，先确认当前量化底座的原生质量和时间成本。

### 阶段 E：第一章小样片

完成 S01-S06，约 62 秒；再加入 S07-S13，形成约 160 秒第一章初剪。对白、旁白和音效单独生成，不把角色说话依赖在 H3 视频口型上。

## 5. H3 本地首测参数

```yaml
mode: T2VA  # 若当前 ComfyUI 工作流只暴露 FL2VA，则改用关键帧工作流
resolution: 480p
fps: 24
duration_seconds: 5
steps: 20
seed: 20260811
lora: none
sage_attention: enabled
block_cache: disabled
audio: disabled_for_stage_A
reference_strategy: accepted_last_frame_as_next_clip_reference
```

首测阶段不同时打开 Turbo LoRA、BlockCache、SageAttention 以外的模型补丁，避免无法判断速度或质量变化来自哪一个组件。T8 Turbo LoRA 需要非 pruned BF16/INT8 底座，不能直接套当前 `pruned_int4`；T8 Audio 和 BlockCache 等 H3 自定义节点待 Linux 主机恢复连接后逐个验证。

## 6. 记录合同

每个片段保存以下信息：

- `shot_id`、`clip_id`、源段落/漫画格号；
- 模式、分辨率、帧数、steps、seed、采样器、调度器；
- 是否使用 LoRA、SageAttention、BlockCache；
- 生成耗时、峰值显存、是否 OOM；
- 首帧/末帧路径、审核结论、重生成原因；
- 通过后才允许作为下一个片段的参考帧。

## 8. 阶段 A 实测记录（2026-08-12）

- 工作流：[workflow_h3_stage_a_s04.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_stage_a_s04.json)；ComfyUI `0.31.0` 原生 H3 节点；SageAttention 已启用。
- 最终参数：`832x480`、`124 frames`、`24 fps`、`20 steps`、`Euler/simple`、`CFG 1.0`、`seed 20260811`、无 LoRA、无 BlockCache、无音频输出；VAE 使用 GPU FP16，扩散模型低显存动态卸载，文本编码器在 CPU。
- 运行结果：[h3_stage_a_s04_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_stage_a_s04_00001_.mp4)，H.264、832×480、124 帧、5.166667 秒；端到端 585.60 秒，峰值显存约 11.4 GB，无 OOM。
- 运行时结论：通过。媒体封装、帧率、时长、显存稳定性和 SageAttention 加载均通过；VAE 冒烟工作流也已通过。
- 画面结论：暂不通过。抽取首/中/尾帧后发现滑梯出现重复结构，黑色积水不明显，末帧未稳定露出石桥；因此不把该片段作为 S05 的人物参考帧。
- 下一步：把 S04 改拆为 `S04-A 路径+单个滑梯` 与 `S04-B 抬升露出石桥` 两个镜头；先以 S04-A 的通过帧作为 S04-B 的 I2V 首帧，再继续人物段。不要在当前基线未通过前叠加 Turbo LoRA、BlockCache 或 T8 音频节点。

## 9. S04 拆镜头复测结果（2026-08-12）

- S04-A 工作流：[workflow_h3_s04a_path_slide.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_path_slide.json)；输出：[h3_s04a_path_slide_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04a_path_slide_00001_.mp4)。真实运行耗时 `588.77s`，H.264、`832x480`、`24fps`、`124` 帧、`5.166667s`，完整 ffmpeg 解码通过；画面通过为“路径/滑梯建立”，滑梯仍有双轨造型偏差，但未再承担石桥叙事。
- S04-B 首次 I2V：[h3_s04b_bridge_reveal_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04b_bridge_reveal_00001_.mp4) 视觉不通过：中后段再次坍缩为地面洞口/隧道近景，已保留作失败对照，不进入成片。
- S04-B2 工作流：[workflow_h3_s04b2_bridge_wide_reveal.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04b2_bridge_wide_reveal.json)；输出：[h3_s04b2_bridge_wide_reveal_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04b2_bridge_wide_reveal_00001_.mp4)。以 S04-A 末帧为首帧，提示词改为“保持地平线、镜头后拉并上升、中远景、完整单拱石桥”，真实运行耗时约 `740s`，H.264、`832x480`、`24fps`、`124` 帧、`5.166667s`，完整解码通过；首帧承接滑梯，中段和末段均出现完整石桥，视觉通过。
- 阶段性成片：[h3_s04_final_10s.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04_final_10s.mp4)。由 S04-A + S04-B2 拼接，H.264、`832x480`、`24fps`、`248` 帧、`10.333333s`、`4,705,517 bytes`；`ffmpeg -v error -f null` 全量解码通过，关键帧检查了起始路径、A/B 接缝、桥体中段和末帧，画面连续性阶段性通过。
- 结论：H3 INT4 在 RTX 3060 12GB/32GB 上已完成真实“生成—取回—拼接—解码—抽帧”闭环，并产出可播放视频文件；下一阶段才进入 S05 人物背影，不把本轮结果宣称为第一章 160 秒成片。

## 7. 阶段 A 首测提示词

### 正向提示词

```text
横屏电影化科幻惊悚镜头，初冬傍晚，城市边缘的荒废公园，镜头以低机位沿着开裂的石板路缓慢向前移动，路边是一座歪斜的旧儿童滑梯，滑道里积着近乎黑色的雨水，杂草从石板缝隙疯长，锈红色夕阳从危墙后面斜照进来，镜头在最后缓慢抬升，露出干涸河床上的一座小型旧石桥，桥洞内部是浓重得不自然的黑暗，空气中有轻微尘埃和冷雾，压抑、荒凉、超自然恐怖前兆，运动连续缓慢，石桥、滑梯、危墙和夕阳方向保持稳定，写实电影构图，统一空间透视，细节清晰，无人物，无对白
```

### 负向约束

```text
不要人物，不要儿童，不要车辆，不要现代广告，不要可读文字，不要字幕，不要水印，不要霓虹灯，不要大幅镜头抖动，不要快速变焦，不要场景瞬移，不要改变桥洞结构，不要新增建筑，不要重复滑梯，不要画面闪烁，不要塑料质感，不要卡通风，不要照片级人脸
```

阶段 A 只验证环境和镜头运动；若工作流的 `T2VA` 节点不能接受此类提示词，先不要修改提示词内容，改用当前 H3 工作流实际要求的输入模式，并在记录合同中注明模式差异。
## 10. S04 INT4 T2V/音频与 Turbo 对照复测（2026-08-12）

- 纠正：H3 官方 T2V 工作流使用 `MiniMaxH3ImageToVideo`，首帧/末帧端口可留空；本轮官方 1MP T2V 和 Turbo 1MP 工作流均未加载首帧图片。此前出现的 `LoadImage: h3_s04a_official_last.png` 是误投 I2V 工作流的历史校验错误，不是 T2V 的要求。
- 官方 INT4 T2V+音频：[workflow_h3_s04a_official_int4_audio_1mp.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_official_int4_audio_1mp.json)，`1344x768`、`124帧`、`24fps`、`20 steps`、seed `20260815`、无 LoRA；采样 `19:46`，总耗时 `00:25:25`。输出：[h3_s04a_official_int4_audio_1mp_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04a_official_int4_audio_1mp_00001_.mp4)，H.264 视频 + AAC `32kHz/2ch` 音频，完整解码通过。画面空间连续，但单拱桥仍会漂移，不作为单桥通过镜头。
- Turbo v4-600 EMA：[workflow_h3_s04a_turbo_v4_audio_1mp.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_turbo_v4_audio_1mp.json)，`strength=1.0`、`low_vram=false`、`6 steps`、simple 调度器、H3 Turbo sampler、视频/音频联合输出；采样 `06:12`，总耗时 `471.83s`。输出：[h3_s04a_turbo_v4_int4_audio_1mp_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04a_turbo_v4_int4_audio_1mp_00001_.mp4)，H.264 + AAC，完整解码通过。相对官方 20 步约 `3.23x` 加速，滑梯结构更稳定，但桥体三拱语义偏差仍需拆镜头解决。
- 当前生产策略：S04 继续采用“路径/滑梯”与“抬升露桥”拆镜头；Turbo 6 步只作为预演和候选筛选，单镜头通过后再用官方 20 步或 Turbo 6--8 步复现；对白、旁白和最终音效走独立 TTS/后期混音。

## 11. S04 T8 Dual-Clock 4 步复测（2026-08-12）

- T8 节点已安装并在 ComfyUI `0.31.0` 中真实加载，使用 `MiniMaxH3DualClockSamplerT8`、视频 shift `12`、音频 shift `3`、`dual_clock_euler/native_flow`、4 步；本轮仍是 T2VA，首帧留空，不把首帧误认为 T2V 必需输入。
- 480p 输出：[h3_s04a_t8_dualclock_v4_int4_audio_480p_5s_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04a_t8_dualclock_v4_int4_audio_480p_5s_00001_.mp4)。总耗时 `375.06s`，采样 `59s`，峰值显存约 `11.7GB`；首、中、尾帧都维持滑梯右侧、单拱桥左侧，作为空间连续性候选通过，但滑梯栏杆/形体仍有轻微变形。
- 1MP 输出：[h3_s04a_t8_dualclock_v4_int4_audio_1mp_5s_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/output/h3_s04a_t8_dualclock_v4_int4_audio_1mp_5s_00001_.mp4)。总耗时 `581.31s`，采样 `4m20s`，峰值显存约 `10.7GB`；清晰度更高，但桥体从单拱漂移为三至四拱，镜头语义不通过。
- 对照结论：本机 480p T8 比 Larry v4-600 EMA 慢约 `2.84x`，但本次空间关系更稳；1MP T8 比官方 INT4 20 步快约 `2.62x`，仍比 Larry 1MP 慢约 `1.23x`。因此 S04 采用“两级工作流”：Larry 4/6 步做低成本预演，T8 480p 做带音频的空间连续性候选；单拱桥最终镜头继续用 S04-A→S04-B2 的拆镜头结构。
- 不启用 T8 BlockCache、Multi-Rate 或 Prompt Enhancer 作为默认项；这些属于独立变量，必须另开 A/B 记录，尤其 Prompt Enhancer 涉及外部 API，不能混入本地离线验收。

## 12. T8 底座/LoRA 兼容性纠正与复测矩阵（2026-08-12）

- 复核 T8 转换 LoRA README 后，正式 T8 测试必须使用非裁剪 `minimax_h3_fl2va_int8_convrot.safetensors`，LoRA 为 `minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors`，节点为 `LoraLoaderBypassModelOnly`；不能使用 pruned INT8/INT4 底座。
- 因此前一轮 T8 使用 Larry 原始 v4 LoRA + pruned INT4，故此前 T8 视频只保留为“探索性结果”，不代表 T8 正式兼容性或最终速度质量结论。
- 正式矩阵：非裁剪 INT8 官方20步、非裁剪 INT8+Larry v4 6/8步、非裁剪 INT8+T8转换 LoRA 双时钟4步、同一 T8链8步；统一 S04 提示词、seed、124帧、480p与1MP，分别核对速度/峰值显存/画面细节/空间连续性/音频。
- 速度与质量的默认候选暂定为 T8 双时钟8步；4步只作为极速预演，若4步音频和动作同时通过，才保留为低成本预演档。最终结论等待非裁剪 INT8 资产实际加载后再下。

## 13. 速度与画质平衡复测准备（2026-08-12）

- 复测分组固定为官方 20 步、Larry v4 6/8 步、T8 转换 LoRA 双时钟 4 步和 8 步；同一提示词、seed、124 帧、音频、480p 起步，记录总耗时、采样耗时、峰值显存、画面细节、空间连续性和音频洁净度。
- 已修正 1MP/8 步工作流残留的 INT4/原始 LoRA/4 步配置，改为非裁剪 INT8 + NVFP4/AWQ + T8 转换 LoRA + `LoraLoaderBypassModelOnly` + 8 步。
- 正确 INT8 资产因 Linux 端 Xet TLS EOF，改由本机外盘 aria2c 断点续传；完整性和 ComfyUI 加载验证完成前，不开始新的正式视频结论。

## 14. 夸克整合包与 RunningHub 国外宽审核版对照（2026-08-12）

- 夸克公开目录显示 97.4G 低配整合包，含完整/裁剪 INT8 底模、INT8/NVFP4 文本编码器、双 VAE、4-step LoRA、T8 音频节点、KJ、UniBlockSwap 和 ReservedVRAM。当前 Linux 已有 VAE、INT4、NVFP4、音频节点；新增正式 A/B 的裁剪 INT8 底模和 INT8 文本编码器已加入外盘下载，等待精确大小、header 和 ComfyUI 加载验证。
- 8189 隔离端已加载 SageAttention、KJ、T8、Jjk、rgthree、VHS、UniBlockSwap、ReservedVRAM；使用动态显存和 `expandable_segments`，不与旧 8188 同时进行 GPU 生成。
- RunningHub 国外宽审核版公开节点为 22 节点合集，核心是 `MiniMaxH3ReferenceToVideo` + `JjkText` + rgthree 旁路 + 音频解码。页面目前未登录，云端运行待用户在 RunningHub 页面完成登录后继续；不以公开节点列表代替实际云端工作流下载。
- 正式测试顺序：T8 双时钟 8 步优先，官方 20 步作质量基线，裁剪 INT8 + 4-step 作速度档；UniBlockSwap、ReservedVRAM、Spectrum、Motion Context 均作为独立变量。保留 LTX 的“先基线、单变量、完整解码和首中尾帧审核”纪律。

## 15. 非裁剪 INT8 + T8 转换 LoRA 4/8 步正式复测（2026-08-12）

- 资产校验：非裁剪 INT8 底模 `34,038,892,334 bytes / 1035 keys`；NVFP4/AWQ 文本编码器 `15,687,142,551 bytes / 2054 keys`；T8 转换 LoRA `779,858,903 bytes / 518 keys`，均能由 safetensors 读取。NVFP4/AWQ 在当前 ComfyUI 0.31.0 的 `CLIPLoader` 量化配置解析失败（`utf-32-be truncated data`），未进入采样；因此正式可运行链路暂改用已通过的 INT4 文本编码器，失败边界单独保留。
- 4/8 步统一：`MiniMaxH3DualClockSamplerT8`、`shift_video=12`、`shift_audio=3`、`dual_clock_euler/native_flow`、T2VA、无首帧、832×480、124 帧、24fps、seed `20260815`，非裁剪 INT8 底模 + `minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors` + `LoraLoaderBypassModelOnly` + SageAttention。
- 8 步输出：[h3_s04a_t8_dualclock_int8_int4clip_audio_480p_5s_8steps_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-12-h3-int8-t8/h3_s04a_t8_dualclock_int8_int4clip_audio_480p_5s_8steps_00001_.mp4)，ComfyUI prompt `7f9a3c32-48f7-4276-89be-62233745815f`，执行 `485.97s`，峰值约 `8.7GB`，H.264 + AAC，5.167s；完整媒体读取与首中尾帧抽取通过。8 步首中尾帧路径和滑梯/桥空间关系连续，但末段为多拱桥，不能作为单拱桥成片。
- 4 步输出：[h3_s04a_t8_dualclock_int8_int4clip_audio_480p_5s_4steps_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-12-h3-int8-t8/h3_s04a_t8_dualclock_int8_int4clip_audio_480p_5s_4steps_00001_.mp4)，ComfyUI prompt `d7ca78c3-f5a3-489e-887b-9e70c120f01a`，执行 `400.53s`，峰值约 `8.4GB`，H.264 + AAC，5.167s；首中尾帧无结构性崩塌，但细节和稳定性略低于 8 步，末段同样为多拱桥。
- 音频轨道存在但过低：8 步约 `-46.7dB` mean / `-34.3dB` max，4 步约 `-49.9dB` mean / `-37.3dB` max；暂不作为成片音频。下一次如继续优化音频，应只改变音频节点/后期增益，不和 BlockCache、Multi-Rate、Motion Context 同轮叠加。
- 本机档位：4 步用于镜头预演和低成本抽卡；8 步用于速度/画质平衡候选。单拱桥场景仍采用 S04-A→S04-B2 拆镜头和通过帧承接策略；人物背影 S05 之前不再把单镜头复杂空间目标继续堆入一个 prompt。

## 16. 1MP 交付档与 Multi-Rate 音频档复测（2026-08-13）

### 16.1 1MP T8 8步高质量候选

- 工作流：[workflow_h3_s04a_t8_dualclock_int8_audio_1mp_5s_8steps.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_t8_dualclock_int8_audio_1mp_5s_8steps.json)。底座为非裁剪 INT8，LoRA 为 T8 转换版，文本编码器因 NVFP4/AWQ 在当前 CLIPLoader 解析失败而使用已通过的 INT4 版；T2VA 不接首帧，`1344x768`、124 帧、24fps、8 步、seed `20260815`。
- ComfyUI prompt `bda8f2c1-5138-492b-8952-7f8e95280cba`，总执行 `00:16:02`，采样约 `10:17`，无 OOM。输出：[h3_s04a_t8_int8_int4clip_audio_1mp_5s_8steps_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-t8-1mp/h3_s04a_t8_int8_int4clip_audio_1mp_5s_8steps_00001_.mp4)，H.264 + AAC 32kHz/2ch，`1344x768/5.167s`，SHA-256 `6cdcf85c8f353b46ce755bea975c74a20228b4ce31dea80175de99be638cafe2`。
- 首中尾帧通过完整解码检查：首帧为夕阳下滑梯，中段镜头沿路径/滑梯移动，尾帧抬升露出河床石桥；空间关系连续、桥体完整，无全局崩塌，但模型仍将“单拱桥”扩展为多拱桥。可作为高质量空间参考，不直接作为单拱桥叙事成片。
- 音频 `mean -43.2dB / max -31.2dB`，有音轨但过低；继续使用独立对白/旁白/音效混音。

### 16.2 T8 Multi-Rate 4V10A 音频实验

- 新工作流：[workflow_h3_s04a_t8_multirate_int8_audio_480p_5s_4v10a.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_t8_multirate_int8_audio_480p_5s_4v10a.json)。仅将已通过的 T8 4V8A 链改为 `MiniMaxH3MultiRateSamplerEXPT8(video_steps=4,audio_steps=10)`，其余底座、LoRA、文本编码器、提示词、seed、分辨率和音频设置不变。
- ComfyUI prompt `456b21e0-b858-459d-90ec-320292387812`，执行 `520.65s`，无 OOM；输出：[h3_s04a_t8_multirate_4v10a_int8_int4clip_audio_480p_5s_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-multirate-4v10a/h3_s04a_t8_multirate_4v10a_int8_int4clip_audio_480p_5s_00001_.mp4)，H.264 + AAC 32kHz/2ch，`832x480/5.167s`，SHA-256 `ae44ac77fbdd55715138ef76719511e2a156dd0e35e3a9e33ea744d91524c93c2`。
- 音频 `mean -39.7dB / max -27.9dB`，相较 4V8A 约改善 1.5--2dB；但运行时 RAM 约 29GiB、交换约 6.7GiB，且比 4V8A 慢约 50s，故仅作为音频优先实验档。
- 画面首中尾帧保持路径、滑梯和桥的空间承接，无黑屏/全局坍塌；桥仍为多拱结构。4V10A 没有解决单拱桥语义问题，不能替代拆镜头策略。

### 16.3 本轮运行态保护

- 每轮执行前后均检查 `wechat-linux-bot.service=active`。任务结束后只调用 H3 8189 `/free` 释放动态模型，队列为空，GPU 回落约 `632MiB/12GB`；微信 Bot、Qwen 路由、微信 GUI 和 VPN/Clash 均未停止。
- 当前“清理其他模型”执行为清理运行态显存/缓存，不删除可复用模型文件、LoRA、VAE 或工作流；未发现第二个正在占用 GPU 的推理模型进程。

## 17. 优化插件与连续性链路实测（2026-08-13）

### 17.1 BlockCache / Spectrum

- BlockCache 原生 20 步工作流：[workflow_h3_s04a_blockcache_official_int8_audio_480p_5s.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_blockcache_official_int8_audio_480p_5s.json)，总耗时 `566.15s`，缓存 `6/20` 次模型前向，首中尾帧无全局崩坏；输出保留在 [h3_s04a_blockcache_official_int8_audio_480p_5s_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-blockcache/h3_s04a_blockcache_official_int8_audio_480p_5s_00001_.mp4)。
- Spectrum 原生 20 步工作流：[workflow_h3_s04a_spectrum_official_int8_audio_480p_5s.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_s04a_spectrum_official_int8_audio_480p_5s.json)，总耗时 `542.47s`；日志确认实际/预测交替和离线回放，首中尾帧空间连续、无整体塌陷；输出保留在 [h3_s04a_spectrum_official_int8_audio_480p_5s_00001_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-spectrum/h3_s04a_spectrum_official_int8_audio_480p_5s_00001_.mp4)。两者只作为实验加速变量，不叠加 LoRA。

### 17.2 长剧情连续性试验

- Motion Context 第一段工作流：[clip1](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_motion_context_clip1_official_int8_480p_5s.json) 已成功保存 AV latent；视频输出为 [h3_motion_context_clip1_official_int8_480p_5s_00002_.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/h3_motion_context_clip1_official_int8_480p_5s_00002_.mp4)，latent 为 [clip_00001.safetensors](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/h3_context/clip_00001.safetensors)。
- 第二段工作流：[clip2](/Volumes/AJW-Data/Projects/novel-to-comic-engine/projects/destiny-model/workflow_h3_motion_context_clip2_official_int8_480p_5s.json) 在当前 RTX 3060 12GB/32GB 低显存调度下未能完成 trim 后输出，不能作为连续性通过证据。第一章仍采用 S04-A→S04-B2 拆镜头、通过帧承接和单镜头短段生产。

### 17.3 生产决策

- 默认顺序：Larry 6步筛选镜头 → T8 8步或官方20步复现 → 首中尾帧检查 → 独立对白/旁白/音效混音。
- BlockCache/Spectrum 需要固定 seed 做独立质量复核；ReservedVRAM 和 UniBlockSwap 在本机当前组合下不纳入默认链；Motion Context 保留为后续升级 64GB 内存或更高显存后的专项链路。
