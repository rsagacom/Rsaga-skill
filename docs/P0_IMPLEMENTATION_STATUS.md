# P0 控制平面实施状态

日期：2026-08-19
实施者：dsh / codex

## 已实现（代码真实存在且测试通过）

| 交付物 | 位置 | 验证 |
|---|---|---|
| Canon 唯一事实源四件套 | `canon/characters.yaml` `world.yaml` `style.yaml` `episode_map.yaml` | YAML 加载 + 影响引擎读取 |
| 《命运模型》第一章 canon 实例 | 同上（CHAR_001 祁元思远、CHAR_002 黑衣女子、SCENE_012 石桥） | 12 项 unittest |
| 资产身份证 schema | `schemas/ASSET_MANIFEST.schema.json` | 结构已定，未做 jsonschema 运行时校验（package 未装） |
| 镜头级 ShotPlan schema | `schemas/SHOTLIST.schema.json` | 同上 |
| Prompt 预设库 | `canon/PROMPT_PRESET.library.yaml` | YAML 可加载 |
| 模型注册表 | `canon/MODEL_REGISTRY.md` | 内容以 H3 交接文档真实证据为准 |
| 影响分析引擎 | `studio_core/impact.py` | `tests/test_impact_engine.py` 7 项通过 |
| Scene / NarrativeUnit 领域对象 | `studio_core/models.py` 追加（旧 dataclass 未改） | import + to_primitive 验证 |
| 章节→分集→单元→场景→镜头闭环组装 | `studio_core/pipeline.py` | `tests/test_p0_control_plane.py` 通过 |
| OTIO 时间线导出/导入基础 | `studio_core/timeline.py`（纯 stdlib，未引入 opentimelineio 包） | 往返/相对路径/非 Timeline 拒绝，3 项测试通过 |
| 叙事单元/场景 PG 迁移 | `migrations/018_narrative_units_scenes_postgresql.sql` | 未在真实 PG 执行（仅文件） |

## 已实现但未连入生产服务

- 影响引擎：纯文件级（canon yaml + SHOTLIST），不与 DB 联动。

## 仅 schema / 设计（未实现）

- `QC_CHECKLIST.md` / `DEFECT_TAXONOMY.md`（P2）
- `RIGHTS_AND_LICENSES.md` / 内容政策（P2）
- `GOLDEN_SHOTS/`（P2）
- Animatic 工作台（P1）
- RenderJob 持久化（现有 jobs 表可承载，工作流 registry 需扩展）

## 回归

- `tests/test_studio_core.py` + `tests/test_workflow_contract.py`：10 项通过（model 追加无破坏）。
- 未改动 studio_api、web、orchestrator 任何用户代码；git worktree 中的用户改动未触碰。


# P1 实施状态（2026-08-18 追加）

## 已实现

| 交付物 | 位置 | 验证 |
|---|---|---|
| 角色资产包契约 | `studio_core/assets.py`：CharacterDNA、AssetPack、四值判定（pass/warn/fail/unknown）、三视图/表情/姿态/服装槽位 | 6 项 unittest |
| RenderJob 合同 | `studio_core/render.py`：缓存键（15 成分哈希）、失败分类（12 类+UNKNOWN）、按失败类别的重试策略 | 5 项 unittest（含缓存键逐字段失效验证） |
| Workflow Registry | `studio_core/registry.py`：从 production-profile 加载并结构校验 | 门禁报告：4 个 workflow valid、0 missing、0 invalid |
| Animatic 节奏检查 | `studio_core/animatic.py`：对白密度/镜头时长/轴线跳切/连续性链/声音硬切 | 4 项 unittest + 真实 SHOTLIST 预审 0 findings |

## 真实数据验证

- 《命运模型》第一章 SHOTLIST（4 镜）Animatic 预审：0 fails / 0 warns。
- `workflows/h3-production/production-profile.json` 登记门禁：全部 4 个 workflow 文件存在且结构合同通过。
- CharacterDNA 无资产生成合同判定为 `fail`（保守拒绝进入正式视频任务）。

## 边界

- CharacterDNA 的视觉评分维度未实现（P2 一致性引擎范围，当前结构化规则只判 fail/warn/unknown）。
- RenderJob 是数据合同与策略层，尚未接入 BullMQ worker 调度（service 层 P1 后续）。
- Animatic 是规则级预审，不含画面视觉检查。


# P1 收尾 + P2 启动状态（2026-08-19）

## 已实现

| 交付物 | 位置 | 要点 |
|---|---|---|
| 音频链合同 | `studio_core/audio.py` | VoiceCasting（音色授权+音高/语速）、DialogueCue（时码/情绪/重音/ASR核验）、AudioTrack（独立可替换音轨+dip）、SubtitleCue（原文/终稿/说话人）、MixSpec（LUFS/峰门禁）、audio_gate 结构门禁 |
| 镜头候选采用 | `studio_core/adoption.py` | 蓝图不变量1：同 kind 单候选 adopted；adopt 使旧候选 superseded；locked 阻塞再采用；不变量可显式审计 |
| 自动 QC | `studio_core/qc.py` | MediaEvidence 驱动：黑帧/静帧/静音/削波/时长合同/音画偏移；证据缺失 fail-closed；视觉类显式 unknown 不伪造 |
| 迁移 gate | `migrations/018` + `scripts/postgres_schema_check.py` 登记 | narrative_units/scenes 列入 REQUIRED_TABLES/COLUMNS/INDEXES |
| P1 服务层闭环 | `studio_api/store.py`、`studio_api/service.py`、`studio_api/main.py` | SQLite 表、用户隔离 CRUD、分集详情回显和 Scene/NarrativeUnit API；复用现有 FastAPI/Pydantic，不新增基础框架 |
| P1 音频合同接入 | `studio_api/service.py` 的 `audio_contract` | composition settings 映射到 `studio_core.audio.audio_gate`；只返回 structure-only 状态，不伪造真实 TTS/ASR、响度或听审结果 |
| P2 一致性视觉 adapter | `studio_core/consistency.py`、`docs/CONSISTENCY_ENGINE.md` | face/costume/scene/pose 输出契约；本地/fake/异常 provider 只能 unknown；有 evidence 的全维度 pass 才可 ready_for_adoption |
| P2 GOLDEN_SHOTS 回归库 | `GOLDEN_SHOTS/manifest.json`、`schemas/GOLDEN_SHOTS.schema.json`、`studio_core/golden_shots.py` | 3 条已有真实 H3 成片；SHA-256、ffprobe、完整 ffmpeg 解码和人工审阅依据齐全；不把回归样本冒充视觉质量自动通过 |

## 验证

- 新增 30 项测试全过（含本轮 GOLDEN_SHOTS 合同 5 项）。
- 全量 `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`：**433 项全过**（含既有 428 项零回归）。
- 真实执行 `.venv/bin/python scripts/verify_golden_shots.py`：3/3 样本通过 SHA-256、ffprobe 元数据和完整 ffmpeg 解码。

## 本轮开发框架复用门禁

- 本轮没有新增模型、插件、媒体引擎或队列依赖，因此没有重复实现 FFmpeg、OTIO、ComfyUI、Remotion、TTS/ASR 等成熟底座。
- 服务层只负责产品数据合同、权限边界和 adapter；媒体编解码/混音继续交给 FFmpeg/Remotion，生成工作流继续交给 ComfyUI/provider，TTS/ASR 继续走 CosyVoice/Whisper adapter。
- 未来新增组件必须先查本机/国内官方源或可信镜像，记录 Atlas `source-preflight`、许可证、版本/commit、哈希和真实加载/运行验证后才能接入。
