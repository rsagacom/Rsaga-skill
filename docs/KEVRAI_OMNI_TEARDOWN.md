# Kevrai-Omni 代码拆解报告（AI 影视创作工厂参考部件）

> 版本：v1.0 · 2026-08-21 · 记录者：dsh
> 对应蓝图：`AI_FILM_FACTORY_BLUEPRINT.md` §26（v1.2）
> 本报告只记录原理拆解与映射，不复制对方代码；对方代码许可见 §0。

## 0. 获取证据与许可证边界

| 项 | 值 |
|---|---|
| 上游仓库 | `https://github.com/Bullobis/Kevrai-omni`（branch `main`） |
| 下载源 | gh-proxy.com 国内 GitHub 代理镜像（2026-08-21 实测 HTTP 200；gitclone.com 502、gitee 无此仓库、kkgithub 404） |
| 本地快照 | `/Volumes/AJW-Data/Projects/_external_kevrai-omni/repo`（43 个文件） |
| 压缩包 SHA-256 | `cb1b943214bdb979104038bc154bd6063afedebf71eb5f303d8b32650d2eed89` |
| 软件许可 | CC BY-NC-SA 4.0（署名-非商业性使用-相同方式共享）：禁止商用、衍生作品须同协议 |
| 模型许可 | MiniMax H3 Community License（排除美国/欧盟/英国/韩国） |
| 使用方式 | 仅个人学习/研究/原理参考；影视工厂平台侧只吸收事实、参数与门禁思想，由自研薄 adapter 承载，不复制代码、不并入其 PySide6 桌面形态 |

## 1. 总体架构判断

Kevrai-Omni 是一个**单机桌面工作站**（PySide6 GUI + DiffSynth-Studio 内置引擎 + 模型市场），不是服务端平台。它的价值不在形态，而在于四层已核实的工程沉淀：

```text
facts.py     事实层：模型/仓库/输入限制/分辨率档位，全部标注核实日期与来源
planner.py   决策层：硬件报告 → 模型/分辨率/步数/卸载策略推荐
hardware.py  探测层：多芯片后端识别 + 显存分档
engine.py    执行层：DiffSynth MiniMaxH3Pipeline 封装（vram_limit + 分区布局定位）
downloader.py + sources.py  供给层：真实测速选源 + Range 断点续传
customizer.py 门禁层：DIY 拼包 R1–R5 硬校验
```

这正好覆盖影视工厂蓝图中“模型注册表、硬件路由、模型下载门禁、H3 Provider 参数合同”四个缺口。

## 2. 模块拆解与平台映射

### 2.1 `facts.py` → 平台 `canon/MODEL_REGISTRY.md` + capability manifest

原理：把所有“容易编错的事实”集中成单一数据源，每条标注核实日期与来源；估算值显式标 `estimated=True`。

可直接吸收的数据结构：

- 生成规格：fps=24、音频 32kHz、时长 4–15s、宽高 32 倍数、帧数对齐 `17n+5`、CFG 蒸馏（负面提示词无效）。
- 输入上限：Ref2VA ≤9 图 ≤3 视频 ≤3 音频合计 ≤12；单段 2–15s；音频必须伴随视觉；FL2VA 关键帧 ≤2。
- 分辨率档位表（16:9 / 9:16 / 1:1 / 4:3 / 3:4 / 21:9 × 480p/640p/768p）。
- `BUNDLES` 模型包结构：`id / engine(builtin|comfyui|lora) / partition(FL2VA|Ref2VA) / precision / size_gb / min_vram_gb / min_ram_gb / files[] / source_repos{} / processor_repos{}`——每个文件大小经 API 核实，processor 与权重分仓库存放。
- 已核实数据点：NF4 FL2VA 34.4GB（8GB 显存/16GB 内存起）；NF4 双分区 51.5GB；BF16 单分区 144.1GB（48GB 显存/64GB 内存起）。
- 已核实结论：GitHub 不托管 H3 权重（仅代码），权重源只有 HF / 魔搭 / hf-mirror。

平台落点：`canon/MODEL_REGISTRY.md` 的 H3 条目直接采用此事实卡；`studio_core/registry.py` 的 workflow 登记字段参照 `BUNDLES` 的 engine/partition/source_repos 三元结构。

### 2.2 `engine.py` → 平台 H3 Provider 参数合同

原理与关键做法：

- `check_engine_ready()` 分层依赖检查（torch → 加速后端 → diffsynth → av → torchaudio），每层返回明确错误而非静默失败——对应我们 `film_factory.providers` 的 `ProviderNotConfigured` 语义。
- `vram_limit` 计算：手动预算优先；否则 `torch.cuda.mem_get_info()` 可用显存 **减 2GB 余量**；zero 模式传 0 走极限磁盘流式。这是低显存机器防 OOM 的核心技巧。
- `_locate_files()` 按 bundle 布局定位权重（NF4 平铺 / BF16 分目录 / DIY custom 布局），`ModelConfig` 列表组合 DiT+文本编码器+视频VAE+音频VAE，processor 单独配置。
- 分区切换（FL2VA↔Ref2VA）必须重新 `load()`，同一进程先 `unload()`——避免双分区驻留显存。
- 生成参数官方默认：`cfg_scale=1.0`、`flow_shift=12.0`、`audio_flow_shift=3.0`、`tiled VAE (256/64)`、`rand_device=cpu`（跨显卡 seed 一致）。

平台落点：`studio_core/render.py` 的 RenderSpec 扩展 `partition / vram_limit / offload_mode` 字段；视频 Provider adapter 按此合同封装 DiffSynth 后端（隔离 A/B 通道，不替代已收口的 ComfyUI 生产链）。

### 2.3 `planner.py` → 平台硬件路由

原理：`HardwareReport → OptimalPlan` 纯函数映射，按显存分档推荐 bundle + 分辨率 + 步数，并附带来源标注的速度参照。

- 分档：≥48GB→BF16；16–48GB→NF4 双分区；8–16GB→NF4 FL2VA + 建议 Turbo LoRA；<8GB→GGUF/ComfyUI。
- 生成页三档预设（speed 480p/4步+Turbo LoRA；balanced 480p/50步；quality 768p/50步）——与蓝图 §8.3 质量档（草稿/平衡/精修/长链）一一对应。
- 所有速度数字标注来源与日期，未知型号回退“先跑 480P 试探”——符合平台 `declared ≠ verified` 原则。

平台落点：与 §26.4 已合并的硬件路由表互为数据源；未来 worker 调度按此分档拒绝不可行任务。

### 2.4 `downloader.py` + `sources.py` → 平台模型资产下载适配器

原理：

- 测速：对每个源的探针文件（约 7MB tokenizer.json）做真实 HTTP Range 采样 4MB，同时测 TTFB 与吞吐，评分 = 速度 75% + 延迟 25%，串行探测避免并发抢占带宽。
- 下载：Range 断点续传（`.part` 文件）、指数退避重试（5 次）、进度/ETA 回调、可取消。
- 远端目录列举：HF tree API `GET /api/models/{repo}/tree/{branch}/{path}`；魔搭 `GET /api/v1/models/{repo}/repo/files?Revision=&Root=`。
- 源策略：modelscope（国内官方合作源）/ hf-mirror（国内公益镜像）优先，HF 原站为海外回退。

平台落点：自研 `asset_downloader` adapter 采用同一合同；接入 Atlas `source-preflight`（国内源优先、海外回退记录 `OVERSEAS_FALLBACK`）。

### 2.5 `customizer.py` → 平台模型下载前门禁

原理：DIY 拼包五条硬校验（违反即拒绝下载）——R1 引擎匹配（DiffSynth/ComfyUI 组件不混装）、R2 量化成套（NF4/BF16 全家桶不混搭）、R3 分区匹配（FL2VA↔processor_fl2va）、R4 显存可行性、R5 磁盘可行性；另有 W1–W3 风险警告（允许但提示）。

平台落点：已写入蓝图 §26.5；实现时进入 `studio_core/registry.py` 的模型登记门禁。

### 2.6 `hardware.py` → 平台 worker 探测

原理：后端检测顺序 CUDA→ROCm(hip)→NPU(torch_npu)→XPU→DirectML→CPU；`nvidia-smi` 优先查询显存（Windows 下走 System32 绝对路径）；每后端标注“完整支持/实验性/不推荐”。

平台落点：远端 GPU worker 的注册心跳中携带同一后端枚举，便于模型路由按 `local_or_remote` + `vram_profile` 派单。

### 2.7 `image_gen.py` → 图片 Provider 资源租约

原理：视频/图片引擎互斥占用显存，生成前互相 `unload()`；权重按 `transformer/text_encoder/vae` 三目录分片定位。

平台落点：`film_factory.providers` 的图片 Provider 增加“显存租约”语义：同一 GPU 同一时间只允许一个重型 pipeline 驻留。

### 2.8 不采用的部分

- PySide6 GUI 整体（玻璃拟态、页面路由、QSS 主题）：平台是 FastAPI/Next.js 服务端 + Web 工作台，桌面 UI 不迁移。
- PyInstaller/Inno Setup 打包链、`.bat` 启动脚本：无此需求。
- `config.py` 的 JSON 设置持久化：平台配置走数据库/环境变量。
- 许可证弹窗流程：平台用权利台账（§16）替代单机确认弹窗。

## 3. 与我们既有 H3 实测经验的对齐

| 维度 | Kevrai-Omni（DiffSynth 线） | 本平台已收口（ComfyUI 线） | 结论 |
|---|---|---|---|
| 引擎 | DiffSynth-Studio `MiniMaxH3Pipeline`，NF4 量化 8GB 可跑 | ComfyUI 0.31.0 + 剪枝 INT8 + drbaph raw-key Turbo LoRA | 生产默认 ComfyUI 链；DiffSynth 作隔离 A/B |
| 长视频 | 未覆盖（单段 4–15s） | Director 编排 + Motion Context AV latent 接力，15s 已验证 | 长链只有本平台有生产证据 |
| 速度参照 | 3060 480p/5s≈9min（社区实测） | 3060 832×480/124帧 Drbaph 831.04s（本平台实测） | 两线数据已合并进 §26.4 |
| 预演档 | 480p/4步 + InstantX Turbo LoRA | 4 steps 预演档（本平台） | 一致，可互相印证 |
| 输入校验 | facts.py 上限表 | 尚无 | 吸收进 capability manifest |

## 4. 后续代码任务（本轮未实现）

1. `canon/MODEL_REGISTRY.md` 落 H3 事实卡（§26.2 数据）。
2. `studio_core/render.py` RenderSpec 扩展 `partition/vram_limit/offload_mode`。
3. 自研 `asset_downloader` + R1–R5 门禁（Atlas source-preflight 留档）。
4. DiffSynth 后端 Provider adapter（隔离 A/B，不进默认生产）。
5. 图片/视频引擎显存互斥租约。

## 5. 声明

本报告为原理拆解与架构映射，未复制 Kevrai-Omni 任何代码文件；其 CC BY-NC-SA 4.0 许可的非商业限制与 SA 传染条款仍然有效，任何商业用途的复用必须先取得授权。下载快照仅用于本地学习研究，不分发。
