# MiniMax H3 本地生产交接手册（框架无关）

> 版本：v1.7 · 更新时间：2026-08-23（H3 统一迁移至 gaosu_nvme/h3-production） · 适用主机：`host.cachyos-ai-desktop` / `cachyos-ai`  
> 目标：让 Codex、Claude Code、其他 Agent、脚本或人工运维都能从同一份事实记录恢复 H3 本地测试与生产链路。  
> 本文只记录已验证事实、可复现命令和明确边界；不记录密码、Token、Cookie 或私钥内容。

> **📍 2026-08-23 迁移通告（现行事实，优先级高于下文旧路径）**
> H3 本地生产环境已从双盘缠绕（`/mnt/gaosu_sata` + `/mnt/sd_nvme/MiniMax-H3`）整体统一迁移至 **`/mnt/gaosu_nvme/h3-production/`**（自足，三道验收门全过）。
> 路径映射：`/mnt/gaosu_nvme/h3-production/ComfyUI` → `/mnt/gaosu_nvme/h3-production/ComfyUI`；`/mnt/gaosu_nvme/h3-production/runtime` → `/mnt/gaosu_nvme/h3-production/runtime`；`/mnt/gaosu_nvme/h3-production/MiniMax-H3`、`MiniMax-H3-NF4`、`MiniMax-H3-diffsynth` → 同名子目录。
> venv 现行：ComfyUI 主程序用共享环境 `/mnt/gaosu_nvme/AI-Linux/boogu/ComfyUI/.venv`（py3.14.6，含 sqlalchemy/transformers/sageattention/torch）；`runtime/runtime-venv` 为节点侧 torch/sageattention 环境。隔离实例端口建议 **8192**（8189 已被 boogu-api 占用）。
> 旧盘即将随「高速sata NTFS→XFS 转换」清空重格；本文历史证据段落中的旧路径仅作历史记录，不代表现存位置。验收证据：Atlas `task-20260822-150643-0fe352`（节点门 + 2026-08-17 短链×2 + 2026-08-19 长链 Director+MotionContext 接力全部 success，LoRA 每次 call `is_injected=True`，旧盘模型加载 = 0）。

## 0.4 离线评测资料台（2026-08-15）

为便于在笔记本上逐条复核，已生成无需部署的静态资料包：[h3-evaluation-offline/index.html](/Volumes/AJW-Data/Projects/novel-to-comic-engine/h3-evaluation-offline/index.html)。双击 `index.html` 即可打开；必须连同同目录的 `runs/`、`sources/`、`posters/` 和索引文件一起复制。

资料包覆盖 2026-08-12～15，共 29 个 H3 测试批次、120 个视频和 625 个证据文件。每条样本保留视频长度、真实分辨率/帧率/帧数、音视频编码、文件大小、SHA-256、已确认的生成耗时、模型/LoRA/节点/采样参数、提示词预览、报告、日志、工作流 JSON 和抽帧链接。页面明确区分媒体机器验收、抽帧证据和报告中的人工画质结论；未能从证据单独确认的生成耗时不会用视频时长代替。

## 0.2 开源 LoRA 逐个替换 A/B 复测（2026-08-14）

在隔离 Director 端口 `8191` 完成同一三视图 R2V、832×480、124 帧、24fps、同一 seed 的 LoRA 对照。成功样本为 T8 标准 Loader 基线 `891.58s`、Larry v4 EMA merge `941.63s`、Realism People `931.30s`、Drbaph Ref2V `831.04s`、Kijai LightX2V Ref2V `832.00s`；全部输出为 5.167 秒 H.264/AAC 并通过 ffprobe。抽帧观察：Realism People 的真人近景面部和皮肤连续性最好；Drbaph Ref2V 的速度/清晰度综合最好；Kijai 可用但未超过 Drbaph；Larry 仅有轻微清晰度改善且内存较重。

本轮发现 T8-convert 与当前专用 `MiniMaxH3TurboLoRA` 的键前缀不兼容：专用节点日志为 `0 bypass adapters/0 injections`，不能把该诊断当成有效 LoRA 结果；T8-convert 在标准 `LoraLoaderModelOnly` 下才是有效基线。Larry bypass 注入虽能挂载但 32GB RAM/Swap 压力过大，生产只保留 low-vram merge。标准 Loader 的成功样本在采样阶段约 29GiB RAM，必须串行运行、任务前后 `/free`，不能与正式 `8189` 并发。

详细证据：[H3 LoRA A/B 报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-lora-ab/AB_REPORT.md)。

## 0.3 独立 5 秒片段拼接 30 秒复测（2026-08-14～15）

新增一条与 Motion Context 不同的生产路径：用 Drbaph Ref2V 独立生成 6 段 5 秒片段，再用 FFmpeg concat 拼接。六段均为 832×480、124 帧、4 steps、H.264/AAC，真实生成总耗时 `4818.75s`（约 80 分钟）；原始无重编码拼接为 `31.033656s`，精确裁切版为 `30.000000s`，两者均完整解码通过。

抽帧确认六段大体保持同一人物/服装/雨巷，但各硬切点存在明显站位、镜头角度、脸部表情、动作方向和独立声场跳变。因此该方案可作为“5 秒分镜剪辑成 30 秒短视频”，不能替代 Director/Motion Context 的连续 latent 和音频上下文接力。证据见：[CONCAT_REPORT.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-5s-concat-30s/CONCAT_REPORT.md)。

## 0.1 三视图 R2V 角色建立段新增实测（2026-08-14）

在 `8191` 隔离 Director 端口使用三张独立角色参考图做了 832×480、124 帧、8 steps 的 R2V 短测。日志确认 `external r2v groups × 1`；没有首帧连接，也没有使用 `i2v_groups`，所以这是参考主体生视频，不是首帧 I2V。非剪枝 INT8 + INT4 Qwen + T8 LoRA + SageAttention 链路真实完成，端到端耗时 `891.58s`，输出 5.167 秒 H.264/AAC 视频，完整解码通过。采样约 3分33秒，低显存初始化/CPU-交换区搬运约 10 分钟；观察到峰值约 11.5GiB 显存、约 70°C，无 OOM。

抽帧结果：没有三视图白底参考板泄漏；人物从中远景推进到中近景时，脸型、发型、发簪和服装色彩比 704×416 长视频远景更稳定。边界是三视图并不等于面部超分或绝对身份锁定，嘴型与局部五官仍有轻微变化。生产建议：首段/角色建立段用三视图 R2V，后续段固定分辨率走 Director 内置接力或独立 Motion Context，避免每段重复挂全局参考板。完整证据见 [三视图 R2V 测试报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-r2v-3view-832x480-5s/TEST_REPORT.md)。

## 0. 先读什么

任何 AI 框架接手本项目时，按以下顺序读取：

1. 本文：`docs/H3_PRODUCTION_HANDOFF.md`
2. 总蓝图：[AI_MANHUA_STUDIO_BLUEPRINT.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/docs/AI_MANHUA_STUDIO_BLUEPRINT.md)，当前覆盖 v1.99
3. 机器验收清单：[review_manifest.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix/review_manifest.json)
4. 逐项审阅页：[review_index.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix/review_index.md)
5. Atlas 项目实体：`project.novel-to-comic-engine`；主机实体：`host.cachyos-ai-desktop`；模型范围：`model-endpoint.minimax-h3`

不要只依赖某个 AI 框架的会话记忆，也不要把旧版本蓝图中的失败实验当成当前状态。本文的“当前结论”以 2026-08-13～14 的矩阵、Motion Context 和 Director 实测为准。

## 1. 当前结论

H3 本身是画面生成底座，两个连续性组件构成生产控制层：

- **MiniMax H3**：负责画面、风格、动作和原生氛围音频的生成。
- **Motion Context**：负责把上一段尾部的 video latent、音频上下文和关键帧承接到下一段；适合手工控制、研究接缝和故障隔离。
- **MiniMax H3 Director（AIMixer）**：负责分段时间轴、自动上下文承接、缓存清理、局部重跑和生产编排；默认生产入口优先使用它。

当前已经证明“可运行的长视频生产候选链”：Director 内置 Motion Context 已真实生成 30/60/120 秒目标时长。仍不能把它直接宣称为最终成片级别；正式生产还必须通过角色/场景一致性、对白 ASR/口型、最终混音、断点重跑和人工画质门禁。

推荐生产方向：

```text
Director 时间轴编排
        ↓
Motion Context / Director 内置上下文接力
        ↓
H3 + 剪枝 INT8 + drbaph 剪枝专用 raw-key Turbo LoRA + SageAttention
        ↓
独立 TTS / 音效 / 混音
        ↓
首帧-中帧-尾帧 + 完整视频人工验收
```

模型选择门禁已经收口：先用 5 秒固定 seed 短段完成 LoRA 注入、远/中/近/特写、步骤数、画风和分辨率筛选，再只对通过的剪枝 INT8 主链运行 Director/Motion Context 15 秒专项。非剪枝 INT8、GGUF Q4_K_M、剪枝 INT4 和 DiffSynth NF4 的结果只作为质量上限、兼容性或低资源备用证据，不再进入长视频笛卡尔积。

Director 和独立 Motion Context 不要在同一个 ComfyUI 进程叠加运行时补丁；本轮长视频使用 Director 内置接力，独立插件作为手工控制/故障回退链，需要切换时使用不同工作流或隔离端口。

## 2. 主机、服务和安全边界

| 项目 | 已验证事实 |
|---|---|
| 主机 | Linux `cachyos-ai`，LAN `192.168.1.6`，SSH 用户 `rsaga` |
| GPU | NVIDIA RTX 3060 Laptop GPU，显存 12,288 MiB |
| RAM | 32GB |
| 正式 H3 ComfyUI | `8189`，当前优先正式链路 |
| Motion Context 隔离测试 | `8190`，历史测试端口；不要与正式端口并发抢 GPU |
| Director 隔离测试 | `8191`，历史测试端口；不要与正式端口并发抢 GPU |
| 微信 Bot | `/mnt/gaosu_sata/wechat-linux-bot/bot/reply_bot.py`；严禁为 H3 测试停止、清理或改路由 |
| H3 启动目录 | `/mnt/gaosu_nvme/h3-production/runtime/ComfyUI` |
| ComfyUI base directory | `/mnt/gaosu_nvme/h3-production/ComfyUI` |
| H3 user directory | `/mnt/gaosu_nvme/h3-production/runtime/user-8189` |

每轮测试前后必须检查：

```bash
ssh -i /Users/rsaga/.ssh/id_ed25519 rsaga@192.168.1.6 /bin/bash -s
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader
pgrep -af 'wechat-linux-bot/bot/reply_bot.py'
curl -fsS http://127.0.0.1:8189/queue
```

服务保护规则：

- 只清理 H3 自己的模型运行态、测试端口和任务；不删除模型文件、LoRA、VAE、工作流或 latent。
- 不停止微信 Bot、Qwen 路由、微信 GUI、Clash/VPN。
- 正式 8189 正在服务时，长时间变量测试使用隔离端口，测试前后核对显存和队列。
- 运行结束后可释放 H3 模型显存，但不要误杀 Bot 依赖的 Qwen 进程。

## 3. 已验证的模型和节点底座

### 3.0 长期生产选型（2026-08-17 收口）

当前 RTX 3060 12GB + 32GB RAM 的长期生产主链不是“任意 INT8 + 任意 Turbo LoRA”，而是：

```text
剪枝 INT8 FL2VA
+ MiniMax H3 专用 drbaph 剪枝 Turbo LoRA（raw-key 适配文件）
+ INT4 ConvRot Qwen H3 文本编码器
+ 原生视频/音频 VAE
+ --lowvram --fp16-vae --use-sage-attention
```

选型依据是同一机器上的真实媒体和长链门禁：剪枝 INT8 + 专用 LoRA 已通过 4 景别 5 秒矩阵、Director 15 秒和 Motion Context 15 秒；后续段均以 AV latent 接力而非独立片段拼接，输出完成全量解码和音视频漂移检查。当前生产默认分辨率为 `640×384`，8 steps 用于正式镜头，4 steps 只用于预演；`832×480` 作为 10–12 秒候选，`1088×608` 只作为精选单镜头高清档。

非剪枝 INT8 + 匹配 LoRA 保留为质量上限/备用底座；GGUF Q4_K_M 保留为极慢兼容备用；剪枝 INT4 只保留为低资源画风探索。它们不再进入本轮长视频生产穷举。所有 LoRA 都必须先确认实际 injection 日志，再把媒体结果计入排名。

### 3.1 模型资产

正式矩阵使用的是“非剪枝 INT8 扩散底座 + INT4 Qwen 文本编码器 + T8 转换 LoRA”，不是把任意量化模型和任意 LoRA 混搭。

| 用途 | 远端 canonical 路径 | 已核对大小 |
|---|---|---:|
| H3 非剪枝 INT8 FL2VA | `/mnt/gaosu_nvme/h3-production/runtime/quark-package/models/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors`（由 ComfyUI 软链接接入） | 34,038,892,334 bytes |
| H3 裁剪 INT4 FL2VA | `/mnt/gaosu_nvme/h3-production/ComfyUI/models/diffusion_models/minimax_h3_fl2va_pruned_int4_convrot.safetensors` | 11,337,536,776 bytes |
| Qwen H3 INT4 文本编码器 | `/mnt/gaosu_nvme/h3-production/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_int4_convrot.safetensors` | 14,952,506,624 bytes |
| Qwen H3 NVFP4/AWQ | `/mnt/gaosu_nvme/h3-production/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15,687,142,551 bytes |
| 视频 VAE | `/mnt/gaosu_nvme/h3-production/ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496 bytes |
| 音频 VAE | `/mnt/gaosu_nvme/h3-production/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors` | 605,254,808 bytes |
| T8 转换 Turbo LoRA | `/mnt/gaosu_nvme/h3-production/ComfyUI/models/loras/minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors` | 779,858,903 bytes |

主生产 LoRA（剪枝底座专用）另见：
`/mnt/gaosu_nvme/h3-production/ComfyUI/models/loras/minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors`；其原始专用 LoRA 与 raw-key 适配文件的 SHA-256、注入日志和 5/15 秒媒体证据记录在 `test-runs/2026-08-17-h3-pruned-lora-15s/REPORT.md`。

资产注意事项：

- `minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors` 必须配非剪枝 `minimax_h3_fl2va_int8_convrot.safetensors`，不能配 `pruned` 底座。
- 当前目录中存在下载残留的 `.partial`、`.chunk`、带 URL 查询参数的文件；它们不是可加载资产，不能拿来做模型存在性判断。
- `/mnt/gaosu_nvme/h3-production/ComfyUI/models/loras/minimax_h3_turbo_4STEPS_comfyui.safetensors` 当前仅 94 bytes，是占位/残留文件，不是本轮使用的有效 LoRA。
- NVFP4/AWQ 文本编码器曾在当前 ComfyUI 0.31.0 的 CLIPLoader 路径出现 `utf-32-be truncated data`；默认复现使用已通过的 INT4 Qwen。

### 3.2 自定义节点

| 节点目录 | 已核对 commit | 用途 |
|---|---|---|
| `/mnt/gaosu_nvme/h3-production/ComfyUI/custom_nodes/comfyui-minimax-h3-audio-T8` | `7a99dba` | H3 音视频节点、T8 双时钟采样链 |
| `/mnt/gaosu_nvme/h3-production/ComfyUI/custom_nodes/ComfyUI-MiniMax-H3-Turbo` | `55fee86` | H3 Turbo 基础节点 |
| `/mnt/gaosu_nvme/h3-production/ComfyUI/custom_nodes/ComfyUI_MiniMaxH3_Director_AIMixer` | `4c9c58a` | Director 时间轴和段间引导 |
| `ComfyUI-KJNodes` | 白名单加载 | SageAttention KJ、辅助节点 |
| `ComfyUI-Jjk-Nodes` | 白名单加载 | 工作流辅助节点 |
| `rgthree-comfy` | 白名单加载 | 分组/旁路辅助 |
| `ComfyUI-VideoHelperSuite` | 白名单加载 | 视频保存/处理 |
| `ComfyUI_UniBlockSwap` | 白名单加载 | 低显存辅助 |
| `ComfyUI-ReservedVRAM` | 白名单加载 | 显存预留 |

Director 节点必须放在 ComfyUI 实际 `--base-directory` 下的 `custom_nodes`；本机正确目录是 `/mnt/gaosu_nvme/h3-production/ComfyUI/custom_nodes/ComfyUI_MiniMaxH3_Director_AIMixer`。Director 工作流隐藏输入 `bd_grp_sample`、`bd_grp_advanced`、`bd_grp_perf` 必须存在，否则会出现 API 400。

## 4. 正式 H3 运行链（2026-08-30 起：systemd 按需托管，取代裸启动）

**现行唯一启动方式**（cachyos-ai 上）：

```bash
ssh cachyos-ai '~/.local/bin/h3-ctl on'      # 拉起：自动卸 LLM → 起 8192 → 健康轮询
ssh cachyos-ai '~/.local/bin/h3-ctl status'  # 单元状态 + 8192 探活 + 显存占用
ssh cachyos-ai '~/.local/bin/h3-ctl off'     # 主动清退（平时不用：LLM 请求会自动清退它）
```

- 单元：`~/.config/systemd/user/rsaga-h3-comfyui.service`（**故意不 enable**：按需档，
  重启不自启；`Restart=no`）。ExecStart 为原裸进程 cmdline 的逐参数复制：
  venv=`runtime/venv-0.34.0`（注意：不再是 boogu 共享 .venv）、tree=`ComfyUI-v0.34.0-tree`、
  端口 **8192**、user-dir `user-8192-034`、白名单 20+ 节点（ClipProj/GGUF-MiniMax-H3/
  BlockCache-T8/Spectrum/PDD-Acc/FaceRefine/H3-Motion-Context/Director_AIMixer…，
  完整串见单元文件）。`ExecStartPre=gpu-claim-h3.sh` 先调 llama-swap unload 卸 LLM。
- **显存排他**：12GB 卡上 LLM(qwen3.8 48k) 与 H3 互斥——LLM 请求到达时
  `gpu-claim-llama.sh` 会 `systemctl --user stop` 本单元（SIGINT 优雅停，"H3 进程怎么没了"
  先查这里，机制详见 cachyos-ai `~/.config/llama-swap/BLUEPRINT.md` §4.5）。
  **红线：渲染队列进行中勿触发 LLM 请求**，锁会硬停队列。
- 历史（2026-08-23 版）启动形态归档：曾以 boogu 共享 .venv 裸跑 `--port 8189`
  （后被 8192 隔离实例取代）；该段命令已失效，仅留 Git 历史可考。

### 4.1 质量/速度默认参数

```text
模式：T2V，first_frame / last_frame 可不接，不要误判为必须 I2V
帧率：24fps
标准短段：124 帧 ≈ 5.167 秒
默认采样：8 steps
关键镜头：20 steps，仅在 1MP 候选通过人工审阅后使用
视频 shift：12
音频 shift：3
采样器：MiniMaxH3DualClockSamplerT8
schedule：dual_clock_euler / native_flow
LoRA：LoraLoaderBypassModelOnly → MiniMaxH3DualClockSamplerT8
分辨率：必须遵守 H3 节点约束，宽高使用 32 的倍数

生产 LoRA：剪枝底座使用 drbaph 专用 raw-key LoRA；非剪枝底座才使用已验证的 T8-convert LoRA。二者不能互换。
```

4 steps 可用于快速预演，但当前项目的高画质批量基线以 8 steps 为主；官方无 LoRA 20 steps 只保留为失败技术基线，不能作为生产质量基准。

## 5. 两个核心长视频组件

### 5.1 Director：默认生产编排层

- 工作流：[workflow_aimixer_director_t8_2segments_832x480.json](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-director-aimixer/workflow_aimixer_director_t8_2segments_832x480.json)
- 输出：[aimixer_director_2segments.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-director-aimixer/aimixer_director_2segments.mp4)
- 实测：两段、合计 248 帧、832×480、24fps、8 steps、10.334 秒；总耗时约 18:28；峰值显存约 9.3GiB。
- 日志事实：第 2 段接收 22 个视频上下文帧和 40 个音频步，合并 `seam_gap=0`。
- 适用：生产时间轴、批量分段、自动清缓存、局部重跑。
- 当前边界：共享 venv 缺少可选 `scenedetect`；不影响本次手工分段文生视频，但自动切镜仍需独立补依赖验收。

### 5.2 Motion Context：底层接力和故障回退层

- 工作流：[clip1](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/workflows/workflow_clip1_t8_lora_832x480_5s.json)、[clip2](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/workflows/workflow_clip2_t8_lora_motion_context_832x480_5s.json)
- 输出：[clip1.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/clip1.mp4)、[clip2_trimmed.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/clip2_trimmed.mp4)、[chain_concat.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context/chain_concat.mp4)
- 实测：首段 5.167 秒，第二段去掉 22 帧重叠后 4.25 秒，合并 9.449 秒；832×480、24fps、8 steps；两段约 464.97 秒和 521.56 秒；峰值显存约 9.26GiB。
- 固定参数：`context_length=22`、`audio_context_length=24`、`match_tail=true`、关闭 Spectrum 跳步、分辨率完全一致。
- 适用：研究接缝、手工上下文控制、单段故障隔离和 Director 的底层回退。

两个组件共同构成 H3 长视频生产控制层，但不要在同一 ComfyUI 进程同时启用 Director 和独立 Motion Context 运行时补丁。

### 5.3 Director 内置 Motion Context 长视频实测（2026-08-14）

本轮使用国风雨巷 + 普通话人物独白 + 环境音效提示词，在 `8191` 隔离端口完成 30/60/120 秒三档真实测试。这里的“Motion Context”是 Director 内置的上下文接力路径；独立 `ComfyUI-H3-Motion-Context` 没有在同一进程叠加运行时补丁。

| 目标 | 段数 | raw 时长 | final 时长 | 总耗时 |
|---:|---:|---:|---:|---:|
| 30 秒 | 2 | 30.166667s | 30.000000s | 2065.61s / 34分25.61秒 |
| 60 秒 | 4 | 59.917000s | 60.000000s | 4438.23s / 73分58.23秒 |
| 120 秒 | 8 | 119.417000s | 120.000000s | 8667.43s / 2小时24分27.43秒 |

固定为 704×416、24fps、每段 362 帧、22 个视频 latent overlap、40 个音频上下文步、8 steps、非剪枝 INT8 + INT4 Qwen + T8 Turbo LoRA + SageAttention、原生 T2VA 音频。每个后续段的日志都确认了视频/音频上下文接力；120 秒还记录了 7 个接缝的 12 帧亮度平滑，没有 OOM。

输出和逐项边界见：[H3 长视频评测报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-long-director-audio/review_report.md)。最终视频：

- [30 秒](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-long-director-audio/final_30s_director_guofeng_monologue.mp4)
- [60 秒](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-long-director-audio/final_60s_director_guofeng_monologue.mp4)
- [120 秒](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-long-director-audio/final_120s_director_guofeng_monologue.mp4)

结论：30 秒适合重复验证，60 秒适合人工审片，120 秒可以生成但在 32GB RAM 下 Swap 压力和段间 CPU 整理开销很大，不适合批量生产。原生 H3 音频已验证存在、可解码、可封装，但独白逐字 ASR/口型和最终 TTS 混音尚未验收。

### 5.4 高负载、段间卡顿与假死误判规则

120 秒实测中，采样阶段通常约占用 8.0–8.5GB 显存并达到 100% GPU 利用率，ComfyUI 进程 CPU 约 149–164%；系统 RAM 一度达到 29–31GiB，Swap 约 31–36GiB。段与段之间曾出现 GPU 约 1.9GB/0%、日志数分钟无新增，但进程 CPU 仍约 149–157%，随后继续进入下一段。这是 CPU 侧 VAE、视频导出、缓存/latent 整理、序列化或模型重载，不是本次测试中的死锁。

后续排障必须把以下情况视为“仍在运行”：GPU 利用率暂时为 0%、显存回落、日志暂时不动、进程状态为 `S`、Swap 增长；先核对进程存活、CPU、RAM/Swap、日志 mtime、文件 I/O 和队列状态。只有进程退出、OOM/Traceback、HTTP 5xx、队列异常结束，或 CPU 与 GPU 同时无活动且日志 mtime/文件 I/O 超过 10 分钟不变，才进入疑似假死处理。不要因为 GPU 空闲窗口直接强杀，尤其要保留正在写出的 raw 视频与上下文文件。

推荐观察命令（在隔离测试端口运行时）：

```bash
watch -n 5 'date; ps -o pid,stat,etime,%cpu,%mem,rss,cmd -p <DIRECTOR_PID>; free -h; nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader; tail -n 8 <RUN_LOG>'
```

本轮 120 秒最终 8/8 段完成、无 OOM/Traceback；因此“段间几分钟无日志”应作为已知高负载特征写入告警降噪规则，而不是自动失败重试条件。

## 6. 高清矩阵实测结果

测试脚本：[run_h3_hd_matrix.py](/Volumes/AJW-Data/Projects/novel-to-comic-engine/scripts/run_h3_hd_matrix.py)、[verify_h3_hd_matrix.py](/Volumes/AJW-Data/Projects/novel-to-comic-engine/scripts/verify_h3_hd_matrix.py)。

固定变量：非剪枝 INT8 FL2VA、INT4 Qwen、T8 转换 LoRA、SageAttention、DualClock、T2V、seed `20260813`、124 帧、24fps、T2VA 原生音频。

| 分辨率 | 8 steps 单条耗时 | 20 steps 单条耗时 | 适用结论 |
|---|---:|---:|---|
| 640×384 | 6.5–7.4 分钟 | 未测 | 最低可运行基线 |
| 832×480 | 8.5–8.9 分钟 | 未测 | 长视频默认起点 |
| 960×544 | 9.7–10.2 分钟 | 未测 | 中间质量档 |
| 1088×608 | 11.7–12.1 分钟 | 未测 | 速度/画质平衡推荐 |
| 1216×672 | 13.9–14.3 分钟 | 未测 | 高清过渡档 |
| 1344×768（约 1MP） | 17.2–17.4 分钟 | 32.4–33.1 分钟 | 8 steps 高清候选；20 steps 关键镜头 |

覆盖风格：真人写实、3D 国风、3D 仙侠、赛璐璐动画、水墨 CG；共 35 条，全部通过 ffprobe、完整 ffmpeg 解码和首/中/尾抽帧检查。矩阵峰值显存约 11,033MiB，32GB RAM 可完成但 1MP 已接近 12GB 显存边界。

## 7. 长时长和显存门禁

| 配置 | 实际结果 | 当前策略 |
|---|---|---|
| 832×480 / 10.125 秒 / 243 帧 | 成功，约 12:33 | 质量/长度平衡档 |
| 832×480 / 12.25 秒 / 294 帧 | 成功，约 14:48 | 质量优先长镜头上限候选 |
| 832×480 / 15.083 秒 / 362 帧 | CUDA OOM | 禁止作为 3060 12GB 默认档 |
| 704×416 / 15.083 秒 / 362 帧 | 成功，约 14:06 | 15 秒可行档，后期超分 |

结论：H3 必须使用“分辨率 × 时长 × LoRA/控制变量”的联合门禁，不能只看显存容量。`832×480` 推荐控制在 10–12 秒；15 秒切换到 704×416 或更低，长剧采用 5～10 秒分段接力。

## 8. 音频与画质验收规则

- H3 输出的 AAC 音轨已经能正确封装和解码，但电平偏低，不能直接承担对白、旁白和最终音效。
- H3 原生音频保留为氛围底层或参考；最终生产必须走独立 TTS、音效、增益和混音。
- 每条样本必须记录：总耗时、采样耗时、峰值显存、分辨率、帧数、视频时长、音频轨、LoRA、seed、工作流路径和 SHA-256。
- 机器验收不等于画面验收。至少检查首帧、中帧、尾帧和完整播放：人物/服装、场景几何、动作方向、闪烁、手部、音画接缝。
- 预演阶段先发现问题；通过的镜头再进入高分辨率/20 steps 或长视频接力，避免成片后返工。

## 9. 可复现命令

### 9.1 只生成 35 个矩阵工作流

```bash
cd /Volumes/AJW-Data/Projects/novel-to-comic-engine
python3 scripts/run_h3_hd_matrix.py --include-20-steps --write-only
```

### 9.2 连接已运行的正式 H3 执行矩阵

```bash
python3 scripts/run_h3_hd_matrix.py \
  --include-20-steps \
  --base-url http://192.168.1.6:8189 \
  --poll-seconds 10
```

脚本会保留 `results.jsonl`，成功任务不会重复提交；工作流落盘到 `test-runs/2026-08-13-h3-hd-matrix/workflows`，视频落盘到 `outputs`。

### 9.3 逐条媒体验收和建立审阅索引

```bash
python3 scripts/verify_h3_hd_matrix.py
python3 scripts/build_h3_hd_review_index.py
```

### 9.4 本地评测网页

```bash
cd /Volumes/AJW-Data/Projects/novel-to-comic-engine/web
npm run build
npx next start -p 3117
```

浏览器打开：`http://127.0.0.1:3117/h3-evaluation`。页面会直接从外盘读取视频，不把大文件复制进 Web 仓库；媒体 API 对 MP4 支持 Range 播放。

## 10. 交接时的固定检查表

```text
[ ] 先读本文、蓝图、review_manifest 和 Atlas project.novel-to-comic-engine
[ ] 核对主机 GPU/RAM、8189 状态、queue、Bot 进程
[ ] 确认加载的是完整非剪枝 INT8，不是 .partial/.chunk/占位文件
[ ] 确认 T8 LoRA 与非剪枝 INT8 底座匹配
[ ] 确认 SageAttention、FP16 VAE、低显存参数和白名单节点
[ ] 确认 Director / Motion Context 只启用一套运行时接力方式
[ ] 先做 832×480、5 秒、8 steps 小样本
[ ] 再按分辨率/时长联合门禁加码
[ ] 每条样本做 ffprobe + 完整 ffmpeg 解码 + 首中尾帧抽查
[ ] 最后确认微信 Bot、Qwen 路由、GPU 和 H3 queue 没被误伤
```

## 11. 还没有完成的生产级门禁

以下内容必须继续真实测试后，才能把 H3 生产链宣称为“整集生产级”：

1. Director 30/60/120 秒连续接力已经通过机器级生成和媒体解码；仍需逐段人工检查人物、服装、场景几何、镜头方向和字幕样式文字。
2. 独立 Motion Context 做同样的多段链路，与 Director 进行 A/B，记录每个接缝的视觉/音频差异。
3. 中途失败后只重跑单段，验证缓存、上下文和最终拼接不会破坏前段。
4. 真实对白、旁白、音效和 H3 氛围音轨的最终混音与响度验收。
5. 704×416 15 秒成功档的后期超分和 1088×608/832×480 分段成片对照。
6. 自动切镜依赖 `scenedetect` 的 Director 功能单独补依赖后验收。

在这些门禁完成前，当前项目的准确表述是：**H3 + T8 + SageAttention + Director 内置 Motion Context 已形成可复现的 30/60/120 秒本地长视频候选链，但整集级生产仍需对白/口型、最终混音、断点重跑和人工画质门禁。**

## 12. 证据索引

- [H3 高清评测页](http://127.0.0.1:3117/h3-evaluation)
- [35 样本逐项审阅索引](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix/review_index.md)
- [35 样本机器验收清单](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix/review_manifest.json)
- [H3 高清矩阵工作流目录](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix/workflows)
- [Motion Context 产物目录](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context)
- [Director 产物目录](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-director-aimixer)
- [30/60/120 秒 Director 独白音频评测目录](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-14-h3-long-director-audio)
- [H3 评测网页代码](/Volumes/AJW-Data/Projects/novel-to-comic-engine/web/app/h3-evaluation)

## 13. 2026-08-15 人物一致性优化复测

本轮在隔离 ComfyUI `8191` 复测“独立面部近景 + 4 张身份参考 + 参考尺寸 + 视频锚点”，正式 `8189` 未改动。完整证据见 [IDENTITY_AB_REPORT.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-15-h3-identity-reference-ab/IDENTITY_AB_REPORT.md)。

### 13.1 已验证结果

- `832×480 / 124 帧 / 4 步 / Drbaph Ref2V LoRA / 面部近景+正侧背，共 4 张图`成功，实际 `932.62s`，视频 `5.167s`，H.264/AAC，完整解码通过；五官在首、中、尾帧明显比上一轮远景推进镜头稳定。
- 同 seed、同提示词把 `ref_max_size` 从 `832` 改为 `2048` 后成功，实际 `941.52s`，仅慢约 9 秒；抽帧与 832 组几乎无肉眼差异，不能证明参考尺寸放大带来画质收益。
- 同样 4 张图再接入 5 秒视觉锚点视频后，`20分39秒`仍停留在参考视频 VAE 准备阶段，安全中断，无成片。当前 RTX 3060 12GB/32GB 配置不把“整段视频作参考锚点”列为默认生产链。

### 13.2 当前人物稳定性规则

1. 对白镜头优先面部近景/中近景：让脸占画面高度约 35%～45%，并限制镜头为近似锁机位、轻微呼吸和小幅转头。
2. 4 张身份参考的有效新增变量是独立面部近景；原有正面、侧面、背面参考继续承担体型、发型、服装和背面轮廓，不把三视图本身误认为新结论。
3. `ref_max_size=832` 作为 12GB 默认；`2048` 只在原始参考图确实包含更多细节时做单镜头复核，不能默认提高五官质量。
4. 远景用于空间和走位，近景用于脸部与对白；不要要求同一条低分辨率远景同时承担可审阅五官。
5. 长剧情先用通过的 5～10 秒镜头分段接力。当前硬件优先采用关键帧/尾帧承接；不要对每一段直接挂 5 秒视频锚点，除非愿意接受超过 20 分钟的参考预处理等待。

## 14. 2026-08-15 3DCG 人物一致性复测

完整证据见 [3DCG_IDENTITY_REPORT.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-15-h3-3dcg-identity-ab/3DCG_IDENTITY_REPORT.md)。本轮用同一套 3DCG 正面、侧面、背面和面部近景参考，固定非裁剪 INT8、INT4 文本编码器、Drbaph Ref2V LoRA、SageAttention、Director R2V、832×480、4 步和同一 seed，隔离比较 3DCG 三视图与 3DCG 面部近景参考。

- 三视图组成功，5.167 秒，实际 `819.08s`；首/中/尾帧中脸型、发型、玉簪、朱红汉服和腰带稳定，3DCG 短镜头人物一致性已经通过。
- 面部近景组成功，5.167 秒，实际 `988.64s`；眼距、鼻口、面部渲染和玉簪细节有小幅稳定收益，但比三视图多约 20.7% 耗时，不是所有镜头都必须启用。
- 3DCG 的优势表现为 5 秒内材质/服装/发型/脸部漂移较小；不能据此宣称 30 秒、60 秒或 120 秒连续长视频已经通过。

当前 3DCG 生产档：普通构图/预演先用三视图；对白近景、角色首次出场或嘴部细节要求高的镜头再加面部近景。下一阶段应使用面部参考组跑 15～30 秒，检查转身、镜头远近变化、服装和剑鞘位置，不再把 `ref_max_size` 或整段视频锚点作为默认优化。

## 15. 2026-08-15 3DCG 远近景 30 秒连续性复测

完整逐段日志、工作流、SHA-256、ffprobe、完整解码和抽帧见 [3DCG_LONG_CONTINUITY_REPORT.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-15-h3-3dcg-long-continuity/3DCG_LONG_CONTINUITY_REPORT.md)。

| 方案 | 耗时 | 机器验收 | 视觉验收 |
|---|---:|---|---|
| Director + 三视图 | `3576.19s` | 30.166667秒、724帧、完整解码通过 | 通过；远景细节有限，约25秒接缝有轻微重影 |
| Director + 面部增强 | `3460.52s` | 30.166667秒、完整解码通过 | 不通过；约25～30秒第6段为整段彩噪 |
| Motion Context-only + 三视图 | 七段约 3666.7秒 | 精确30秒、719帧、完整解码通过 | 通过；远/中/近/侧/背面稳定 |
| Motion Context-only + 面部增强 | 七段约 3920.21秒 | 精确30秒、719帧、完整解码通过 | 通过；近景五官更稳，尾段无彩噪 |

Motion Context 面部增强成片：[motion_facepack_30s_7segment_final.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-15-h3-3dcg-long-continuity/motion_facepack_30s_7segment_final.mp4)，SHA-256 `dc32d8c90fdd9c86795db443a9006172b5705c1439d466c0eb7552c52e3fde3f`；三视图成片：[motion_3view_30s_7segment_final.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-15-h3-3dcg-long-continuity/motion_3view_30s_7segment_final.mp4)，SHA-256 `473620e2d5b84740b845c032b6be3fab37877c84142cea9b361db538012e5ccf`。

关键决策：Director 和独立 Motion Context 不能在同一 ComfyUI 进程叠加；8191 的失败根因是两个插件同时 patch H3 layout，必须拆分端口/进程并重启切换。Motion Context 首段交付 5.167 秒、后续段约 4.25 秒，6 段不足 30 秒，必须补第 7 段再统一裁切。当前本机默认采用 `8192 Motion Context-only + 三视图`，对白和表情镜头采用面部近景 + 三视图；Director 面部增强版尾段彩噪解决前不作为默认长片底座。

### 15.1 复现与运行陷阱

- Motion Context 每段必须保存/加载上一段 AV latent，使用 `context_length=22`、`audio_context_length=24`；不能将后续段重新当作独立 T2V。
- Director 各段可能出现 `unexpected audio grid` 和自动 `trim_prev_export=5f`，要检查接缝抽帧和音频波形。
- 每段结束使用带 `Content-Type: application/json` 和 `-d '{}'` 的 `/free`；空 body 会触发 ComfyUI JSONDecodeError。
- 704×416 是 RTX 3060 12GB 的当前实用连续性档；高分辨率与长时长分开做门禁。H3 原生音频只证明连续封装，正式对白/音效仍走 TTS 和后期混音。

## 16. 2026-08-16 DiffSynth NF4 隔离基线

完整证据见 [DiffSynth NF4 实测报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-16-h3-nf4-diffsynth/REPORT.md)。本轮采用国内 ModelScope `DiffSynth-Studio/MiniMax-H3-NF4`，独立 venv 和官方 `MiniMaxH3Pipeline`，不接入现有 ComfyUI LoRA/SageAttention 链。

- 512×288、124 帧、24fps、50 步、T2VA、Torch SDPA、磁盘卸载、NF4 `vram_limit=5.5GiB`：真实耗时 `734.13s`，输出 5.175 秒 H.264/AAC，完整解码通过。
- 640×384、832×480 在同一 12GiB 卡上均未通过 50 步：bitsandbytes NF4 反量化/DiT MLP 临时张量 OOM；832×480 即使暂停 Qwen/MiniCPM 并关闭 SageAttention仍失败。
- 该成功样本只证明 NF4 官方 T2VA 底座可在 3060 12GB 低分辨率出片；不等于 GGUF `Q4_K_M` 已通过，也不等于生产级画质已通过。
- 生产链仍优先采用已验证的 ComfyUI 非剪枝 INT8 + 匹配 LoRA + SageAttention + Director/Motion Context；NF4 暂列低显存实验底座，继续单独做分辨率、LoRA 和画质门禁。

证据目录：`/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-16-h3-nf4-diffsynth/`；Linux 原始 run：`/mnt/gaosu_sata/MiniMax-H3-diffsynth/runs/nf4-pruned-fl2va-smoke-attempt-08-512x288-vram55/`。

## 17. 2026-08-17 剪枝底座 T8/Turbo LoRA 复测边界

完整报告：[剪枝 T8/Turbo 烟测](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-turbo-smoke/REPORT.md)。本轮先用同一 3DCG 中景、640×384、124 帧、5 秒目标做兼容性分层：

- 剪枝 INT4 + T8 DualClock、无 LoRA 4 steps 成功，端到端 473.92 秒，5.167 秒 H.264/AAC，完整解码通过；因此剪枝模型与 T8 采样器本身可运行。
- 剪枝 INT4/INT8 + 通用 `LoraLoaderBypassModelOnly` + T8-convert LoRA 在 4/8 steps 均报 `1x8 × 2688x16`，属于剪枝时间曲线与通用 bypass 适配错误，不能用于画质结论。
- 当前 H3 专用 `MiniMaxH3TurboLoRA` 在剪枝 INT4 上进一步报 `'MiniMaxH3Model' object has no attribute 'diffusion_model'`，失败在 `_inject_adaln_egrid` 的模型对象路径。节点可注册不等于 LoRA 已实际注入。

标准 `LoraLoaderModelOnly` 的第三条路径能够生成并完整解码：剪枝 INT4 4/8 steps 为 60.44/80.43 秒，剪枝 INT8 4/8 steps 为 100.47/110.64 秒；日志确认 259 个主干 patch，但全部 `adaln_proj` 时间条件 LoRA reshape 被跳过。因此这组只能作为“部分 LoRA 速度/画面探索”，不能替代完整曲线适配。生产默认仍保留非剪枝 INT8 的已验证 T8 LoRA；剪枝 LoRA 继续标记 `loader compatibility pending`。

部分 LoRA 探索链追加的剪枝 INT4 四画风中景（4 steps、640×384、5.167 秒）均完整解码：3D CG 60.44 秒、真人写实 383.91 秒、水墨/线稿 362.16 秒、2D 动漫 352.11 秒。真人样本没有整体塌脸但仍偏插画，水墨与 2D 动漫风格更稳定；不得把这组结果写成完整 Turbo LoRA 画质结论。首次切换画风时远端 RSS 约 16～17GiB、swap 约 12～13GiB，必须串行运行并观察 swap/GPU，不能并发。

因此当前生产表述更新为：非剪枝 INT8 + 匹配 T8 LoRA 的历史链路仍是已验证加速基线；剪枝 INT4/INT8 的 T8 LoRA 链路为 `loader compatibility pending`，在兼容节点/作者工作流验收前只能使用无 LoRA控制链做底座/步骤/画风摸底。所有 LoRA 质量样本必须记录实际 injection 数、显存/RAM、完整 MP4 解码与首中尾帧。

## 18. 2026-08-17 GGUF Q4_K_M 双链路门禁

完整报告：[GGUF Q4_K_M 报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-gguf-q4-baseline/REPORT.md)。独立 ComfyUI `8192` 仅启用 `ComfyUI-GGUF-MiniMax-H3`，没有修改正式端口、微信 Bot 或 Qwen 路由。

- Q4 FL2VA T2V：`--novram --cpu-vae --use-sage-attention`、8 steps、640×384、124 帧、24fps，实际 `2683.57s`，输出 5.167 秒 H.264/AAC，完整解码通过；首中尾帧为偏写实 3D CG/插画质感，人物与环境短段连续。
- Q4 REF2VA R2V：同一启动参数和采样尺寸，正确女性参考图匹配提示词的 `refmatch` 实际 `2558.32s`，输出 5.167 秒并完整解码；青蓝外袍、米色内衬、腰带、肩包、发簪和面部主体在首中尾帧保留。此前男性提示词样本单独标为提示词-参考图冲突负控。
- `--lowvram` 在 Qwen Q4 反量化阶段 OOM；`--novram` 虽可运行，但单个 5 秒段约 42～45 分钟、累计读盘约 209GB。SageAttention 参数已启用但因当前 H3 输入 shape 报错自动回退 PyTorch，不记录为 Sage 加速。

生产决策：Q4_K_M 只作为 12GB/32GB 机器的兼容性备用底座，不进入全画风、LoRA 或 10/15/30 秒长视频穷举；后续精力集中在剪枝 INT8 的步骤/画风筛选及非剪枝 INT8 + 匹配 LoRA 的长视频链路。

## 19. 2026-08-17 剪枝 INT8 8 steps 生产前筛选

完整证据：[剪枝矩阵报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-16-h3-pruned-shot-matrix/REPORT.md)。无 LoRA、官方原生 T2VA、640×384、124 帧、24fps、5.167 秒、剪枝 INT8 FL2VA + INT4 Qwen，四景别和四画风中景全部通过全量解码。

| 类别 | 耗时 | 当前判断 |
|---|---:|---|
| 3DCG 远/中/近/强特写 | 351.18/413.13/412.87/423.52s | 强特写脸占约60%时五官最稳；远景只判构图和走位 |
| 真人写实中景 | 412.82s | 结构稳定，但该条为背/侧面，需单独正脸特写 |
| 国风水墨/线稿中景 | 413.23s | 线稿、符纹、轮廓稳定 |
| 2D 动漫中景 | 424.70s | 赛璐璐轮廓稳定 |

与剪枝 INT8 20 steps 四景别 `462.30～534.11s` 相比，8 steps 适合第一轮景别/画风筛选；20 steps 只对入选正脸/特写和生产候选复核。剪枝 LoRA 仍未通过完整 injection 门禁，本轮不能用作 LoRA 画质结论。重复任务的模型 staged/卸载使 GPU 短时 0% 但 CPU/I/O 持续，属于正常高负载，不应判假死。

## 20. 2026-08-17 剪枝 INT8 Motion Context-only 10/15/30 秒

完整证据：[剪枝 INT8 长连续性报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-long-continuity/REPORT.md)。为避免 Director 与 Motion Context patch 冲突，Motion Context 使用独立 `8192` 隔离服务；本轮只加载 Motion Context，不把 Director 节点混入同一进程。

- 固定链路为剪枝 INT8 FL2VA + INT4 Qwen + 原生视频/音频 VAE + Sage/KJ patch，无 LoRA，640×384、24fps、8 steps、Euler/simple、`context_length=22`、`audio_context_length=24`。首段使用 3DCG 三视图，后续段只加载上一段 AV latent。
- 7 段均成功：首段 5.1667 秒，后续每段 4.25 秒；端到端耗时 `501.36/461.67/483.20/493.65/482.47/482.28/484.26s`。每段通过 `ffprobe`、全量 `ffmpeg -f null -`，且日志确认 `loaded AV latent` 和 `drift 0.00ms`。
- 严格裁切成片均通过：10 秒 `786807930fc3c46b77a34dbcb862c6f420f20a75781dae3ef9b2541fa54d9f22`，15 秒 `5e194fe3d83397dc759d13645e0d4458b75efcedc8c131a02ec045c47b084885`，30 秒 `c2cc14b2d0458603f41f81db6ea53b407dff44d061196e846aab611e77663a0e`。输出均为精确目标时长、640×384、24fps、音视频精确同长。
- 3DCG 远景、中景、近景和正面特写在连续链中保持；第 5～7 段特写眼距、鼻梁、嘴形和发饰无结构性熔化。当前 3DCG 长连续性默认优先 Motion Context-only，Director 作为编排入口和候选段选择工具。

## 21. 2026-08-17 剪枝 INT8 真人写实长链

证据包：`/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-style-long/REPORT.md`。剪枝 INT8、640×384、8 steps、无 LoRA 的原生 T2V 首段和 Motion Context 后续 6 段全部完成；7 段实际采样合计 2,983.96 秒。最终 10/15/30 秒文件均完整解码且音画精确对齐，30 秒 SHA-256 为 `6e814d255e4c89a5046a3f30246f7ee54df496d2943100c0eb1773ed1c4be759`。

画面核验：真人背影、背包、符文墙和色调连续；中近景未出现结构性脸部熔化。由于本轮没有正脸特写，不得把该结果标记为“真人特写生产级通过”。水墨、2D 动漫和 LoRA/高分辨率门禁仍未完成。
- 资源边界：单段约 7.7～8.4 分钟，完整七段约 56 分钟；主权重约 19,995MB staged，GPU 10.3～11.6GiB，RSS 16～18GiB，CPU 230～247%。高 CPU、swap 和短暂 GPU 0% 均已在日志中确认是正常 staging/动态卸载过程，不是失败。
- 该组无 LoRA，不能证明剪枝 Turbo LoRA 兼容；当前剪枝 LoRA 仍是 `loader compatibility pending`。1000px 以上也没有在该长链通过，必须单独做 OOM/画质门禁。

## 22. 2026-08-17 剪枝 INT8 水墨与 2D 动漫长链

统一证据包：[多画风长链报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-style-long/REPORT.md)。水墨 7 段总采样 `3117.83s`，2D 动漫 7 段总采样 `3118.51s`；两组均使用原生 T2V 首段 + Motion Context 后续 6 段，640×384、24fps、8 steps、Euler/simple、无 LoRA。10/15/30 秒导出均通过 ffprobe、完整解码和音画同步。

- 水墨链通过风格生效、砖拱/符文/人物轮廓连续和 `drift 0.00ms`；30 秒 SHA-256：`986062555449e8fcd664e7f463d263b7d83fac50d61a922e4bbb155ec3158a63`。
- 2D 动漫链通过赛璐璐线条、平涂阴影和近景正脸抽帧核验；30 秒 SHA-256：`9ddde10c39a45f6dbe27a30cc0e5d3f59e3894b40029ee4b90ba72143f703570`。

三画风长链采样耗时差异小于约 4.5%，当前主要瓶颈是剪枝 H3 权重 staging，不是画风提示词。三组仍是无 LoRA 低分辨率连续性门禁，不能替代真人正脸特写、LoRA、832×480/1000px、对白口型验收。

## 23. 2026-08-17 剪枝 INT8 高分辨率与 LoRA 复测

高分辨率证据：[分辨率门禁报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-resolution-gate/REPORT.md)。剪枝 INT8、2D 动漫、原生 T2VA、8 steps、无 LoRA 的 832×480/5 秒耗时 522.33 秒，1088×608/5 秒耗时 684.34 秒，均通过完整解码；1088×608 当前可作为 12GB 机器的高分辨率候选，但不等于 30 秒高分辨率长链已通过。

LoRA 证据：[LoRA 复测报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-lora-repass/REPORT.md)。剪枝 INT8 + T8-convert LoRA + 标准 ModelOnly Loader 可出片，但多个 `adaln_proj.linear.weight` reshape 错误证明时间条件 LoRA 没有完整注入，当前只能标记 `partial-injection-only`；当前 8192 服务还缺少 `MiniMaxH3AudioConditioningT8`，不能将官方 T8 DualClock 链误报为通过。

## 27. 2026-08-17 生产底座选择与 15 秒插件专项（当前结论）

完整证据：[模型与插件专项报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-lora-15s/REPORT.md)。本节更新前文“剪枝 LoRA compatibility pending”：前文记录的是通用 T8-convert LoRA 和旧节点路径；本轮使用匹配当前 ComfyUI H3 包装器的 `MiniMaxH3TurboLoRA` 与 drbaph 剪枝专用 raw-key LoRA，日志已确认真实注入。

### 27.1 选型

当前长期生产主候选是：**剪枝 INT8 FL2VA + drbaph 专用剪枝 Turbo LoRA + INT4 Qwen + 原生视频/音频 VAE + SageAttention 启动参数，640×384/24fps**。它同时满足正确 LoRA 注入、4 景别 5 秒有效出片、15 秒两套接力插件有效出片和 3060 12GB 可串行运行；画质比剪枝 INT4 更适合 3DCG，资源压力又低于非剪枝 INT8。

对照排序：

1. **主生产候选：剪枝 INT8 + 专用 drbaph LoRA**。5 秒 4 景别冷启动 305.30～320.66 秒；4 steps 适合预演，8 steps 适合正式镜头。
2. **质量上限：非剪枝 INT8 + full EMA LoRA merge**。5 秒 336.57 秒，约 32.4GB staged，适合作为关键镜头质量参考，不适合作为默认长链。
3. **速度/兼容备用：GGUF Q4_K_M + Dynamic Qwen**。5 秒 216.66 秒且可完整解码，但 direct Q4 文本编码器 OOM、full-base GGUF 链质量和长期稳定性仍属实验级。
4. **低资源备用：剪枝 INT4 + 专用 drbaph LoRA**。可运行但画面更软、更偏插画，3DCG 生产不优先。

### 27.2 Director 15 秒

剪枝 INT8 + 专用 LoRA + Director 3 段、8 steps、640×384 真实输出 15.291667 秒，367 帧，音频 15.292 秒，耗时 1082.09 秒，SHA-256 `721148db479e525a6d3e27d9e23857388ea80b0f29749874bdf8878b554f0f98`。首/中/尾抽帧的桥面远景、侧脸中景和侧脸近景稳定；但日志出现 `unexpected audio grid` 与自动 `trimmed 5f`，生产仍需接缝波形检查。

### 27.3 Motion Context 15 秒

固定 `context_length=22`、`audio_context_length=24`，4 段真实接力，耗时 `296.67/297.77/296.65/296.75s`，第 2～4 段均读取上一段 AV latent。最终成片路径为 [`pruned_int8_drbaph_motion_context_15s_640x384.mp4`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-lora-15s/pruned_int8_drbaph_motion_context_15s_640x384.mp4)，15.000000 秒、360 帧、640×384、音视频同长、完整解码通过，SHA-256 `509f5a087e75e871fb46185cc565afcf3765fb367e660cb02457e6638341db4a`。画面由背影、背部正面、侧身推进到侧脸，人物和场景连续，未见结构性面部崩坏。

### 27.4 运行边界

- Director 和独立 Motion Context 不能在同一 ComfyUI 进程叠加；切换必须重启隔离进程。
- 生产优先采用 Motion Context 做底层接力，Director 做时间轴编排和局部重跑。
- 本轮 15 秒不是正面对白特写的最终门禁；30 秒按用户要求暂缓。
- 测试服务结束后 GPU 回落约 475MiB/0%；`wechat-linux-bot` PID `967884` 与 `qwen36_router_linux.py` PID `125643` 保持运行。

## 28. 2026-08-17 《命运模型》第一章 3DCG 试拍与实时端口校正

完整证据：[第一章 H3 3DCG / Motion Context 试拍报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-destiny-ch01-pilot/REPORT.md)。本轮先完成 5 秒 T2V 开篇试拍，再用同一底座进入 15 秒独立 Motion Context 接力；30 秒按当前阶段门禁暂缓。

- 5 秒 T2V：剪枝 INT8 FL2VA + INT4 Qwen + `minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors`，使用 `MiniMaxH3TurboLoRA` 真实注入，640×384、24fps、8 steps、原生 T2VA 音频，端到端 `370.838s`，完整解码通过。
- 15 秒 Motion Context：4 段、640×384、24fps、4 steps、`context_length=22`、`audio_context_length=24`；段端到端耗时 `301.64/301.04/389.98/382.75s`，四段均读取新的 `h3_destiny_ch01_motion_context_15s_640x384/clip_000N.safetensors`，日志报告 `drift 0.00ms`。
- 精确封装文件：`test-runs/2026-08-17-h3-destiny-ch01-pilot/output/final_destiny_ch01_motion_context_15s_640x384_exact15_v3.mp4`，15.000000 秒、H.264 640×384/360 帧、AAC 32kHz 双声道、完整解码通过，SHA-256 `e9e845e2a8de53b2ff64fac107bd039d6372257f7f15265e0dcbf4a726160f64`。AAC 轨道 14.976 秒是帧边界，不是接力漂移。
- 抽帧结论：祁元思远的眼镜、短黑发、米色外套、蓝色内搭、背包和石桥结构保持；风格为风格化写实 3D CG/游戏过场。该证据尚不覆盖正面对白特写、口型、TTS/最终混音或高分辨率长链。

本轮暴露并修正一条必须保留的工作流门禁：SaveLatent 和 LoadLatent 曾短暂使用不同目录，导致第二段读取旧实验 latent；任务已中止且未纳入排名。今后长链启动前必须同时核对 context 目录、`clip_index`、分辨率和 latent 类型。

实时端口探测（2026-08-17）已校正旧文档假设：`8188` 当前是 vanilla ComfyUI（无 H3 节点），`8189` 是 `boogu_api_wrapper.py`，`8192` 是本轮 Motion Context 隔离 H3 服务。因此不能仅凭旧文档把 `8189` 当作 H3 正式端口；每次执行前必须用 `/queue`、`/object_info`、`/proc/<pid>/cmdline` 复核实际服务。微信 Bot `8077` 与 Qwen `8090` 是保护对象。

## 29. 2026-08-18 正面对白与两个插件同参数 A/B（当前专项结论）

详细证据：[H3 第一章插件公平 A/B 报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-plugin-ab/REPORT.md)。本轮固定选定底座为剪枝 INT8 FL2VA + drbaph 剪枝专用 raw-key Turbo LoRA + INT4 ConvRot Qwen + 原生视频/音频 VAE + SageAttention，640×384/24fps；首次把“正面对白”和“同为 8 steps 的 Director/Motion Context”放进同一个第一章剧情门禁。

- 5 秒正面对白门禁通过：`346.88s`，H.264/AAC，`5.166667s`，完整解码通过，SHA `727aa406799ca4307605261001464903d02ca1d9f544af0cec4eacadc91a28c2`；抽帧看到眼镜、短发、脸型、服装、背包稳定并有视觉口部运动。未做 ASR/音素级口型/最终混音。
- Director 15 秒通过媒体门禁：3 段、8 steps、`1094.20s`，输出 `15.291667s`，SHA `556979840fd458295219d633de257b94907822382de0a53ca027684d4a638ebe`；远景→中景→正面近特写的脸部结构可审阅。日志有 `unexpected audio grid` 和 5 帧自动裁切，交付前要统一裁切到目标时长。
- Motion Context 8 steps 公平 A/B 通过：四段实际包装耗时 `360.98/418.68/419.01/402.49s`，每段从唯一目录 `/mnt/gaosu_sata/ComfyUI/output/h3_destiny_ch01_motion_context_15s_640x384_8step_ab` 读取上一段 AV latent，日志每段 `drift 0.00ms`。精确成片 `15.000000s`，SHA `1902ad5a8a82b071978ed60647e9dec65c043e5920acf6056e9c6a61b17d208`，完整解码通过；边界抽帧未见硬接缝或结构性面部塌陷。

当前生产分层明确为：**Director 做时间轴编排/局部重跑，Motion Context 做底层 AV latent 接力和断点恢复**。两者不能在同一 ComfyUI 进程同时 patch；当前默认仍是独立进程/端口。LoRA 是否生效以 `qkv_proj.forward_owner=BypassForwardHook` 和专用节点注入日志为准，不能单独以 Director clone 后的 `is_injected=False` 调试属性判失败。

本轮高负载记录：H3 RSS 约 `18.1～18.8GiB`、GPU 约 `11.2/12GiB`、CPU 约 `230～246%`；第 4 段解码期间约 `28GiB/31GiB` 内存并使用约 `12GiB` swap，但最终正常完成。后续长视频评测必须把 swap、VAE 解码和 H3 队列一起观察，不能因 GPU 短时低利用率误判假死；验收后精确停止 H3，复核微信 Bot `8077` 与 Qwen `8090`。

## 30. 2026-08-18 选定底座高分辨率 LoRA 与 1088×608 长链门禁

完整证据：[高分辨率 LoRA 门禁报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-hd-lora-gate/REPORT.md)。本轮没有更换长期生产模型，而是在选定底座上验证画质档位：剪枝 INT8 FL2VA + drbaph 剪枝专用 raw-key Turbo LoRA + INT4 ConvRot Qwen + 原生视频/音频 VAE + SageAttention，24fps、8 steps。

- 832×480 单段 5 秒：`425.97s`，H.264/AAC、124 帧、完整解码通过，SHA `85768a43ddfa2f663bd33833c8381c33bde05413b172d58706ec9d91aeb25a16`；人物五官和服装稳定，细节比 640×384 清楚。
- 1088×608 单段 5 秒：`671.53s`，H.264/AAC、124 帧、完整解码通过，SHA `931e3e4a907682b7fba68a3e762b8aadc91b00b7a751181373cad433b20db612`；眼睛、鼻梁、嘴部、眼镜和衣物细节明显提升，无结构性面部崩坏。
- 1088×608 Motion Context 15 秒：四段本地耗时 `633.89/703.91/709.96/714.26s`，合计 `2762.02s`（约 46 分 02 秒）；每段均真实读取上一段 AV latent，日志均为 `drift 0.00ms`，完整解码通过。
- 精确成片：[`final_destiny_ch01_motion_context_15s_1088x608_8steps_exact15.mp4`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-hd-motion-8step-gate/output/final_destiny_ch01_motion_context_15s_1088x608_8steps_exact15.mp4)，15.000000 秒、1088×608、24fps、360 帧，SHA `9f062ecb739785827c8a95e0e55ccc6004a07a55a2bf7a8e0cd42a5a69e32cde`，完整解码通过。

生产分辨率分层：640×384 是批量默认档；832×480 是常规精选档；1088×608 是极限画质档，只用于正脸、关键道具、片头片尾等少量镜头。1088 在 32GB 内存机器上会使用约 12～13GiB swap，单段约 11 分钟，15 秒约 46 分钟；它技术上通过，但不适合作为整部剧默认分辨率。后续专项只围绕已选底座进入 Director 编排、Motion Context 接力、口型、TTS/混音和局部重跑，不再对未选模型做长视频笛卡尔积。

## 31. 2026-08-18 独立 TTS、ASR 与混音门禁

完整证据：[第一章独立音频门禁报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-audio-gate/REPORT.md)。在已选底座的 1088×608 正脸片和 15 秒 Motion Context 长链上，接入 Linux `127.0.0.1:8789` 的 CosyVoice3，完成独立对白替换、分句时移、长链混音、Whisper small ASR 和完整媒体验收。

- `刚才那里，明明有人。` 独立 TTS 为 24kHz 单声道 WAV，2.200s，HTTP 200；Whisper 核验为 `刚才那里,明明有人。`。
- 5 秒正脸片独立音频替换通过：1088×608、124 帧、5.166667s、AAC 32kHz/2ch、完整解码；分句延迟版也通过，用于对齐约 1.8～2.6s 和 3.8～4.4s 的口部张合区间。
- 15 秒 Motion Context 片独立 TTS 混音通过：精确 15.000000s、1088×608、360 帧、AAC 32kHz/2ch、完整解码。干净对白版 SHA `ef24d135f83a776a60ec223f685ec557bb6593bb1a44a49b722420d2aba7ecae`；保留 H3 原生音频床的 A/B 版 SHA `92d35dddee425d61d73e67aca70a42c90fd5b66b9f07c92b0bdce35a9cb3b4a0`。

生产音频边界已经明确：H3 原生音频只能作为可选氛围参考，不能直接承担最终对白；独立 CosyVoice3 负责角色/旁白，独立环境声和 SFX 负责氛围，FFmpeg/Remotion 负责混音与响度，Whisper/人工听审负责文字核验。本轮证明的是“独立 TTS + 时序混音”可行，不是逐音素口型已经通过；严格对白镜头仍需口型驱动或带对白重新生成。

## 32. 2026-08-17 选定底座 3DCG Director / Motion Context 公平复测

完整证据：[3DCG 15 秒公平 A/B 报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-selected-3dcg-15s/REPORT.md)。本轮用同一 3DCG 角色、桥面场景、提示词、640×384、24fps、8 steps、剪枝 INT8 FL2VA、drbaph 剪枝专用 raw-key Turbo LoRA、INT4 ConvRot Qwen、原生视频/音频 VAE、SageAttention，分别实测 Director 与独立 Motion Context；没有参考图，属于严格 T2V 插件 A/B。

- Director：3 段自动编排，端到端 `1091.79s`（约 18 分 12 秒）；raw 15.291667 秒，精确交付版 15.000000 秒，360 帧，SHA-256 `878860f0533d0e091d64992317682a648afdac5e13e924af11702f14d509e072`，完整解码通过。远/中景稳定并产生可审阅正脸近景；出现未在提示词声明的眼镜漂移；日志有 `unexpected audio grid` 和自动裁剪 5 帧。
- Motion Context：4 段真实 AV latent 接力，段耗时 `330.64/411.66/421.72/422.01s`，合计 `1586.03s`（约 26 分 26 秒）；后续段均读取上一段 latent，日志为 `drift 0.00ms`。精确交付版 15.000000 秒，SHA-256 `f39687400bf51064733f5de90ffb0e1bf3f10b65a35bedbcce17cb68d642ca6c`，完整解码通过；画面连续性较好但景别未按提示推进到正面，末帧出现中英字幕样文字伪影。

本轮生产分层落定：**Director 是 3DCG 对白/表情/景别编排的默认入口；Motion Context 是底层 AV 接力、断点恢复与故障回退链。** 两者仍必须独立端口运行，不能在同一 ComfyUI 进程叠加 patch。H3 视觉提示词不再写长段对白，统一采用“静默表演、无可读文字/字幕”，角色对白改由 CosyVoice3 + 独立环境声/SFX + FFmpeg 后期完成；本轮 T2V 不能替代三视图/参考图门禁。

下一专项只做已选底座的生产级参考链：三视图/面部参考图 + 无对白视觉提示词 + Director 首段编排，再用 Motion Context 承接后续段，最后接入独立 TTS/ASR/混音。暂不继续对未选模型做长视频笛卡尔积。

## 34. 2026-08-17 平台 Provider 真实 T2V 入口验收

证据：[H3 选定底座 Provider T2V 真实验收](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-provider-t2v-smoke/REPORT.md)。项目平台的 `ComfyUIVideoProvider` 已经在隔离 `8192` 真实提交并下载 H3 T2V 结果：640×384、124 帧、24fps、8 steps、原生 T2VA 音频，端到端 `316.132s`，输出 `5.167000s`，H.264/AAC 完整解码通过。队列中实际替换了业务提示词，日志确认剪枝 INT8 + drbaph LoRA 真实注入和 `lora ACTIVE`，不是仅结构 dry-run。

本次只验证平台任务入口，没有把 Director 和 Motion Context 同时放进同一进程；两个插件继续按既有隔离实测使用。临时 `8192` 已在验收后停止，正式 `8188/8189`、微信 Bot `8077`、Qwen `8090`、CosyVoice3 `8789` 均保持监听。

## 33. 2026-08-17 三视图 R2V + Director / Motion Context + TTS 15 秒专项

完整证据：[三视图参考链专项报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-reference-audio-gate/REPORT.md)。本轮在同一选定底座（剪枝 INT8 FL2VA、INT4 ConvRot Qwen、drbaph 剪枝专用 raw-key Turbo LoRA、原生双 VAE、SageAttention、640×384、24fps、8 steps）上进行公平参考链测试。

- Director 5 秒：`572.21s`，5.166667 秒、124 帧、完整解码通过；首段人物身份和场景稳定。
- Director 15 秒：`1400.49s`，精确版 15 秒、360 帧、完整解码通过；前 10 秒可用，但约 12.5 秒出现三视图参考板泄漏。原因是每段重复注入四张参考图并推进到近景，不是 INT8 或显存故障。
- Motion Context：首段四图 R2V，后续只传 AV latent；四段耗时 `547.04/439.72/458.54/464.34s`，合计 `1909.64s`（约 31 分 50 秒）。后 3 段均 102 帧、4.2500 秒、`drift 0.00ms`，四个 latent 均成功保存。
- Motion Context 精确片：[`selected_reference_motion_context_15s_exact.mp4`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-reference-audio-gate/motion-context/selected_reference_motion_context_15s_exact.mp4)，15 秒、640×384、360 帧，SHA `35678c1f303010c80510d122a30c7a5a956dd91ef0aecd8abf42af3c0439849a`，完整解码通过；远中近景连续、无参考板泄漏、近景五官稳定性优于本轮 Director。
- CosyVoice3 `127.0.0.1:8789` 已生成 4.4 秒中文对白，并完成保留氛围床/纯对白两版 15 秒封装；当前 8790 ASR 服务和本地 Whisper small 权重缺失，ASR 本轮明确记为待补门禁。

生产默认链更新为：Director 做上层分段/提示词编排；首段 R2V 使用三视图/面部包；Motion Context 后续段只传递 AV latent；H3 视觉提示词保持静默、无字幕、无可读文字；CosyVoice3 负责对白，独立环境声/SFX + FFmpeg/Remotion 负责最终音频。640×384/8 steps 是本机当前长视频候选档，832×480/1088×608 仍只用于精选镜头。

## 35. 2026-08-17 平台可恢复长视频编排合同

项目平台新增 `POST /api/episodes/{episode_id}/long-video` 与对应状态查询。它
把 Director 与 Motion Context 固定为生产链的两个层次：第 1 段必须是
`director`，第 2 段起必须是 `motion-context`；每段完成即把状态、prompt、输出
媒体、SHA/ffprobe 证据和 latent 引用写入 `jobs.payload_json`。Worker 中断后
重新执行会跳过已完成段，失败段可以通过现有 retry 重新排队，避免整条长片从
头抽卡。

节点类型门禁：Motion Context 的 `context_length` 必须提交为枚举字符串（例如
`"22"`），`audio_context_length` 与 `clip_index` 才提交为整数。平台 Provider
按官方节点输入 schema 分开替换；类型校验失败时应在 `/prompt` 阶段阻断，不得
把无输出的片段当成成功。

新增配置：

```dotenv
COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW=/path/to/h3-director-first-segment-av-latent-640x384-8steps.json
COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW=/path/to/h3-motion-context-segment-api.json
```

两份 workflow 仍必须挂在隔离的 H3 ComfyUI 进程，并分别通过
`/object_info`、实际提交、latent 文件检查、MP4/ffprobe/完整解码验收；平台的
local preview 单测只证明状态恢复和媒体合同，不代表 GPU 画质或真实插件节点已经
接通。未配置两份长视频 workflow 时，单段 T2V/R2V 保持可用，长视频任务会
明确失败，不会隐式退化为独立 5 秒视频拼接。

首段不能只保存 Director 节点的已解码视频。当前平台首段模板使用核心
`MiniMaxH3ImageToVideo`、采样器和 `MiniMaxH3MotionContextSaveLatent`，由
Director 计划层提供段落提示词；只有原始 AV latent 文件经过存在性和 clip index
检查后，后续 Motion Context 段才允许提交。

## 2026-08-17 平台长视频真实接力 v3

报告：`test-runs/2026-08-17-h3-platform-long-video-15s-real-gpu-v3/REPORT.md`。

真实平台 Job `job_aa1f1a37bcf54f1f` 已完成 4 段、端到端 `1587.236s`，后 3 段均成功加载上一段 AV latent，并分别保存 `clip_00002`、`clip_00003`、`clip_00004`。最终文件为 640×384/24fps/H.264/AAC、精确 15 秒，完整解码通过，SHA-256 `fd34134156479415c4b3f32fdb5881c59b6acd10207d0ea8a23a35cfb0e2df48`。

正确路径契约是：SaveLatent 写入 `.../context/clip_NNNNN.safetensors`；LoadLatent 使用 `.../context` 目录并传 `clip_index=N`。`context_length` 仍必须是节点枚举字符串，例如 `"22"`。若出现 `.../context/clip is neither a file nor a folder`，应判定为 Provider 路径适配错误，不能判定模型或 LoRA 失败。v1/v2 错误已在报告中保留为反例。

## 36. 2026-08-17 平台多参考图 R2V + Motion Context 15 秒生产门禁

完整证据：[平台三视图/面部参考专项报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/REPORT.md)。本轮补齐了平台此前只有单一 `source_asset_id` 的缺口，新增 `reference_asset_ids` 列表合同，首段由 `ComfyUIVideoProvider` 将正面、侧面、背面和面部特写四张 ready image 逐张上传到 `MiniMaxH3ReferenceToVideo`；后续段只读取上一段 AV latent，不重复上传参考板。

- 固定选定底座：剪枝 INT8 FL2VA + INT4 Qwen + drbaph 剪枝专用 raw-key Turbo LoRA + 原生双 VAE + `--lowvram --fp16-vae --use-sage-attention --reserve-vram 1`，640×384、24fps、8 steps、Euler/simple、`context_length=22`、`audio_context_length=24`。
- 平台 Job `job_d00225c5f1024939` 四段全部完成：首段 R2V/Director `518.354s`，后续 Motion Context `433.734/415.910/416.474s`；墙钟 `1784.481s`（约29分44秒）。
- 最终成片：[job_d00225c5f1024939.final.mp4](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/assets/job_d00225c5f1024939.final.mp4)，精确 `15.000000s`、640×384、24fps、359帧、H.264/AAC、完整解码通过，SHA-256 `28512e3f48417524ac482ec15016c50c30bcf188207a1919a995249eba472543`。
- 真实日志同时确认 LoRA 注入：`208 backbone modules / 158 bypass adapters / 1 injections / 50 int8 fc2 via merge`，并出现 `BypassForwardHook => lora ACTIVE`。后续段逐段 `loaded AV latent`，音画裁切 `drift 0.01ms`。
- 抽帧确认四张身份参考没有白底参考板泄漏，远景背面、中景、面部近景和剑鞘近景均可审阅；但本轮不等于逐音素口型通过，最终对白仍应走 CosyVoice3 + 独立环境声/SFX + FFmpeg/Remotion。

平台实现的新门禁：workflow 若包含 `REFERENCE_IMAGE_n_REF`，Provider 必须验证并上传对应数量的 ready image；后续段不得重复注入参考板。相关代码、模板和复现脚本见专项报告第 8 节。该门禁已通过 156 项相关回归测试和一次真实 GPU Job。

## 37. 2026-08-17 平台多参考图长链独立 TTS / 混音门禁

在平台 Job `job_d00225c5f1024939` 的真实 15 秒最终片上，复用 Linux 常驻 CosyVoice3 `127.0.0.1:8789`（`Fun-CosyVoice3-0.5B`）生成独立中文对白：`这座桥，为什么还在呼吸……`。没有启动、停止或重新加载微信 Bot、Qwen、8188/8189 或 CosyVoice3。

- WAV：`test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/audio/guofeng_reference_r2v_monologue.wav`，24kHz/单声道/2.880s，HTTP 200，ffprobe 和 SHA 已落盘。
- 混音 A：`audio/h3_reference_r2v_15s_tts_mix.mp4`，保留 H3 原生音频床并降到约 0.22 线性音量，TTS 延迟 8.5s 混入；精确 15.000s，640×384，完整解码通过，SHA `5b8c1f8b9473dc7b698ddc551029d442ccdb4c1ecb110f4c7bba063d6d31d0f8`。
- 纯对白 B：`audio/h3_reference_r2v_15s_tts_voice_only.mp4`，去除 H3 原生音频，只保留同一条延迟对白；精确 15.000s，完整解码通过，SHA `7354ce5931ed81db65f94eb1d24952a9368ccc1f9fa6bd4bfcd7a866fed33530`。

这条门禁证明“平台多参考图 R2V → Motion Context AV latent 接力 → 独立 TTS → 音频床 A/B 混音 → 精确封装”已经跑通。`8790` SenseVoice 未运行、Whisper small 权重未缓存，因此文字级 ASR 和逐音素口型不能写成已通过；严格对白镜头仍要走口型驱动或带对白重采样。下一步按专项顺序做人物一致性局部重跑与失败恢复。

## 38. 2026-08-17 局部重跑与失败恢复门禁

服务层回归用例 `tests.test_long_video.LongVideoPlanTests.test_failed_context_segment_retries_without_regenerating_completed_segment` 已通过：第 1 段完成，第 2 段首次模拟 Motion Context 中断，retry 后调用序列为 `[1, 2, 2]`，证明只重跑失败段，不重采样已完成段。平台真实 GPU Job `job_d00225c5f1024939` 的四段仍保持 `completed`，每段均引用同一 `long-video/job_d00225c5f1024939/context` 目录，最终 15 秒片可复核。

恢复边界固定为：先保留失败段日志、GPU/RAM/swap 采样、workflow、clip index 和 latent 文件，再只重跑失败段；若上一段 latent 缺失或分辨率/clip index 不一致，不得盲目续跑，必须进入人工修复。证据：`test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/recovery/RECOVERY_EVIDENCE.json`。

## 39. 2026-08-18 ClipProj / EasyCache 低显存专项封存

本轮新增证据：`test-runs/2026-08-18-h3-clipproj-easycache-gate/REPORT.md`、`DOWNLOAD_MANIFEST.md`、`docs/H3_PRODUCTION_CYCLE_REVIEW_2026-08-18.md`。

在 RTX 3060 12GB、32GB RAM 上，剪枝 INT8 FL2VA + 剪枝兼容 raw-key Turbo LoRA + SageAttention + 640×384/24fps/8 steps 的原始 Qwen3-VL-32B INT4 基线为 `383.93s`；替换为 Qwen3-VL-4B FP8 + ClipProj v3.1 ridge 为 `151.98s`，MLP 为 `204.72s`。MLP 日志 `cos_test 0.8116` 高于 ridge `0.6874`，抽帧的脸、眼镜、服装和桥体更稳，故低显存生产候选更新为 `4B FP8 + v3.1 MLP`，ridge 仅作预演。

EasyCache 做了真实 20 steps A/B：文章阈值 `0.05/0.15/0.80` 跳过 `0/20`；阈值 `0.20/0.15/0.80` 跳过 `2/20`，墙钟 `261.88s`、日志 `1.11×`。本机 H3 不采纳论文 2～3×作为预算，EasyCache 只保留为按场景回退的实验项。SolAttn 代码已落盘但未在 3060 启用。

边界：本轮是 5 秒短片门禁，不等于 15/30 秒新底座长链已通过；Director + Motion Context 仍使用前一轮已验证的生产链。8B 资产即使下载完成也只标为备用，未经真实采样不得写入“已验证”。

## 40. 2026-08-19 ClipProj 4B/8B 候选与 15 秒长链复测

完整报告：[H3 ClipProj 4B/8B 候选与 15 秒长链复测](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-19-h3-clipproj-8b-long-gate/REPORT.md)。本轮已经把 8B 从“已下载备用”提升为“短片和一条 15 秒长链真实通过”，但仍不替代低压默认档。

- 4B FP8 + v3.1 MLP：5 秒 `204.72s`，作为低显存/批量默认候选；4B EasyCache 真实 A/B 在阈值 0.05 跳过 `0/20`，阈值 0.20 跳过 `1/20`，不按论文速度外推。
- 8B NVFP4 ridge/MLP：5 秒分别 `172.17/171.41s`；NVFP4 MLP 本轮第二抽帧出现未请求的字幕样文字，不能因为更小就判定更优。
- 8B FP8 ridge/MLP：5 秒分别 `176.37/166.80s`；MLP 是本轮速度和抽帧质量最平衡的候选，进入长链。
- 8B FP8 + v3.1 MLP + 剪枝 INT8 H3 + 匹配 Turbo LoRA + SageAttention 的 Director 首段 / Motion Context 三段接力真实通过；Job `job_48f8ca68bde948da`，采样合计 `723.01s`，平台墙钟约 `728.93s`，最终 `640×384/24fps/15.000s`，SHA-256 `d819998f78d158f3a5db539449cfcd4191c32a9e3832c6ce003ce2d7adf3aeca`。
- 四段均保存 AV latent，最终 MP4 和四个片段完整解码；最终音频 `mean -15.1 dB / max -2.7 dB`。抽帧显示远景背面→中景→侧脸近景→持剑近景连续，未见本轮明显脸部崩塌或参考板泄漏。
- 8B FP8 运行峰值：文本编码器约 `10097.97MB`，主机可用内存最低约 `2.1GB`，zram 约 `12GB`；因此它是质量/速度优先的高压档，禁止和其他大模型并行驻留。

第一次 15 秒提交失败的根因是隔离启动白名单漏放行 `ComfyUI-H3-Motion-Context` 与 `ComfyUI_MiniMaxH3_Director_AIMixer`，导致第二段报 `MiniMaxH3MotionContextSaveLatent not found`。修复白名单后 `/object_info` 验证节点注册，重试全部完成；该失败必须归类为节点注册/部署错误，不得误判成 OOM 或模型画质失败。
