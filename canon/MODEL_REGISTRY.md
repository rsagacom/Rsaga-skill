# MODEL_REGISTRY — 模型与工作流登记

> 注册规则：只登记**真实加载并完整解码验证过**的模型与工作流。
> 每条必须引用 test-runs 证据目录；未经真实采样只写明"仅落盘，未验证"。
> 事实源以 H3 交接手册（docs/H3_PRODUCTION_HANDOFF.md）为准。

registry_version: 1.0
last_updated: 2026-08-18

## 生产主链（已收口）

| 组件 | 路径/标识 | 状态 | 证据 |
|---|---|---|---|
| 扩散底座 | `minimax_h3_fl2va_pruned_int8_convrot.safetensors`（剪枝 INT8 FL2VA） | **默认生产** | `test-runs/2026-08-17-h3-pruned-lora-15s/REPORT.md` |
| Tokens 编码器 | `qwen3vl_32b_minimax_h3_int4_convrot.safetensors`（INT4 ConvRot Qwen） | **默认生产** | 同主链报告 |
| 低显存编码器 | Qwen3-VL-4B FP8 + ClipProj v3.1 MLP（`cos_test 0.8116`） | 低压批量候选 | `test-runs/2026-08-18-h3-clipproj-easycache-gate/REPORT.md` |
| LoRA | `minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors`（drbaph 剪枝专用 raw-key） | **默认生产**，仅配剪枝 INT8 底座 | 同主链报告 |
| 视频 VAE | `minimax_h3_video_vae_fp16.safetensors` | 生产 | `H3_PRODUCTION_HANDOFF.md` §3.1 |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | 生产 | 同上 |

生产分层不变式：

- `640×384 / 24fps / 8 steps` 批量默认档；`832×480` 精选档；`1088×608` 极限档（约 12–13GiB swap）。
- Director 负责上层编排/局部重跑；Motion Context 负责底层 AV latent 接力；**不在同一 ComfyUI 进程叠加 patch**。
- 4 steps 仅预演；20 steps 仅已通过人工评审的关键镜头复核。
- **不再对未选模型运行 Director/Motion Context 长视频笛卡尔积。**

### 已验证 LoRA 兼容矩阵

| LoRA | 底座 | 结论 | 证据 |
|---|---|---|---|
| drbaph raw-key（专用） | 剪枝 INT8 | 完整注入：`lora ACTIVE`、208 backbone、158 bypass | `2026-08-17-h3-pruned-lora-15s` |
| T8-convert | 非剪枝 INT8 | 有效加速基线 | `2026-08-13-h3-hd-matrix` |
| T8-convert | 剪枝底座 | **不兼容**（`adaln_proj` reshape 跳过）；partial-injection-only | `2026-08-17-h3-pruned-lora-repass` |

## 备用/封存底座（不进长链）

| 底座 | 结论 | 证据 |
|---|---|---|
| 非剪枝 INT8 + full EMA/T8 LoRA | 质量参考上限；约 32GB staged，不能批量默认 | `2026-08-14-h3-lora-ab` |
| 剪枝 INT4 + drbaph LoRA | 画面偏软，极低资源备用 | `2026-08-17-h3-pruned-turbo-smoke` |
| GGUF Q4_K_M | 兼容救援档：T2V/R2V 可解码但约 42–45 分钟/5s | `2026-08-17-h3-gguf-q4-baseline` |
| DiffSynth NF4 | 512×288/5s 独立通过；不进 ComfyUI 生产链 | `2026-08-16-h3-nf4-diffsynth` |

## 工作流登记（workflows/h3-production/）

| 工作流文件 | role | 状态 |
|---|---|---|
| `h3-t2v-640x384-8steps.json` | T2V 单段 | 真实 GPU 通过（`2026-08-17-h3-provider-t2v-smoke`） |
| `h3-r2v-640x384-8steps.json` | R2V 角色建立段 | 平台多参考图门禁通过（`2026-08-17-h3-platform-reference-long-video-15s-real-gpu`） |
| `h3-director-first-segment-av-latent-640x384-8steps.json` | long_video_first | 平台真实接力 v3 通过 |
| `h3-motion-context-segment-640x384-8steps.json` | long_video_context | 平台真实接力 v3 通过（drift 0.00ms） |
| `h3-director-first-segment-640x384-8steps.json` | Director 首段 | 15s Director 通过 |
| `h3-reference-first-segment-av-latent-640x384-8steps.json` | 多参考图首段 | 四图 R2V 通过 |
| `h3-motion-context-segment-640x384-8steps.json` | Motion Context 段 | 15s 通过 |

## 音频链

| 组件 | 结论 |
|---|---|
| CosyVoice3（`127.0.0.1:8789`，Fun-CosyVoice3-0.5B） | 独立对白已真实通过；最终混音走 FFmpeg/Remotion |
| H3 原生 T2VA 音频 | 仅氛围参考/音画同步参考，不承担最终对白 |
| Whisper small / ASR | **未通过**：权重缺失，P1 补门禁 |

## 未验证（仅落盘）

- Qwen3-VL-8B FP8/NVFP4 weight 文件已下载，`2026-08-19-h3-clipproj-8b-long-gate` 已把 8B FP8+MLP 提为质优候选；其余 8B 组合标备用。
- SolAttn 代码已落盘，3060 未启用。