#!/usr/bin/env bash

set -Eeuo pipefail

# 在没有 Docker/Podman 的开发机上复现完整的 SQLite + local worker + Next
# preview 栈。这个入口故意在启动 npm start 前重建 Web，并显式注入
# NEXT_PUBLIC_API_BASE；Next 的 public env 是 build-time 配置，不能依赖旧的
# .next 目录。脚本只管理自己启动的 PID 和自己创建的临时 runtime。

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="${PYTHON_FALLBACK:-python3}"
fi

API_URL="${STUDIO_LOCAL_SMOKE_API_URL:-http://127.0.0.1:8787}"
WEB_URL="${STUDIO_LOCAL_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
API_PORT="${STUDIO_LOCAL_SMOKE_API_PORT:-8787}"
WEB_PORT="${STUDIO_LOCAL_SMOKE_WEB_PORT:-3000}"
EVIDENCE_DIR="${STUDIO_LOCAL_SMOKE_EVIDENCE_DIR:-${PROJECT_ROOT}/output/playwright/local-preview-smoke-$(date +%Y%m%d-%H%M%S)}"

if [[ -n "${STUDIO_LOCAL_SMOKE_RUNTIME_DIR:-}" ]]; then
  RUNTIME_DIR="${STUDIO_LOCAL_SMOKE_RUNTIME_DIR}"
else
  RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/novel-to-comic-preview.XXXXXX")"
fi

mkdir -p "${RUNTIME_DIR}" "${EVIDENCE_DIR}"

API_LOG="${EVIDENCE_DIR}/api.log"
WORKER_LOG="${EVIDENCE_DIR}/worker.log"
WEB_BUILD_LOG="${EVIDENCE_DIR}/web-build.log"
WEB_LOG="${EVIDENCE_DIR}/web.log"
STACK_SMOKE_JSON="${EVIDENCE_DIR}/stack-smoke.json"

# 这些只用于本地合同验收。默认值不写入仓库、不打印到终端；生产密钥不能
# 复用本入口的本地配置。
INTERNAL_TOKEN="${STUDIO_LOCAL_SMOKE_INTERNAL_TOKEN:-local-preview-internal-token-20260804}"
BILLING_SECRET="${STUDIO_LOCAL_SMOKE_BILLING_SECRET:-local-preview-billing-webhook-secret-20260804}"

PIDS=()

stop_children() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid}" ]]; then
      wait "${pid}" 2>/dev/null || true
    fi
  done
}

finish() {
  local status=$?
  trap - EXIT INT TERM
  stop_children
  if [[ "${status}" == "0" ]]; then
    echo "status=passed"
  else
    echo "status=failed" >&2
  fi
  echo "runtime_dir=${RUNTIME_DIR}"
  echo "evidence_dir=${EVIDENCE_DIR}"
  echo "api_log=${API_LOG}"
  echo "worker_log=${WORKER_LOG}"
  echo "web_build_log=${WEB_BUILD_LOG}"
  echo "web_log=${WEB_LOG}"
  exit "${status}"
}

trap finish EXIT
trap 'exit 130' INT TERM

if [[ ! -x "${PYTHON}" ]] && ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "python_not_found" >&2
  exit 127
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm_not_found" >&2
  exit 127
fi

wait_for_http() {
  local url="$1"
  local name="$2"
  local attempts="${3:-90}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if "${PYTHON}" - "${url}" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        response.read(128)
    raise SystemExit(0)
except Exception:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    for pid in "${PIDS[@]:-}"; do
      if [[ -n "${pid}" ]] && ! kill -0 "${pid}" 2>/dev/null; then
        echo "${name}_process_exited" >&2
        return 1
      fi
    done
    sleep 1
  done
  echo "${name}_health_timeout" >&2
  return 1
}

export STUDIO_ENV=development
export STUDIO_STORE=sqlite
export STUDIO_RUNTIME_DIR="${RUNTIME_DIR}"
export STUDIO_REQUIRE_AUTH=false
export STUDIO_AUTH_COOKIE_SECURE=false
export STUDIO_ALLOWED_HOSTS="localhost,127.0.0.1"
export STUDIO_CORS_ORIGINS="${WEB_URL}"
export STUDIO_QUEUE_BACKEND=local
export STUDIO_STORAGE=local
export STUDIO_COMPOSE_ENGINE=remotion
export STUDIO_BILLING_PROVIDER=signed-webhook
export STUDIO_BILLING_WEBHOOK_SECRET="${BILLING_SECRET}"
export STUDIO_BILLING_CHECKOUT_URL="${WEB_URL}/billing/checkout"
export STUDIO_BILLING_SUCCESS_URL="${WEB_URL}/billing/success"
export STUDIO_BILLING_CANCEL_URL="${WEB_URL}/billing/cancel"
export STUDIO_ALLOW_LOCAL_TOP_UP=true
export STUDIO_INTERNAL_TOKEN="${INTERNAL_TOKEN}"
export STUDIO_TEXT_PROVIDER=local
export STUDIO_IMAGE_PROVIDER=local
export STUDIO_VIDEO_PROVIDER=local
export STUDIO_VISION_PROVIDER=local
export STUDIO_SPEECH_PROVIDER=local
export STUDIO_SPEECH_ALLOWED_VOICES="cedar,marin,alloy,ash,ballad,coral,echo,fable,nova,onyx,sage,shimmer,verse"

# 必须先构建 Web，再启动 production preview server；否则旧 .next 产物可能
# 没有当前 API origin，浏览器会把 /api 请求发给 Next 自身。
(
  cd "${PROJECT_ROOT}/web"
  NEXT_PUBLIC_API_BASE="${API_URL}" npm run build
) >"${WEB_BUILD_LOG}" 2>&1

(
  cd "${PROJECT_ROOT}"
  "${PYTHON}" -m uvicorn studio_api.main:app --host 127.0.0.1 --port "${API_PORT}"
) >"${API_LOG}" 2>&1 &
PIDS+=("$!")

wait_for_http "${API_URL}/api/health" api 90
wait_for_http "${API_URL}/api/ready" api_ready 30

(
  cd "${PROJECT_ROOT}"
  "${PYTHON}" -m studio_api.worker --runtime-dir "${RUNTIME_DIR}" --interval 0.5
) >"${WORKER_LOG}" 2>&1 &
PIDS+=("$!")

(
  cd "${PROJECT_ROOT}/web"
  NEXT_PUBLIC_API_BASE="${API_URL}" npm start -- --hostname 127.0.0.1 --port "${WEB_PORT}"
) >"${WEB_LOG}" 2>&1 &
PIDS+=("$!")

wait_for_http "${WEB_URL}" web 90

STUDIO_SMOKE_API_URL="${API_URL}" \
STUDIO_SMOKE_WEB_URL="${WEB_URL}" \
STUDIO_SMOKE_INTERNAL_TOKEN="${INTERNAL_TOKEN}" \
STUDIO_SMOKE_BILLING_WEBHOOK_SECRET="${BILLING_SECRET}" \
  "${PYTHON}" "${PROJECT_ROOT}/scripts/stack_smoke.py" \
    --api-url "${API_URL}" \
    --web-url "${WEB_URL}" \
    --include-source-files \
    --include-av \
    --include-billing \
    >"${STACK_SMOKE_JSON}"

if [[ "${STUDIO_LOCAL_SMOKE_BROWSER:-0}" == "1" ]]; then
  if [[ "${API_URL}" != "http://127.0.0.1:8787" || "${WEB_URL}" != "http://127.0.0.1:3000" ]]; then
    echo "browser_smoke_requires_default_local_urls" >&2
    exit 2
  fi
  STUDIO_SMOKE_API_URL="${API_URL}" \
  STUDIO_SMOKE_WEB_URL="${WEB_URL}" \
  STUDIO_BROWSER_SESSION="local-preview-full-$(date +%s)" \
  STUDIO_BROWSER_EVIDENCE_DIR="${EVIDENCE_DIR}/full" \
    bash "${PROJECT_ROOT}/scripts/browser_full_pipeline_smoke.sh" \
    >"${EVIDENCE_DIR}/browser-full.log" 2>&1
  STUDIO_SMOKE_API_URL="${API_URL}" \
  STUDIO_SMOKE_WEB_URL="${WEB_URL}" \
  STUDIO_BROWSER_SESSION="local-preview-narration-$(date +%s)" \
  STUDIO_BROWSER_EVIDENCE_DIR="${EVIDENCE_DIR}/narration" \
    bash "${PROJECT_ROOT}/scripts/browser_narration_smoke.sh" \
    >"${EVIDENCE_DIR}/browser-narration.log" 2>&1
fi
