# 本地优先复用仓库虚拟环境；CI 没有 .venv 时回退到 runner 的 Python。
# 命令行显式传入 PYTHON=... 仍可覆盖这个默认值。
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: test compile web-build rendering-check orchestrator-check compose-check local-preview-smoke edge-smoke production-preflight production-edge-preflight quality

test:
	$(PYTHON) -m unittest discover -s tests -q

compile:
	$(PYTHON) -m compileall -q studio_api studio_core scripts tests

web-build:
	cd web && npm run build

rendering-check:
	cd rendering && npm run typecheck && npm test

orchestrator-check:
	cd orchestrator && npm run typecheck && npm test

compose-check:
	@if docker compose version >/dev/null 2>&1; then \
		docker compose -f infra/docker-compose.yml config --quiet; \
		STUDIO_PUBLIC_HOST=studio.example.com ACME_EMAIL=ops@example.com \
			docker compose -f infra/docker-compose.yml -f infra/docker-compose.edge.yml config --quiet; \
		docker compose -f infra/docker-compose.dev.yml config --quiet; \
	elif podman compose version >/dev/null 2>&1; then \
		podman compose -f infra/docker-compose.yml config --quiet; \
		STUDIO_PUBLIC_HOST=studio.example.com ACME_EMAIL=ops@example.com \
			podman compose -f infra/docker-compose.yml -f infra/docker-compose.edge.yml config --quiet; \
		podman compose -f infra/docker-compose.dev.yml config --quiet; \
	else \
		$(PYTHON) scripts/compose_static_check.py --json; \
	fi

local-preview-smoke:
	bash scripts/local_preview_smoke.sh

edge-smoke:
	$(PYTHON) scripts/edge_runtime_smoke.py --json

production-preflight:
	$(PYTHON) scripts/compose_static_check.py --json
	$(PYTHON) scripts/runtime_preflight.py --json \
		--require-live --require-production-config --require-container-runtime \
		--require-postgres --require-redis --require-minio \
		--require-orchestrator --require-comfyui \
		--orchestrator-url "$${STUDIO_ORCHESTRATOR_URL:-http://127.0.0.1:8790}" \
		$(if $(REQUIRE_GPU),--require-gpu,) \
		$(if $(PROBE_DATA_SERVICES),--probe-data-services,)

production-edge-preflight: production-preflight edge-smoke

quality: test compile web-build rendering-check orchestrator-check compose-check
