# BullMQ 编排服务

这是可选的 Redis/BullMQ 触发器，不替换 FastAPI 的领域和积分账本。

启动条件：

```bash
npm install
REDIS_URL=redis://127.0.0.1:6379 npm run server
STUDIO_API_URL=http://127.0.0.1:8787 \
STUDIO_INTERNAL_TOKEN='仅在本机环境变量中设置' \
REDIS_URL=redis://127.0.0.1:6379 npm run worker
```

API 设置 `STUDIO_QUEUE_BACKEND=bullmq`、`STUDIO_ORCHESTRATOR_URL=http://127.0.0.1:8790`、同一个 `STUDIO_INTERNAL_TOKEN` 后，图片、视频、角色三视图和合成默认以 queued Job 提交到此服务；`?queued=false` 可显式覆盖为同步调试调用。没有 Redis 时使用 `studio_api.worker`，不影响本地开发。

生产建议同时设置 `ORCHESTRATOR_TOKEN`，并在 API 侧设置同值的 `STUDIO_ORCHESTRATOR_TOKEN`；入队接口会校验 `X-Orchestrator-Token`。未设置时保留本机开发兼容模式。

`POST /jobs` 必须使用 `application/json`，只接受 `image`、`video`、`text`、`prompt`、`compose`、`character-reference`、`story-bible`、`structure`、`vision-review`、`narration` 十种任务；默认请求体上限为 `ORCHESTRATOR_MAX_BODY_BYTES=65536`。非法 JSON、未知任务类型和超长标识符会在入队前拒绝。

容器部署时编排器绑定 `0.0.0.0`，`GET /health` 会实际执行 Redis `PING`；Redis 不可用时返回 503，避免 Compose 把未连接队列误判为 ready。

Worker 只有在 FastAPI 返回 Job `completed` 或 `cancelled` 时确认 BullMQ 消息；如果 API 返回 `queued`/`running`，worker 会抛错让 BullMQ 重试。API 的 `STUDIO_JOB_STALE_SECONDS` 默认 1800 秒，用于回收进程崩溃遗留的 running Job。
