#!/usr/bin/env python3
"""在本机用真实 Caddy 进程验收 Edge 路由和 API/Web upstream。

该 smoke 不启动项目服务、不读取凭据，也不申请公网证书。它只启动两个
进程内 HTTP 假 upstream，再用临时 Caddyfile 将生产路由改为本机高位 HTTP
端口，验证 Edge 自身、API、媒体和 Web fallback 的真实反代行为。
"""

from __future__ import annotations

import argparse
import http.server
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = ROOT / "infra" / "Caddyfile"
STARTUP_TIMEOUT_SECONDS = 8.0


class _FakeUpstreamHandler(http.server.BaseHTTPRequestHandler):
    role = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.role == "api" and self.path == "/api/health":
            body = b'{"status":"ok"}'
        elif self.role == "api" and self.path.startswith("/assets"):
            body = b"EDGE-SMOKE-ASSET"
        elif self.role == "api":
            body = b"EDGE-SMOKE-API"
        else:
            body = b"EDGE-SMOKE-WEB"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_upstream(port: int, role: str) -> http.server.ThreadingHTTPServer:
    handler = type(f"{role.title()}SmokeHandler", (_FakeUpstreamHandler,), {"role": role})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _smoke_caddyfile(api_port: int, web_port: int, health_port: int, public_port: int) -> str:
    source = CADDYFILE.read_text(encoding="utf-8")
    source = source.replace("admin off\n\temail {$ACME_EMAIL}", "admin off\n\tauto_https off")
    source = source.replace("{$STUDIO_PUBLIC_HOST}", f"http://127.0.0.1:{public_port}")
    source = source.replace("api:8787", f"127.0.0.1:{api_port}")
    source = source.replace("web:3000", f"127.0.0.1:{web_port}")
    source = source.replace(":8080", f":{health_port}")
    if "{$STUDIO_PUBLIC_HOST}" in source or "api:8787" in source or "web:3000" in source:
        raise RuntimeError("production Caddyfile smoke substitutions are incomplete")
    return source


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        return 0, ""


def run(*, require_caddy: bool = False) -> dict[str, Any]:
    caddy = shutil.which("caddy")
    if not caddy:
        report = {"status": "skipped", "reason": "caddy-not-found", "required": require_caddy}
        if require_caddy:
            report["status"] = "failed"
        return report

    api_port, web_port, health_port, public_port = (_free_port() for _ in range(4))
    upstreams = [_start_upstream(api_port, "api"), _start_upstream(web_port, "web")]
    caddy_process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="edge-runtime-smoke-") as directory:
            config_path = Path(directory) / "Caddyfile"
            config_path.write_text(_smoke_caddyfile(api_port, web_port, health_port, public_port), encoding="utf-8")
            caddy_process = subprocess.Popen(
                [caddy, "run", "--config", str(config_path), "--adapter", "caddyfile"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
            health_url = f"http://127.0.0.1:{health_port}/_edge_health"
            while time.monotonic() < deadline:
                if caddy_process.poll() is not None:
                    return {"status": "failed", "reason": "caddy-exited-during-startup"}
                status, _ = _get(health_url)
                if status == 200:
                    break
                time.sleep(0.1)
            else:
                return {"status": "failed", "reason": "caddy-startup-timeout"}

            checks = {
                "edge_process": (health_url, ""),
                "edge_api_upstream": (f"http://127.0.0.1:{health_port}/_edge_api_health", '{"status":"ok"}'),
                "edge_web_upstream": (f"http://127.0.0.1:{health_port}/_edge_web_health", "EDGE-SMOKE-WEB"),
                "public_api_route": (f"http://127.0.0.1:{public_port}/api/health", '{"status":"ok"}'),
                "public_assets_route": (f"http://127.0.0.1:{public_port}/assets/demo.png", "EDGE-SMOKE-ASSET"),
                "public_web_route": (f"http://127.0.0.1:{public_port}/", "EDGE-SMOKE-WEB"),
            }
            failures: list[str] = []
            for name, (url, expected_body) in checks.items():
                status, body = _get(url)
                if status != 200 or (expected_body and body != expected_body):
                    failures.append(name)
            if failures:
                return {"status": "failed", "reason": "route-contract-failed", "failed_checks": failures}
            return {"status": "passed", "checks": list(checks)}
    except (OSError, RuntimeError) as exc:
        return {"status": "failed", "reason": "edge-smoke-error", "error_type": type(exc).__name__}
    finally:
        if caddy_process is not None:
            caddy_process.terminate()
            try:
                caddy_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                caddy_process.kill()
                caddy_process.wait(timeout=3)
        for upstream in upstreams:
            upstream.shutdown()
            upstream.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--require-caddy", action="store_true", help="Caddy 不可用时返回失败")
    args = parser.parse_args()
    report = run(require_caddy=args.require_caddy)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"edge_runtime_smoke: {report['status']}")
        for key, value in report.items():
            if key != "status":
                print(f"- {key}: {value}")
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
