# Project Agent Entry Point

本项目涉及 MiniMax H3 本地视频生成时，任何 AI 框架或 Agent 先读取：

1. [`docs/H3_PRODUCTION_HANDOFF.md`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/docs/H3_PRODUCTION_HANDOFF.md)
2. [`docs/AI_MANHUA_STUDIO_BLUEPRINT.md`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/docs/AI_MANHUA_STUDIO_BLUEPRINT.md)
3. [`docs/H3_PRODUCTION_SELECTION.md`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/docs/H3_PRODUCTION_SELECTION.md)
4. [`test-runs/2026-08-13-h3-hd-matrix/review_manifest.json`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix/review_manifest.json)

关键事实：RTX 3060 Laptop GPU 12GB + 32GB RAM；生产默认优先 Director，Motion Context 作为底层接力/故障回退；两者不要在同一 ComfyUI 进程叠加运行时补丁。微信 Bot、Qwen 路由、微信 GUI、Clash/VPN 是保护对象，H3 测试不得停止或清理它们。

实时端口必须先探测，不能把历史端口当当前事实：2026-08-17 实测 `8188` 为 vanilla ComfyUI，`8189` 为 Boogu API wrapper，`8192` 为 Motion Context 隔离 H3。执行前检查 `/queue`、`/object_info` 和 `/proc/<pid>/cmdline`，再选择已验证的 H3 端口。

当前本地评测页：`http://127.0.0.1:3117/h3-evaluation`。任何新测试都必须固定底座、LoRA、采样器、seed、分辨率和时长，并留下工作流、视频、抽帧、耗时、显存、完整解码和 SHA-256 证据。
