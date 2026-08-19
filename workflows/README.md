# ComfyUI workflow 目录

此目录只放部署方提供的 ComfyUI API-format workflow JSON，不提交模型权重、API key 或私有配置。

`ci-image.json` 和 `ci-video.json` 是仓库内受控的 CI mock workflow，只供
`infra/docker-compose.ci.yml` 的生产拓扑集成 smoke 使用；它们不代表任何真实
checkpoint、采样器或 Wan/LTX 生产工作流，也不能直接用于生产 GPU。

API-format 不是 ComfyUI 编辑器保存的普通画布 JSON。每个顶层键必须是节点 ID，
值必须至少包含 `class_type` 和 `inputs`；节点引用使用 `["node_id", output_index]`。
生产启动的 `config-check`、`provider_smoke.py` 和视频 benchmark 会先执行同一结构
校验，只输出固定错误代码，不输出 workflow 内容或宿主机路径。生产角色还会执行输入
语义校验：图片 workflow 必须消费 `{{PROMPT}}`，视频 workflow 必须消费
`{{ASSET_ID}}`、`{{SHOT_ID}}` 或 `{{CLIENT_ID}}` 之一；未支持的 `{{...}}` 占位符会被
拒绝，避免静默透传。它们不会猜测模型 checkpoint、采样器或 Wan/LTX 自定义节点，
这些仍需在目标 ComfyUI/GPU 上实测。

建议在 ComfyUI 前端使用“Save (API Format)”导出后再放入此目录，并先执行：

```bash
./.venv/bin/python scripts/provider_smoke.py --kind image --base-url http://127.0.0.1:8188 --workflow /path/to/image-api.json
./.venv/bin/python scripts/benchmark_video_provider.py --label wan22 --base-url http://127.0.0.1:8188 --workflow /path/to/wan22-api.json
```

上述命令默认不联网、不提交 GPU 任务；只有显式加入 `--run` 才会调用 ComfyUI。

Compose 会将它只读挂载到 API 容器的 `/app/workflows`。例如：

```dotenv
STUDIO_IMAGE_PROVIDER=comfyui
STUDIO_VIDEO_PROVIDER=comfyui
COMFYUI_BASE_URL=http://comfyui:8188
COMFYUI_IMAGE_WORKFLOW=/app/workflows/image.json
COMFYUI_VIDEO_WORKFLOW=/app/workflows/wan22.json
```

workflow 中可使用 Provider 支持的占位符：图片为 `{{PROMPT}}`、`{{NEGATIVE_PROMPT}}`、`{{CLIENT_ID}}`；视频为 `{{PROMPT}}`、`{{NEGATIVE_PROMPT}}`、`{{ASSET_ID}}`、`{{SHOT_ID}}`、`{{CLIENT_ID}}`。文生视频 workflow 不应放入图像占位符，只需消费 `{{PROMPT}}` 和一个身份占位符；图生视频 workflow 应在 `LoadImage` 等节点使用 `{{IMAGE_REF}}`（推荐，包含 ComfyUI 返回的 subfolder/name），也可分别使用 `{{IMAGE_FILENAME}}`、`{{IMAGE_SUBFOLDER}}`、`{{IMAGE_TYPE}}`。API 会先把采用的关键帧上传到 ComfyUI `/upload/image`，不会把 API 容器路径传给 ComfyUI；benchmark 则通过 `--source-image` 提供关键帧。

MiniMax H3 的项目级生产配置见 [`h3-production/README.md`](./h3-production/README.md)。它记录当前 RTX 3060/32GB 已验证的 pruned INT8 + 专用 drbaph LoRA 底座、T2V/R2V 工作流入口和 Director/Motion Context 长视频参数；模型权重和真实 ComfyUI workflow 仍只保存在 Linux 部署机，不提交到仓库。

提交前先用 `scripts/benchmark_video_provider.py` 做 dry-run，确认 JSON、占位符和输出节点合同；显式 `--run` 时，图生视频 workflow 必须同时提供 `--source-image`。
