# 小说转漫画引擎（v1.0 — 2026-06-17）

一款从小说/文本创作延伸到影视分镜、人物资产、AI 视频、音频、时间线和交付的 AI 影视创作工厂；保留漫画/漫剧作为一种输出形态。支持小说逐句解析、AI 分镜生成、多模态视觉审核、古籍竖排对话框、自动排版输出 PDF。

**适用场景**：将中文长篇小说章节转换为东亚漫画风格（黑白、水墨、B5判）的分镜稿。

> AI 影视创作工厂的总产品蓝图见 [`docs/AI_FILM_FACTORY_BLUEPRINT.md`](docs/AI_FILM_FACTORY_BLUEPRINT.md)；漫剧/H3 生产子蓝图与当前 Web/API 实现见 [`docs/AI_MANHUA_STUDIO_BLUEPRINT.md`](docs/AI_MANHUA_STUDIO_BLUEPRINT.md)。MiniMax H3 的框架无关运行、长视频接力、显存门禁、复现命令和跨 Agent 交接见 [`docs/H3_PRODUCTION_HANDOFF.md`](docs/H3_PRODUCTION_HANDOFF.md)；项目 Agent 入口见 [`AGENTS.md`](AGENTS.md)。

---

## 特性

- **1句=1格逐句解析**：不预设分镜数量，每句原文独立成格
- **DS v4 Pro + Kimi K2.7 双模型分工**：文字剧情（改编层）→ DS v4 Pro，画面描述（分镜）+ 配文排版 → Kimi K2.7
- **角色唯一性控制**：`{Q}/{S}` 固定模板嵌入每个 prompt
- **情绪连续性**：相邻画格角色情绪渐变，prompt 显式标注
- **回忆场景年龄**：闪回场景自动标注角色年龄
- **古籍竖排对话框**：从上往下读，从右往左列，黑框透明底
- **多模态视觉审核**：Step 3.7 Flash / Kimi 识图
- **自动修复问题画格**：根据审核结果自动重新生成
- **漫画排版输出**：B5判 @ 150dpi，手绘边框、竖排对话框、旁白条
- **Claude Code Skill 集成**：通过自然语言交互完成分镜规划和修改

---

## 目录结构

```
novel-to-comic-engine/
├── README.md
├── config.yaml.example          # 配置模板
├── comic_engine/                # 核心引擎库
│   ├── config.py
│   ├── generator.py             # 分镜生成
│   ├── auditor.py               # 视觉审核
│   ├── layout.py                # 排版引擎
│   └── utils.py
├── scripts/                     # CLI 脚本入口
│   ├── generate_panels.py
│   ├── audit_panels.py
│   ├── fix_panel.py
│   ├── layout_chapter.py
│   └── kimi-vision.sh
├── skills/claude-code/          # Claude Code skill 注册
│   └── skill.json
├── templates/                   # LLM 提示词模板
│   └── system-prompt.md
└── projects/example/            # 示例项目输出目录
```

---

## 30 秒示例

```bash
# 1. 初始化项目
python3 scripts/init_project.py my-novel

# 2. 粘贴小说到 projects/my-novel/chapter_01.md

# 3. 在 Claude Code 中说：
#    「读取 projects/my-novel/chapter_01.md，生成改编层和分镜脚本」

# 4. CC 生成 storyboard 后说「开始生成画格」

# 5. 生成完毕后说「排版第一章」→ 输出 PDF
```

## 平台工作台开发入口

```bash
# API（默认 SQLite + 本地预览 provider）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_api.py

# Web（另一个终端）
cd web && npm install && npm run dev

# queued Job 的单机 worker（可选）
.venv/bin/python -m studio_api.worker --runtime-dir runtime
```

开发服务器与 `npm run build` 不要并行运行（两者共用 `web/.next`）；做生产构建回归时先停掉 `next dev`，构建完成后再启动开发服务器。

具备 Docker/Podman 时，也可以用独立的本地预览栈一键启动 API + Web + 持久化本地 worker；它使用 SQLite、本地预览 Provider 和独立命名卷，不会启动 PostgreSQL、Redis、MinIO 或生产配置：

```bash
docker compose -f infra/docker-compose.dev.yml up --build
```

本地预览栈访问 `http://localhost:3000`，停止时使用 `docker compose -f infra/docker-compose.dev.yml down`；生产仍必须使用 `infra/docker-compose.yml`，不能把 dev 文件当作生产部署合同。

平台蓝图、API 路由、账户隔离、小说改编审计、旁白/TTS、PostgreSQL 迁移和 BullMQ 编排说明见 [`docs/AI_MANHUA_STUDIO_BLUEPRINT.md`](docs/AI_MANHUA_STUDIO_BLUEPRINT.md)。具备 Docker 时可复制 `infra/.env.example` 为 `infra/.env`，再用 `docker compose --env-file infra/.env -f infra/docker-compose.yml up --build -d` 启动 PostgreSQL、Redis、MinIO、独立 migration job、schema gate、FastAPI、BullMQ server/worker 和 Next.js 全栈；migration job 会在 API 前执行，已有 PostgreSQL 数据卷也会按顺序补齐迁移，并在 `studio_schema_migrations` 中记录文件校验和以阻止静默改写已应用迁移；仅开发 API 时仍可直接运行 SQLite。
PostgreSQL 已有数据卷或手工部署可用 `.venv/bin/python scripts/postgres_schema_check.py --check-only --data-smoke --json` 做脱敏结构和数据合同验收；数据 smoke 在事务中执行后回滚，只输出 host:port、结构缺口、迁移计数和检查结果，不输出 DSN 凭据或业务数据。Compose 会先运行 `config-check`；默认的 `change-me-local-only`、本地内部 token、SQLite/local queue/local storage、关闭认证或 Host/CORS 通配符会在依赖启动前失败，必须由 secret manager/部署环境注入生产值。
生产启动与分层验收步骤见 [`docs/PRODUCTION_RUNBOOK.md`](docs/PRODUCTION_RUNBOOK.md)。
Wan2.2/LTX 的 ComfyUI 视频基准入口是 `scripts/benchmark_video_provider.py`；默认 dry-run，显式 `--run` 才会触发真实 GPU 工作流。图生视频 workflow 使用 `{{IMAGE_REF}}` 时，API/benchmark 会先把采用的关键帧上传到 ComfyUI `/upload/image`，真实 benchmark 需追加 `--source-image /path/to/keyframe.png`。图片/视频 workflow 还必须符合 API-format 节点结构（顶层节点包含 `class_type` 与 `inputs`），图片角色必须消费 `{{PROMPT}}`，视频角色必须消费资产/镜头身份占位符，未知占位符会被拒绝；生产 config gate、Provider smoke 和 benchmark 共用同一校验器。

文本/视觉/图片/视频 Provider 的统一验收入口是 `scripts/provider_smoke.py`；默认只做脱敏配置检查，显式 `--run` 才访问外部 Provider，不创建项目、不扣积分、不把 API key 或原始视觉响应写入报告。例如：`.venv/bin/python scripts/provider_smoke.py --kind text --run`、`.venv/bin/python scripts/provider_smoke.py --kind vision --image /path/to/keyframe.png --run`。旁白使用 `STUDIO_SPEECH_PROVIDER=local|openai`，生产必须配置 `STUDIO_SPEECH_BASE_URL`、`STUDIO_SPEECH_MODEL`、`STUDIO_SPEECH_API_KEY_ENV=OPENAI_API_KEY` 和 secret manager 注入的 `OPENAI_API_KEY`；默认只允许 OpenAI Audio API 内置音色，`STUDIO_SPEECH_ALLOWED_VOICES` 只能收窄内置集合，不能添加自定义 voice，`GET /api/health` 会向 Web 暴露当前生效的非敏感列表，Web 的“生成旁白”通过下拉选择而不是自由输入，并提供语速预设和最多 1000 字表达指令。超过单次 Provider 输入上限的长文本会在服务端分段并合并成一个音频资产，同时按文本长度回填可人工修订的估算字幕（已有人工字幕不覆盖），并明确提示真实 TTS 是 AI 生成语音。生产验收顺序和失败边界见 `docs/PRODUCTION_RUNBOOK.md`。
视频 benchmark 报告只保留 `provider`/`source` 白名单短标签、耗时、输出大小和 ffprobe 合同；Provider 原始 metadata 与异常正文不会写入报告，避免远端文件名、路径或厂商响应泄露。
Web 时间线支持按分集编辑并保存旁白稿；稿件写入 `composition_settings.narration_text`，刷新、项目 JSON/ZIP 交换包和省略文本的再次生成都会沿用它。保存稿件只持久化用户明确提交的文本，Provider/资产 metadata 不记录表达指令原文。
`scripts/stack_smoke.py` 的 HTTP 失败、公开健康探针失败和异步 Job 失败同样只输出固定方法/状态类别，不回显响应体、Provider 错误正文、任务错误字段或带凭据的探针 URL；因此 smoke 失败后应到受控服务日志中排查根因，不把终端摘要当作原始诊断日志。

本地回归：`.venv/bin/python -m unittest discover -s tests -v`。无容器时可直接执行 `make local-preview-smoke`：它会用当前 `NEXT_PUBLIC_API_BASE` 重建 Web，再启动自有 SQLite API/worker/Next preview 栈，执行来源文件、A/V、签名支付完整 smoke；设置 `STUDIO_LOCAL_SMOKE_BROWSER=1` 还会继续跑真实 Playwright 全流程和旁白流程。成功/失败均保留脱敏证据和受控日志路径，并只清理自己启动的进程，不会把旧 `.next` 构建或 API/Web 地址错误当成业务失败。健康探针：`GET /api/health`；数据库 readiness：`GET /api/ready`；失败任务通过 `POST /api/jobs/:id/retry` 显式重试，排队任务通过 `POST /api/jobs/:id/cancel` 取消并退款；`STUDIO_JOB_STALE_SECONDS` 控制 worker 崩溃后 running Job 的租约回收，默认 1800 秒。生产切换 PostgreSQL 见 `requirements-postgres.txt`，S3/MinIO 见 `requirements-storage.txt`。
可重复质量门：运行 `make quality` 会依次执行 Python 测试/compile、Web 构建、Remotion/Orchestrator 检查和 Compose 部署合同校验；有 Docker/Podman 时执行真实 `compose config`，本机没有容器运行时时回退到无依赖的静态安全/服务/镜像版本检查。部署前仍必须在真实容器运行时执行 `compose config`、build/up 和 stack smoke。CI 的 preview smoke 不调用真实 Provider；`production-compose-smoke` 使用 CI-only mock Provider 验证生产容器拓扑，不把 mock 结果当作真实模型/GPU/支付完成，也不把生产配置 gate 的失败误报为部署成功。
CI 的 `production-compose-smoke` 现在还用独立 `mock-stripe` 走 Stripe Checkout Session HTTP、订单级 Idempotency-Key、原始 `Stripe-Signature` 回调和一次性积分入账；仅当 `CI=true` 且显式 `STUDIO_BILLING_CI_MODE=true` 时允许该 mock 使用 HTTP API base，真实生产仍强制 Stripe HTTPS API base 和 HTTPS 回跳地址。
Provider HTTP 合同测试位于 `tests/test_provider_http_contracts.py`，使用本地临时 HTTP 服务验证 OpenAI-compatible 文本/视觉和 ComfyUI 图片/视频适配器；它不需要真实 key、GPU 或外部服务，不能替代真实 Provider smoke。
语音 Provider 的配置/真实调用前检查使用同一入口：`.venv/bin/python scripts/provider_smoke.py --kind speech` 只做脱敏 dry-run，显式追加 `--run` 才会调用 OpenAI Audio API 并在临时目录验证音频输出。Web 音轨行在资产 ready 后提供原生试听控件；长旁白最多 48,000 字，按句末边界切成最多 16 个不超过 4,096 字的 Provider 请求后合并，仍按一次 narration Job 预留 1 积分；语速与表达指令会进入 Job payload，资产 metadata 仅保存指令是否配置；自动字幕是时长估算，不是 ASR 强对齐，用户可在时间线里修改或关闭。服务层和 Provider 会拒绝未在内置/部署白名单中的 voice。旁白稿保存后会跨刷新和 JSON/ZIP 交换包保留，未传 `text` 的再次生成读取已保存稿件。试听成功不等于成片渲染成功，仍需验证 Remotion 合成。
认证依赖预检：默认 `runtime_preflight.py` 仍只做端口/HTTP 健康检查；部署机可追加 `--probe-data-services`，或运行 `PROBE_DATA_SERVICES=1 make production-preflight`，使用已注入的 `STUDIO_DATABASE_URL`、Redis URL 和 S3/MinIO 配置执行只读 PostgreSQL `SELECT 1`、Redis `PING`、S3 `HeadBucket`，输出只含固定状态，不输出 DSN、URL、凭据或异常正文。该探针通过也不等于真实 Provider、GPU、容器和公网验收完成。
任务审计：`GET /api/jobs/:id/events` 返回由 SQLite/PostgreSQL 触发器记录的创建、状态转换、尝试次数和错误消息；Web 任务中心可展开查看时间线。`GET /api/jobs` 和 `GET /api/jobs/:id` 同时返回服务端持久化的 `progress_percent`/`progress_message`，刷新后进度可恢复；任务状态和进度均以服务端 Job 为准，不以浏览器内存状态为准。
项目制作门禁：`GET /api/projects/{project_id}/readiness` 按用户归属汇总来源、改编审校、故事资产、结构、角色母版、提示词、采用关键帧、视觉审核、视频和分集合成，返回 `ready_for_video`、`ready_for_composition`、`ready_for_delivery` 以及 `needs_human_review`。其中 `ready_for_delivery` 只有在所有必需阶段都 ready 且结构/关键帧/视频/合成仍绑定当前改编内容 revision 时才为 true；改编稿或镜头语义变化会把结构标记为 stale，必须重新生成结构和下游媒体，旧媒体会保留但不再计入门禁；改编单元处于 `review` 或 `rejected` 时，提示词、关键帧、视频和合成服务端入口直接返回 409，重新批准后才恢复；Web 首页的“制作就绪度”状态卡直接消费该服务端快照；本地 Provider 的 `UNKNOWN` 视觉结果会保留人工复核标记，不会被当成自动通过。
真实浏览器回归：API、Web 和本地 worker 启动后运行 `bash scripts/browser_full_pipeline_smoke.sh`，覆盖小说导入、改编、故事资产、结构、角色母版、关键帧、视觉审核、视频和成片；脚本会把脱敏快照写入 `output/playwright/`，并用 `11-backend-audit.json` 强制核对 3 个镜头全部 ready。来源文件回归脚本覆盖 TXT、DOCX、EPUB、PDF 四种真实文件选择、multipart 解析、媒体类型回读和批量审校：`browser_source_file_import_smoke.sh`、`browser_docx_source_file_smoke.sh`、`browser_epub_source_file_smoke.sh`、`browser_pdf_source_file_smoke.sh`。本地新鲜证据在 `output/playwright/browser-adaptation-audit-20260804/`、`output/playwright/browser-full-audit-20260804/`、`output/playwright/browser-source-file-import-smoke-r1/`、`output/playwright/browser-docx-source-file-smoke-r3/`、`output/playwright/browser-epub-source-file-smoke-r1/` 和 `output/playwright/browser-pdf-source-file-smoke-r1/`；CI `browser-smoke` 也会执行这四条脚本并上传 `browser-source-file-smokes` artifact。生产验收仍需替换为真实 Provider、PostgreSQL/BullMQ/MinIO、GPU、支付和公网环境。
状态机保护：Job 状态写入统一经过服务层迁移入口，SQLite/PostgreSQL 触发器拒绝非法迁移；`running → queued` 仅用于租约回收，`queued → failed` 仅用于余额不足。
组合渲染：默认 `STUDIO_COMPOSE_ENGINE=ffmpeg`；设置为 `remotion` 后由 `rendering/` 独立 worker 使用官方 Remotion React 时间线渲染，支持音频轨道和字幕，失败会记录回退原因并继续使用 FFmpeg。分集卡片可上传音频并编辑音轨起点/音量、删除音轨和编辑“开始-结束 | 文本”字幕时间线，草稿不会被 5 秒轮询覆盖；服务端通过 `/api/episodes/{episode_id}/audio` 和 `/api/episodes/{episode_id}/composition-settings` 校验并持久化；配置附加轨道时 FFmpeg/playlist 会明确返回 409。Remotion API 镜像构建阶段和每次渲染前调用官方 `ensureBrowser()` 准备 Chrome for Testing，支持 `STUDIO_REMOTION_BROWSER_EXECUTABLE` 覆盖路径。`/api/ready` 会检查组合引擎包和运行时配置；渲染包本地可运行 `npm install && npm run typecheck && npm test`。

运行时预检：`.venv/bin/python scripts/runtime_preflight.py --json` 只读检查 Docker/Podman daemon、命令、组合引擎、可选 NVIDIA GPU、端口、API/Web/编排器/MinIO/ComfyUI 健康端点、Provider 配置和生产默认凭据 gate，不打印密钥。部署前可用 `make production-preflight` 一次执行静态 Compose 和真实依赖失败 gate；GPU 节点使用 `REQUIRE_GPU=1 make production-preflight`，启用 Caddy overlay 则使用 `make production-edge-preflight`。底层命令也支持显式依赖：`.venv/bin/python scripts/runtime_preflight.py --json --require-live --require-production-config --require-container-runtime --require-postgres --require-redis --require-minio --require-orchestrator --require-comfyui --orchestrator-url http://127.0.0.1:8790`。生产 gate 会同时要求已启用的 ComfyUI workflow 文件存在且为非空 JSON 对象，ComfyUI `/system_stats` 必须返回 JSON object。`--require-live` 会同时要求已启用的真实 Provider 具备 URL、模型、密钥环境变量和 workflow 路径，单独使用 `--require-provider-config` 可只做这项配置 gate。预检通过仍需运行下面的 `stack_smoke.py`，真实生成和存储读写不能用“端口可达”替代。

Web 工作台已包含任务中心（轮询、重试、取消）、角色三视图生成、关键帧绑定角色母版、积分流水、用户作用域 Provider 偏好和服务端登出；Provider 偏好只保存 provider/model/base_url，不保存 API key，并会在改编、角色卡、分集、分镜、提示词、角色母版、关键帧、视频和视觉审核时按用户配置解析运行时 provider。浏览器认证使用 HttpOnly session cookie + CSRF token，不再把 session token 写入 localStorage；CLI/API 仍兼容 Bearer。用户 base_url 仅可使用对应服务端环境地址或 `STUDIO_ALLOWED_PROVIDER_BASE_URLS` 白名单地址。
充值底座已接入：`GET /api/billing/packages` 返回平台商品目录，`POST /api/billing/orders` 以 `Idempotency-Key` 创建 pending 订单；`signed-webhook` 模式使用受保护参数的 checkout redirect，`stripe` 模式通过标准 Checkout Session 表单创建托管结账页，并把订单/积分 metadata 传到 PaymentIntent，便于后续退款/拒付事件回溯订单。Web 工作台按 `checkout_available` 显示商品/禁用未完成配置，并展示订单状态、关联调整和负余额提示；`/billing/success` 与 `/billing/cancel` 会携带本地订单上下文返回工作台，工作台最多做 6 次有界状态复核，但明确以 Webhook 结算为准；`POST /api/billing/webhook` 分别验证 HMAC 或 `Stripe-Signature` 后归一化为支付、取消和调整合同，并以金额/币种匹配、事件幂等和独立 `billing_adjustments` 账本记录 purchase/refund/chargeback/资金恢复。默认本地 `STUDIO_BILLING_PROVIDER=disabled`；生产可选 `signed-webhook` 或 `stripe`，密钥只由 secret manager 注入，真实商户 checkout、生产回调、公网和退款/争议处理仍需外部验收。
生产 paid 回调还必须携带并匹配订单的 `amount_cents` 与 `currency`；`scripts/stack_smoke.py --include-billing` 会按 `/api/health` 的 provider 验收 signed-webhook 或 Stripe：前者使用 `STUDIO_SMOKE_BILLING_WEBHOOK_SECRET`，后者使用 `STUDIO_SMOKE_STRIPE_WEBHOOK_SECRET`，两者都验证 checkout、原始签名、重复事件、异步失败/过期取消、refund/chargeback 调整、资金恢复和一次性积分入账；取消路径还确认不会发放额外积分，退款/拒付会写负积分流水，拒付恢复只恢复对应拒付额度，余额不足时允许进入负数欠账并由现有 reservation gate 阻止继续消费。本地 queued smoke 还必须让 API 的 `STUDIO_INTERNAL_TOKEN` 与 smoke 进程的 `STUDIO_SMOKE_INTERNAL_TOKEN` 使用同一配置；signed-webhook 还需 `STUDIO_BILLING_WEBHOOK_SECRET` 与 `STUDIO_BILLING_CHECKOUT_URL`，Stripe 还需 `STRIPE_SECRET_KEY`、`STRIPE_WEBHOOK_SECRET`、`STRIPE_API_BASE_URL` 和 HTTPS 成功/取消回跳 URL。生产 Stripe webhook 至少配置 `refund.created`、`charge.dispute.funds_withdrawn`、`charge.dispute.funds_reinstated`；生产使用 BullMQ worker，不使用这个本地内部驱动令牌。
生产配置 gate 还会拒绝 local preview Provider：文本/视觉必须配置 `openai-compatible` 的 URL、模型和标准服务端 key，图片/视频必须配置 ComfyUI URL、已挂载且可解析为非空 JSON 对象的 API-format workflow；直接运行 production API 时 `/api/ready` 也会重复检查。生产用户设置不能把已部署 Provider 降级为 local 或改到未允许的 base_url，历史数据库偏好在运行时也会复检；`.env.example` 的 local 值只适用于本地预览，不代表生产可用。
项目统计区提供“批量视觉审核”，调用 `POST /api/projects/{project_id}/asset-reviews` 对 ready 关键帧逐项审核；真实 Provider 返回 FAIL 时服务端会清除一致性确认，阻止不合格关键帧进入视频生成。本地 Provider 只返回 UNKNOWN，不伪造视觉通过。
角色母版支持逐角色生成、项目级批量生成和用户自有参考图：`POST /api/projects/{project_id}/character-references` 会先预检本批次所需积分，复用逐项 Job/幂等/退款合同，已完成或排队中的角色不会重复扣费；`POST /api/characters/{character_id}/reference-upload` 接受 PNG/JPEG/WebP 的正面、侧面或背面参考图，上传不调用 Provider、不扣积分，三视图齐全后才进入 readiness；Web 角色母版面板提供“批量生成三视图”和三个用户上传入口。
关键帧支持项目级/指定镜头批量生成：`POST /api/projects/{project_id}/image-assets` 会预检总积分，跳过已有 ready/pending/generating 任务，并为每个镜头派生子幂等键；Web 分集面板提供“批量生成关键帧”。
已确认关键帧支持项目级/指定素材批量生成视频：`POST /api/projects/{project_id}/video-assets` 只接受 ready 且已采用/连续性确认的图片，预检积分并跳过已有视频；Web 分集面板提供“批量生成视频”。
本地预览 Provider 会保留 SVG 供 Web 展示，并生成同资产 PPM 光栅副本，用当前采用关键帧生成 1 秒 FFmpeg 动态片段；只有素材无法物化或解码时才回退为明确标记的纯色预览，真实模型仍通过 ComfyUI Provider 接入。
项目级支持批量合成各分集：`POST /api/projects/{project_id}/compositions` 会校验项目归属，跳过已完成或正在运行的分集，按批量幂等键派生分集子键并复用单集合成 Job/引擎合同；Web 项目统计区提供“批量合成项目”。
单集合成 `POST /api/episodes/{episode_id}/compose` 也支持 `Idempotency-Key`；Web 单集按钮用幂等键复用同一 Job/成片，避免重复点击产生重复合成。
同一镜头支持多张关键帧候选：采用新候选会自动撤销旧候选的采用/一致性确认；预览优先展示当前采用候选，合成只消费当前采用且已确认关键帧对应的视频。
数据层也保证该不变量：SQLite 使用部分唯一索引，PostgreSQL 通过 `migrations/006_selected_keyframe_invariant_postgresql.sql` 迁移并修复旧数据，避免并发写入产生多个采用候选。
生成后的角色卡、分集大纲和分镜可在 Web 内直接人工修订；镜头场景/情绪/描述改变后旧图片提示词引用会失效，旧资产保留作审计，需重新生成提示词再出图。
镜头卡还支持生成并人工编辑正/负面图片提示词；保存通过 `PATCH /api/image-prompts/{prompt_id}` 执行项目归属校验和长度边界校验。
账户安全还支持“退出全部设备”：`POST /api/auth/logout-all` 服务端撤销该用户全部 session，浏览器清理 HttpOnly cookie 和 CSRF token，不返回 session token；Bearer 和 cookie+CSRF 两种认证合同都保留。
小说改编支持 `POST /api/projects/{project_id}/rewrite?queued=true` 创建可恢复的 `text` Job；本地默认同步，BullMQ/生产队列下由 Worker 回写改编单元，保留原文、diff、审核状态和来源追溯。该接口也接受 `Idempotency-Key`，同一用户/项目/改编模式复用原 Job，跨项目或跨模式复用返回 409。
故事资产层支持 `POST /api/projects/{project_id}/story-bible` 抽取地点、道具和关系；人物引用已有角色卡，模型返回的来源段落 ID 会与项目来源段落求交集。结果默认是 draft，Web 可看到来源段落数量和 Provider fallback 状态；`PATCH /api/story-entities/{id}`、`PATCH /api/story-relationships/{id}` 用于审核修订。重新抽取会 supersede 未审核旧草稿但保留 `story_bible_runs` 运行快照，`GET /api/projects/{project_id}/export` 和 ZIP 会携带并在导入时重新映射这些故事资产。只有 approved 的故事资产会进入后续分镜和图片提示词，draft/rejected 设定不会被当成确定事实。
故事资产抽取也复用统一 Job 合同：本地无 `queued` 参数时同步返回，`?queued=true` 或 BullMQ/生产默认返回 `story-bible` queued Job；支持用户作用域 `Idempotency-Key`、SQLite worker/BullMQ 入队恢复、状态事件、重试和取消。异步完成后通过 `GET /api/projects/{project_id}/story-bible` 读取结果。
“准备项目结构”通过 `POST /api/projects/{project_id}/structure` 统一生成角色卡、分集大纲和分镜；本地无 `queued` 参数时同步预览，`?queued=true` 或 BullMQ/生产默认返回 `structure` Job。任务按阶段复用已有实体，失败重试不会重复创建，Web 只提交一个可轮询任务。
兼容的 `POST /api/projects/{project_id}/characters`、`POST /api/projects/{project_id}/outline` 和 `POST /api/episodes/{episode_id}/shots` 空 payload 入口也复用 `structure` 阶段 Job；BullMQ/`?queued=true` 下不会在 API 请求线程触发文本 Provider，`jobs.payload_json.stage` 标记具体阶段，本地无队列仍保持历史列表响应。带明确角色卡数组的 characters 请求是人工写入，不经过 Provider。
图片生成在队列模式下会把缺失的镜头提示词延迟到 Worker 内创建；手动 `POST /api/shots/{shot_id}/image-prompts` 也支持 `prompt` Job，Web 单镜头按钮不再先同步调用文本 Provider。单张或批量视觉审核可通过 `?queued=true` 或 BullMQ/生产默认创建 `vision-review` Job。Job 输入会持久化自定义审核提示词/类型，不消耗积分，完成后仍写入逐资产 PASS/FAIL/UNKNOWN 审核记录；本地无队列参数继续同步预览。Web 故事资产抽取、单素材视觉审核和批量视觉审核按钮会发送用户作用域 `Idempotency-Key`；任务中心展开时间线会随轮询刷新最新 Job 事件，登录表单也提供浏览器可识别的密码字段约束。
本地文件优先流程也可运行 `.venv/bin/python scripts/generate_story_bible.py PROJECT_ID --runtime-dir runtime`，输出不含凭据的故事资产 JSON。
改编审校支持显示全部单元、保存草稿、单条通过和批量通过：批量接口为 `POST /api/projects/{project_id}/adaptation-units/review`，不覆盖改编文本与来源追溯，并保留版本历史。
BullMQ 编排器短暂不可用时，重复进入已有 `queued` 的文本改编或合成请求会自动重新投递；编排器入队接口仅接受受限 JSON 任务合同，并受 `ORCHESTRATOR_MAX_BODY_BYTES` 限制。
BullMQ Worker 调用 API 的成功响应限制为 64 KiB，非 2xx 响应只记录状态码并以 4 KiB 上限消费错误体；超限或非法 JSON 会以稳定脱敏错误触发 BullMQ 重试，不把 Provider/API 原始响应写入 Worker 日志。

项目创建使用用户作用域 `Idempotency-Key`：网络重试会复用同一项目，使用同一键提交不同标题、故事、风格或目标时长会返回 409；项目设置面板可编辑标题、故事梗概、视觉风格和目标时长，保存使用 `PATCH /api/projects/:id` 并执行用户归属校验，后续结构化生成读取保存后的项目配置。
生产 Compose 的应用镜像使用 UID/GID 10001、只读根文件系统、`no-new-privileges` 和独立临时文件系统；数据库/Redis/MinIO 镜像不使用 `latest` 默认标签，升级镜像后必须重跑配置、schema 和 stack gate。已有旧 runtime volume 升级时先按 [PRODUCTION_RUNBOOK.md](/Volumes/AJW-Data/Projects/novel-to-comic-engine/docs/PRODUCTION_RUNBOOK.md) 调整归属，不删除数据卷。
分集结构化生成会读取已保存的视觉风格和目标时长；`30s/1min/3min/5min` 分别作为默认 30/60/180/300 秒，模型未返回时长时使用项目目标值。

项目交换包：调用 `GET /api/projects/{project_id}/export`，或运行 `.venv/bin/python scripts/export_project.py PROJECT_ID --runtime-dir runtime --output projects/exported`，会生成 `project-export.json`、`storyboards/episode-*_storyboard.py` 和可读目录；可在 Web 首页选择 JSON，或运行 `.venv/bin/python scripts/import_project.py projects/exported/project-export.json --runtime-dir runtime` 导入为新项目。导入会验证原文 SHA-256 并重新生成实体 ID，不复制任务、积分预留或凭据。
交换包还会携带项目级 `audio_assets` 和分集 `composition_settings`，导入时重新映射音频 asset ID，不把服务器绝对路径写入浏览器或交换包。
导入会对历史交换包做确定性修复：视频源素材即使排在图片前也会在实体全部映射后补链，同一镜头误带多个采用图片时按更新时间/创建时间/ID 只保留一个。
项目交付包：调用 `GET /api/projects/{project_id}/archive`，或点击 Web 的“下载项目包”，会下载 ZIP；ZIP 包含 JSON 交换包、可读分镜目录、当前可物化的图片/视频/音频、角色三视图、播放清单和成片文件。`manifest.json` 记录归档内副本路径与无法回读的素材，不请求任意外部 URL。Web 和 CLI 也支持直接导入 ZIP：Web 选择 ZIP 后调用 `POST /api/projects/import-archive`，CLI 运行 `.venv/bin/python scripts/import_project.py project-bundle.zip --runtime-dir runtime`；服务端先校验 ZIP，再导入 JSON，并把归档内二进制挂载到新实体 URL。JSON 交换包仍是跨环境重新导入的主合同。
本地媒体 URL（`/assets/{filename}`）由 API 按项目归属鉴权，不再由无条件静态目录公开；图片、视频、音频、角色三视图和分集成片均必须属于当前用户，路径穿越或跨用户文件名返回 404。
改编审计：每个改编单元会保留导入初稿、模型重写和人工编辑的 `adaptation_revisions` 版本历史，包含 diff、Provider/事件元数据；项目导出/导入会保留并重新映射这些版本。
原创化改编审校还会在当前单元和每个版本中记录 `adaptation_quality` 启发式指标（字符序列相似度、二元片段重叠、风险级别和人工复核标记）。它只是编辑提示，不是法律意见或抄袭判定；高/中风险结果不能绕过人工审核。
来源文件导入支持 TXT/Markdown/DOCX/EPUB/PDF；文本和文件接口都由服务端强制要求用户确认拥有或获授权使用内容，不能只依赖 Web 复选框；两条导入 API 接受用户作用域 `Idempotency-Key`，网络重试会复用同一来源文档和改编单元，换内容复用会返回 409。PDF 由基础运行时 `pypdf` 解析，系统 `pdftotext` 作为兼容回退；EPUB 按 OPF spine 保持章节顺序，DOCX/EPUB 归档拒绝路径穿越、重复成员和超出解压预算，服务端保存真实 `media_type` 并限制抽取文本总量（`STUDIO_MAX_EXTRACTED_SOURCE_CHARS`）。主路由合同测试、真实浏览器四格式文件选择和 `stack_smoke.py --include-source-files` 生产拓扑合同均覆盖 DOCX/EPUB/PDF 的二进制 multipart 传递；CI `production-compose-smoke` 会在 PostgreSQL/BullMQ/MinIO 栈中复核四格式抽取、媒体类型持久化和幂等重放。
PostgreSQL `schema-check --data-smoke` 同时核对迁移声明的全部命名业务索引、来源幂等列、项目级部分唯一索引和重复键约束；因此 migration 已执行但 schema 不完整时会在 API 启动前失败。

容器栈探针：API `http://localhost:8787/api/ready`，指标 `http://localhost:8787/api/metrics`，BullMQ 编排器 `http://localhost:8790/health`，Web `http://localhost:3000`。生产推荐叠加 `infra/docker-compose.edge.yml` 使用官方 Caddy 自动 HTTPS；Caddy 将 `/api`、`/assets`、文档路径送到 API，其余页面送到 Web，Edge healthcheck 还会通过 Caddy 检查 API `/api/health` 和 Web upstream。启用 edge 时，`STUDIO_PUBLIC_HOST` 必须同时出现在 `STUDIO_ALLOWED_HOSTS`，并对应 `https://<host>` 的 CORS origin；生产 Web 的 `NEXT_PUBLIC_API_BASE` 默认为空，要求 HTTPS ingress 使用同源路由；只有 API 使用独立公网域名时才配置 HTTPS `NEXT_PUBLIC_API_BASE`。本机可用 `make edge-smoke` 复用生产路由做无证书真实反代 smoke。生产基础 Compose 的内部服务只绑定主机回环，开发 Compose 与 CI-only 生产集成 smoke 才直接使用本地端口。生产环境必须替换 `infra/.env` 中的本地占位符，并通过 secret manager 注入数据库、对象存储、provider 凭据和 OTLP headers；容器 API 默认关闭文档暴露、限制请求体为 16 MiB、要求 Bearer 登录，并可通过 Redis 启用多实例共享限流。普通请求和 chunked 的来源文件/webhook 流都会边读边执行请求体上限，避免只依赖 `Content-Length`。S3/MinIO bucket 保持私有，媒体通过 API `/assets/{filename}` 经过项目归属校验后回读。
Compose 中 API 依赖 BullMQ 编排器的 Redis-backed healthcheck；若编排器或 Redis 未 healthy，API 不会被错误标记为可启动。生产 Compose 固定开启 `ORCHESTRATOR_REQUIRE_AUTH`，编排器缺少 `ORCHESTRATOR_TOKEN` 会在启动时失败，`POST /jobs` 只接受与 API 相同的 `STUDIO_ORCHESTRATOR_TOKEN`；`GET /health` 保持公开供探针使用。
Compose 中 API 还依赖一次性 `migrate` 服务成功；它复用 `studio_api.migrate`，避免 PostgreSQL 已有数据卷因跳过 `docker-entrypoint-initdb.d` 而缺少后续迁移。`workflows/` 会只读挂载到 API 容器的 `/app/workflows`，真实 ComfyUI workflow 由部署方放入该目录并通过 `COMFYUI_*_WORKFLOW` 指向容器路径；文本/视觉 key 默认通过 `STUDIO_*_API_KEY` 环境注入，生产应改由 secret manager 提供。
`GET /api/ready` 在 BullMQ 模式会实际检查编排器 `/health`，在 S3/MinIO 模式会检查 bucket access；数据库、队列或对象存储不可用时返回 503。

全栈验收：栈启动后运行 `.venv/bin/python scripts/stack_smoke.py --orchestrator-url http://localhost:8790 --require-orchestrator --expect-store postgres --expect-queue-backend bullmq --expect-storage s3`；脚本会轮询异步 Job 到完成，并验证角色/分集/分镜/图片提示词人工编辑和镜头提示词失效保护。没有 GPU/provider 时加 `--skip-generation`，只跳过图片/视频/合成，仍验证账户、数据库、项目导入、导出、画布、Web 和编排器健康。组合引擎设为 Remotion 后可追加 `--include-av`，验证合法 WAV 上传、字幕时间线、A/V 成片和音频交换包重映射。

关键帧审核：`POST /api/assets/{asset_id}/review`。本地预览只会记录 `UNKNOWN` 并要求人工复核；接入 OpenAI 兼容视觉模型后再启用自动 PASS/FAIL。
人工复核：本地或自动审核返回 `UNKNOWN` 时，Web 提供“人工通过/人工驳回”；对应 API 为 `POST /api/assets/{asset_id}/review/decision`，决策会以 `provider=manual` 保留审计记录，只有最新结果为 `PASS` 的关键帧才能通过项目视觉审核就绪度门禁。该写接口支持用户作用域 `Idempotency-Key`，网络重试不会重复追加同一人工审核记录；同一 key 改变素材、状态或问题列表会返回 409。视频生成和分集合成服务端也会重新检查最新审核结果，不能靠旧视频或前端按钮绕过 `UNKNOWN`/`FAIL`。

## 模型分工

| 步骤 | 模型 | 说明 |
|------|------|------|
| 改编层（编剧） | **DS v4 Pro** | 理解小说，按1句=1格输出改编层。文字剧情创作是 DS v4 Pro 的强项 |
| 分镜脚本 | **Kimi K2.7** | 生成英文prompt，嵌入{Q}/{S}，标注情绪状态 |
| 批量生图 | **Step Image Edit 2** | 通过本地 mm-gateway 调用 |
| 视觉审查 | **Step 3.7 Flash**（降级）/ **Kimi K2.7**（首选） | 逐格识图检查画面质量 |
| 配文排版 | **Kimi K2.7** | 生成页面布局和旁白配置 |
| PDF输出 | 本地 PIL + ReportLab | B5判 @ 150dpi 渲染

## 快速开始

### 1. 克隆项目

```bash
git clone <repo-url> novel-to-comic-engine
cd novel-to-comic-engine
```

### 2. 配置 API Key

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入你的 API key
```

支持的模型：
- **生图**：阶跃星辰 Step Image Edit 2（推荐）
- **视觉审核**：Kimi K2.6

### 3. 安装 Claude Code skill

在 `~/.claude/skills/` 下创建软链接：

```bash
ln -s /path/to/novel-to-comic-engine ~/.claude/skills/novel-to-comic
```

（Windows 用户用 mklink，macOS/Linux 用 ln -s）

CC 启动时会自动识别 `~/.claude/skills/` 下的 SKILL.md。

### 4. 使用流程

在 Claude Code 中：

```
用户：读取 projects/example/chapter_3.md，生成分镜脚本
Claude：生成 PANELS 列表并保存到 projects/example/storyboard_ch3.py

用户：开始生成第三章画格
Claude：调用 scripts/generate_panels.py 批量生图

用户：审核第三章
Claude：调用 scripts/audit_panels.py 全量审核

用户：修复问题画格 P06
Claude：调用 scripts/fix_panel.py 重新生成

用户：排版第三章
Claude：调用 scripts/layout_chapter.py 输出 PDF
```

---

## 配置说明

编辑 `config.yaml`：

```yaml
providers:
  image:
    default: step
    step:
      api_key: "${STEP_API_KEY}"      # 从环境变量读取
      api_url: "https://api.stepfun.com/v1"
      model: "step-image-edit-2"
      size: "1024x1024"

  vision:
    default: step
    step:
      api_key: "${STEP_API_KEY}"
      api_url: "https://api.stepfun.com/v1"
      model: "step-3.7-flash"
      max_tokens: 400
    kimi:
      api_key: "${KIMI_API_KEY}"
      api_url: "https://api.moonshot.cn/v1"
      model: "kimi-k2.6"
      max_tokens: 2000

project:
  output_dir: "./projects/example/output"
  font_title: "/System/Library/Fonts/STHeiti Medium.ttc"
  font_body: "/System/Library/Fonts/STHeiti Medium.ttc"

style:
  prompt_suffix: "Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."
  page_width_mm: 182
  page_height_mm: 257
  dpi: 150
  margin_mm: 12
  gutter_mm: 5
```

---

## 核心流程

```
小说原文
  ↓
[DS v4 Pro] 改编层（1句=1格，文字剧情）
  ↓
[Kimi K2.7] 分镜脚本（英文prompt + 情绪标注 + {Q}/{S}）
  ↓
[mm-gateway → Step Image Edit 2] 批量生图
  ↓
[Step 3.7 Flash / Kimi] 视觉审查 → 修复问题画格
  ↓
[Kimi K2.7] 配文排版（古籍竖排对话框）
  ↓
[本地 PIL + ReportLab] → B5 PDF
```

---

## 注意事项

1. **本工具为不通用的 Claude Code skill 版本**，面向个人创作者，不保证跨平台/跨模型兼容
2. API 调用会产生费用，请合理控制批量生成数量
3. 视觉审核可能触发 429 限流或 451 内容过滤，需耐心重试
4. 大文件输出默认建议放在外置硬盘，避免占用系统盘空间
5. 请勿将真实 API key 提交到 git，始终使用 `config.yaml` 并确保它在 `.gitignore` 中

---

*最后更新: 2026-06-19*（v1.0.1 — 修正改编层模型路由为 DS v4 Pro）

---

## License

MIT
