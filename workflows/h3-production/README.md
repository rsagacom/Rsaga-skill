# MiniMax H3 项目级生产配置

本目录是 `novel-to-comic-engine` 的 H3 Provider 合同与生产 profile，不是模型权重包。真实权重、LoRA、VAE 和 ComfyUI API-format workflow 仍落在 Linux `cachyos-ai`，项目只保存可复用的角色、参数和验收边界。

本目录现在包含两份可挂载到平台 ComfyUI Provider 的 API-format 工作流：

- `h3-t2v-640x384-8steps.json`：文生视频，使用 `{{PROMPT}}` 和 `{{ASSET_ID}}`，不需要首帧。
- `h3-r2v-640x384-8steps.json`：单参考图生视频，使用 `{{IMAGE_REF}}`、`{{PROMPT}}` 和 `{{ASSET_ID}}`。
- `h3-director-first-segment-av-latent-640x384-8steps.json`：Director 计划的首段
  T2VA 采样器，额外保存可被 Motion Context 读取的 AV latent；不能把已解码的
  Director 视频输出冒充成可继续采样的 latent。
- `production-profile.json`：模型、LoRA、分辨率、长视频分工和音频门禁的机器可读 profile。

长视频平台入口为 `POST /api/episodes/{episode_id}/long-video`。它接受
Director 已拆好的 `segments`，第 1 段固定使用 `director`，第 2 段起固定使用
`motion-context`；Job 会逐段持久化状态，失败重试时跳过已经完成的片段。
需要额外挂载两份已经在隔离 H3 进程验证过的 API-format workflow：

```dotenv
# 可选；配置后才能启用平台长视频 Job
COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW=/path/to/h3-director-first-segment-av-latent-640x384-8steps.json
COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW=/path/to/h3-motion-context-segment-api.json
```

第一份 workflow 必须能接收 `{{SEGMENT_PROMPT}}`（或 `{{PROMPT}}`）并输出视频；
第二份必须接收 `{{CONTEXT_LATENT_PATH}}`、`{{CONTEXT_CLIP_INDEX}}`，并保存
上一段 AV latent。平台不把任意客户端路径透传给 ComfyUI，latent 目录由 Job
按 `long-video/<job-id>/context/clip` 生成。两份 workflow 不在同一个 ComfyUI
进程叠加运行时 patch；实际节点兼容性仍必须以隔离 GPU 运行和输出文件检查为准。

模板已通过本地 workflow 结构合同、dry-run 和一次真实平台 Provider Job；真实证据见 `test-runs/2026-08-17-h3-provider-t2v-smoke/REPORT.md`。Director/Motion Context 的长视频专项沿用各自隔离进程和已有 15 秒实测证据，不能将本次单段 Provider smoke 误写成两插件长链验收。

## 当前默认底座

| 组件 | 当前生产选择 |
|---|---|
| 扩散底座 | pruned INT8 FL2VA，实际文件名 `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| 文本编码器 | INT4 Qwen H3 文本编码器 |
| LoRA | drbaph 专用 pruned Turbo LoRA raw-key adapter；不要与非剪枝 T8-convert 混挂 |
| VAE | 原生视频 VAE + 音频 VAE |
| 加速 | ComfyUI `--lowvram`、FP16 VAE、SageAttention/KJ；以启动日志和采样日志为准 |
| 平衡参数 | 640×384、24fps、4 steps 预演 / 8 steps 生产候选、T2VA 音频 |
| 默认 Provider | ComfyUI `ComfyUIVideoProvider` |

以上是 2026-08-17 在 RTX 3060 12GB + 32GB RAM 上完成 5 秒四景别与 15 秒长视频接力后的默认档。更高分辨率或 20 steps 只能对人工通过镜头专项复现，不能当作批量默认。

## Provider 接口

平台现在同时保留两条入口：

```text
POST /api/assets/{asset_id}/video
  已采用且通过视觉审核的关键帧 -> 图生视频（I2V/R2V 工作流）

POST /api/shots/{shot_id}/text-video
  {"prompt": "..."} -> 文生视频（T2V 工作流，不要求首帧）
```

两条入口都进入同一个 `video` Job、积分账本、重试/退款、媒体落盘和 ComfyUI 轮询合同。T2V 资产的 `metadata.generation_mode` 为 `t2v`，并保存脱敏后的提示词；它的 `source_asset_id` 保持为空，避免把文生视频误报成图生视频。

ComfyUI 视频 workflow 需要：

- 至少消费一个 `{{ASSET_ID}}`、`{{SHOT_ID}}` 或 `{{CLIENT_ID}}`；
- T2V workflow 消费 `{{PROMPT}}`，不包含 `{{IMAGE_REF}}`；
- I2V/R2V workflow 消费 `{{IMAGE_REF}}` 或其拆分字段；Provider 会先上传关键帧；
- 输出节点必须能被 Provider 识别为 `videos`、`gifs` 或 `images`，并通过 `ffprobe` 完整解码。

Provider 对旧的第三方实现保持兼容：如果某个视频 Provider 没有声明 `prompt` 参数，服务不会强行传入该关键字；H3 ComfyUI Provider 会把 T2V/R2V 提示词替换到 `{{PROMPT}}`。

## 可复用单段 runner

`scripts/run_h3_production_job.py` 是 Director/Motion Context 之外的执行门：它只负责一个视频段，先检查 `/system_stats`、`/queue`、`/object_info` 和 workflow 合同，再调用同一个 `ComfyUIVideoProvider`。完成后写入 `runner-state.json`，并保存真实 Provider metadata、ffprobe、完整解码和 SHA-256；`--resume` 会对已有 MP4 做同样的 QA，合格就跳过重复采样。

示例：

```bash
python3 scripts/run_h3_production_job.py \
  --base-url http://192.168.1.6:<verified-h3-port> \
  --workflow workflows/h3-production/h3-t2v-640x384-8steps.json \
  --output-dir test-runs/<run-id>/segment-0001 \
  --asset-id <asset-id> \
  --shot-id <shot-id> \
  --prompt '静默表演、无字幕、无可读文字的 3DCG 镜头' \
  --resume
```

它不是长视频拼接器：Director 仍决定段落和时间轴，Motion Context 仍负责上一段 AV latent 的接力；runner 负责把每一段的状态和媒体验收固定下来。

## 长视频生产参数

```text
Director：默认时间轴编排、分段、自动清理和局部重跑
Motion Context：底层 video latent + audio context 接力；Director 故障回退/手工精修
context_length：22
audio_context_length：24 或作者工作流的 40 audio steps
帧率：24fps
固定分辨率：全链路一致，宽高为 32 的倍数
拼接：优先连续上下文；独立 5 秒 concat 只作为硬切剪辑备选
```

Director 与独立 Motion Context 不要在同一 ComfyUI 进程叠加运行时补丁。长任务必须单段串行、任务前后 `/free`、核对 GPU/队列/RAM/swap，并保存每段 workflow、日志、耗时、MP4、ffprobe 和 SHA-256。

## Linux 部署映射

项目服务端只需要通过环境变量指向 Linux ComfyUI：

```dotenv
STUDIO_VIDEO_PROVIDER=comfyui
COMFYUI_BASE_URL=http://192.168.1.6:<verified-h3-port>
# 兼容旧配置：只填一个时，所有视频任务复用该工作流
COMFYUI_VIDEO_WORKFLOW=/path/to/fallback-video-api.json
# 推荐：按是否存在参考图自动路由
COMFYUI_T2V_VIDEO_WORKFLOW=/path/to/h3-t2v-640x384-8steps.json
COMFYUI_R2V_VIDEO_WORKFLOW=/path/to/h3-r2v-640x384-8steps.json
# 长视频 Director → Motion Context 链（可选）
   COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW=/path/to/h3-director-first-segment-av-latent-640x384-8steps.json
COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW=/path/to/h3-motion-context-segment-api.json
```

当 `source_image_path` 为空时，Provider 选择 `COMFYUI_T2V_VIDEO_WORKFLOW`；存在参考图时选择 `COMFYUI_R2V_VIDEO_WORKFLOW`。两条工作流共享同一选定底座和 LoRA，但不把 R2V 结果误写成 T2V。若只设置 `COMFYUI_VIDEO_WORKFLOW`，保留旧版单工作流行为。

当前主机的实时端口不能从旧配置静态推断：2026-08-17 探测到 `8188` 是 vanilla ComfyUI、`8189` 是 Boogu API wrapper、`8192` 是 Motion Context 隔离 H3。正式接入前必须用 `/queue`、`/object_info` 和 `/proc/<pid>/cmdline` 确认 `<verified-h3-port>`，再用 `scripts/benchmark_video_provider.py` dry-run 校验 API-format workflow；真实 `--run` 只在确认当前 H3 队列为空、微信 Bot/Qwen 进程未受影响、显存可用后执行。

## 选型结论

生产候选顺序固定为：

1. pruned INT8 + drbaph 专用 pruned Turbo LoRA：当前 3060/32GB 的速度/画质/内存平衡首选；
2. 非剪枝 INT8 + 兼容 T8-convert：质量上限和对照基线，长批量成本高；
3. Q4_K_M 或 pruned INT4：低资源回退，画面更软，必须单独复测，不得与上面 LoRA 混用。

最终能否进入长期生产，不由单个 5 秒视频决定：至少要通过远/中/近/特写、8 steps、15 秒 Director/Motion Context、原生音频完整解码、角色/动作/接缝人工审阅，再进入小说第一章的镜头批量生产。

### 平台长视频真实通过样本

`test-runs/2026-08-17-h3-platform-long-video-15s-real-gpu-v3/REPORT.md` 是当前平台合同的真实 GPU 样本：4 段、15 秒精确交付、首段 AV latent 保存、后三段 Motion Context 读取上一段目录并递增 `clip_index`。LoadLatent 的 `latent_path` 必须是 `.../context` 目录，不是 `.../context/clip` 前缀；后者会在节点门禁阶段失败。
