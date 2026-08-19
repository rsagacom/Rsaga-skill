# H3 长期生产底座选择

更新时间：2026-08-18  
硬件：Linux `cachyos-ai`，RTX 3060 12GB，32GB RAM  
证据总览：`/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/`

## 最终选择

当前长期生产默认底座为：

```text
剪枝 INT8 FL2VA
+ INT4 ConvRot Qwen 文本编码器
+ 原生视频 VAE / 音频 VAE
+ drbaph 剪枝专用 raw-key Turbo LoRA
+ SageAttention 启动参数
+ 640×384、24fps、8 steps、Euler/simple、CFG 1.0
```

Linux 路径：

- 扩散模型：`/mnt/sd_nvme/MiniMax-H3/quark-package/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- Qwen：`/mnt/gaosu_sata/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_int4_convrot.safetensors`
- LoRA：`/mnt/sd_nvme/MiniMax-H3/community-src/drbaph/minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors`
- 视频 VAE：`/mnt/gaosu_sata/ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors`
- 音频 VAE：`/mnt/gaosu_sata/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors`

LoRA 必须使用 H3 专用 `MiniMaxH3TurboLoRA` 节点和剪枝兼容 raw-key 文件。日志必须同时出现 `lora ACTIVE`、`BypassForwardHook` 以及实际 injection/merge 统计；通用 T8-convert LoRA 在剪枝底座上的 reshape 错误或“部分注入”不能算生产通过。

## 模型评测门禁已经收口

本轮先用短段门禁选长期生产底座，再把 15 秒及以上长链成本集中到唯一通过者。当前矩阵已覆盖剪枝 INT4、剪枝 INT8、非剪枝 INT8、GGUF Q4_K_M、DiffSynth NF4；并归档了 LoRA 兼容性、远/中/近/特写、4/8/20 steps、真人/3DCG/水墨/2D 动漫、640/832/1088 级别，以及 15 秒 Director/Motion Context 证据。

| 模型链 | LoRA门禁 | 短段/长链证据 | 3060长期生产结论 |
| --- | --- | --- | --- |
| 剪枝 INT8 + drbaph 专用 raw-key | 完整：`lora ACTIVE`、208 backbone、158 bypass、50 INT8 fc2 merge | 远/中/近/特写通过；Director 与 Motion Context 15 秒通过，后续段 `drift 0.00ms` | **唯一默认底座** |
| 非剪枝 INT8 + full EMA/T8 | 完整样本通过，质量上限更高 | 5 秒和历史长链通过 | 关键镜头备用；约 32GB 以上 staged，不能批量默认 |
| GGUF Q4_K_M | Dynamic GGUF 链可出片；直接 Q4 文本编码器曾 OOM | T2V/R2V 可解码，速度过慢 | 兼容性救援档，不进长链 |
| 剪枝 INT4 + drbaph/标准 Loader | 可出片，但部分 LoRA/适配链存在边界 | 画面更软，未进入长链排名 | 极低资源备用 |
| DiffSynth NF4 | 当前 ComfyUI LoRA 链未复用 | 512×288/5 秒通过 | 独立实验，不进生产 |

后续固定规则：**不再对未选模型运行 Director/Motion Context 长视频；只在上述剪枝 INT8 主链上做 R2V/三视图、对白、Director 和 Motion Context 专项。**

## 为什么选它

| 候选 | 真实证据 | 结论 |
| --- | --- | --- |
| 剪枝 INT8 + drbaph 专用剪枝 LoRA | 5 秒四景别可出片；15 秒 Director/Motion Context 均通过；人物和 3DCG 场景可连续；LoRA 有真实注入日志 | **默认长期生产** |
| 非剪枝 INT8 + full EMA LoRA merge | 画质上限较高，5 秒可通过；约 32.4GB staged，内存和 swap 压力大 | 关键镜头质量参考/备用 |
| GGUF Q4_K_M | 官方 FL2VA/REF2VA/Qwen 工作流 T2V/R2V 能完整解码；Q4 文本编码器直接链曾 OOM，动态 CPU/offload 极慢，Sage 曾回退 PyTorch | 低显存兼容性备用，不进长链 |
| 剪枝 INT4 + 专用剪枝 LoRA | 能运行，速度/资源较省，但 3DCG 材质、眼部和脸部细节更软 | 极低资源备用 |
| DiffSynth NF4 | 512×288/5 秒兼容性通过，但不是当前 ComfyUI 长视频生产链 | 独立实验对照 |

## 两个长视频组件的分工

二者不是二选一，也不应在同一个 ComfyUI 进程同时 patch H3：

1. **Director**：上层时间轴、叙事单元、镜头顺序、提示词、局部重跑和人工编排入口。
2. **Motion Context**：底层 AV latent 接力和断点恢复。首段使用三视图/面部参考图建立角色，后续段只加载上一段 latent，不重复注入参考板。

已验证的当前默认链：`Director 编排 → 首段 R2V → Motion Context 接力 → 独立 TTS/环境声/SFX → FFmpeg/Remotion 混音`。

### 15 秒结果

- 640×384：Motion Context 4 段，8 steps，精确 15 秒，后续段 `drift 0.00ms`；适合默认批量生产档。
- 640×384：Director 3 段可完成，但出现 `unexpected audio grid` 和 5 帧 phase-align 裁切；必须统一时长并复核接缝后再交付。
- 1088×608：Motion Context 4 段合计约 46 分钟，15 秒精确成片和完整解码通过；适合少量正脸/关键道具/片头片尾精选镜头，不适合全剧默认。

证据：

- `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-plugin-ab/REPORT.md`
- `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-hd-lora-gate/REPORT.md`
- `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-reference-audio-gate/REPORT.md`

## 分辨率生产档位

| 档位 | 用法 | 实测判断 |
| --- | --- | --- |
| 640×384 | 预演、普通镜头、长链默认 | 速度和内存余量最好，15 秒接力已通过 |
| 832×480 | 常规精选镜头、人物中近景 | 5 秒 LoRA 单段通过，细节明显好于 640 |
| 1088×608 | 极限精选镜头 | 5 秒和 15 秒接力通过；32GB RAM 约使用 12–13GiB swap，单段约 11 分钟 |

不要把 1088×608 的单段通过误写成整部剧可用。长剧应先以 640×384 完成构图、动作和连续性，只有通过人工画面门禁的镜头升级分辨率。

## 音频和对白边界

H3 原生音频可作为氛围参考，但不直接承担最终对白。生产链使用 CosyVoice3 生成角色对白/旁白，独立环境声和 SFX 补足氛围，FFmpeg/Remotion 混音和封装，Whisper small + 人工抽听/抽帧做 QA。独立 TTS、ASR、5 秒替换和 15 秒混音均已有真实通过证据；逐音素口型同步仍未通过，严格对白镜头需要口型驱动或带对白重新生成。

## 每条生产片的最低门禁

1. 固定模型、LoRA、seed、分辨率、steps、采样器和唯一 latent 目录。
2. 保存工作流 JSON、prompt id、每段耗时、模型/LoRA 注入日志和 AV latent。
3. 保存 MP4、ffprobe、完整 `ffmpeg -f null -` 解码结果、首/中/尾帧、接缝抽帧和 SHA-256。
4. 检查 GPU、RSS、swap、CPU/I/O；GPU 短时 0% 不能单独判定假死。
5. 交付前检查人物脸部、手部、服装、场景几何、参考板泄漏、字幕伪影、音频漂移和口型。
6. 保护微信 Bot `8077`、Qwen 路由 `8090`、CosyVoice3 `8789`，H3 必须使用隔离进程/端口。

## 暂不扩大的范围

模型选型已经收口，后续不再对所有模型做长视频笛卡尔积。下一阶段只在上述底座上优化：3DCG 人物参考包、Director 编排、Motion Context 接力、832/1088 精选镜头、对白口型、TTS/混音和失败分段重跑。30 秒及以上正式生产必须在 15 秒和高清对白门禁后再加码。

## 2026-08-17 平台 Provider 真实入口验收

选定底座已通过项目 `ComfyUIVideoProvider` 的真实 T2V Job：640×384、124 帧、24fps、8 steps、T2VA 原生音频，端到端 `316.132s`，MP4/AAC 完整解码通过。队列中实际保留了业务提示词；日志确认 `MiniMaxH3TurboLoRA` 的 `BypassForwardHook => lora ACTIVE`、208 个 backbone modules、158 个 bypass adapters、1 次 injection。证据：[Provider T2V 报告](../test-runs/2026-08-17-h3-provider-t2v-smoke/REPORT.md)。

因此模型评测主线已经收口：不再对未选模型做长视频笛卡尔积。后续只在该底座上推进 Director 编排、首段 R2V/三视图参考、Motion Context 接力、TTS/混音和生产失败恢复。

## 平台化专项门禁（2026-08-17）

选型结论之后的平台执行链已经落为可恢复 Job，而不是一次性脚本：

- `POST /api/episodes/{episode_id}/long-video` 接收 Director 已拆好的分段提示词；
- 第 1 段只能使用 `director`，后续段只能使用 `motion-context`；
- 每段完成即落盘状态、时长、媒体 SHA/ffprobe、workflow role 与 latent 引用；
- 进程或 GPU 中断后，retry 只执行未完成段；
- 未配置真实长视频 workflow 时明确失败，不把独立 5 秒片段冒充连续长视频。

因此后续专项仍只使用“剪枝 INT8 + drbaph 专用 raw-key Turbo LoRA”底座，先在
640×384/8 steps 完成任务链和接缝门禁，再按 832×480/1088×608 精选镜头升级。

## 2026-08-17 选定底座平台长链最终门禁

报告：`test-runs/2026-08-17-h3-platform-long-video-15s-real-gpu-v3/REPORT.md`。

选定链已由项目平台真实执行通过：Director 首段保存 AV latent，Motion Context 连续承接 3 段，平台持久化每段状态并封装精确 15 秒。端到端 1587.236 秒，最终 640×384/24fps、H.264/AAC，完整解码通过。该结果将“模型可跑”提升为“平台可恢复长视频链可跑”。

长期生产决策保持不变：默认底座为剪枝 INT8 FL2VA + INT4 Qwen + drbaph 剪枝专用 raw-key Turbo LoRA + 原生双 VAE + SageAttention；Director 负责上层分段/镜头与局部重跑，Motion Context 负责 AV latent 接力和断点恢复；默认批量档为 640×384/8 steps，832×480、1088×608 只做精选镜头；不再横向测试未选模型的长视频组合。

## 2026-08-17 平台多参考图 R2V 生产门禁

报告：`test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/REPORT.md`。

平台已经支持 `reference_asset_ids` 多图输入：首段使用四张角色身份图进入 `MiniMaxH3ReferenceToVideo`，后续段只加载上一段 AV latent。选定剪枝 INT8 + drbaph raw-key LoRA 在 640×384、8 steps 下真实完成四段、精确 15 秒成片；端到端 `1784.481s`（约 29 分 44 秒），所有片段和最终文件完整解码通过。最终 SHA-256：`28512e3f48417524ac482ec15016c50c30bcf188207a1919a995249eba472543`。

这一结果把“模型和插件能跑”进一步提升为“平台能安全消费多张参考图并恢复长链”。生产规则更新为：三视图/面部参考只在首段或角色建立段注入，后续段禁止重复参考板；人物对白和最终音频继续走 CosyVoice3、环境声/SFX 与混音链；30 秒以上仍需在 15 秒通过后按段加码，不与未选量化模型做笛卡尔积。

## 2026-08-18 ClipProj / EasyCache 低显存复测更新

新报告：`test-runs/2026-08-18-h3-clipproj-easycache-gate/REPORT.md`。

本轮 5 秒短片公平 A/B 后，3060 12GB 的低显存首选更新为：剪枝 INT8 FL2VA + Qwen3-VL-4B FP8 + ClipProj v3.1 MLP + 剪枝兼容 raw-key Turbo LoRA + SageAttention + 640×384/8 steps。实测 `204.72s`，相同条件 32B INT4 为 `383.93s`；4B ridge 为 `151.98s`，仅用于预演。MLP 不是最快，但抽帧和 `cos_test 0.8116` 更适合正脸、对白和关键镜头。

EasyCache 的本机 H3 结果必须以日志为准：阈值 0.05 跳过 0/20，阈值 0.20 跳过 2/20、1.11×；暂不升级为默认无损加速。SolAttn 只备用，不和 EasyCache 同时引入。该结论没有外推到新底座 15 秒长链；Director + Motion Context 的已验证长视频链仍是生产编排标准。

## 平台参考长链的独立对白门禁（2026-08-17）

在平台四图 R2V + Motion Context 的真实 15 秒成片上，使用常驻 CosyVoice3 生成 `这座桥，为什么还在呼吸……`，并完成保留 H3 氛围床与纯对白两种 A/B 封装。两个版本均为 640×384、15.000 秒、AAC 32kHz/2ch，并通过完整解码；证据目录为：

`/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/audio/`

该结果把音频链也纳入平台门禁，但边界必须保留：TTS 已真实通过，文字级 ASR 因 `8790` 与 Whisper small 权重缺失暂未通过，后期混音不等于逐音素口型同步。生产默认继续采用 `H3 视觉/可选氛围 → CosyVoice3 对白 → 独立环境声/SFX → FFmpeg/Remotion 混音`。

## 局部重跑与失败恢复门禁（2026-08-17）

服务层失败恢复用例已通过：第 1 段完成、第 2 段首次失败，retry 后调用序列 `[1, 2, 2]`，已完成段不重新采样。真实 GPU Job 的四段状态和 AV latent 引用也已复核为完整。生产中必须保存失败段的日志、显存/内存/swap、workflow、`context` 目录与 `clip_index`；只有在上一段 latent 和尺寸契约完整时才自动重跑，否则转人工修复。

## 2026-08-19 ClipProj 8B 候选的 15 秒真实长链门禁

报告：`test-runs/2026-08-19-h3-clipproj-8b-long-gate/REPORT.md`。

本轮补齐了 8B 文本编码器的有效采样，而不是只登记下载。8B NVFP4 与 8B FP8 的 ridge/MLP 均完成 5 秒短片；其中 8B FP8 + MLP 为 `166.80s/5s`，本轮抽帧无明显字幕样伪影，并进入 Director 首段 + Motion Context 后续段的 15 秒真实长链。最终 640×384、24fps、15.000 秒，采样合计 `723.01s`，平台墙钟约 `728.93s`，SHA `d819998f78d158f3a5db539449cfcd4191c32a9e3832c6ce003ce2d7adf3aeca`。

选型保持双档：4B FP8 + MLP 是 3060 12GB 的低压批量档；8B FP8 + MLP 是质量/速度优先档，仅用于精选镜头和在确认系统可用内存不少于约 2GB 时运行。8B NVFP4 作为备用，不因体积小直接替换 FP8。EasyCache 本机跳步极少，仍不是默认加速 SLA。

本轮第一次长链失败是白名单遗漏 Motion Context/Director 节点，修正后才完成；以后使用 `--disable-all-custom-nodes` 时必须把 `ComfyUI-H3-Motion-Context`、`ComfyUI_MiniMaxH3_Director_AIMixer` 和实际 workflow 依赖逐项做 `/object_info` 门禁。
