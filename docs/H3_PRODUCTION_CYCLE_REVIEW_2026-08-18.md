# MiniMax H3 本轮生产评测复盘

日期：2026-08-18（Asia/Shanghai）

## 结论先行

本轮新增的 ClipProj 方案已经在 RTX 3060 12GB、32GB RAM 上完成真实 H3 采样，最值得保留的是：

`剪枝 INT8 FL2VA + Qwen3-VL-4B FP8 + ClipProj v3.1 MLP + 剪枝兼容 Turbo LoRA + SageAttention + 8 steps`

它相对原 Qwen3-VL-32B INT4 的同条件 5 秒基线，把墙钟从 `383.93s` 降到 `204.72s`；4B ridge 预演档进一步降到 `151.98s`，但 MLP 的条件映射质量和抽帧稳定性更好。EasyCache 在本机 H3 20 steps 上不是文章宣称的自动 2～3×：阈值 `0.05` 跳过 `0/20`，阈值 `0.20` 跳过 `2/20`，实测只报 `1.11×`。因此本轮将 MLP 定为低显存生产候选，EasyCache 定为可回退的场景级实验项，SolAttn 只落盘不启用。

本轮至此封存，不继续跑 8B、15 秒新底座或长视频笛卡尔积。8B 资产已经完成落盘和哈希核验，但不把“下载完成”写成“已测试”。

## 一、原项目的生产认识

### 1. H3 本身和周边组件的职责

- H3 是全模态视频/音频生成底座；33B DiT 是采样主负载，Qwen3-VL 文本编码器是采样前的大内存/CPU 前置负载。
- 20 steps 是质量基线，不等于在当前 3060 上必然可交付；早期实测表明没有正确 LoRA、VAE、采样器和显存预留时，画面会崩坏，不能把问题简单归咎于 INT4。
- 4/8 steps Turbo LoRA 主要换时间，必须与对应剪枝/未剪枝底座匹配；前两步原版、后四步 Turbo 的两阶段方案可改善运动闪烁，但不能替代人物参考和镜头控制。
- SageAttention 是单步 attention 内核优化，ClipProj 是文本编码器替代路径，EasyCache 是运行时跳步/复用，三者解决的是不同层级的问题；不能把它们都理解成“换一个更小的 H3”。

### 2. 人物一致性和长视频的已知边界

单张首帧、三视图、面部参考图能改善身份锚定，但图生视频的画面仍会随景别、运动和时长漂移。稳定的生产链不是把三视图重复塞进每一段，而是：

`人物参考包 → Director 规划/分段 → 首段 R2V 建立身份 → Motion Context 使用上一段 AV latent 接力 → 独立对白/环境声/SFX → 混音封装`

前一轮真实 15 秒多参考链已经完成：640×384、24fps、15.000 秒，墙钟 `1784.481s`（约 29 分 44 秒），SHA `28512e3f48417524ac482ec15016c50c30bcf188207a1919a995249eba472543`。Director 和 Motion Context 不是二选一：Director 是上层编排和局部重跑，Motion Context 是底层连续性和断点接力。

远景脸部崩坏的主要排查顺序已经固定为：输出像素/景别是否超过本机稳定档 → 首帧/参考图身份是否稳定 → LoRA 是否与底座匹配且真正注入 → steps/采样器/CFG 是否合理 → 参考图是否被错误重复注入 → 长链接缝和 audio context 是否一致。不能先把全部失败归于量化。

### 3. 音频边界

H3 可以生成原生视频/音频，但当前生产对白不直接依赖 H3 原生音频：上一轮使用 Linux 常驻 CosyVoice3 生成对白，再保留/替换 H3 音频床，用 FFmpeg 混音封装。TTS 通过不等于逐音素口型通过；严格对白镜头仍需口型驱动或带对白重采样。

## 二、本轮新增方案的技术判断

### 1. ClipProj

官方 [ComfyUI-ClipProj](https://github.com/nicolab28/ComfyUI-ClipProj) 的核心是把 Qwen3-VL-32B 文本编码器替换成 4B/8B 编码器，再用匹配的投影矩阵把 hidden state 接回 H3。4B 组合适合 12GB 级显卡；8B 组合更接近质量/语义上限，但内存压力明显更高。4B encoder 和 8B matrix 不能混用。

本机单卡安全链为：`CLIPLoader(type=krea2) → ClipProj Apply → H3 clip 输入`。没有采用会把多个大模型同时常驻 GPU 的 all-in-one loader。ClipProj 版本必须满足矩阵要求；v3 矩阵配旧节点可能出现 KeyError `W`，因此本轮固定官方节点版本并先做 `/object_info` 门禁。

### 2. EasyCache

[EasyCache upstream](https://github.com/H-EmbodVis/EasyCache) 的论文速度数据主要来自 HunyuanVideo/Wan，不是 H3 本机保证。ComfyUI 当前有原生 [`nodes_easycache.py`](https://raw.githubusercontent.com/comfyanonymous/ComfyUI/master/comfy_extras/nodes_easycache.py)，可以输出 `skipped x/y steps`，这是本机是否真的获益的唯一直接门禁。本轮没有把 upstream 的 Wan/Hunyuan 实现直接接进 H3，也没有以论文 PSNR 代替视频审片。

### 3. SolAttn

[ComfyUI-SolAttn_triton](https://github.com/kijai/ComfyUI-SolAttn_triton) 属于实验性稀疏 attention，公开验证重点是 RTX 4090/5090，首次运行还会编译 Triton kernel。RTX 3060/Ampere 不能凭名称推定可用；本轮只保存代码快照，避免和 EasyCache、SageAttention 同时引入不可归因变量。

## 三、严格 A/B 测试结果

固定条件：剪枝 INT8 FL2VA、剪枝兼容 raw-key Turbo LoRA、原生视频/音频 VAE、SageAttention、Euler/simple、640×384、24fps、124 帧、同一 3DCG 国漫人物/桥面场景、同一 prompt 和 seed `20260818001`。所有成功媒体均通过 ffprobe、全量解码和 SHA。

| A/B | 编码器/矩阵 | steps | EasyCache | 墙钟 | 结果 |
|---|---|---:|---|---:|---|
| 原始基线 | Qwen3-VL-32B INT4 | 8 | 关闭 | 383.93s | 可用，但该随机结果出现字幕样文字伪影 |
| 快速预演 | Qwen3-VL-4B FP8 + v3.1 ridge | 8 | 关闭 | 151.98s | 约 2.53×；无字幕，构图/语义有变化 |
| 生产候选 | Qwen3-VL-4B FP8 + v3.1 MLP | 8 | 关闭 | 204.72s | 约 1.87×；`cos_test 0.8116`，脸/眼镜/服装/桥体抽帧更稳 |
| EasyCache A | 4B ridge | 20 | `0.05/0.15/0.80` | 302.15s | `0/20` 跳步，`1.00×` |
| EasyCache B | 4B ridge | 20 | `0.20/0.15/0.80` | 261.88s | `2/20` 跳步，`1.11×`，抽帧可审阅 |

证据媒体与 workflow 全部在：

- `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-clipproj-easycache-gate/`
- `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-clipproj-mlp-gate/`

上表只把有效的 20 steps t020 计入结论。第一次生成器误把 t020 变成 8 steps 的文件已保留在 `results.jsonl`，作为脚本调试证据，不计为 EasyCache 20 steps 样本。

## 四、负载和速度的真实解释

32B 基线的主要问题是文本编码阶段把 32GB 主机推入高 swap；4B loader 日志显示约 `4999.47 MB`，所以 ClipProj 确实降低了文本编码器的内存和前置时间。但采样阶段 GPU 仍接近 11GB，说明 33B DiT 仍是 3060 的主负载；ClipProj 不能把 H3 采样本体变成 5GB 模型。

本轮 4B ridge 的速度最好，但 `cos_test 0.6874` 和抽帧稳定性不如 MLP；MLP 文件多约 481MB，速度慢约 34.7%，却更适合生产正脸、对白和关键镜头。EasyCache 的收益取决于实际相邻步相似度，阈值越激进越可能跳过必要更新，必须按场景读取日志并审片。

## 五、最终工作流建议

### 低显存批量档

`Qwen3-VL-4B FP8 + v3.1 MLP + H3 剪枝 INT8 + 匹配 Turbo LoRA + SageAttention + 640×384 + 8 steps`

这是本轮唯一升级为“生产候选”的新链，但要保持“5 秒实测通过、15 秒新底座长链未复测”的边界。长剧正式生产仍沿用已验证的 Director + Motion Context，并先做短段抽样。

### 快速预演档

`4B FP8 + v3.1 ridge + SageAttention + 8 steps`。只用于动作、构图和镜头筛选；人物面部、关键对白和最终交付回到 MLP。

### EasyCache 档

先从 `reuse_threshold=0.20, start=0.15, end=0.80, warmup` 做独立 A/B，必须保存日志中的 `skipped x/y`。跳步为 0 时不算加速；出现脸部撕裂、运动断裂或音频接缝异常就关闭。不要把文章的 2～3×直接填入生产预算。

### 不纳入本轮默认档

8B ClipProj、SolAttn、20 steps + Turbo LoRA 的高分辨率组合、15/30 秒新底座长链，均留待下一轮，不在本轮继续扩张。

## 六、复现和验收门禁

1. 下载源预检：国内/内网/可信镜像优先；海外回退必须在 Atlas 记录原因。
2. 资产门禁：文件尺寸、SHA、encoder/matrix 4B/8B 配对、节点 `/object_info`。
3. 服务门禁：任务专用 8192 隔离；不能停止 `8077` 微信 Bot、`8090` Qwen、`8188/8189` 正式 ComfyUI、`8789` CosyVoice3。
4. 媒体门禁：真实 GPU 采样、workflow、日志、资源快照、ffprobe、全量解码、首中尾抽帧、SHA。
5. 长链门禁：每段保存 AV latent、clip index、context_length/audio_context_length；失败只重跑失败段，不重新采样已完成段。
6. 结论门禁：下载/节点注册/HTTP 200 不等于有效采样；论文和社区宣称不等于本机 RTX 3060 性能。

## 七、本轮停止点

本轮目标已完成：新组件已下载或进入可追踪的落盘流程，4B ClipProj ridge/MLP 已真实采样，EasyCache 已做阈值 A/B，结果、哈希、抽帧、日志和复现脚本已保存。8B 资产只完成下载核验后归档，不再启动新的 8B 采样。任务专用 H3 进程在文档和证据完成后关闭，并复核保护服务和 GPU 回落。
