# MiniMax H3 本地执行可复用蓝图

> 版本：v1.6  
> 更新时间：2026-08-17  
> 适用项目：`novel-to-comic-engine` / AI 漫剧与短剧生产链  
> 适用硬件：Linux `cachyos-ai`、RTX 3060 12GB、32GB RAM  
> 证据总入口：[H3_PRODUCTION_HANDOFF.md](./H3_PRODUCTION_HANDOFF.md) 与 [H3 离线评测台](../h3-evaluation-offline/index.html)

> **📍 2026-08-23 迁移通告（现行事实，优先级高于下文旧路径）**
> H3 本地生产环境已从双盘缠绕（`/mnt/gaosu_sata` + `/mnt/sd_nvme/MiniMax-H3`）整体统一迁移至 **`/mnt/gaosu_nvme/h3-production/`**（自足，三道验收门全过）。
> 路径映射：`/mnt/gaosu_sata/ComfyUI` → `/mnt/gaosu_nvme/h3-production/ComfyUI`；`/mnt/sd_nvme/MiniMax-H3` → `/mnt/gaosu_nvme/h3-production/runtime`；`/mnt/gaosu_sata/MiniMax-H3`、`MiniMax-H3-NF4`、`MiniMax-H3-diffsynth` → 同名子目录。
> venv 现行：ComfyUI 主程序用共享环境 `/mnt/gaosu_nvme/AI-Linux/boogu/ComfyUI/.venv`（py3.14.6，含 sqlalchemy/transformers/sageattention/torch）；`runtime/runtime-venv` 为节点侧 torch/sageattention 环境。隔离实例端口建议 **8192**（8189 已被 boogu-api 占用）。
> 旧盘即将随「高速sata NTFS→XFS 转换」清空重格；本文历史证据段落中的旧路径仅作历史记录，不代表现存位置。验收证据：Atlas `task-20260822-150643-0fe352`（节点门 + 2026-08-17 短链×2 + 2026-08-19 长链 Director+MotionContext 接力全部 success，LoRA 每次 call `is_injected=True`，旧盘模型加载 = 0）。

## 1. 蓝图目的

这份文档把 MiniMax H3 在本地 ComfyUI 上的实际执行经验沉淀成框架无关的 SOP。新 Agent、脚本或人工接手时，应先按本蓝图恢复环境，再按评测门禁逐项验证；不能只根据“模型能加载”或“MP4 能导出”判断生产可用。

本蓝图特别固化以下经验：

- RTX 3060 12GB/32GB 的长期生产主链是已验证的剪枝 INT8 FL2VA + 剪枝专用 drbaph raw-key Turbo LoRA + INT4 Qwen；非剪枝 INT8 + T8-convert 保留为质量上限/备用链，不能与剪枝底座混配。
- H3 文生视频不要求首帧；首帧/参考图是可选的 I2V/R2V 控制变量。为了人物身份稳定，生产通常采用参考图生视频或“角色建立段 + 后续接力”。
- Director 是生产编排入口；Motion Context 是底层手工接力和故障回退工具。二者不能在同一 ComfyUI 进程叠加运行时补丁。
- 速度、画质、人物一致性、音频洁净度必须分别验收；一条视频完整解码通过，不等于脸、动作、对白、音频和接缝通过。
- 4 步适合预演，8 步是本机当前速度/画质平衡档；官方无 LoRA 20 步保留为失败技术基线，不能因为步数高就直接视为生产高画质。
- 模型评测必须先完成短段模型门禁，再把 15 秒及以上长链成本集中到唯一通过者；未选模型只保留兼容性/质量上限证据，不再做 Director/Motion Context 笛卡尔积。

## 2. 总体生产架构

```text
剧本/小说
  -> 叙事单元与镜头表
  -> Director 时间轴分段
  -> H3 T2V / R2V / I2V 短段生成
  -> Motion Context 或 Director 内置上下文接力
  -> 独立 TTS、对白、音效与最终混音
  -> FFmpeg 规范化、字幕、封装
  -> 机器门禁 + 首/中/尾帧 + 接缝 + 人工画质审阅
```

模块职责：

| 模块 | 责任 | 当前结论 |
|---|---|---|
| MiniMax H3 | 画面、动作、风格、原生氛围音频 | 已在 3060/32GB 上真实运行 |
| Turbo LoRA | 少步数速度和基础渲染稳定性 | 剪枝 INT8 使用 drbaph 剪枝专用 raw-key；非剪枝 INT8 使用 T8-convert |
| SageAttention | 注意力计算加速 | 已通过启动参数和 KJ 节点链路验证 |
| Director AIMixer | 时间轴、段落编排、自动清缓存、局部重跑 | 默认生产编排入口 |
| Motion Context | 上一段 video latent、音频上下文、尾部接力 | 手工接力/研究/回退入口 |
| 三视图/面部参考 | 身份、服装、发型和配饰约束 | 5 秒和 30 秒连续性测试有效，但不等于超分 |
| FFmpeg/VideoHelperSuite | 拼接、裁切、封装、完整解码 | 机器验收必需 |
| 独立 TTS/混音 | 对白、旁白、最终音效 | H3 原生音频音量偏低，不直接承担对白成片 |

## 3. 主机与安全边界

| 项目 | 已验证事实 |
|---|---|
| Linux 主机 | `rsaga@192.168.1.6` / `cachyos-ai` |
| GPU | NVIDIA RTX 3060 Laptop GPU，12,288 MiB VRAM |
| 内存 | 32GB RAM；长任务会使用 swap |
| 正式 H3 服务 | ComfyUI `8189` |
| 历史隔离端口 | Motion Context `8190`；Director `8191`；I2V 复测曾使用 `8193` |
| H3 ComfyUI | `/mnt/sd_nvme/MiniMax-H3/ComfyUI` |
| ComfyUI base | `/mnt/gaosu_sata/ComfyUI` |
| H3 user dir | `/mnt/sd_nvme/MiniMax-H3/user-8189` |
| 微信 Bot | `/mnt/gaosu_sata/wechat-linux-bot/bot/reply_bot.py`，严禁停止 |
| Qwen 路由 | 属于生产依赖，严禁为 H3 测试清理 |

每次测试前后都要执行：

```bash
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader
pgrep -af 'wechat-linux-bot/bot/reply_bot.py'
curl -fsS http://127.0.0.1:8189/queue
```

允许的清理范围：H3 自身的动态模型、队列、隔离端口和测试进程。禁止删除模型文件、LoRA、VAE、工作流、latent；禁止停止微信 Bot、Qwen、微信 GUI、Clash/VPN。

## 4. 模型资产与兼容性

### 4.1 已验证资产

| 用途 | 路径 | 大小 |
|---|---|---:|
| H3 非剪枝 INT8 FL2VA | `/mnt/sd_nvme/MiniMax-H3/quark-package/models/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors` | 34,038,892,334 bytes |
| H3 剪枝 INT8 FL2VA（主生产） | `/mnt/sd_nvme/MiniMax-H3/quark-package/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 约 20GB staged，SHA 见交接手册 |
| H3 剪枝 INT4 FL2VA | `/mnt/gaosu_sata/ComfyUI/models/diffusion_models/minimax_h3_fl2va_pruned_int4_convrot.safetensors` | 11,337,536,776 bytes |
| Qwen H3 INT4 文本编码器 | `/mnt/gaosu_sata/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_int4_convrot.safetensors` | 14,952,506,624 bytes |
| Qwen NVFP4/AWQ | `/mnt/gaosu_sata/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15,687,142,551 bytes |
| 视频 VAE | `/mnt/gaosu_sata/ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496 bytes |
| 音频 VAE | `/mnt/gaosu_sata/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors` | 605,254,808 bytes |
| T8 转换 Turbo LoRA | `/mnt/gaosu_sata/ComfyUI/models/loras/minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors` | 779,858,903 bytes |
| drbaph 剪枝专用 raw-key LoRA（主生产） | `/mnt/gaosu_sata/ComfyUI/models/loras/minimax_h3_turbo_v4_step600_ema_pruned_rawkeys.safetensors` | SHA 见 `test-runs/2026-08-17-h3-pruned-lora-15s/REPORT.md` |

### 4.2 兼容性硬规则

1. `T8-convert` 必须配非剪枝 `minimax_h3_fl2va_int8_convrot.safetensors`；不能把它和 `pruned` 底座混为默认生产链。
2. Linux 中的 `minimax_h3_turbo_4STEPS_comfyui.safetensors` 曾只有 94 bytes，是无效占位/残留文件，不能当作可用 LoRA。
3. T8-convert 在专用 `MiniMaxH3TurboLoRA` 节点中出现过 `0 bypass adapters/0 injections`；当前有效方式是标准 `LoraLoaderModelOnly` 或与作者工作流一致的 T8 loader，不得把“节点加载成功”当作 LoRA 生效。
4. NVFP4/AWQ Qwen 在当前 ComfyUI 0.31.0 的 CLIPLoader 路径出现过 `utf-32-be truncated data`；默认复现使用已经跑通的 INT4 Qwen。
5. 夸克包、RunningHub 工作流和 GitHub 节点必须按作者工作流成套核对，不能只下载一个 LoRA 后凭名称猜兼容关系。

## 5. 节点、版本与启动链

### 5.1 已核对节点

| 目录 | commit/用途 |
|---|---|
| `/mnt/gaosu_sata/ComfyUI/custom_nodes/comfyui-minimax-h3-audio-T8` | `7a99dba`，H3 音视频节点和 T8 双时钟采样 |
| `/mnt/gaosu_sata/ComfyUI/custom_nodes/ComfyUI-MiniMax-H3-Turbo` | `55fee86`，H3 Turbo 基础节点 |
| `/mnt/gaosu_sata/ComfyUI/custom_nodes/ComfyUI_MiniMaxH3_Director_AIMixer` | `4c9c58a`，Director 时间轴/段间引导 |
| `ComfyUI-KJNodes` | SageAttention KJ 与辅助节点 |
| `ComfyUI-Jjk-Nodes` | 工作流辅助 |
| `rgthree-comfy` | 分组/旁路辅助 |
| `ComfyUI-VideoHelperSuite` | 视频处理和保存 |
| `ComfyUI_UniBlockSwap` | 低显存兜底，当前不作默认加速 |
| `ComfyUI-ReservedVRAM` | 显存预留，当前不作默认加速 |

### 5.2 可复现启动命令

```bash
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
/mnt/gaosu_sata/AI-Linux/boogu/ComfyUI/.venv/bin/python \
/mnt/sd_nvme/MiniMax-H3/ComfyUI/main.py \
  --listen 0.0.0.0 \
  --port 8189 \
  --base-directory /mnt/gaosu_sata/ComfyUI \
  --user-directory /mnt/sd_nvme/MiniMax-H3/user-8189 \
  --database-url sqlite:////mnt/sd_nvme/MiniMax-H3/user-8189/comfyui.db \
  --disable-auto-launch \
  --lowvram \
  --fp16-vae \
  --use-sage-attention \
  --disable-all-custom-nodes \
  --whitelist-custom-nodes ComfyUI-MiniMax-H3-Turbo comfyui-minimax-h3-audio-T8 ComfyUI-KJNodes ComfyUI-Jjk-Nodes rgthree-comfy ComfyUI-VideoHelperSuite ComfyUI_UniBlockSwap ComfyUI-ReservedVRAM
```

启动后必须检查 `/system_stats`、`/object_info`、ComfyUI 日志和实际采样日志；`--use-sage-attention` 只代表请求启用，不代表每个工作流都实际调用了 Sage 节点。

## 6. 默认参数档位

| 档位 | 分辨率 | steps | 用途 | 结论 |
|---|---:|---:|---|---|
| 预演 | 704×416 或 832×480 | 4 | 构图、动作、提示词抽卡 | 快，画质只作筛选 |
| 平衡 | 704×416/832×480 | 8 | 批量候选、带音频短段 | 当前默认 |
| 高清平衡 | 1088×608 | 8 | 约 0.66MP 的成片候选 | 速度/质量最佳折中 |
| 高清质量 | 1344×768 | 8 | 约 1MP 的通过镜头复现 | 约 17 分钟/5 秒，不能批量抽卡 |
| 最高质量实验 | 1344×768 | 20 | 通过镜头最终复现 | 约 32 分钟/5 秒；高负载 |

固定基础参数：24fps；标准短段 124 帧≈5.167 秒；video shift 12；audio shift 3；CFG 1.0；`res_multistep`/`simple`；分辨率为 32 的倍数；T2VA 原生音频按需开启。

生产规则：4 步先预演；8 步做速度/画质平衡；20 步只对人工通过的镜头做复现。官方无 LoRA 20 步在本机出现画面崩坏，不能因为步数更高而升级为质量档。

### 6.1 模型选择顺序

```text
模型/量化版 + 匹配 LoRA
  -> 5 秒固定 seed 短段
  -> 远景 / 中景 / 近景 / 特写
  -> 4 步预演、8 步生产候选；20 步只作单镜头复现
  -> 真人 / 3DCG / 水墨 / 2D 动漫风格抽检
  -> 640 / 832 / 1088 分辨率与显存门禁
  -> 选出唯一长期生产底座
  -> 仅对选定底座运行 Director + Motion Context 15 秒专项
```

本机已经完成该顺序，当前选定 `pruned INT8 FL2VA + INT4 Qwen + drbaph pruned raw-key Turbo LoRA`。后续如果更换量化版、LoRA 或 ComfyUI 核心版本，必须重新从 5 秒门禁开始，不能沿用旧长链结论。

## 7. 两个长视频核心组件

### 7.1 Director

Director AIMixer 是默认的生产编排层，负责可视化时间轴分段、每段独立提示词、自动保存/清理和局部重跑。已验证工作流：

- 工作流：`test-runs/2026-08-13-h3-director-aimixer/workflow_aimixer_director_t8_2segments_832x480.json`
- 输出：`test-runs/2026-08-13-h3-director-aimixer/aimixer_director_2segments.mp4`
- 实测：2 段、248 帧、832×480、24fps、8 steps、10.334 秒、约 18 分 28 秒、峰值显存约 9.3GiB。
- 日志确认第 2 段收到 22 个视频上下文帧和 40 个音频步，`seam_gap=0`。

### 7.2 Motion Context

独立 Motion Context 负责手工 latent/音频尾部接力，适合研究接缝、故障隔离和 Director 的底层回退。固定：`context_length=22`、`audio_context_length=24`、`match_tail=true`、关闭 Spectrum 跳步、所有段分辨率一致。

- 首段约 5.167 秒，第二段去除 22 帧重叠后约 4.25 秒。
- 独立两段实测总成片约 9.449 秒，段耗时约 464.97 秒和 521.56 秒，峰值显存约 9.26GiB。
- 通过 `Save Latent`/`Load Latent` 传递 AV latent 时，必须保存 video latent 与 audio latent，不能只传普通帧。

### 7.3 组件选择

Director 与独立 Motion Context 都会 patch H3 layout；同一进程叠加会导致第二段 layout patch ownership 冲突。切换方式必须是：停旧实例 → 确认队列为空 → `/free` → 启新隔离端口 → 载入对应官方工作流。

## 8. 真实长视频耗时基线

使用国风雨巷、普通话人物独白、环境音效；704×416、24fps、每段 362 帧、22 个视频 latent overlap、40 个音频上下文步、8 steps、非剪枝 INT8 + INT4 Qwen + T8 + SageAttention + 原生 T2VA。

| 目标时长 | 段数 | raw | 精确交付 | 总耗时 |
|---:|---:|---:|---:|---:|
| 30 秒 | 2 | 30.166667s | 30.000000s | 2065.61s（34分25.61秒） |
| 60 秒 | 4 | 59.917000s | 60.000000s | 4438.23s（73分58.23秒） |
| 120 秒 | 8 | 119.417000s | 120.000000s | 8667.43s（2小时24分27.43秒） |

120 秒期间 RAM 约 29～31GiB，swap 约 31～36GiB；段间切换时 GPU 利用率会短时很低但 CPU 仍在工作，不应仅以低 GPU 利用率判定假死；全程无 OOM/Traceback，7 个接缝完成 12 帧亮度平滑。

独立 5 秒拼接 30 秒：6 段、832×480、4 steps、Drbaph Ref2V，总耗时 4818.75s（约 80分18.75秒），raw 31.033656s，裁切后 30.000s。画面和声场在硬切点跳变，只适合“分镜剪辑式长视频”，不能替代上下文接力。

## 9. 画质、人物与风格实测

### 9.1 LoRA A/B

统一三视图 R2V、832×480、124 帧、24fps、同 seed：

| LoRA | 耗时 | 结论 |
|---|---:|---|
| T8 标准 Loader | 891.58s | 当前兼容基线 |
| Larry v4 EMA merge | 941.63s | 轻微清晰度改善，RAM 压力大 |
| Realism People | 931.30s | 真人脸/皮肤连续性最好 |
| Drbaph Ref2V | 831.04s | 速度/清晰度综合最好 |
| Kijai LightX2V Ref2V | 832.00s | 可用备选，未超过 Drbaph |

当前推荐：Larry 6 步做预演；T8 8 步做带原生音频的平衡候选；Drbaph Ref2V 做参考图速度/清晰度候选；Realism People 仅在真人近景需要时做 A/B。

### 9.2 三视图、面部增强与真人 I2V

- 三视图 R2V：832×480、8 steps、891.58s、5.167 秒；比 704×416 长视频远景更能保持脸型、发型、发簪和服装色彩，但不是面部超分。
- 面部参考 A/B：4 张图、`ref_max_size=832` 为 932.62s；`ref_max_size=2048` 为 941.52s，肉眼无明显收益。视频锚点组 1242.07s 仍停在视频 VAE，安全中断，不纳入默认方案。
- 真人 I2V：704×416、124 帧、24fps、5.167 秒、584.98s；人物脸型、发型、服装和场景稳定。参考帧会把模式从纯 T2V 变成首帧约束的 R2V/I2V，不能再把结果当纯文生视频基线。

人物生产规则：远景承担空间和走位；对白/口型镜头让脸占画面高度约 35%～45%；角色首次出场使用面部近景+正/侧/背参考；后续段固定镜头尺度和服装，不要每段重复挂全局参考板。

### 9.3 3DCG

3DCG 三视图组 819.08s（13分39.08秒），面部近景+三视图组 988.64s（16分28.64秒），均为 832×480、4 steps、5.167 秒、H.264/AAC，完整解码通过。5 秒近景内，3DCG 比真人更容易保持脸型、发型、玉簪、服装材质和腰带位置；这是资产统一优势，不是“必然更快”。

30 秒连续性结论：Motion Context 三视图与面部增强组已产出 30 秒样本；Motion Context-only 的视觉接力更稳定，Director 三视图可用但面部增强版尾段出现彩噪，暂不作为默认长片底座。3DCG 30 秒仍必须做接缝、尾段和局部五官人工审阅，不能只看首段。

## 10. 高负载与失败经验

| 现象 | 真实原因/边界 | 处理 |
|---|---|---|
| 官方 20 步画面崩坏 | 本机 INT8 + 无 LoRA 渲染链不稳定，不是“步数越高越好” | 作为失败基线；改用兼容 LoRA，或只复测单变量 |
| 第二段长时间不动 | CPU/swap/VAE/上下文搬运，不能仅凭 GPU 低利用率判断 | 查日志、RAM、swap、进程树；未超过安全阈值前不杀进程 |
| Motion Context 第二段卡在初始化 | 低显存/32GB 调度成本高 | 保留第一段和 latent，安全中止，降分辨率/缩短上下文后重测 |
| Director 与 Motion Context 冲突 | 同时 patch H3 layout | 分端口、分进程、切换前 `/free` |
| `T8-convert` 0 injections | 专用 LoRA 节点键名不兼容 | 使用标准 Loader + 作者工作流，不把结果计入有效 A/B |
| NVFP4/AWQ 报 UTF-32 截断 | 当前 CLIPLoader 路径兼容问题 | 回退 INT4 Qwen，单独升级环境后再复测 |
| 参考图不改善远景五官 | 低分辨率远景承载了过多面部目标 | 采用近景对白镜头、合理参考尺寸和分镜拆分 |
| 视频锚点极慢 | 视频 VAE 预处理成本远高于图片参考 | 默认用三视图/面部近景，不把视频锚点当首选 |
| ReservedVRAM 触发换页 | 手动预留 1GB 在本机加重 CPU/swap | 不纳入默认链 |
| UniBlockSwap 不进入采样 | 显存兜底不是速度优化，可能长期换入 | 仅在内存更大的机器做单变量实验 |
| H3 音轨有 AAC 但对白太小 | 原生音频均值约 -47～-40dB，音量不足 | 独立 TTS/混音；H3 音频只做氛围或参考 |
| 30 秒硬切跳变 | 独立 5 秒片段无 latent/音频上下文 | 用 Director/Motion Context；或明确按剪辑镜头验收 |

## 11. 标准执行 SOP

### 阶段 A：审计

1. 读取本蓝图、`H3_PRODUCTION_HANDOFF.md`、目标工作流和对应报告。
2. 在 Linux 核对 GPU、RAM、端口、Bot/Qwen 进程、磁盘空间。
3. 检查模型文件大小、SHA-256、LoRA 是否为占位文件、节点 commit。
4. 记录基线：无任务 GPU 显存、ComfyUI `/queue`、当前正式端口。

### 阶段 B：单段预演

1. 先用 4 steps、704×416 或 832×480、4～5 秒。
2. 每轮只改变一个变量：底座、LoRA、步数、分辨率、参考图、节点或上下文长度。
3. 保存 workflow JSON、prompt id、日志、运行时 GPU/RAM/swap、输出 SHA-256。
4. 抽取首/中/尾帧，检查人物、场景、动作方向、手部、五官、音频和编码。

### 阶段 C：平衡候选

使用非剪枝 INT8 + INT4 Qwen + 兼容 T8/目标 LoRA + SageAttention，8 steps、24fps；通过后才进入 1088×608 或 1MP 复现。

### 阶段 D：长视频

1. Director 默认编排；每段保留 prompt、segment id、输入尾帧、AV latent 路径和真实耗时。
2. 若需手工接力或故障隔离，切换到独立 Motion Context，不叠加进程。
3. 每段完成后检查接缝、音画漂移、亮度、五官和服装；失败只重跑对应段。
4. 最终统一帧率、时长和封装，做完整解码和人工审片。

### 阶段 E：落盘和移交

每个 run 目录至少保留：

```text
workflow*.json
prompt/request/response 日志
stdout/stderr 或 ComfyUI 日志
输出 MP4
首帧/中帧/尾帧
ffprobe JSON
SHA-256
report.md
concat manifest（若有）
中断原因与清理记录
```

## 12. 评测门禁

### 机器门禁

- ffprobe 能读出真实 width/height/fps/frame_count/duration/audio codec。
- 视频全量解码，不只抽首帧。
- 音轨采样率、声道、时长和视频时长一致或有明确裁切说明。
- workflow JSON 可重新提交，模型与 LoRA 文件存在且非 placeholder。
- 运行前后 GPU/RAM/swap、队列、Bot/Qwen 状态有记录。

### 画面门禁

- 首/中/尾帧无黑屏、全局彩噪、严重坍塌。
- 人物脸型、发型、服装、配饰和手部在镜头内可接受。
- 场景门窗、道路、光向和动作方向不跳变。
- 长视频逐接缝检查，尾段必须单独抽帧。

### 音频门禁

- H3 原生音频只作为氛围参考，对白必须由独立 TTS/混音验证。
- 检查口型与对白不明显错位，检查底噪、爆音、左右声道和音量。

## 13. 复用决策树

```text
需要纯文生视频？
  -> T2V，不接首帧；先 4 步 5 秒预演
需要角色稳定？
  -> 三视图 R2V；对白近景增加面部近景参考
需要首帧构图？
  -> I2V/R2V；保存首帧并在报告中明确不再是纯 T2V
需要 30 秒以上连续镜头？
  -> Director 默认；需要手工接力时用 Motion Context
需要批量速度？
  -> 4 步预演，8 步候选；不要先上 20 步
需要最高画质？
  -> 先在 1088×608/1MP 通过画面，再对单镜头 20 步复现
```

## 14. 证据索引

项目所有真实证据位于 `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/`，按批次保留：

- `2026-08-12-h3-baseline`、`h3-int8-t8`、`h3-official-int8`
- `2026-08-13-h3-blockcache`、`director-aimixer`、`hd-matrix`、`hq-matrix`
- `2026-08-13-h3-longvideo`、`motion-context`、`spectrum`、`t8-1mp`
- `2026-08-14-h3-long-director-audio`、`long-director-r2v-character`、`lora-ab`、`r2v-3view-832x480-5s`、`5s-concat-30s`
- `2026-08-15-h3-live-i2v-5s`、`3dcg-identity-ab`、`3dcg-long-continuity`、`identity-reference-ab`

### 14.1 2026-08-16 DiffSynth NF4 底座实测

- 国内 ModelScope `DiffSynth-Studio/MiniMax-H3-NF4` 已完整落盘并通过 SHA-256；与 Hugging Face GGUF `Q4_K_M` 分开管理，不能混称。
- 官方 DiffSynth 隔离链在 RTX 3060 12GB + 32GB RAM 上用 512×288、50 步、Torch SDPA、磁盘卸载完成 5.175 秒 T2VA，真实耗时 `734.13s`，视频和 32kHz 双声道音频完整解码通过。
- 640×384、832×480 在本机均 OOM；关闭 Qwen/MiniCPM、关闭 SageAttention、降低 `vram_limit` 只能改善调度，不能消除 bitsandbytes NF4 反量化与 H3 音视频双分支的临时峰值。
- 新 Agent 必须先运行 Torch SDPA 的纯底座基线，再单变量加入 SageAttention/LoRA；否则无法区分量化底座、加速实现和工作流错误。
- 报告与 MP4：`test-runs/2026-08-16-h3-nf4-diffsynth/REPORT.md`；可审阅副本已纳入离线评测台，当前为 121 个视频、31 个批次、636 个证据文件，约 505MB。

离线资料台：

```text
/Volumes/AJW-Data/Projects/novel-to-comic-engine/h3-evaluation-offline/index.html
```

统计：29 个测试批次、120 个视频、625 个证据文件、约 504MB。整目录复制时必须保持 `runs/`、`sources/`、`posters/`、`catalog.json`、`BUNDLE_MANIFEST.json` 与 `index.html` 的相对结构。

### 14.2 2026-08-16 H3 底座角色与摸底矩阵

当前“未剪枝版”准确名称是 `minimax_h3_fl2va_int8_convrot.safetensors`：它不是 FP16 原精度，而是保留完整网络结构的 INT8 ConvRot 量化底座。与剪枝版相比，它占用更多磁盘/RAM、低显存动态搬运更慢，但保留的层数、通道和时空建模容量更完整；此前 8 steps + 匹配 T8 LoRA 的人物五官稳定性证据来自这一底座，不能把该结论外推给剪枝 INT4 或 GGUF Q4_K_M。

| 底座 | 当前文件大小 | 量化/结构 | 在本机摸底中的位置 |
|---|---:|---|---|
| H3 非剪枝 INT8 ConvRot | 34,038,892,334 bytes | INT8，未做结构剪枝 | 当前画质/人物稳定性主基线；已有 8 steps 高清矩阵和长视频证据 |
| H3 剪枝 INT4 ConvRot | 11,337,536,776 bytes | INT4 + 结构剪枝 | 12GB 卡的低占用探索基线；本轮 640×384、20 steps、3DCG 四景别均完整出片，但特写构图控制仍需单独修正 |
| H3 剪枝 INT8 ConvRot | 20,970,379,616 bytes | INT8 + 结构剪枝 | 已落盘的中间候选；需固定提示词/seed 重新有效采样后才能判断画质与速度 |
| H3 FL2VA GGUF Q4_K_M | 19,864,208,160 bytes | GGUF Q4_K_M，非 NF4、非 safetensors ConvRot | 已从 ModelScope 国内源完整落盘并校验；实际文件 SHA-256 为 `5e8fa6e960d5fbd547390ceec63fcead275435d8f3bd2466a8a2cbd8c2e361e3`；8192 官方 GGUF 链 T2V 8-step 已通过媒体门禁 |
| DiffSynth H3 NF4 | 独立报告记录 | NF4 + DiffSynth 官方链 | 已完成 512×288 低分辨率样本；与 ComfyUI GGUF/INT8 链分开维护 |

Qwen3-VL 文本编码器是独立变量，不应写进 H3 扩散底座名称：当前可见 INT4 ConvRot 与 NVFP4/AWQ 两套，ModelScope GGUF 仓库另列 Q4_K_M 版本。摸底顺序固定为：先复用已验证的非剪枝 INT8 + INT4 Qwen + T8 + SageAttention 8 steps 作为 3DCG 主基线，再逐项替换剪枝 INT8、剪枝 INT4、Qwen 编码器和 GGUF 工作流；每次只改一个变量，并保存加载、采样、显存/RAM、完整 MP4 解码及首/中/尾帧证据。

“八步人物五官较稳”目前是运行记录支持的经验判断：相较 4 steps，8 steps 给面部与局部结构更多去噪/时序修正机会；同时 T8 LoRA、非剪枝 INT8、稳定镜头和较低运动幅度共同影响结果，不能归因于步数单一因素。之后以相同 seed、同一 3DCG 提示词和分辨率复测，分别标注“底座贡献”“LoRA 贡献”和“步数贡献”。

### 14.3 H3 完整模型组不是单一文件

一套可交付的 H3 生成链至少要记录：任务对应的扩散主权重、Qwen H3 文本/视觉编码器、视频 VAE、音频 VAE、任务节点/工作流，以及可选但必须匹配的 LoRA 和加速节点。`FL2VA` 与 `REF2VA` 是两种不同的扩散权重：前者用于 FL2V/T2VA 工作流并可在无参考输入时承担 T2V，后者用于原生多参考输入的 R2V；不能因为已有 FL2VA 就宣称 REF2VA 参考图链完整。此前 Director 的外部 R2V 证据使用 FL2VA + Director 编排，不等价于原生 REF2VA 权重已部署。

当前 Linux 资产边界：非剪枝 INT8、剪枝 INT4、剪枝 INT8 的 FL2VA 主权重，以及 REF2VA 非剪枝/剪枝 INT8 已落盘；共享视频/音频 VAE、INT4 Qwen 和 GGUF Q4 FL2VA/REF2VA/Qwen 已落盘。非剪枝 INT8 + INT4 Qwen + T8 的 T2VA 8 steps、剪枝 INT4 原生 20 steps 的 3DCG 四景别 5 秒首轮、GGUF Q4_K_M 的 T2V/R2V 8 steps 均已有真实媒体证据。GGUF R2V 已使用 `character_front_reference.png` 做正确的女性角色匹配复测；后续重点转向剪枝 INT8 的步骤/画风矩阵和可生产 LoRA 适配，不再把“下载完成”误写成“所有变量已测试”。

### 14.4 2026-08-16 剪枝 INT4 首轮景别实测

- 证据目录：`test-runs/2026-08-16-h3-pruned-shot-matrix/`；脚本：`scripts/run_h3_pruned_shot_matrix.py`。
- 共同链路：剪枝 INT4 FL2VA + INT4 Qwen + 原生 H3 T2VA + 视频/音频 VAE + SageAttention + LOW_VRAM 动态卸载；无 LoRA，20 steps，640×384，124 帧，24fps。
- 4 条视频均为 5.167 秒、H.264 + AAC，完整解码通过；端到端为 421.13～453.75 秒，平均 444.87 秒。
- 画面判断：远景桥体和人物运动连续；中景侧脸轮廓与服装连续；近景转脸后有轻微细节软化；“face”案例仍输出中远景，属于镜头控制失败而非五官质量结论。
- 运行注意：首条加载/初始化会明显慢；后续条目核心 20 steps 约 2 分钟，但 VAE、动态权重卸载和封装使端到端仍约 7～8 分钟。普通单次 `nvidia-smi` 可能读到 0% GPU，必须用连续 dmon/日志交叉判断，不能据此判假死。

### 14.5 2026-08-17 剪枝 INT8 对照与强特写

- 同一官方原生 T2VA 链路下，剪枝 INT8 FL2VA + INT4 Qwen、无 LoRA、20 steps、640×384、124 帧、24fps 四条均完整解码通过。
- 端到端耗时：远景 534.11 秒、中景 462.30 秒、近景 484.69 秒、强化特写 522.85 秒；首条含约 19,995MB staged 主权重的底座切换成本，后续仍有明显动态卸载开销。
- 强化 `face_strong` 提示使 INT8 真正形成脸部占屏特写，首/中/尾帧五官轮廓、眼鼻口比例和光照过渡保持，无整体崩坏。相同提示下剪枝 INT4 也能形成特写，端到端 181.29 秒，但更偏赛璐璐/插画，眼部和皮肤细节软化更明显。
- 当前排序不是“INT8 一定全面优于 INT4”：INT4 是低显存/低耗时档；INT8 是画质和面部细节优先档。两者都必须继续做 4/8 steps、LoRA、四画风和长时长验证；本轮没有把 20 steps 的无 LoRA 结果外推到 Turbo LoRA。

## 15. 尚未通过的生产级门禁

- 30/60/120 秒虽然真实生成成功，但还需对白 ASR、口型、最终混音、长片级角色/场景一致性人工门禁。
- 3DCG 长连续性已产出样本，但不同景别、快速转身、复杂手部和面部特写仍需更大矩阵。
- NVFP4/AWQ Qwen、UniBlockSwap、ReservedVRAM、Spectrum、独立 Motion Context 第二段需要在升级或更大内存环境重新单变量复测。
- Director 的自动场景切分依赖可选 `scenedetect`，当前共享 venv 尚未完成该可选依赖验收。
- H3 原生音频不可替代独立 TTS/对白混音。

## 15.1 2026-08-17 剪枝底座 T8/Turbo LoRA 兼容性烟测

完整证据见 [`test-runs/2026-08-17-h3-pruned-turbo-smoke/REPORT.md`](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-turbo-smoke/REPORT.md)。本轮必须区分“采样器可用”和“LoRA 适配可用”：

- 剪枝 INT4 + T8 DualClock、无 LoRA、4 steps、640×384、124 帧、5.167 秒真实出片，耗时 473.92 秒，`ffprobe` 和全量解码通过。剪枝底座与 T8 采样器本身可运行。
- 剪枝 INT4/INT8 + 通用 `LoraLoaderBypassModelOnly`、T8-convert LoRA，在 4/8 steps 均报 `mat1 and mat2 shapes cannot be multiplied (1x8 and 2688x16)`；这是剪枝时间曲线与通用 LoRA bypass 的维度不匹配，不能归因于 4/8 steps 或画质。
- 改用 H3 专用 `MiniMaxH3TurboLoRA` 后，当前 ComfyUI 包装器在 `_inject_adaln_egrid` 查找 `diffusion_model` 前缀时又报 `'MiniMaxH3Model' object has no attribute 'diffusion_model'`。当前节点注册成功但执行不兼容，不能把该组算作 LoRA 通过。
- 再用标准 `LoraLoaderModelOnly` 复测，剪枝 INT4/INT8 的 4/8 steps 均能生成并完整解码，端到端约 60.44～110.64 秒；但日志明确跳过所有 `adaln_proj` 的 `[*,8]` reshape，实际是主干 patch 成功、时间条件 LoRA 未完整注入的“部分 LoRA”链，不能写成完整 Turbo LoRA 已通过。
- 在同一标准 Loader 链补跑剪枝 INT4、4 steps、640×384、5.167 秒的四画风中景：3D CG 60.44 秒、真人写实 383.91 秒、国风水墨/线稿 362.16 秒、2D 动漫 352.11 秒；均完整解码。画面上 3D CG 环境细节较稳，真人未整体塌脸但仍偏插画，水墨线稿和 2D 动漫风格更明确。以上仍是部分 LoRA 探索链，不是完整 Turbo LoRA 结论。
- 画风首次切换期间远端 RSS 约 16～17GiB、GPU 约 10～11GiB、swap 约 12～13GiB，GPU 利用率可达 100%；任务串行可以完成，但必须把高 swap/换页记录为性能边界，不能据此判定假死。
- 非剪枝 INT8 的 T8 4/8 步历史成功不受影响；其结论不能外推给剪枝底座。剪枝 LoRA 需等待匹配当前 ComfyUI 模型包装器的节点/作者工作流，或做受控适配修复后重新验收。

本轮复现目录：

```text
/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-turbo-smoke/
/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-turbo-custom-lora/
```

控制组 MP4 的 SHA-256 为 `d11ea3fb81563a12cd4c2554e43c734336d5fe05c4177178723e0b70ea0cefc3`。标准 ModelOnly 追加结果在 `/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-17-h3-pruned-turbo-modelonly/`；后续剪枝画风/步数筛选可同时保留无 LoRA控制链和部分 LoRA探索链，但进入生产画质排名前必须重新通过“无 reshape 错误、LoRA injection 完整 + MP4 完整解码”三重门禁。

### 15.2 全量矩阵的补充门禁

后续所有底座和长视频测试统一增加以下字段：T2V/R2V 任务类型及参考输入数量、音频流和音画偏移、固定 seed 的重复运行、首次加载/缓存命中耗时、GPU/RSS/可用内存/swap/温度、分辨率升级后的 OOM 边界、首中尾帧人工标签、SHA-256 与完整解码结果。这样可以把“能生成 MP4”“结构稳定”“生产可用”分成三层，不因队列完成或页面显示成功而提前判定。

### 15.3 2026-08-17 GGUF Q4_K_M 真实门禁

- Q4 FL2VA T2V 使用独立 8192 端口、`--novram --cpu-vae --use-sage-attention`、Q4 Qwen、视频/音频 VAE、8 steps、640×384、124 帧、24fps，无 LoRA；真实生成 5.167 秒 H.264/AAC 视频，端到端 2683.57 秒（约 44 分 44 秒），`ffprobe` 与全量 `ffmpeg -f null -` 解码通过，SHA-256 为 `32c783cc36d0d73a7f563e8cebd1b0828bc01ced662a77d4a14569102f9b0283`。
- 首中尾帧人工检查显示人物脸型、服装、背包、废墟桥体和蓝绿色符文在短段内连续，未见整体塌脸；画面为偏写实 3D CG/插画质感。该结论只属于 Q4 T2V 8-step，不外推至 R2V、LoRA、长视频或高分辨率。
- 资源边界：H3 约 19.4GB 权重全部 offload，8 步扩散进度约 11 分 46 秒，累计读盘约 209GB；完整音视频后处理把总耗时拉到 2683.57 秒，显存约 2.7GB。该链可作为 12GB 卡的兼容性备用链，不适合作为生产速度底座。
- SageAttention 参数已启用，但当前 GGUF/H3 输入形状重复触发 `list indices must be integers or slices, not NoneType`，运行时自动回退 PyTorch attention；本轮不能记作 Sage 加速。
- Q4 REF2VA R2V 也完成了同参数参考图、音频和解码门禁：8 steps、640×384、124 帧、24fps、5.167 秒，端到端 2558.32 秒（42 分 38 秒），H.264/AAC 32000Hz 双声道，SHA-256 为 `b375e7b20bb3762b30d96dc72739ad49b4b1980a8a51006e3fff164369022a05`；输出位于 `test-runs/2026-08-17-h3-gguf-q4-baseline/outputs/gguf_q4_r2v_refmatch_8steps_640x384_5s.mp4`。
- R2V 的正确性必须看提示词与参考图是否一致：此前同组 2565.54 秒样本明确提示“成年男性”，因此是提示词-参考图冲突负控，不能拿来判定身份失败；改为女性角色匹配提示后，首/中/尾帧保留青蓝外袍、米色内衬、腰带、肩包、发簪和面部主体，近景没有整体塌脸。Q4 的结论仍是“可运行但极慢的备用链”，不进入全画风/全时长生产排名。

### 15.4 2026-08-17 剪枝 INT8 8 steps 景别与画风复测

- 证据目录：`test-runs/2026-08-16-h3-pruned-shot-matrix/`；同一无 LoRA 官方 T2VA 链、640×384、124 帧、24fps、5.167 秒目标。
- 剪枝 INT8 8 steps 四景别均通过完整媒体门禁：远景 `351.18s`、中景 `413.13s`、近景 `412.87s`、强特写 `423.52s`。强特写采用脸占约 60% 的独立提示，首/中/尾帧眼鼻嘴比例稳定；远景只评价构图和走位，不评价五官。
- 同一中景提示下的四画风也均通过：真人写实 `412.82s`、3D CG `413.13s`、国风水墨/线稿 `413.23s`、2D 动漫 `424.70s`。3D CG/真人仍偏插画质感，水墨和 2D 线条/赛璐璐关系较稳；真人本条主要是背/侧面，正脸需另做 face shot。
- 与剪枝 INT8 20 steps 已有的 `462.30～534.11s` 对照，8 steps 节省约 15%～35% 端到端时间，但不是线性按步数缩短，因为每条都要重复 staged/卸载约 20GB 主权重、Qwen CPU 负载、VAE 和视频封装。
- 本轮峰值显存约 `10.7～11.5GB`，RSS 约 `17～19GB`，swap 约 `12GB`；任务串行可以完成，模型切换期间 GPU 0% 但 CPU/I/O 持续工作不能判假死。剪枝 LoRA 仍未通过完整 injection 门禁，因此这些画风结论属于无 LoRA 底座筛选。

## 15.5 2026-08-17 剪枝 INT8 Motion Context 长连续性门禁

证据目录：`test-runs/2026-08-17-h3-pruned-long-continuity/`。这是 Director 10/15/30 秒之后的独立 Motion Context-only 对照，使用端口 `8192`，不与 Director 同进程加载。

- 固定链路：剪枝 INT8 FL2VA、INT4 Qwen、视频/音频 VAE、Sage/KJ patch、无 LoRA、640×384、24fps、8 steps、Euler/simple、`context_length=22`、`audio_context_length=24`。
- 首段采用 3DCG 三视图；第 2～7 段只加载上一段 AV latent。7 个原始段全部完成，首段 5.1667 秒、后续每段 4.25 秒，端到端耗时分别为 `501.36/461.67/483.20/493.65/482.47/482.28/484.26s`；每段都通过 `ffprobe`、完整 `ffmpeg -f null -` 和 SHA-256。
- 严格裁切后的 Motion Context-only 成片：10 秒 `786807930fc3c46b77a34dbcb862c6f420f20a75781dae3ef9b2541fa54d9f22`，15 秒 `5e194fe3d83397dc759d13645e0d4458b75efcedc8c131a02ec045c47b084885`，30 秒 `c2cc14b2d0458603f41f81db6ea53b407dff44d061196e846aab611e77663a0e`；均为 640×384、24fps、音视频精确同长并完整解码。
- 视觉结论：国风 3DCG 的远景、中景、近景、正面特写在接力中维持红色汉服、青色腰带、发饰、脸型与眼鼻嘴比例；本次特写稳定性证据强于本轮 Director 雨巷片。该组是无 LoRA底座门禁，不能记为剪枝 Turbo LoRA 已兼容。
- 性能边界：每段约 7.7～8.4 分钟，7 段总链约 56 分钟；主权重约 19,995MB staged，GPU 10.3～11.6GiB，RSS 16～18GiB，CPU 230～247%。模型换入期间 GPU 短时 0% 不等于假死；必须结合日志、CPU、I/O、队列和输出文件判断。
- 接力机制：日志确认每段 `loaded AV latent`、保存下一段 `clip_0000N.safetensors`，并将每段尾部裁切 267 samples（8.34ms）后报告 `drift 0.00ms`。这条音频对齐表现优于本轮 Director 的 `unexpected audio grid`/5 帧裁切警告。

生产默认：剪枝 INT8 采用 640×384 + 8 steps + Motion Context-only 作为当前连续性基线；Director 用作分段规划与人工选段入口。若要引入 LoRA、1000px 以上分辨率、真人/水墨/动漫画风，必须按同一证据结构重新做单变量门禁。

## 15.6 2026-08-17 剪枝 INT8 多画风长链扩展

真人写实长链已完成独立证据包：`test-runs/2026-08-17-h3-pruned-style-long/REPORT.md`。固定 `640×384 + 24fps + 8 steps + Euler/simple + 无 LoRA`，首段使用原生 H3 T2V，后续 6 段全部读取上一段 AV latent；7 段 H3 采样总耗时 2,983.96 秒，10/15/30 秒版本均通过 `ffprobe` 和完整解码，音画 drift 为 0ms。

这组真人链证明剪枝 INT8 的 Motion Context 接力可用，但未证明正脸特写门禁；它也没有使用三视图参考图，因此不能和 3DCG 三视图链混为同一实验。水墨与 2D 动漫仍必须用相同参数和独立 context 目录分别跑完后再判定。

同日扩展已完成：水墨 7 段总采样 3,117.83 秒，2D 动漫 7 段总采样 3,118.51 秒；三种画风的 10/15/30 秒导出都通过完整解码，Motion Context 音画 drift 为 0ms。水墨链通过画风生效与接力门禁；2D 动漫链还通过近景正脸的赛璐璐风格核验。当前三画风长链仍固定 640×384/8 steps/无 LoRA，不能外推到高分辨率、LoRA 或对白口型。

## 15.7 2026-08-17 剪枝 INT8 高分辨率门禁

剪枝 INT8 原生 T2VA、2D 动漫、8 steps、无 LoRA 的 5 秒门禁：832×480 耗时 522.33 秒通过；1088×608 耗时 684.34 秒通过。两条都通过 ffprobe、完整解码和抽帧，显存约 11GiB；1088×608 已是当前 RTX 3060 12GB 的可用高分辨率候选，但还没有扩展到高分辨率 Motion Context 长链、LoRA 或三视图。

## 15.8 2026-08-17 剪枝 INT8 LoRA 复测门禁

`LoraLoaderModelOnly + T8-convert LoRA` 在剪枝 INT8 上能够产出 640×384、4 steps、5.166667 秒 MP4，但日志明确出现多个 `adaln_proj.linear.weight` reshape 错误；因此标记为 `partial-injection-only`，不是完整 Turbo LoRA 生产链。T8 专用节点在当前 8192 隔离服务缺少 `MiniMaxH3AudioConditioningT8`，不能把缺少节点的工作流提交误当成 T8 通过。

## 16. 维护规则

每次新增测试必须同时更新：

1. 对应 `test-runs/<run>/report.md`；
2. 本文的参数/结论/失败边界；
3. `H3_PRODUCTION_HANDOFF.md` 的当前结论；
4. 离线资料台索引（若新增可审阅视频）；
5. Agent Atlas 的 finding/change/verification/checkpoint；
6. 桌面移交文档 `~/Desktop/H3.md`（若改变可复现链路）。

任何“尚未验证”的内容必须明确写成 pending，不得用历史日志、页面截图或模型文件存在替代真实运行证据。

## 17. 2026-08-17 生产模型门禁后的长视频插件专项

### 17.1 生产默认链

模型筛选完成后，当前 RTX 3060 12GB/32GB 的默认连续生产链固定为：剪枝 INT8 FL2VA、drbaph 剪枝专用 raw-key Turbo LoRA、INT4 ConvRot Qwen、原生视频/音频 VAE、SageAttention 参数、640×384/24fps。4 steps 用于预演，8 steps 用于正式短镜头；所有 LoRA 结论必须以日志中的真实 injection 计数为准。

专用剪枝 LoRA 的正确性条件：剪枝模型使用 `MiniMaxH3TurboLoRA`；不要用通用 `LoraLoaderBypassModelOnly` 硬套 T8-convert LoRA，也不要把只成功 patch 主干、跳过 `adaln_proj` 的结果当成完整 Turbo LoRA。

### 17.2 两个插件的职责边界

```text
Director：时间轴/分段/批量/局部重跑/生产编排
    ↓ 选择和组织片段
Motion Context：保存上一段 AV latent，承接视频和音频上下文
    ↓
H3 采样 + VAE + MP4/AAC
```

Director 15 秒样本：3 段、8 steps、15.291667 秒、1082.09 秒、完整解码通过；存在 `unexpected audio grid` 和 5 帧自动裁切记录。Motion Context 15 秒样本：4 段、4 steps、四段各约 296～298 秒，最终精确 15.000000 秒、音画 drift 0.00ms。当前推荐 Motion Context 做底层接力，Director 做上层编排；两者不能同进程同时 patch。

### 17.3 长期生产门禁

每个模型候选在进入长视频前，必须依次通过：

1. 5 秒 4 景别，首/中/尾帧和完整解码；
2. LoRA injection、模型文件、文本编码器、VAE、Sampler 和工作流路径核对；
3. 15 秒至少 3～4 段真实 latent 接力；
4. `ffprobe`、完整 `ffmpeg -f null -`、音画时长/漂移和接缝抽帧；
5. RAM、swap、显存、温度、CPU/I/O 与受保护服务核验；
6. 正面对白特写、口型、独立 TTS/混音和断点重跑门禁。

30 秒及以上只有在 15 秒通过后才进入，不能用独立片段拼接冒充上下文接力。

## 18. 2026-08-17 第一章 3DCG 试拍复用规则

证据报告：`test-runs/2026-08-17-h3-destiny-ch01-pilot/REPORT.md`。

当前选定底座在《命运模型》第一章开篇完成了两级门禁：

1. 5 秒原生 T2V：剪枝 INT8 FL2VA + INT4 Qwen + drbaph 剪枝专用 raw-key LoRA，`MiniMaxH3TurboLoRA` 日志确认真实注入，640×384/24fps/8 steps，`370.838s`。
2. 15 秒 Motion Context：四段真实 AV latent 接力，640×384/24fps/4 steps，`context_length=22`、`audio_context_length=24`，段耗时 `301.64/301.04/389.98/382.75s`，每段 `drift 0.00ms`；精确封装 SHA `e9e845e2a8de53b2ff64fac107bd039d6372257f7f15265e0dcbf4a726160f64`。

最终文件：`test-runs/2026-08-17-h3-destiny-ch01-pilot/output/final_destiny_ch01_motion_context_15s_640x384_exact15_v3.mp4`。它是 15.000000 秒、640×384、24fps、360 帧、H.264/AAC、完整解码通过；AAC 有效样本 14.976 秒属于封装帧边界，不能误报成 Motion Context 漂移。

### 长链目录隔离门禁

每个故事/角色/参数组必须有唯一 context 目录，SaveLatent 与 LoadLatent 必须逐段指向同一个目录并递增 `clip_index`。如果 LoadLatent 读到旧实验目录，立即中止、标记该段无效、保留日志但不得纳入画质或速度排名。该规则来自本轮一次已中止的错误链路。

### 实时端口复核门禁

旧资料中的端口只是历史配置，不是当前事实。2026-08-17 实测：`8188` 为 vanilla ComfyUI，`8189` 为 Boogu wrapper，`8192` 为 Motion Context 隔离 H3；正式 H3 端口必须以 `/object_info` 节点存在性和 `/proc/<pid>/cmdline` 为准。每轮前后都要核对 H3 队列、GPU PID、微信 Bot `8077` 和 Qwen `8090`，禁止按“端口号=服务角色”操作。

## 19. 2026-08-18 模型选定后的插件专项模板

证据模板：[H3 第一章插件公平 A/B 报告](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-18-h3-destiny-ch01-plugin-ab/REPORT.md)。模型筛选完成后，不再对所有模型做长视频笛卡尔积，而是把入选模型固定在同一剧情、同一提示词语义、同一分辨率和同一步数，分别跑：

1. 5 秒正面对白/正脸门禁，验证人物五官、口部运动、音频流和真实 LoRA injection；
2. Director 15 秒，记录分段、时间轴、音频 grid、自动裁切和可局部重跑性；
3. Motion Context 15 秒，逐段保存/加载 AV latent，记录每段时长、`drift`、断点目录、边界抽帧和精确封装。

当前选定底座为剪枝 INT8 FL2VA + drbaph 剪枝专用 raw-key Turbo LoRA + INT4 ConvRot Qwen + 原生视频/音频 VAE + SageAttention，RTX 3060 12GB/32GB 的默认交付档为 640×384/24fps、4 steps 预演、8 steps 正式短镜头。

当前 A/B 事实：5 秒正面对白 `346.88s` 通过；Director 15 秒 8 steps 为 `1094.20s`，但输出 `15.291667s` 且出现 `unexpected audio grid`/5 帧裁切；Motion Context 8 steps 四段为 `360.98/418.68/419.01/402.49s`，最终精确 `15.000000s`，每段 `drift 0.00ms`。因此默认架构是 Director 上层编排、Motion Context 下层接力，两个 patch 必须拆进程。

复用时必须保留以下门禁：每组故事/角色/参数创建唯一 latent 目录；每段前核对 `clip_index`、分辨率、latent 类型和加载路径；每段后做 `ffprobe`、全量 `ffmpeg -f null -`、接缝抽帧和音画时长检查；出现高 RSS、swap 或 GPU 低利用率时先看进程/日志/磁盘 I/O，不得立即判假死；结束后精确释放 H3 并验证微信 Bot `8077`、Qwen `8090` 未受影响。

## 20. 2026-08-18 选定模型后的高分辨率与长链分层

证据报告：`test-runs/2026-08-18-h3-destiny-ch01-hd-lora-gate/REPORT.md`。

长期生产底座不因分辨率测试改变：剪枝 INT8 FL2VA + drbaph 剪枝专用 raw-key Turbo LoRA + INT4 ConvRot Qwen + 原生视频/音频 VAE + SageAttention。高分辨率只作为同一底座的交付档位：

| 档位 | 参数 | 实测 | 用途 |
|---|---|---:|---|
| 批量默认 | 640×384，8 steps | 5 秒约 5～6 分钟；15 秒 Motion Context 已通过 | 预演和大多数镜头 |
| 常规精选 | 832×480，8 steps | 5.166667 秒，425.97 秒，完整解码通过 | 关键中近景 |
| 极限精选 | 1088×608，8 steps | 5.166667 秒，671.53 秒；15 秒四段合计 2762.02 秒 | 正脸、关键道具、片头片尾 |

1088×608 的 15 秒 Motion Context 由四段真实 AV latent 接力组成，最终 15.000000 秒、360 帧、音画 `drift 0.00ms`、完整解码通过。代价是 RSS 约 16～19GiB、最低可用内存约 1.8GiB、swap 约 12～13GiB；这属于技术可行但吞吐不适合批量的极限档。看到 GPU 短时 0% 时，先查 H3 日志、模型读写和 `vmstat`，不得把 staging/换页误判为假死。

每次生产任务先用 640×384 完成动作、身份和对白门禁；只有通过后才把同一工作流的宽高切换为 832×480 或 1088×608。分辨率升级不得同时更换 LoRA、Sampler、提示词和上下文目录，否则无法归因。高分辨率长链仍需保留唯一 latent 目录、递增 `clip_index`、逐段 SHA/ffprobe/完整解码和接缝抽帧规则。

## 21. 2026-08-18 独立音频生产链

证据报告：`test-runs/2026-08-18-h3-destiny-ch01-audio-gate/REPORT.md`。

视频模型和音频模型必须拆开验收：

```text
H3 T2V/T2VA + LoRA + Motion Context
        ↓ 画面/可选氛围音
CosyVoice3 TTS（角色对白/旁白）
        ↓ 独立对白轨
环境声/SFX 素材
        ↓
FFmpeg/Remotion 混音、响度、封装
        ↓
Whisper ASR + 人工抽听/抽帧
```

最低门禁：TTS HTTP 200、WAV 解码、文本 ASR、音频替换后 ffprobe、完整 `ffmpeg -f null -`、音画同长。H3 原生音频含有模型自行生成的对白时，不能把它当干净环境床；应使用独立环境声或只交付 TTS voice-only 版本。

口型需要独立分类：

1. H3 提示词中的“自然口部运动”只证明模型尝试生成口型；
2. TTS 替换只证明后期声音可接入，不自动保证逐音素同步；
3. 生产对白镜头须记录 TTS 起始时间、口部张合抽帧，必要时使用口型驱动或重新生成；
4. ASR 对两三个字的短台词不稳定，必须保留输入文本、音频 SHA 和人工听审，不能只看 ASR 单项结果。

当前通过样本：1088×608 正脸 5 秒独立对白替换、1088×608 Motion Context 15 秒独立 TTS 混音；后续整集生产沿用“先画面、后对白、再环境声/SFX、最后混音”的顺序。

## v1.42 三视图参考链专项结论（2026-08-17）

证据报告：`test-runs/2026-08-17-h3-reference-audio-gate/REPORT.md`。

本轮固定使用剪枝 INT8 FL2VA + INT4 ConvRot Qwen + drbaph 剪枝专用 raw-key Turbo LoRA + 原生视频/音频 VAE + SageAttention，640×384、24fps、8 steps。参考链不再把三视图当作每一段的持续画面条件：首段 R2V 注入四张参考图，后续段用 Motion Context 的 AV latent 接力。

| 链路 | 真实成本 | 画面结论 | 可复用定位 |
|---|---:|---|---|
| Director 15 秒、每段重复四图 | 1400.49 秒 | 前 10 秒可用，约 12.5 秒参考板泄漏 | 上层编排/预览，不作为本轮默认长片链 |
| Motion Context 15 秒、首段四图后 latent | 1909.64 秒 | 无参考板泄漏，远→中→侧→近景连续，近景五官稳定 | 默认长视频底层接力 |

生产工作流固定为：

1. Director 规划叙事单元和镜头，但把每段视觉提示词写成静默表演、无字幕、无可读文字；
2. 首段使用人物前/侧/后/脸部参考包生成 5 秒级 R2V；
3. 后续段不重复喂参考板，使用 `MiniMaxH3MotionContextLoadLatent` → `MiniMaxH3MotionContext` → `MiniMaxH3MotionContextTrim`；
4. 每段保存唯一 `clip_index`，记录工作流、prompt id、真实耗时、上一段 latent、帧数、drift 和接缝抽帧；
5. 最终接入 CosyVoice3、独立环境声/SFX、FFmpeg/Remotion；ASR 服务或本地权重缺失时必须显式标为未完成，不能把 TTS 输入文本当 ASR 证据。

已验证边界：640×384/8 steps 在 3060 12GB/32GB RAM 上可完成 15 秒参考链；H3 进程峰值约 10.2–11.5GB VRAM、28–29GiB RAM，swap 约 11–12GiB。832×480/1088×608 仍需精选镜头复测，不能直接外推到整剧。

## v1.43 平台 Provider 真实 T2V 入口验收（2026-08-17）

证据报告：`test-runs/2026-08-17-h3-provider-t2v-smoke/REPORT.md`。

选定底座已从离线模型/插件评测推进到平台真实 Provider Job：

- 入口：`ComfyUIVideoProvider`，工作流 `workflows/h3-production/h3-t2v-640x384-8steps.json`；
- 参数：剪枝 INT8 + INT4 Qwen + drbaph 剪枝专用 raw-key Turbo LoRA，640×384、124 帧、24fps、8 steps、T2VA 原生音频；
- 结果：端到端 `316.132s`，输出 `5.167000s`，H.264/AAC，完整 `ffmpeg -f null -` 解码通过；
- 真实性：队列中保留业务提示词，日志出现 `BypassForwardHook => lora ACTIVE`、208 backbone modules、158 bypass adapters、1 injection，并确认 `Using sage attention`；
- 边界：本次只验收平台 T2V 入口，不替代 Director/Motion Context 15 秒专项；临时 `8192` 验收后关闭，保护服务未受影响。

长期生产链因此固定为：平台任务层 → Director 编排 → 首段 R2V/三视图参考 → Motion Context AV latent 接力 → CosyVoice3/TTS、环境声/SFX 和后期混音。模型评测阶段收口，后续只在该底座上做生产专项和失败恢复。

## v1.41 选定底座 3DCG Director / Motion Context 公平复测（2026-08-17）

证据报告：`test-runs/2026-08-17-h3-selected-3dcg-15s/REPORT.md`。

固定底座为剪枝 INT8 FL2VA + drbaph 剪枝专用 raw-key Turbo LoRA + INT4 ConvRot Qwen + 原生视频/音频 VAE + SageAttention，640×384、24fps、8 steps。严格 T2V、无三视图/首帧参考，用同一 3DCG 桥面提示词做公平 A/B：

- Director：3 段自动编排，`1091.79s`；raw `15.291667s`，精确交付 `15.000000s`，360 帧，完整解码通过，SHA `878860f0533d0e091d64992317682a648afdac5e13e924af11702f14d509e072`。远中景稳定，后段正脸/三分之二侧脸可审阅；有眼镜配饰漂移与 audio-grid/5 帧裁切告警。
- Motion Context：4 段 AV latent 接力，`330.64/411.66/421.72/422.01s`，合计 `1586.03s`；后续段 `drift 0.00ms`，精确交付 `15.000000s`，完整解码通过，SHA `f39687400bf51064733f5de90ffb0e1bf3f10b65a35bedbcce17cb68d642ca6c`。桥面/光向/背包连续，但景别推进弱，末帧有字幕样文字伪影。

可复用决策：Director 负责时间轴、景别、对白/表情镜头和局部重跑；Motion Context 负责 AV latent 连续、断点恢复和底层回退。生产提示词不得把长对白当作视觉生成指令，改用静默表演/无字幕视觉提示词，对白由 CosyVoice3、独立 SFX/环境声和后期混音完成。下一门禁是参考图/三视图链，不再对未选模型做长视频全组合测试。

## v1.2 平台长视频任务编排（2026-08-17）

平台已增加一条可恢复的长视频任务合同：

```text
POST /api/episodes/{episode_id}/long-video
  -> Director 分段计划（第 1 段）
  -> Motion Context AV latent 接力（第 2 段起）
  -> 每段状态/耗时/媒体证据/latent 引用落盘
  -> FFmpeg 合并最终 MP4
```

实现位置：`studio_core/long_video.py` 负责计划门禁；`studio_api/service.py`
把每段状态写回现有 `jobs.payload_json`；`studio_api/providers.py` 的
`generate_segment` 使用两个独立 API-format workflow 文件；`studio_api/main.py`
提供提交和状态查询 API。任务沿用幂等键、积分预留、失败退款、显式 retry 与
stale-worker requeue，完成段不会在重试中重新采样。

首段模板采用 `MiniMaxH3ImageToVideo` + `SamplerCustomAdvanced`，并把采样器
原始 AV latent 交给 `MiniMaxH3MotionContextSaveLatent`。Director 在平台层负责
时间轴和首段角色，不能用 Director 已解码的 IMAGE/AUDIO 输出替代可继续采样的
AV latent；旧的 Director UI-only 模板只保留为单段编排参考。

Motion Context 的 `context_length` 是节点枚举字符串（例如 `"22"`），而
`audio_context_length`、`clip_index` 是整数；Provider 必须按节点 schema 分别
替换，不能对所有数值占位符统一保留 Python 整数类型。

配置：

```dotenv
COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW=/path/to/h3-director-first-segment-av-latent-640x384-8steps.json
COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW=/path/to/h3-motion-context-segment-api.json
```

两个变量未配置时，普通单段 T2V/R2V 不受影响；长视频 Job 会明确失败而不
偷偷降级成互不连续的独立视频。真实生产仍须挂载隔离 H3 端口中已验证的
Director/Motion Context workflow，并检查 latent 文件确实落盘；local preview
测试只证明任务恢复和媒体合同，不能替代 GPU 画质验收。

## v1.3 平台可恢复长视频真实接力通过（2026-08-17）

证据报告：`test-runs/2026-08-17-h3-platform-long-video-15s-real-gpu-v3/REPORT.md`。

项目平台在隔离 H3 `8192` 上完成真实 4 段长链：第 1 段由 Director 计划层驱动核心 `MiniMaxH3ImageToVideo` 并保存 AV latent，第 2～4 段分别加载前一段 `clip_index` 对应 latent，经 Motion Context 采样后保存下一段。端到端墙钟 `1587.236s`（约 26 分 27 秒），最终 `15.000000s`、640×384、24fps、H.264/AAC，完整解码通过，SHA-256 `fd34134156479415c4b3f32fdb5881c59b6acd10207d0ea8a23a35cfb0e2df48`。

关键适配门禁：Motion Context `context_length` 是字符串枚举；LoadLatent 必须传保存目录 `.../context` 和整数 `clip_index`，不能传不存在的 `.../context/clip` 前缀。Provider 已规范化旧前缀、具体 safetensors 文件和目录三种输入，并在长视频 metadata 中保存远端 context 目录引用。

## v1.4 多参考图 R2V 生产门禁（2026-08-17）

平台层新增 `reference_asset_ids`，把四张 ready image 参考图纳入长视频 Job 合同。首段工作流必须使用 `MiniMaxH3ReferenceToVideo`，通过 Provider 的 `REFERENCE_IMAGE_n_REF` 占位符逐张上传；后续段只使用上一段 AV latent，禁止每段重新注入白底三视图。参考图数量由 workflow 中实际出现的占位符决定，服务端上限为 8 张，并校验资产归属、kind 和 ready 状态。

真实门禁：选定剪枝 INT8 FL2VA + INT4 Qwen + drbaph raw-key LoRA，640×384、24fps、8 steps、首段 R2V + 3 段 Motion Context，端到端 `1784.481s`，最终精确 15 秒，SHA-256 `28512e3f48417524ac482ec15016c50c30bcf188207a1919a995249eba472543`。四张参考图没有泄漏成参考板，近景脸部可审阅；H3 原生音频只作为氛围层，独立对白仍走 CosyVoice3 和后期混音。

复现入口：

```bash
PYTHONPATH=/Volumes/AJW-Data/Projects/novel-to-comic-engine \
H3_COMFYUI_BASE_URL=http://192.168.1.6:8192 \
python3 /Volumes/AJW-Data/Projects/novel-to-comic-engine/scripts/run_h3_platform_reference_long_video_real.py
```

该命令只能在已经通过端口、节点、模型路径和保护服务 preflight 后运行；结束后停止任务专用 8192 进程并核对 GPU 回落，不能停止微信 Bot/Qwen/CosyVoice/正式 ComfyUI。

## v1.5 独立对白与混音门禁（2026-08-17）

在 `test-runs/2026-08-17-h3-platform-reference-long-video-15s-real-gpu/` 的真实平台 R2V + Motion Context 15 秒成片上，复用常驻 Linux CosyVoice3 `127.0.0.1:8789` 生成短对白 `这座桥，为什么还在呼吸……`，再用 FFmpeg 产出两份交付候选：

1. 保留 H3 原生音频床并降到约 0.22 线性音量，TTS 延迟 8.5 秒后混入；
2. 去除 H3 原生音频，只保留同一条延迟对白。

两份均为 640×384、15.000 秒、AAC 32kHz/2ch，完整 `ffmpeg -map 0:v:0 -map 0:a:0 -f null -` 解码通过。WAV 为 24kHz/单声道/2.880 秒，TTS HTTP 200。当前 `8790` SenseVoice 和 Whisper small 权重未就绪，不能把输入文本当成 ASR 结果；该门禁证明的是音频生产与封装，不是逐音素口型同步。

复用规则：先完成 H3 视觉长链和视频门禁，再接入 TTS/环境声/SFX；不得为验证对白而重跑已经通过的视频；所有音频 A/B 都要保留 WAV、ffprobe、SHA、完整解码证据，并在报告中明确 ASR/口型边界。

## v1.6 局部重跑与失败恢复（2026-08-17）

服务层回归用例 `tests.test_long_video.LongVideoPlanTests.test_failed_context_segment_retries_without_regenerating_completed_segment` 已通过，故障注入后的调用序列为 `[1, 2, 2]`：第 1 段成功、第 2 段首次失败，retry 只重新执行第 2 段。真实 GPU Job 的四段状态和 context 目录引用也已复核。

生产 SOP：

1. 失败后先冻结该 Job 的 `payload_json`、分段 workflow、prompt、`clip_index`、context latent、H3 日志和 GPU/RAM/swap 采样。
2. 检查前一段 latent 文件、分辨率、帧数、`context_length` 和 `audio_context_length`；任何一项不一致都不能直接 retry。
3. 契约完整时只重跑失败段，完成段禁止重新采样；重跑后重新做 MP4、ffprobe、完整解码、SHA 和接缝抽帧。
4. 失败恢复门禁证明的是任务可靠性，不替代人物脸部/动作质量审片；证据目录必须和该 Job 绑定。

## v1.7 ClipProj / EasyCache 低显存组件门禁（2026-08-18）

组件清单：`test-runs/2026-08-18-h3-clipproj-easycache-gate/DOWNLOAD_MANIFEST.md`；完整复盘：`docs/H3_PRODUCTION_CYCLE_REVIEW_2026-08-18.md`。

### 下载源与资产门禁

先查 ModelScope/国内可信镜像；4B FP8 从 ModelScope 命中并采用。ClipProj 节点、ClipProj 矩阵、EasyCache upstream、SolAttn 在国内源没有对应可核验命中，记录 `OVERSEAS_FALLBACK` 后使用官方 GitHub/HF mirror。每一个模型和矩阵必须记录绝对路径、尺寸、SHA-256；HF mirror 跳海外 CDN 仍算海外源。4B/8B encoder 与 matrix 严格配对。

### 3060 默认验证链

`CLIPLoader(type=krea2) → ClipProj Apply → H3 clip`，使用 Qwen3-VL-4B FP8 + v3.1 MLP；保留剪枝 INT8 H3、匹配 Turbo LoRA、SageAttention 和低显存启动项。单卡不要使用会并存加载多个大 TE 的 all-in-one loader。先做 640×384、5 秒、8 steps 的真实采样，再决定是否接入长链。

### EasyCache / SolAttn 隔离规则

EasyCache 每次必须保存 `skipped x/y`、threshold、start、end 和完整媒体证据；本机 H3 20 steps 的 0.05 档跳过 0/20，0.20 档跳过 2/20，不得套用论文 2～3×。SolAttn 只作为独立实验，不能与 EasyCache、其他 attention patch 同时首测；RTX 3060 不因节点可加载就视为兼容。

### 完成和停止

短片门禁必须有 MP4、ffprobe、完整解码、抽帧、SHA、workflow、日志和资源快照。新组件完成 5 秒门禁后，若本轮目标是复盘，则停止任务专用 8192 进程，核对微信 Bot/Qwen/正式 ComfyUI/CosyVoice3 仍存活、GPU 回落，再结束任务；不能把下载、节点注册或 HTTP 200 当作生产通过。

## v1.8 ClipProj 4B/8B 与长链复测（2026-08-19）

证据：[test-runs/2026-08-19-h3-clipproj-8b-long-gate/REPORT.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-19-h3-clipproj-8b-long-gate/REPORT.md)。

可复用的模型选择顺序：先用 4B FP8 + v3.1 MLP 做 640×384/8 steps 5 秒门禁；再用 8B NVFP4/FP8 的 ridge 与 MLP 各做一次同 seed 短片；只有实际 MP4、ffprobe、完整解码、抽帧和资源快照都通过，才允许把候选接入长链。8B FP8 + MLP 本轮完成了 15 秒链，但由于文本编码器约 10.1GB、系统可用内存最低约 2.1GB，只能作为高压质量档。

15 秒长链固定为：Director 首段四图参考 R2V → 保存 `context/clip_00001.safetensors` → Motion Context 只接上一段 AV latent → 每段保存下一个 clip → 最终封装。统一记录每段耗时、prompt id、workflow、clip index、latent 路径、内存/zram/GPU、MP4 SHA 和接缝抽帧。四段本轮耗时 `260.08/151.89/158.27/152.77s`，最终精确 15 秒。

隔离启动必须先做节点白名单门禁：如果使用 `--disable-all-custom-nodes`，至少放行 `ComfyUI-ClipProj`、匹配的 H3/LoRA 节点、`ComfyUI-H3-Motion-Context`、`ComfyUI_MiniMaxH3_Director_AIMixer`、VHS 及 workflow 实际依赖；随后用 `/object_info` 确认 `MiniMaxH3MotionContextSaveLatent/LoadLatent/Trim` 均注册。缺节点的 `missing_node_type` 属于部署失败，不属于模型失败。

EasyCache 仍按本机日志判定，不得把论文或社区宣传速度写入 SLA；本机 H3 0.05 阈值跳步 0/20，0.20 阈值仅 1/20。SolAttn、EasyCache 和其他 attention patch 必须拆成独立 A/B，不能一次叠加后无法归因。
