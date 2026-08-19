#!/usr/bin/env python3
"""在没有 Docker/Podman 时检查 Compose 的静态部署合同。

这不是 ``docker compose config`` 的替代品：它不展开变量，也不验证镜像
能否拉取。它只检查仓库中必须保持稳定的服务名、应用容器安全基线、镜像
版本基线和本地预览栈隔离，避免本地质量门被容器运行时工具硬阻断。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SERVICES = (
    "config-check",
    "api",
    "migrate",
    "schema-check",
    "orchestrator",
    "orchestrator-worker",
    "web",
)
REQUIRED_PRODUCTION_SERVICES = {
    "postgres",
    "redis",
    "minio",
    "minio-init",
    "migrate",
    "config-check",
    "schema-check",
    "api",
    "orchestrator",
    "orchestrator-worker",
    "web",
}
IMAGE_VARIABLES = ("POSTGRES_IMAGE", "REDIS_IMAGE", "MINIO_IMAGE", "MINIO_MC_IMAGE")


def _service_section(compose: str, service: str) -> str | None:
    """Extract a top-level service block without pretending to parse YAML."""

    lines = compose.splitlines()
    marker = f"  {service}:"
    start = next((index for index, line in enumerate(lines) if line == marker), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z0-9_.-]+:\s*$", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _service_names(compose: str) -> set[str]:
    names: set[str] = set()
    for line in compose.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if match:
            names.add(match.group(1))
    return names


def check(root: Path = ROOT) -> list[str]:
    production_path = root / "infra" / "docker-compose.yml"
    development_path = root / "infra" / "docker-compose.dev.yml"
    ci_path = root / "infra" / "docker-compose.ci.yml"
    edge_path = root / "infra" / "docker-compose.edge.yml"
    caddyfile_path = root / "infra" / "Caddyfile"
    env_path = root / "infra" / ".env.example"
    errors: list[str] = []
    try:
        production = production_path.read_text(encoding="utf-8")
        development = development_path.read_text(encoding="utf-8")
        ci_override = ci_path.read_text(encoding="utf-8")
        edge_override = edge_path.read_text(encoding="utf-8")
        caddyfile = caddyfile_path.read_text(encoding="utf-8")
        env_example = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"无法读取 Compose 合同文件: {exc}"]

    missing_services = sorted(REQUIRED_PRODUCTION_SERVICES - _service_names(production))
    if missing_services:
        errors.append(f"production services missing: {','.join(missing_services)}")
    for service in PRODUCTION_SERVICES:
        section = _service_section(production, service)
        if section is None:
            continue
        for required in (
            'user: "10001:10001"',
            "read_only: true",
            "no-new-privileges:true",
            "/tmp:rw,noexec,nosuid",
        ):
            if required not in section:
                errors.append(f"{service} missing {required}")
    for service in ("orchestrator", "orchestrator-worker", "web"):
        section = _service_section(production, service)
        if section is not None and "cap_drop:" not in section:
            errors.append(f"{service} missing cap_drop")

    production_web = _service_section(production, "web") or ""
    if "NEXT_PUBLIC_API_BASE: ${NEXT_PUBLIC_API_BASE:-}" not in production_web:
        errors.append("production web must default NEXT_PUBLIC_API_BASE to same-origin")
    for service in ("config-check", "api"):
        section = _service_section(production, service) or ""
        if "STUDIO_PUBLIC_HOST: ${STUDIO_PUBLIC_HOST:-}" not in section:
            errors.append(f"production {service} must receive STUDIO_PUBLIC_HOST")
    if "STUDIO_CORS_ORIGINS=https://studio.example.com" not in env_example:
        errors.append("production env example must align STUDIO_CORS_ORIGINS with edge HTTPS host")
    if "NEXT_PUBLIC_API_BASE: http://localhost:8787" not in development:
        errors.append("development web must retain explicit local NEXT_PUBLIC_API_BASE")
    web_dockerfile = root / "web" / "Dockerfile"
    try:
        dockerfile_source = web_dockerfile.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"无法读取 Web Dockerfile: {exc}")
    else:
        if "ARG NEXT_PUBLIC_API_BASE=" not in dockerfile_source:
            errors.append("Web Dockerfile must default NEXT_PUBLIC_API_BASE to empty")
    if "NEXT_PUBLIC_API_BASE=" not in env_example:
        errors.append("production env example must document NEXT_PUBLIC_API_BASE")
    for required in (
        "edge:",
        "caddy:2.11.4-alpine",
        "CADDY_IMAGE",
        "STUDIO_PUBLIC_HOST",
        "ACME_EMAIL",
        "80:80",
        "443:443",
        "manhua-caddy-data",
        "condition: service_healthy",
    ):
        if required not in edge_override:
            errors.append(f"production edge overlay missing {required}")
    if ":latest" in edge_override:
        errors.append("production edge overlay must not use :latest")
    for required in (
        "{$STUDIO_PUBLIC_HOST}",
        "@api path /api /api/* /assets /assets/* /docs /docs/* /openapi.json",
        "reverse_proxy api:8787",
        "reverse_proxy web:3000",
        ":8080",
        "/_edge_health",
    ):
        if required not in caddyfile:
            errors.append(f"Caddyfile missing {required}")

    if ":latest" in production:
        errors.append("production Compose must not use :latest")
    for variable in IMAGE_VARIABLES:
        if variable not in production or variable not in env_example:
            errors.append(f"missing explicit image variable baseline: {variable}")
    for version in ("RELEASE.2025-04-22T22-12-26Z", "RELEASE.2025-04-16T18-13-26Z"):
        if version not in production:
            errors.append(f"missing pinned MinIO image baseline: {version}")

    for required in (
        "STUDIO_ENV: development",
        "STUDIO_STORE: sqlite",
        "STUDIO_QUEUE_BACKEND: local",
        "STUDIO_STORAGE: local",
        "STUDIO_TEXT_PROVIDER: local",
        "STUDIO_IMAGE_PROVIDER: local",
        "STUDIO_VIDEO_PROVIDER: local",
        "STUDIO_VISION_PROVIDER: local",
        "STUDIO_SPEECH_PROVIDER: local",
    ):
        if required not in development:
            errors.append(f"development Compose missing {required}")
    for forbidden in ("\n  postgres:", "\n  redis:", "\n  minio:"):
        if forbidden in development:
            errors.append(f"development Compose must not define {forbidden.strip()}")

    for required in (
        "CI-only production integration override",
        "mock-text:",
        "mock-comfyui:",
        "STUDIO_TEXT_PROVIDER: openai-compatible",
        "STUDIO_IMAGE_PROVIDER: comfyui",
        "STUDIO_VIDEO_PROVIDER: comfyui",
        "STUDIO_VISION_PROVIDER: openai-compatible",
        "STUDIO_SPEECH_PROVIDER: openai",
        "condition: service_healthy",
    ):
        if required not in ci_override:
            errors.append(f"CI production Compose override missing {required}")

    dockerfiles = (
        (root / "infra" / "api.Dockerfile", "USER studio:studio"),
        (root / "web" / "Dockerfile", "USER nextjs:nextjs"),
        (root / "orchestrator" / "Dockerfile", "USER studio:studio"),
    )
    for path, user_directive in dockerfiles:
        try:
            contents = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read {path}: {exc}")
            continue
        if user_directive not in contents or "10001" not in contents:
            errors.append(f"{path.name} missing non-root UID/GID 10001 contract")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()
    errors = check()
    report = {
        "status": "failed" if errors else "passed",
        "mode": "static",
        "errors": errors,
        "docker_compose_config_not_run": True,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"compose_static_check: {report['status']}")
        for error in errors:
            print(f"- {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
