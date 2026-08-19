#!/usr/bin/env python3
"""CI-only bounded mock for OpenAI-compatible and ComfyUI HTTP contracts.

This server deliberately does not emulate model quality.  It exists only for the
production Compose integration job, where the real external providers and GPU are
not available.  It returns deterministic, schema-valid content and small media
files so PostgreSQL, Redis/BullMQ, MinIO, API, worker and Web can be exercised as
one container topology.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ProviderState:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.lock = threading.Lock()
        self.jobs: dict[str, str] = {}
        self.stripe_sessions: dict[str, dict[str, object]] = {}
        self.video_path = self._make_video() if kind == "comfyui" else None

    @staticmethod
    def _make_video() -> Path:
        target = Path(tempfile.gettempdir()) / f"ai-manhua-ci-mock-{os.getpid()}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x282645:s=320x180:r=25",
                "-t",
                "1",
                "-an",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-y",
                str(target),
            ],
            check=True,
        )
        return target

    def create_job(self, workflow: object) -> str:
        prompt_id = f"ci-{uuid.uuid4().hex}"
        serialized = json.dumps(workflow, ensure_ascii=False)
        job_kind = "video" if "MockVideo" in serialized else "image"
        with self.lock:
            self.jobs[prompt_id] = job_kind
        return prompt_id

    def job_kind(self, prompt_id: str) -> str:
        with self.lock:
            return self.jobs.get(prompt_id, "")

    def create_stripe_session(self, form: dict[str, list[str]], idempotency_key: str) -> dict[str, object]:
        stable_key = idempotency_key.strip() or form.get("client_reference_id", [""])[0].strip()
        digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
        session_id = f"cs_ci_{digest}"
        session = {
            "id": session_id,
            "url": f"https://checkout.ci.example.test/session/{session_id}",
            "client_reference_id": form.get("client_reference_id", [""])[0],
            "payment_status": "unpaid",
            "amount_total": int(form.get("line_items[0][price_data][unit_amount]", ["0"])[0] or "0"),
            "currency": form.get("line_items[0][price_data][currency]", [""])[0],
            "metadata": {
                "order_id": form.get("metadata[order_id]", [""])[0],
                "provider_order_id": form.get("metadata[provider_order_id]", [""])[0],
                "package_code": form.get("metadata[package_code]", [""])[0],
            },
        }
        with self.lock:
            return dict(self.stripe_sessions.setdefault(session_id, session))


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-manhua-ci-mock/1"

    @property
    def state(self) -> ProviderState:
        return self.server.provider_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        # Requests can contain user story text or image data; never log them.
        return

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, payload: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16 * 1024 * 1024:
                raise ValueError
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        if self.state.kind == "text":
            if parsed.path == "/health":
                self._send_json({"status": "ok"})
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        if self.state.kind == "stripe":
            if parsed.path == "/health":
                self._send_json({"status": "ok"})
                return
            if parsed.path.startswith("/v1/checkout/sessions/"):
                session_id = parsed.path.rsplit("/", 1)[-1]
                with self.state.lock:
                    session = self.state.stripe_sessions.get(session_id)
                if session:
                    self._send_json(session)
                    return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        if parsed.path in {"/health", "/system_stats"}:
            self._send_json({"status": "ok", "system": {"devices": []}})
            return
        if parsed.path.startswith("/history/"):
            prompt_id = parsed.path.rsplit("/", 1)[-1]
            kind = self.state.job_kind(prompt_id)
            if not kind:
                self._send_json({})
                return
            if kind == "video":
                outputs = {"1": {"videos": [{"filename": "mock.mp4", "subfolder": "", "type": "output"}]}}
            else:
                outputs = {"1": {"images": [{"filename": "mock.png", "subfolder": "", "type": "output"}]}}
            self._send_json({prompt_id: {"status": {"status_str": "success"}, "outputs": outputs}})
            return
        if parsed.path == "/view":
            query = parse_qs(parsed.query)
            filename = query.get("filename", [""])[0]
            if filename.endswith(".mp4") and self.state.video_path:
                self._send_bytes(self.state.video_path.read_bytes(), "video/mp4")
            else:
                self._send_bytes(ONE_PIXEL_PNG, "image/png")
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        if self.state.kind == "text":
            if parsed.path != "/v1/chat/completions":
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            payload = self._read_json()
            if not isinstance(payload, dict):
                return
            messages = payload.get("messages") or []
            instruction = ""
            vision_request = False
            if messages and isinstance(messages[0], dict):
                raw_content = messages[0].get("content", "")
                vision_request = isinstance(raw_content, list) and any(
                    isinstance(item, dict) and "image_url" in item for item in raw_content
                )
                instruction = raw_content if isinstance(raw_content, str) else json.dumps(raw_content, ensure_ascii=False)
            if vision_request:
                content = "PASS"
            elif isinstance(payload.get("response_format"), dict):
                content = self._structured_content(instruction)
            else:
                source = instruction.rsplit("原文：", 1)[-1].strip()
                content = f"CI mock 改编：{source[:800]}"
            self._send_json({"choices": [{"message": {"role": "assistant", "content": content}}]})
            return

        if self.state.kind == "stripe":
            if parsed.path != "/v1/checkout/sessions":
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if not self.headers.get("Authorization", "").startswith("Basic "):
                self._send_json({"error": "authorization required"}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 64 * 1024:
                    raise ValueError
                form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                if not form.get("client_reference_id") or not form.get("metadata[order_id]"):
                    raise ValueError
                session = self.state.create_stripe_session(form, self.headers.get("Idempotency-Key", ""))
            except (UnicodeDecodeError, ValueError, TypeError):
                self._send_json({"error": "invalid checkout form"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(session)
            return

        if parsed.path == "/prompt":
            payload = self._read_json()
            if not isinstance(payload, dict):
                return
            prompt_id = self.state.create_job(payload.get("prompt", {}))
            self._send_json({"prompt_id": prompt_id, "number": 1})
            return
        if parsed.path == "/upload/image":
            # The provider only needs a safe ComfyUI reference; input bytes are
            # intentionally discarded by this CI mock.
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self._send_json({"name": "uploaded.png", "subfolder": "", "type": "input"})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    @staticmethod
    def _structured_content(instruction: str) -> str:
        lowered = instruction.lower()
        if "故事资产编辑" in instruction or '"entities"' in instruction and '"relationships"' in instruction:
            return json.dumps(
                {
                    "entities": [{"kind": "location", "name": "雨夜街口", "description": "来源中的街道路口", "attributes": {}, "source_segment_ids": []}],
                    "relationships": [],
                },
                ensure_ascii=False,
            )
        if "角色设定编辑" in instruction or '"characters"' in instruction:
            return json.dumps(
                {"characters": [{"name": "CI 主角", "role": "protagonist", "description": "短发、深色风衣、手持怀表，适合连续性锁定"}]},
                ensure_ascii=False,
            )
        if "短剧编剧" in instruction or '"episodes"' in instruction:
            return json.dumps(
                {"episodes": [{"number": 1, "title": "雨夜来客", "summary": "主角在雨夜推开一扇不该打开的门。", "conflict": "门外的人知道她的秘密", "hook": "门后传来熟悉的声音", "target_duration_seconds": 60}]},
                ensure_ascii=False,
            )
        if "漫剧分镜师" in instruction or '"shots"' in instruction:
            return json.dumps(
                {"shots": [{"sequence": 1, "scene": "雨夜街口", "emotion": "不安", "duration_seconds": 3, "description": "主角握紧怀表，推开雨夜街口的旧门。", "adaptation_unit_sequences": [1]}]},
                ensure_ascii=False,
            )
        if "输出 json 提示词" in lowered or '"negative_prompt"' in instruction:
            return json.dumps({"prompt": "东亚黑白漫画，雨夜街口，主角握紧怀表，G笔线条", "negative_prompt": "水印，畸形手指，乱码文字"}, ensure_ascii=False)
        if "image_url" in instruction or "视觉" in instruction:
            return "PASS"
        return json.dumps({}, ensure_ascii=False)


class MockServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: ProviderState) -> None:
        super().__init__(address, Handler)
        self.provider_state = state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("text", "comfyui", "stripe"), required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = MockServer((args.host, args.port), ProviderState(args.kind))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
