# AI 漫剧工作台生产运行手册

本手册只描述已在代码中存在的启动、预检和验收合同。它不把端口可达、页面打开或本地预览 provider 当成真实模型生成完成。

## 1. 启动前

在具备 Docker/Podman daemon 的部署机上执行。所有密钥由 secret manager 或受控环境注入，不提交 `infra/.env`：

```bash
cd /Volumes/AJW-Data/Projects/novel-to-comic-engine
cp infra/.env.example infra/.env
# 编辑 infra/.env：替换本地占位符、域名、数据库、对象存储和 Provider 配置；未替换的默认值会被 config-check 拒绝
```

发布前先通过仓库质量门：

```bash
make quality
```

这一步只验证代码、渲染器、编排器和 Compose 部署合同；具备 Docker/Podman 时会执行真实 `compose config`，没有容器运行时时只做静态服务、安全和镜像版本检查。它不代替下面的容器启动、真实 Provider、支付回调或公网验收。

GitHub Actions 的 `container-smoke` job 会在真实 Docker runner 上 build/up `infra/docker-compose.dev.yml`，等待 API/Web 健康后运行 `scripts/stack_smoke.py --skip-generation`，最后收集日志并销毁临时开发卷。它证明容器镜像和本地预览栈可运行，不替代生产 Compose 的 PostgreSQL/BullMQ/MinIO、真实 Provider、GPU 或支付验收。

GitHub Actions 的 `browser-smoke` job 会在独立 Docker runner 启动同一 preview 栈，安装 Chromium，通过 `scripts/browser_adaptation_smoke.sh` 真实操作注册、项目创建、TXT 导入、改编预览、单条驳回、页面 reload 和批量通过；随后还运行 `browser_source_file_import_smoke.sh`、`browser_docx_source_file_smoke.sh`、`browser_epub_source_file_smoke.sh`、`browser_pdf_source_file_smoke.sh`，分别验收四种来源文件的真实选择、multipart 解析、媒体类型回读和批量审校，并上传 `browser-source-file-smokes` 独立 artifact。它证明 CI 中的浏览器 UI 状态闭环；本地或 CI preview 仍不等于生产域名、真实 Provider、支付回调或 PostgreSQL/BullMQ/MinIO 验收。

同一 `browser-smoke` job 还运行 `scripts/browser_full_pipeline_smoke.sh`，覆盖故事资产、项目结构、角色三视图、关键帧、视觉审核、视频和分集合成。脚本在浏览器上下文中使用 CSRF 保护的辅助请求完成多镜头采用/审核，再通过 UI 触发视频/合成，并强制审计 `3/3` 镜头的 selected、reviewed、video-ready 和最终成片链接；Playwright CLI 的 `### Error` 会使步骤失败。证据上传为独立的 `browser-full-pipeline-smoke` artifact，临时注册字段在快照前清空。它只证明本地 preview 的浏览器/API/worker 合同，不能替代真实 Provider、GPU、PostgreSQL/BullMQ/MinIO、支付回调或公网验收。

GitHub Actions 的 `postgres-schema-smoke` job 会启动 PostgreSQL 16.4 service container，执行 `studio_api.migrate` 和 `scripts/postgres_schema_check.py --check-only --data-smoke --json`。它用于在真实 CI 数据库中验收迁移账本、命名索引、JSONB、Job 事件/非法状态保护、来源幂等和关键帧唯一采用等生产 schema/data 合同；本机无 PostgreSQL/Docker 时只能验证 workflow/合同配置，不能将 job 配置视为已完成数据库验收。

GitHub Actions 的 `production-compose-smoke` job 会用 `infra/docker-compose.yml` 加 `infra/docker-compose.ci.yml` 启动生产配置拓扑：PostgreSQL、Redis、MinIO、migration/schema gate、认证编排器、BullMQ worker、API、Web 和 Remotion。文本/视觉、ComfyUI 和 Stripe 由仓库内 CI-only mock 提供确定性 JSON、图片、MP4、视觉 PASS 和 Checkout Session，业务 smoke 还发送原始 `Stripe-Signature` 并验证重复事件只入账一次；目的是验证生产配置 gate、真实容器依赖、队列、对象存储、媒体适配器、支付适配器和业务 smoke 的连接关系。它不模拟模型质量，也不替代真实 GPU、Wan/LTX workflow、Provider 额度、商户支付或公网验收。本机没有 Docker/Podman 时只能检查该 job 和 override 的合同，必须看到 GitHub job 成功和 artifact 才能报告容器拓扑已验收。

生产推荐使用仓库提供的 Caddy edge overlay；它使用官方 `caddy:2.11.4-alpine` 镜像自动申请/续期 HTTPS，把 `/api`、`/assets`、API 文档路径转发到 API，其余页面转发到 Web。域名和 ACME 邮箱只通过 `infra/.env` 注入，Caddy 的证书数据写入独立命名卷；Edge healthcheck 不只检查 Caddy 自身，还通过容器内探针检查 API `/api/health` 与 Web upstream。CI 的 `production-compose-smoke` 还会用同一镜像执行 Caddyfile validate。也可以省略该 overlay，改用 Cloudflare/ALB/Nginx 等受控 ingress，但必须保持同样的路由合同。Caddy 配置位于 [infra/Caddyfile](/Volumes/AJW-Data/Projects/novel-to-comic-engine/infra/Caddyfile)。

本机具备 Caddy 时，可运行 `make edge-smoke`；该命令从生产 Caddyfile 派生临时高位 HTTP 配置，启动进程内假 API/Web upstream，真实验证 Edge、自身健康、API、`/assets` 和 Web fallback，不读取密钥、不申请证书、不启动业务服务。部署机可追加 `./.venv/bin/python scripts/edge_runtime_smoke.py --json --require-caddy`，Caddy 不存在时将明确失败。

2026-08-03 本机 Edge 回归已通过：`make edge-smoke` 和 `scripts/edge_runtime_smoke.py --json` 均返回 `status=passed`，覆盖真实 Caddy 进程、API/Web upstream、公开 `/api`、`/assets` 和 Web fallback；这只证明反代路由合同，不证明 ACME/DNS、公网 HTTPS、生产容器或真实 Provider 已完成。

生产镜像基线在 `infra/docker-compose.yml` 中显式指定，默认不使用 `latest`；部署升级应通过 `POSTGRES_IMAGE`、`REDIS_IMAGE`、`MINIO_IMAGE`、`MINIO_MC_IMAGE` 评审后变更，并重新执行预检、schema gate 和 stack smoke。API、迁移、schema gate、编排器、Worker 和 Web 以 UID/GID 10001 运行，应用服务使用只读根文件系统、`no-new-privileges` 和 `/tmp` tmpfs；API 只有 `/app/runtime` volume 可写，workflow 挂载保持只读。

从旧 root 镜像升级且已有 `manhua-runtime` volume 时，先在维护窗口执行一次权限迁移，再启动新 API；不要删除 volume：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml run --rm --user root api \
  sh -c 'chown -R 10001:10001 /app/runtime'
```

该命令只调整运行目录归属，不删除项目数据；完成后由正常 Compose 服务以非 root 身份运行。

先做不输出配置值的静态配置检查：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml config --quiet
./.venv/bin/python scripts/runtime_preflight.py --json \
  --require-live --require-production-config --require-container-runtime \
  --require-postgres --require-redis --require-minio \
  --require-orchestrator --require-comfyui \
  --orchestrator-url http://127.0.0.1:8790
```

如果要证明数据服务的认证和最小读合同，而不只证明端口/健康端点可达，在同一受控环境追加 `--probe-data-services`：

```bash
./.venv/bin/python scripts/runtime_preflight.py --json \
  --probe-data-services --require-live --require-production-config \
  --require-container-runtime --require-postgres --require-redis --require-minio \
  --require-orchestrator --require-comfyui \
  --orchestrator-url http://127.0.0.1:8790
```

该选项只执行 PostgreSQL `SELECT 1`、Redis `PING` 和 S3/MinIO `HeadBucket`，从 `STUDIO_DATABASE_URL`、`STUDIO_RATE_LIMIT_REDIS_URL`/`REDIS_URL`、`STUDIO_S3_*` 与 AWS/MinIO 环境变量读取配置；报告不会输出 DSN、URL、凭据或原始异常。探针通过仍需继续执行真实 Provider/GPU workflow、容器 build/up、支付和公网验收。

预检的 `provider_configuration` 会检查已启用的文本/视觉 Provider 的 URL、模型和密钥环境变量是否存在，以及 ComfyUI 的 workflow 路径是否配置；它只输出配置布尔值和错误类型，不输出 key 或 workflow 内容。Compose 会把仓库内 `workflows/` 只读挂载为 API 容器的 `/app/workflows`，因此生产配置应使用 `/app/workflows/<name>.json`，并由 secret manager 注入 `STUDIO_TEXT_API_KEY` / `STUDIO_VISION_API_KEY`。启用 OTel collector 鉴权时，由 secret manager 注入 `OTEL_EXPORTER_OTLP_HEADERS`（`key=value,key2=value2`，值可按 OTEL 环境变量约定 URL 编码）；API 只把它传给 exporter，不写入业务日志或响应。

也可以用 `make production-preflight` 一次执行静态 Compose 合同和真实 PostgreSQL/Redis/MinIO/orchestrator/ComfyUI/生产配置 gate；GPU 节点追加 `REQUIRE_GPU=1`，需要认证读探针时追加 `PROBE_DATA_SERVICES=1`。启用仓库 Caddy overlay 时使用 `PROBE_DATA_SERVICES=1 make production-edge-preflight`，它在依赖预检后再执行 Edge 真实反代 smoke。两个目标都只输出脱敏状态；本机缺少依赖时应失败，不能把缺依赖当作通过。

生产 Compose 将 `STUDIO_ENV=production` 和 `ORCHESTRATOR_REQUIRE_AUTH=true` 固定写入编排器，并把同一份 `ORCHESTRATOR_TOKEN` 传给 API 的 `STUDIO_ORCHESTRATOR_TOKEN`。缺少或仍为占位值时，前置 `config-check` 会阻止依赖服务启动；即使绕过该 gate，编排器也会因生产环境缺 token 直接退出。`GET /health` 仍供健康探针访问，`POST /jobs` 必须携带 `X-Orchestrator-Token`。直接本地启动编排器时不要绑定公网地址，开发环境且未设置开关时才保留兼容模式。

如果当前机器就是 ComfyUI/GPU 节点，再额外执行 `--require-gpu`；它只读调用 `nvidia-smi`，报告 GPU 数量和显存，不把“有 GPU”当成模型可用。ComfyUI `/system_stats` 必须返回 JSON object；之后仍需用已挂载 workflow 执行 `provider_smoke.py --run` 和单次视频 benchmark。

若 Provider 使用本地预览，去掉 `--require-comfyui`；若使用 `STUDIO_COMPOSE_ENGINE=ffmpeg`，不需要 Remotion 浏览器验收。生产目标通常使用 `remotion`，应在真实镜像中验收 Chrome for Testing 下载与渲染；`/api/ready` 和 `scripts/runtime_preflight.py` 都会检查默认 `render` 脚本依赖的 `rendering/node_modules/.bin/tsx`/`.cmd`，若使用 `STUDIO_REMOTION_RENDER_COMMAND` 自定义 runner，则该命令必须可解析、非空且首个命令可执行，非法引号也会判定为未就绪。这样可以在付费合成 Job 入队前发现不完整的 Remotion 依赖卷或错误运行命令，不能把 `ready` 误解为真实成片质量或 Provider/GPU 已验收。

## 2. 启动顺序

```bash
docker compose --env-file infra/.env \
  -f infra/docker-compose.yml -f infra/docker-compose.edge.yml \
  up --build -d
docker compose --env-file infra/.env -f infra/docker-compose.yml ps
```

若使用托管 ingress，启动命令省略 `-f infra/docker-compose.edge.yml`，并将托管 ingress 的后端指向主机回环上的 Web/API；生产 Compose 的 PostgreSQL、Redis、MinIO、API、编排器和 Web 端口均只绑定 `127.0.0.1`，不会直接对公网开放。Caddy overlay 是唯一发布 `80/443` 的服务。

Compose 的关键依赖链是：

```text
PostgreSQL healthy -> migrate completed -> schema-check passed -> API healthy -> orchestrator-worker / Web
Redis healthy -> orchestrator healthy -> API
MinIO healthy -> minio-init completed -> API
```

生产 Web 的 `NEXT_PUBLIC_API_BASE` 默认为空，表示浏览器使用同源相对路径；Caddy/其它 HTTPS 反向代理必须把 `/api`、`/assets`、`/docs`、`/openapi.json` 路由到 API，把其余页面路由到 Web，包含 `/billing/success` 和 `/billing/cancel` 支付回跳页。启用 edge 时，`STUDIO_PUBLIC_HOST` 必须是纯主机名，并同时出现在 `STUDIO_ALLOWED_HOSTS` 与 `https://<host>` 的 `STUDIO_CORS_ORIGINS` 中；配置 gate 会阻止 Host/CORS 分裂。若部署方确实使用独立 API 公网域名，才在 `infra/.env` 设置 `NEXT_PUBLIC_API_BASE=https://...`；生产配置 gate 会拒绝 HTTP、凭据、fragment 或非法 URL。开发 Compose 和 CI-only 生产集成 smoke 显式使用本地 HTTP 地址，因为它们直接暴露 `3000/8787`，不代表生产 ingress 配置。

`config-check` 会先于这些依赖完成生产配置 gate：拒绝已知默认凭据、过短内部 token、未开启认证、local queue/local storage、local preview Provider、Host/CORS 通配符或 `null` origin、生产环境本地充值，以及未配置合法的 `STUDIO_BILLING_PROVIDER=signed-webhook|stripe`。signed-webhook 需要无凭据 HTTPS `STUDIO_BILLING_CHECKOUT_URL` 和 webhook secret；Stripe 需要 `STRIPE_SECRET_KEY`、`STRIPE_WEBHOOK_SECRET`、HTTPS API base 和成功/取消回跳 URL。唯一例外是 CI override 明确同时设置 `CI=true` 与 `STUDIO_BILLING_CI_MODE=true`，仅为仓库内 `mock-stripe` 允许 HTTP API base；该模式缺少 CI 标志或显式开关时仍失败。它只输出环境变量名与错误类型，不输出 secret 值。文本/视觉必须配置 URL、模型和标准 `STUDIO_TEXT_API_KEY`/`STUDIO_VISION_API_KEY`，图片/视频必须配置 ComfyUI URL 与 workflow 路径；启用 `--require-workflow-files` 时，workflow 文件必须由 `/app/workflows` 只读挂载且可解析为非空 JSON 对象。即使绕过 Compose 直接启动 API，`/api/ready` 也会重复执行该 gate，不会报告 production ready。

旁白/TTS 生产 gate 还要求 `STUDIO_SPEECH_PROVIDER=openai`、`STUDIO_SPEECH_BASE_URL`、`STUDIO_SPEECH_MODEL`、`STUDIO_SPEECH_API_KEY_ENV=OPENAI_API_KEY` 和 secret manager 注入的 `OPENAI_API_KEY`；`local` 只允许开发/本地预览。默认 voice 使用 OpenAI Audio API 内置音色白名单，部署可通过非秘密配置 `STUDIO_SPEECH_ALLOWED_VOICES` 收窄允许集合，但不能添加自定义 voice；配置 gate 对未知 voice fail closed。`GET /api/health` 返回的 `speech_voices` 是 Web 选择器的生效配置来源，只包含音色名，不包含秘密，服务层和 Provider 仍会双重拒绝未知 voice。Web 旁白控制还应验收 `0.75×/1×/1.25×/1.5×` 语速选择和最多 1000 字表达指令，检查生成 narration Job payload 的 `speed/instructions` 与资产 metadata 的 `speed/instructions_configured` 一致，但不把完整指令写入媒体 metadata/日志。真实验收应先检查 `narration` Job 的 queued/running/completed 或 failed/退款事件，再检查 `assets(kind=audio)`、`composition_settings.audio_tracks`、`composition_settings.subtitles` 和 Remotion 成片。服务端允许最多 48,000 字，按句末边界切成最多 16 个不超过 4,096 字的 Provider 请求并合并为一个音频资产；默认按文本长度估算字幕并只在字幕列表为空时回填，用户可通过 `auto_subtitles=false` 关闭，估算字幕不是 ASR 强对齐；超过上限应明确 422，不能静默截断。Web 必须保留 AI 生成语音披露。

`migrate` 会复用 `PostgresStore.initialize()`，并使用 PostgreSQL advisory lock 串行化迁移。初始化会在 `studio_schema_migrations` 中记录迁移文件名、SHA-256 和应用时间；同一文件校验和未变化时跳过，已应用文件被改写时会明确失败，禁止静默修改生产 schema。已有数据卷不会因为跳过 `docker-entrypoint-initdb.d` 而缺少后续迁移。不要用 `docker compose down -v` 作为普通故障恢复操作，它会删除数据库、Redis、MinIO 数据卷。

迁移失败时先保留原始错误和 `migrate` 容器日志，不要手工删除 `studio_schema_migrations` 或覆盖已应用的 SQL。若只是新增迁移，修复代码后重新运行 `migrate`；若出现 checksum mismatch，应恢复原文件或新增一个后续迁移修复 schema。可在受控数据库会话中只读核对账本：

```sql
SELECT migration_name, checksum, applied_at
FROM studio_schema_migrations
ORDER BY migration_name;
```

`REQUIRED_COLUMNS` 与 migrations/*.sql 的持久化合同保持一致，schema gate 会覆盖项目、来源、改编、故事资产、镜头、媒体资产、Job、积分和成片组合的迁移字段；字段缺失会在 API/Worker 启动前 fail closed。

质量门中的 `test_gate_covers_every_column_declared_by_postgres_migrations` 会自动扫描迁移文件中的建表列和增量列；新增 PostgreSQL migration 时必须同步 `REQUIRED_COLUMNS`，否则本地/CI 测试失败。

`schema-check` 是 Compose 中 API 前的结构 gate；它核对生产所需表、关键列（包括 `composition_settings.narration_text` 旁白稿字段）、所有迁移声明的命名业务索引、Job 状态/事件触发器和每个迁移文件的 SHA-256，并用 `--data-smoke` 在单个事务中验证 JSONB 往返、Job 事件、非法状态保护、来源幂等和关键帧唯一索引，最后回滚探针数据。手工部署或已有栈升级后可单独运行同一检查：

```bash
# 由 secret manager / 受控环境注入 STUDIO_DATABASE_URL，不把真实 DSN 写入命令或文档
.venv/bin/python scripts/postgres_schema_check.py --check-only --data-smoke --json
```

命令只在终端中使用受控环境变量，报告只包含数据库 host:port、结构缺口和迁移计数；返回码非 0 时不能继续报告 API 已完成部署。

## 2.1 无容器本地预览启动

没有 Docker/Podman 时，不要手工复用旧的 `web/.next` 后直接 `npm start`。仓库提供统一入口，会在启动 Web preview server 前用当前 API 地址重新构建 Web，同时启动 SQLite API、本地 worker 和 Next Web，并在退出时只停止自己记录的 PID：

```bash
cd /Volumes/AJW-Data/Projects/novel-to-comic-engine
make local-preview-smoke
```

该入口默认执行 `scripts/stack_smoke.py --include-source-files --include-av --include-billing`，使用本地 Provider、Remotion、临时 SQLite/runtime 和本地签名支付合同；不调用真实模型、GPU、商户支付或外部数据库。需要继续做真实浏览器全流程时使用：

```bash
STUDIO_LOCAL_SMOKE_BROWSER=1 make local-preview-smoke
```

成功或失败都会打印脱敏的 runtime、证据和 API/Worker/Web 日志绝对路径；失败时先看这些日志，不把端口可达或单个 HTTP 200 当作链路完成。默认临时 runtime 为了保留失败现场不会自动删除，确认无误后可手工移入系统废纸篓；不要把真实密钥放入该入口的本地变量。

## 3. 业务验收

先确认健康和 readiness：

```bash
curl -fsS http://127.0.0.1:8787/api/health
curl -fsS http://127.0.0.1:8787/api/ready
curl -fsS http://127.0.0.1:8790/health
```

跳过真实图片/视频 Provider，先验收 API、数据库、队列边界：

```bash
./.venv/bin/python scripts/stack_smoke.py \
  --orchestrator-url http://127.0.0.1:8790 \
  --require-orchestrator \
  --expect-store postgres \
  --expect-queue-backend bullmq \
  --expect-storage s3 \
  --include-source-files \
  --skip-generation
```

验收 JSON 必须包含 `adaptation_review_contract=true`、`adaptation_rejection_contract=completed` 和 `ownership_contract=completed`：前者证明批量审校接口可用，第二项证明脚本先单条驳回改编单元、再批量通过并验证状态回收，第三项证明第二个用户既看不到第一个用户的项目列表，也不能读取项目详情。该结果仍只代表 API/本地栈合同；生产浏览器权限、真实 Provider 和外部运行时要分别验收。

不使用 `--skip-generation` 的完整 smoke 还必须包含 `asset_ownership_contract=completed`，证明第二个用户不能读取第一个用户生成的关键帧资产；若跳过生成，该字段为 `skipped`，不能据此声称媒体对象隔离已验收。

本地 UI 回归可用 Playwright CLI 打开 Web 工作台，完成注册/登录、创建项目、导入 TXT、点击单条“驳回”，刷新后确认仍显示 `rejected`，再点击“批量通过”确认所有单元为 `approved`。最新快照证据保存在 `output/playwright/browser-adaptation-smoke-local-20260804/`；浏览器 DOM 通过不等于生产域名、Cookie 策略或多用户公网验收。

关系画布 UI 回归还应打开项目的 `/board/{project_id}`，确认状态筛选“缺失/待处理”会隐藏 `normal/ready/approved/completed` 节点，类型筛选只保留对应节点组，且边不会连接到已隐藏节点；本地验证通过不替代生产项目归属和公网浏览器验收。

账户安全 UI 回归还应登录同一账户两次，打开“账户安全”，确认 `/api/auth/sessions` 只返回短设备标签、当前标记和活跃时间，不出现 token/token_hash；撤销另一设备后该 session 不能继续访问 `/api/auth/me`，当前设备按钮必须禁用，服务端直接撤销当前 session 应返回 409。第二个账户使用第一个账户的 session id 必须返回 404。PostgreSQL 验收需确认 `012_session_device_metadata_postgresql.sql` 已进入迁移账本，旧 SQLite 库启动后能自动补齐两列。

2026-08-04 最新本地完整复验使用 `scripts/stack_smoke.py --include-av --include-billing`，结果为 `status=passed`、`generation=true`、`billing_contract=completed`、`billing_cancellation_contract=completed`、`ownership_contract=completed`、`asset_ownership_contract=completed`、`archive_import.status=completed`、`mounted=16`、`failed=0`。该命令使用临时 SQLite/runtime、local Provider、Remotion 和签名 smoke secret；它证明业务合同，不替代 PostgreSQL/BullMQ/MinIO/ComfyUI、真实 Provider、商户支付和公网验收。

本地 queued smoke 的驱动条件要显式区分：启动 API 时设置 `STUDIO_INTERNAL_TOKEN`，运行 smoke 时设置同一个值的 `STUDIO_SMOKE_INTERNAL_TOKEN`，否则脚本会按真实生产行为保持 Job 为 queued，并在本地验收时报缺少驱动令牌。带 `--include-billing` 时按 API provider 配置：signed-webhook 需要 `STUDIO_BILLING_PROVIDER=signed-webhook`、`STUDIO_BILLING_WEBHOOK_SECRET`、可用的 `STUDIO_BILLING_CHECKOUT_URL` 和 smoke 进程的 `STUDIO_SMOKE_BILLING_WEBHOOK_SECRET`；Stripe 需要 `STUDIO_BILLING_PROVIDER=stripe`、Stripe API/webhook/回跳配置和 smoke 进程的 `STUDIO_SMOKE_STRIPE_WEBHOOK_SECRET`。烟测会先验证 paid 幂等入账，再为新的 pending 订单验证 Stripe `checkout.session.async_payment_failed` 与 `checkout.session.expired`（signed-webhook 为 provider-neutral `cancelled`）的签名、取消幂等重放和零积分副作用。这里的取消不等于退款/争议处理；已支付订单的退款/拒付仍需独立账务流程和真实商户验收。这些变量只用于本地合同验收，真实密钥不得写入命令、报告或仓库。生产环境使用 BullMQ worker，不使用本地 smoke 内部驱动接口。

本轮新鲜浏览器证据位于 `output/playwright/browser-adaptation-audit-20260804/` 和 `output/playwright/browser-full-audit-20260804/`；后者的 `11-backend-audit.json` 必须显示 `shots=3`、`selected=3`、`reviewed=3`、`video_ready=3`、`composed=true`。证据通过本地 API/Web/worker 合同，不代表生产域名、GPU Provider、外部支付或公网部署完成。

若组合引擎为 Remotion，再追加 `--include-av`；它会上传合法 WAV、保存字幕时间线、检查成片同时存在 H.264 视频和 AAC 音频，并验证项目交换包重新映射音频资产。

项目交付还要验证归档下载：`GET /api/projects/{project_id}/archive` 应返回可打开的 ZIP，至少包含 `project-export.json`、`manifest.json` 和 `storyboards/`。`manifest.json` 的 `binary_assets` 是素材交付证据；`missing_binary_asset_count` 大于 0 时，只能按“工程包已交付、部分二进制待回读”报告，不能声称成片素材完整。跨环境迁移时用原始 ZIP 调用 `POST /api/projects/import-archive` 或 CLI 导入；服务端先校验成员路径、重复项、软链接、成员数和解压总量，再重新生成实体 ID 并重挂载本地/S3 二进制。验收响应中的 `archive_import.status` 应为 `completed`；`partial` 只能带着 `failed` 和 `declared_missing` 明确报告，不能当作素材完整。

视频模型 benchmark 使用同一个 ComfyUI Provider，不复制业务队列逻辑：

```bash
# 只校验 Wan2.2 workflow，不提交 GPU 任务
./.venv/bin/python scripts/benchmark_video_provider.py \
  --label wan22 --base-url http://127.0.0.1:8188 \
  --workflow /path/to/wan22-video-workflow.json

# 具备 GPU 后才显式触发；建议先从 1 次开始
./.venv/bin/python scripts/benchmark_video_provider.py \
  --label ltx --base-url http://127.0.0.1:8188 \
  --workflow /path/to/ltx-video-workflow.json --source-image /path/to/keyframe.png --run --runs 1
```

benchmark 输出中的 `completed` 只表示 workflow 产出了文件；是否可用于生产还要检查 `ffprobe.stream_types`、时长、画质/一致性审核和实际积分成本。

## 4. Provider 验收顺序

每个 Provider 可以先运行统一的脱敏 smoke；默认不联网，只有加 `--run` 才会调用外部服务：

```bash
./.venv/bin/python scripts/provider_smoke.py --kind text
./.venv/bin/python scripts/provider_smoke.py --kind vision --image /path/to/keyframe.png
./.venv/bin/python scripts/provider_smoke.py --kind image --workflow /app/workflows/image.json
./.venv/bin/python scripts/provider_smoke.py --kind video --workflow /app/workflows/wan22.json --source-image /path/to/keyframe.png
```

图片/视频 workflow 必须是 ComfyUI “Save (API Format)”导出的可读 UTF-8 JSON：顶层节点对象必须包含 `class_type` 和 `inputs`，节点引用必须指向同一 workflow 中的节点。图片角色必须消费 `{{PROMPT}}`；视频角色必须消费 `{{ASSET_ID}}`、`{{SHOT_ID}}` 或 `{{CLIENT_ID}}` 之一，图生视频再按需使用 `{{IMAGE_REF}}` 等已支持的输入；未知占位符会在启动前拒绝。生产 config gate、dry-run/`--run` Provider smoke 和视频 benchmark 共用同一结构/角色校验；错误报告只返回固定原因和 `workflow_valid=false`，不输出本地绝对路径、原始 JSON 或 Provider 响应。workflow 节点是否适配具体模型仍需通过真实 GPU smoke/benchmark 验收。
ComfyUI `/view` 的图片/视频下载由 Worker 分块读取，默认受 `COMFYUI_OUTPUT_MAX_BYTES=268435456`（256 MiB）限制，服务端硬上限为 2 GiB；超限在落盘前以脱敏 ProviderError 失败。若真实视频模型输出接近上限，应在目标 GPU 节点按实际码率调整该值并重新跑 Provider smoke/benchmark。

仓库本地回归还包含 `tests/test_provider_http_contracts.py`，通过临时本地 HTTP 服务验证请求/响应合同和媒体落盘；这只证明适配器边界，不证明真实模型、ComfyUI workflow、GPU 显存或商用额度可用。

OpenAI-compatible 文本/视觉以及 ComfyUI prompt/history/upload 控制响应体默认限制为 8 MiB，可通过 `STUDIO_PROVIDER_RESPONSE_MAX_BYTES` 调整，但服务端会将其限制在 1 KiB 至 64 MiB；超限统一返回脱敏 ProviderError，不把原始响应或解析细节写入 Job 错误。修改该上限后应重新执行 Provider HTTP 合同测试，并结合目标厂商最大输出 token/JSON 大小做一次真实 Provider smoke；图片/视频媒体下载另受 `COMFYUI_OUTPUT_MAX_BYTES` 限制。

BullMQ readiness 对编排器 `/health` 只接受不超过 64 KiB 的字节 JSON；超限、非字节响应或解析失败都按编排器不可用处理，`/api/ready` 必须保持 503。生产验收需在真实 Redis + 编排器容器中确认健康响应正常、异常健康 body 不会让 API 进入 ready。

确认配置和输入无误后，按同样参数追加 `--run`。报告只包含状态、目标主机、耗时、输出合同和问题数量；它不创建业务 Job，因此通过 smoke 不能替代积分、队列、对象存储和 Web 链路验收。
视频 benchmark 额外只输出 `provider`/`source` 白名单短标签、输出字节数、耗时和 ffprobe 流类型；Provider 原始 metadata、异常正文、远端文件名和本地路径均不会进入报告。出现失败时以固定 `comfyui-provider-contract-failed` 或 `benchmark-output-error` 表示，需结合服务端日志在受控环境排查。
`scripts/stack_smoke.py` 的 HTTP 失败、公开健康探针失败和异步 Job 失败也只输出固定方法/状态类别，不回显响应体、Provider 错误正文、任务错误字段或带凭据的探针 URL。烟测终端摘要用于判断合同失败；根因排查必须回到受控的 API/Worker/Provider 日志。

对象存储必须保持 private；不要为方便 Web 读取执行 MinIO `mc anonymous set download`。API 返回的媒体相对 URL 会在 `/assets/{filename}` 处校验当前项目归属，缓存不存在时按记录的 storage key 回读 S3/MinIO。

1. 文本：使用一个小项目验证小说导入、改编 Job、故事资产抽取/人工审核、角色卡、分集、分镜和提示词结构化结果；确认 `provider/model` 与 Job 事件一致，并检查故事资产的来源段落引用仍属于当前项目。
2. 图片：只生成一个镜头，确认 ComfyUI workflow 返回的文件能写入 MinIO 并能由 Web 读取。
3. 视频：在一个已审核关键帧上生成一个片段，确认资产状态、积分提交/失败退款和重试均有 Job 事件。
4. 视觉审核：验证 PASS、FAIL 和空响应三种结果；空响应必须保持 UNKNOWN/人工复核，不得冒充通过。
5. 旁白：用一个分集验证 `POST /api/episodes/{episode_id}/narration` 创建 `narration` Job、预留/提交 1 积分、生成音频资产并自动写入 `composition_settings.audio_tracks`；先在 Web 编辑并保存 `composition_settings.narration_text`，刷新项目后确认稿件仍在，省略 `text` 再生成时确认 Job payload 使用该稿件，并抽查 JSON/ZIP 导出导入仍保留它；确认 Web 下拉音色、内置 voice 白名单、`speed`、`instructions`、失败退款、幂等重放和 AI 语音披露。先用本地确定性 WAV 验证管线，再在受控环境用真实 OpenAI key/官方 SDK 做付费 smoke；不能把本地 WAV 或浏览器试听当作真实 TTS 完成。
6. 成片：用一个分集验证视频片段、音频轨道、字幕和最终 MP4；任何附加轨道渲染失败都不能静默回退成无声纯视频。

Provider 的 API key 不进入项目数据库、交换包、日志或 Web 设置；用户级设置只保存 provider/model/base_url，服务端仍从环境或 secret manager 读取 key。生产环境以部署方 provider 为准，用户设置不能降级到 local/其它未部署 Provider；base_url 只能使用部署环境地址或显式白名单地址，历史设置在 Provider 解析时也会复检。Compose `config-check` 和 `/api/ready` 还会检查 ComfyUI workflow 文件确实已挂载。TXT/Markdown/DOCX/EPUB/PDF 来源导入的授权确认由 API 服务端强制校验，不能通过绕过 Web 直接调用接口跳过。
语音 Provider 单独执行 `.venv/bin/python scripts/provider_smoke.py --kind speech` 做配置 dry-run；部署前必须确认报告里的 `provider` 为 `openai`、URL/模型/凭据均已配置，再在受控环境显式 `--run`。该 smoke 只保留目标主机、模型是否配置、输出字节数和白名单 metadata，不输出 key、完整文本或本地绝对路径。Web 音轨行提供试听控件；浏览器能播放 WAV/MP3 只证明媒体可读，不替代真实 Remotion 成片验收。

小说来源导入后还应验收故事资产抽取：`POST /api/projects/{project_id}/story-bible` 使用部署的文本 Provider 生成地点、道具和关系草稿；`source_segment_ids` 只能落在当前项目来源段落内，未知引用会被丢弃并保留运行警告。人工审核通过 `PATCH /api/story-entities/{id}` 和 `PATCH /api/story-relationships/{id}` 完成，重新抽取只 supersede 未审核草稿，不覆盖 approved 资产。必须确认 approved 故事资产会进入后续分镜/图片提示词，而 draft/rejected 不会进入确定性提示词；同时确认 `GET /api/projects/{project_id}/export` 和 ZIP 导入后故事资产数量、关系端点和来源段落映射仍正确；本地 Provider 的 `fallback=true` 只表示未调用模型，不能当作真实抽取完成。
生产验收还必须验证故事资产的异步合同：BullMQ 模式下 POST 默认返回 `story-bible` queued Job，使用 `GET /api/jobs/{id}` 或任务中心等待 `completed`，再读取 GET 故事资产；重放同一 `Idempotency-Key` 必须复用同一 Job，编排器短暂不可用时重复请求不得创建重复任务，失败重试和排队取消必须留下 Job 事件。`?queued=false` 仅作为受控诊断覆盖，不用于生产长文本请求。
所有异步 Job 还必须验收服务端进度合同：`GET /api/jobs` 和 `GET /api/jobs/{id}` 应返回 `progress_percent` 与 `progress_message`；queued 初始为 0，running 阶段在 1–99 之间推进，completed 为 100/“已完成”，failed、cancelled 和 retry 后分别显示终态/重新排队语义。刷新任务中心或重新打开浏览器后，进度必须从 API 恢复，不能只依赖前端内存状态。验收至少抽查一个文本/结构 Job 和一个媒体 Job，并把 Job JSON 与 `/api/jobs/{id}/events` 一并归档；如果只看到页面 200 或状态 completed，不能据此声称进度合同完成。
仓库提供真实浏览器回归入口 `bash scripts/browser_job_progress_smoke.sh`：它要求运行中的 API、Web 和本地 SQLite worker，使用临时浏览器账户创建 `?queued=true` 文本 Job，检查任务中心 `role=progressbar` 的 100%/“已完成”，然后 reload 再检查相同 Job。证据写入 `output/playwright/`，脚本在认证字段快照前清空临时输入；CI 的 `browser-smoke` 会上传独立证据。该脚本通过本地回归只证明浏览器/API/worker 合同，生产仍须在真实 BullMQ/PostgreSQL/Provider 栈重复验收。

完整制作链路也可以由 `STUDIO_LOCAL_SMOKE_BROWSER=1 make local-preview-smoke` 一次启动并验收；若 API/Web/worker 已经在运行，可单独执行：`STUDIO_BROWSER_EVIDENCE_DIR=output/playwright/browser-full-pipeline-local bash scripts/browser_full_pipeline_smoke.sh`。成功条件不是页面打开或单个 Job completed，而是 `11-backend-audit.json` 的三镜头审计和 `10-composition.yml` 中的“打开成片 ↗”；若仅有部分视频，合成必须失败并由脚本报错。该脚本使用本地预览 provider，视觉结果预期为 `UNKNOWN`，不代表真实视觉模型通过。
项目级制作门禁可用 `GET /api/projects/{project_id}/readiness` 复核：它按当前用户返回来源、改编、故事资产、结构、角色母版、提示词、采用关键帧、视觉审核、视频和分集合成的 `ready/pending/needs-review/missing` 阶段，并单独给出 `ready_for_video`、`ready_for_composition`、`ready_for_delivery`。服务端只有在所有必需阶段（包括角色母版、提示词、完整视频和分集合成）都 ready，且结构/关键帧/视频/合成绑定当前改编内容 revision 时才返回 `ready_for_delivery=true`。改编稿或镜头语义变化后，结构阶段会标记 stale；应先重新生成项目结构，再重新生成提示词、关键帧、审核、视频和合成，旧媒体保留用于审计但不再计入 readiness。改编单元处于 `review` 或 `rejected` 时，提示词、关键帧、视频、批量视频和分集合成入口统一返回 409，重新批准后才恢复；后台 Worker 也会在真正执行前复核审核状态和 revision，避免排队期间改稿/改审校状态导致旧任务写回。本地预览视觉审核的 `UNKNOWN` 会保留 `needs_human_review=true`，不能只因视频或合成已生成就报告“可交付”；生产验收应把该快照与 Job 事件、真实 Provider PASS/FAIL 和人工抽检一起归档。Web 首页的“制作就绪度”卡只是该服务端合同的可视化，不是新的事实来源。

角色母版还支持用户上传自己的参考图：`POST /api/characters/{character_id}/reference-upload` 只接受 PNG/JPEG/WebP，服务端验证魔数、大小和当前用户项目归属；上传不扣积分，单视图状态为 `uploaded`，正面/侧面/背面齐全后才是 `ready`。对象存储模式使用 `character-references/{reference_id}/{filename}` 私有 key，媒体仍必须经 `/assets/{filename}` 归属鉴权回读。生产部署需要用真实 MinIO/S3 私有 bucket 和跨用户 404 测试复核该路径；不能把上传成功的 HTTP 200 当作对象存储验收完成。
项目结构也必须单独验收：BullMQ 模式下 `POST /api/projects/{project_id}/structure` 默认返回 `structure` queued Job，完成后项目必须同时存在角色、分集和每集镜头；中途失败重试不得重复插入已完成阶段，重放 `Idempotency-Key` 必须复用同一 Job。该入口替代 Web 端串行等待角色/分集/分镜三个文本请求。

兼容 API 也必须保持同一边界：空 payload 调用 `POST /api/projects/{project_id}/characters`、`POST /api/projects/{project_id}/outline` 或 `POST /api/episodes/{episode_id}/shots` 时，BullMQ/显式 `?queued=true` 应返回带 `payload_json.stage` 的 `structure` queued Job，完成后分别检查角色、分集或镜头结果；同一 `Idempotency-Key` 重放必须复用 Job，失败可重试、排队可取消。无队列参数的本地预览继续返回历史列表响应；带明确角色卡 payload 的 characters 请求属于人工写入，不触发文本 Provider。

提示词和视觉审核必须单独验收异步合同：BullMQ 模式下 `POST /api/shots/{shot_id}/image-prompts` 默认返回 `prompt` queued Job，`POST /api/assets/{asset_id}/review` 和 `POST /api/projects/{project_id}/asset-reviews` 默认返回 `vision-review` queued Job；API 返回时间不应等待文本/视觉 Provider，也不应因镜头缺少提示词而先调用文本 Provider。使用同一 `Idempotency-Key` 重放必须复用 Job，任务完成后检查 `image_prompts`、`asset_reviews` 的 PASS/FAIL/UNKNOWN、`payload_json` 中的审核类型/提示词和 `job_events`；FAIL 必须清除 `consistency_confirmed`。`?queued=false` 仅用于受控诊断，本地预览可保持同步。

Web 工作台的故事资产抽取、单素材视觉审核和批量视觉审核按钮必须发送新的用户作用域 `Idempotency-Key`；任务中心展开时间线应在 5 秒轮询期间持续读取 `/api/jobs/{id}/events`，而不是只在第一次展开时读取。登录表单必须把密码输入包含在 `<form>` 内，并设置 `autocomplete`/`required`/`minLength`，以便浏览器密码管理和可访问性检查不退化。创建项目的 `POST /api/projects` 也必须发送并保留用户作用域 `Idempotency-Key`；相同键只能复用相同标题、故事、风格和目标时长的项目，payload 改变必须返回 409，不能因客户端重试产生重复项目或重复故事梗概来源。

充值链路可用 `.venv/bin/python scripts/stack_smoke.py --include-billing` 验收：脚本按 `/api/health` 的 billing provider 选择 signed-webhook 或 Stripe，创建 starter pending 订单，验证 checkout URL，发送包含 `amount_cents`/`currency` 的 paid 回调并重放同一事件，确认只增加一次积分；随后验证未支付取消、全额 refund、chargeback funds withdrawn、同一 dispute funds reinstated 以及所有事件的幂等重放。退款/拒付写入独立 `billing_adjustments`，负积分流水允许余额进入负数欠账，拒付恢复只恢复原 withdrawal adjustment；Web 工作台的订单列表与积分流水必须显示这些状态/调整，不能只显示原始 paid。Stripe success/cancel 回跳必须带本地订单号，回到工作台后只允许有限次数读取服务端订单状态，不能用前端回跳直接发放积分；最终以支付 Webhook 和账本为准。signed-webhook 需向 smoke 进程注入 `STUDIO_SMOKE_BILLING_WEBHOOK_SECRET`，API 需配置 `STUDIO_BILLING_WEBHOOK_SECRET` 和 `STUDIO_BILLING_CHECKOUT_URL`；Stripe 需注入 `STUDIO_SMOKE_STRIPE_WEBHOOK_SECRET`；secret 不写入报告。CI production compose 使用 `mock-stripe` 验证真实 Stripe form-encoded Checkout Session、订单级 Idempotency-Key 和 raw-body `Stripe-Signature` adapter 合同；Stripe webhook 至少订阅 `refund.created`、`charge.dispute.funds_withdrawn`、`charge.dispute.funds_reinstated`，接入生产时还必须用 Stripe 测试/生产商户环境验收 Checkout Session、重复事件、异步支付失败/过期、退款/争议和公网回调，不能把本地 mock 当成真实支付完成。

## 5. 故障定位

| 现象 | 先看 | 结论边界 |
|---|---|---|
| API 未启动 | `docker compose ps`、`migrate` 日志 | migration 失败时不能称 API 部署完成 |
| `/api/ready` 503 | `/api/health`、PostgreSQL/Redis/MinIO/orchestrator health | health 200 不代表 ready |
| Job 长时间 running | `/api/jobs/:id`、`/api/jobs/:id/events`、`progress_message` | 检查租约回收、worker 日志和 Provider，不直接改数据库状态；若进度长期不变，先核对 Worker heartbeat/阶段日志 |
| 图片生成失败 | ComfyUI `/system_stats`、workflow、Job error | 端口可达不等于 workflow 成功 |
| 成片无声音/字幕 | composition settings、Remotion manifest、ffprobe streams | 必须同时看到视频和音频流 |
| Web 可打开但无法操作 | `/api/auth/me`、CSRF、CORS、TrustedHost | 页面 200 不等于认证写请求成功 |
| 媒体 URL 返回 404 | 当前 session、资产所属项目、`assets`/角色母版/分集成片记录 | 本地 `/assets/{filename}` 现在按用户归属鉴权，不能用猜文件名绕过项目权限 |

## 6. 回滚与证据

只回滚应用镜像或代码，不删除数据卷：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml stop web orchestrator-worker api orchestrator migrate
# 部署已验证的应用版本后重新执行 up --build -d
```

每次发布至少保存：runtime preflight JSON、Compose `ps`、migration 成功日志、`stack_smoke.py` JSON、包含进度字段的 Job JSON、Job 事件和最终 MP4 的 `ffprobe` 流信息。报告时区分：本地单测、容器 active、真实 Provider 调用、公网/UI 验收。

项目如果只有用户输入的故事梗概、没有小说来源改编单元，改编阶段是可选的 `0/0`，服务端会明确显示“纯故事梗概项目，无需小说改编”；不能把这个阶段误判为缺失。只要其余必需阶段满足门禁，仍可进入视频、合成和交付。

若不用 Compose、直接以 `npm start` 验收 Web，必须在 `next build` 阶段注入 `NEXT_PUBLIC_API_BASE`（例如 `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8787 npm run build`），再启动 `npm start`；Next 客户端公开环境变量会固化在构建产物中，不能只在启动阶段注入。
