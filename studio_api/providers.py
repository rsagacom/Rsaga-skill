"""可替换的文本/图片/视频 Provider 契约。

当前默认实现只写入明确标注的本地预览资产。真实 provider 接入应实现同样
的接口，并把 API key 放在环境变量或受控 secret store，不进入项目数据库。
"""

from __future__ import annotations

import html
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ProviderError(RuntimeError):
    pass


_DEFAULT_PROVIDER_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
_MAX_PROVIDER_RESPONSE_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_COMFYUI_OUTPUT_MAX_BYTES = 256 * 1024 * 1024
_MAX_COMFYUI_OUTPUT_MAX_BYTES = 2 * 1024 * 1024 * 1024


def _provider_response_max_bytes() -> int:
    """Return a bounded JSON response limit without exposing config details."""
    raw_value = os.environ.get(
        "STUDIO_PROVIDER_RESPONSE_MAX_BYTES",
        str(_DEFAULT_PROVIDER_RESPONSE_MAX_BYTES),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return _DEFAULT_PROVIDER_RESPONSE_MAX_BYTES
    return min(max(value, 1024), _MAX_PROVIDER_RESPONSE_MAX_BYTES)


def _read_provider_response(response: Any, provider_label: str) -> bytes:
    """Read a provider JSON response with a hard upper bound."""
    limit = _provider_response_max_bytes()
    try:
        try:
            body = response.read(limit + 1)
        except TypeError:
            # Keeps small unit-test doubles compatible; real HTTPResponse accepts n.
            body = response.read()
    except (OSError, TypeError, ValueError) as exc:
        raise ProviderError(f"{provider_label} provider returned invalid response") from exc
    if not isinstance(body, (bytes, bytearray)):
        raise ProviderError(f"{provider_label} provider returned invalid response")
    if len(body) > limit:
        raise ProviderError(f"{provider_label} provider response exceeds size limit")
    return bytes(body)


def _comfyui_output_max_bytes() -> int:
    """Return a bounded media output limit without exposing configuration values."""
    raw_value = os.environ.get(
        "COMFYUI_OUTPUT_MAX_BYTES",
        str(_DEFAULT_COMFYUI_OUTPUT_MAX_BYTES),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return _DEFAULT_COMFYUI_OUTPUT_MAX_BYTES
    return min(max(value, 1), _MAX_COMFYUI_OUTPUT_MAX_BYTES)


def _read_comfyui_binary_response(response: Any, provider_label: str) -> bytes:
    """Read a ComfyUI media response incrementally under a hard byte limit."""
    limit = _comfyui_output_max_bytes()
    chunks: list[bytes] = []
    received = 0
    while True:
        try:
            try:
                chunk = response.read(min(1024 * 1024, limit - received + 1))
                chunked_read = True
            except TypeError:
                # 保持最小 HTTP test double 兼容；真实 HTTPResponse 支持带长度读取。
                chunk = response.read()
                chunked_read = False
        except (OSError, TypeError, ValueError) as exc:
            raise ProviderError(f"{provider_label} download failed") from exc
        if not isinstance(chunk, (bytes, bytearray)):
            raise ProviderError(f"{provider_label} download returned invalid output")
        if not chunk:
            break
        received += len(chunk)
        if received > limit:
            raise ProviderError(f"{provider_label} download exceeds size limit")
        chunks.append(bytes(chunk))
        if not chunked_read:
            break
    return b"".join(chunks)


def _normalize_message_content(value: Any, provider_label: str) -> str:
    """Normalize OpenAI-compatible string/content-part responses safely."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                part = item
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                part = item["text"]
            else:
                raise ProviderError(f"{provider_label} provider returned invalid response")
            if part.strip():
                parts.append(part.strip())
        return "\n".join(parts).strip()
    raise ProviderError(f"{provider_label} provider returned invalid response")


@dataclass(frozen=True)
class GeneratedAsset:
    relative_url: str
    metadata: dict[str, Any] = field(default_factory=dict)


SPEECH_PROVIDER_INPUT_LIMIT = 4096
DEFAULT_SPEECH_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "cedar",
    "echo",
    "fable",
    "marin",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
)


def configured_speech_voices() -> tuple[str, ...]:
    """Return the built-in voices permitted by this deployment.

    The default mirrors the OpenAI Audio API built-in voice contract. Deployments
    may explicitly narrow the list, but an environment override cannot add
    arbitrary/custom voice identifiers.
    """

    raw = os.environ.get("STUDIO_SPEECH_ALLOWED_VOICES", "").strip()
    if not raw:
        return DEFAULT_SPEECH_VOICES
    values = tuple(dict.fromkeys(item.strip().lower() for item in raw.split(",") if item.strip()))
    built_in_values = tuple(value for value in values if value in DEFAULT_SPEECH_VOICES)
    return built_in_values or DEFAULT_SPEECH_VOICES


def speech_voice_allowed(voice: str) -> bool:
    return str(voice or "").strip().lower() in configured_speech_voices()


def split_speech_text(text: str, max_chars: int = SPEECH_PROVIDER_INPUT_LIMIT) -> list[str]:
    """按句末边界切分旁白，保证每个 Provider 请求不超过输入上限。"""

    normalized = str(text or "").strip()
    if not normalized:
        return []
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(normalized) <= max_chars:
        return [normalized]

    pieces = [piece for piece in re.split(r"(?<=[。！？!?；;\n])", normalized) if piece]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:max_chars])
            piece = piece[max_chars:]
        if not piece.strip():
            continue
        candidate = f"{current}{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def combine_speech_assets(asset_id: str, parts: list[GeneratedAsset], output_dir: Path) -> GeneratedAsset:
    """将多个同格式 Provider 结果合并为一个可挂载的音频资产。"""

    if not parts:
        raise ProviderError("speech provider returned no segments")
    paths = [output_dir / Path(part.relative_url).name for part in parts]
    if any(not path.is_file() or path.stat().st_size <= 0 for path in paths):
        raise ProviderError("speech segment output is unavailable")
    suffixes = {path.suffix.lower() for path in paths}
    if len(suffixes) != 1 or suffixes not in ({".wav"}, {".mp3"}):
        raise ProviderError("speech segments returned incompatible audio formats")
    suffix = next(iter(suffixes))
    final_path = output_dir / f"{asset_id}{suffix}"
    try:
        if suffix == ".wav":
            with wave.open(str(final_path), "wb") as destination:
                first_params = None
                for path in paths:
                    with wave.open(str(path), "rb") as source:
                        params = source.getparams()
                        if first_params is None:
                            first_params = params
                            destination.setparams(params)
                        elif params[:4] != first_params[:4] or params.comptype != first_params.comptype:
                            raise ProviderError("speech segments have incompatible WAV parameters")
                        while True:
                            frames = source.readframes(16_384)
                            if not frames:
                                break
                            destination.writeframesraw(frames)
                destination.writeframes(b"")
        else:
            concat_path = output_dir / f".{asset_id}.concat.txt"
            concat_path.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise ProviderError("speech segments require ffmpeg for MP3 merge")
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", "-y", str(final_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
        if not final_path.is_file() or final_path.stat().st_size <= 0:
            raise ProviderError("speech provider returned empty merged audio")
    except ProviderError:
        final_path.unlink(missing_ok=True)
        raise
    except (OSError, subprocess.SubprocessError, wave.Error) as exc:
        final_path.unlink(missing_ok=True)
        raise ProviderError("speech segment merge failed") from exc
    finally:
        concat_path = output_dir / f".{asset_id}.concat.txt"
        concat_path.unlink(missing_ok=True)
        for path in paths:
            path.unlink(missing_ok=True)

    metadata: dict[str, Any] = {}
    for part in parts:
        for key in ("provider", "model", "voice", "speed", "instructions_configured"):
            if key in part.metadata:
                metadata.setdefault(key, part.metadata[key])
    durations = [float(part.metadata["duration_seconds"]) for part in parts if isinstance(part.metadata.get("duration_seconds"), (int, float))]
    if durations:
        metadata["duration_seconds"] = round(sum(durations), 3)
        metadata["segment_durations"] = [round(value, 3) for value in durations]
    metadata["segment_count"] = len(parts)
    metadata["segment_char_counts"] = [int(part.metadata.get("text_chars", 0)) for part in parts]
    metadata["merged"] = True
    return GeneratedAsset(f"/assets/{final_path.name}", metadata)


class ImageProvider(Protocol):
    name: str

    def generate(self, asset_id: str, description: str, prompt: str, output_dir: Path) -> GeneratedAsset:
        ...


class VideoProvider(Protocol):
    name: str

    def generate(
        self,
        asset_id: str,
        shot_id: str,
        output_dir: Path,
        source_image_path: Path | None = None,
        prompt: str | None = None,
    ) -> GeneratedAsset:
        ...

    def generate_segment(
        self,
        segment_id: str,
        shot_id: str,
        output_dir: Path,
        *,
        role: str,
        prompt: str,
        segment_index: int,
        width: int,
        height: int,
        frames: int,
        fps: float,
        steps: int,
        seed: int,
        sampler: str,
        context_length: int,
        audio_context_length: int,
        source_image_path: Path | None = None,
        context_latent_path: str | None = None,
        context_clip_index: int | None = None,
        latent_output_prefix: str | None = None,
        reference_image_paths: list[Path] | None = None,
    ) -> GeneratedAsset:
        ...


class TextProvider(Protocol):
    name: str

    def rewrite(self, source_text: str, mode: str) -> tuple[str, dict[str, Any]]:
        ...

    def complete(self, instruction: str, json_mode: bool = False) -> tuple[str, dict[str, Any]]:
        ...


class VisionProvider(Protocol):
    name: str

    def review(self, image_path: Path, prompt: str) -> dict[str, Any]:
        ...


class SpeechProvider(Protocol):
    name: str

    def synthesize(
        self,
        asset_id: str,
        text: str,
        output_dir: Path,
        *,
        voice: str,
        speed: float,
        instructions: str = "",
    ) -> GeneratedAsset:
        ...


class LocalPreviewTextProvider:
    name = "local"

    def rewrite(self, source_text: str, mode: str) -> tuple[str, dict[str, Any]]:
        if mode == "faithful":
            return source_text, {"mode": "faithful", "preview": False}
        if mode == "condensed":
            compact = " ".join(source_text.split())
            return f"改编预览（压缩）：{compact[:120]}", {"mode": mode, "preview": True}
        if mode == "originalized":
            return f"改编预览（原创化表达）：镜头从远景切入，{source_text}", {"mode": mode, "preview": True}
        raise ProviderError(f"unsupported adaptation mode: {mode}")

    def complete(self, instruction: str, json_mode: bool = False) -> tuple[str, dict[str, Any]]:
        raise ProviderError("local text provider does not provide model completion")


class LocalPreviewSpeechProvider:
    """确定性 WAV 预览，不把占位音频标成真实 TTS 结果。"""

    name = "local"
    model = "deterministic-wave-preview"

    def synthesize(
        self,
        asset_id: str,
        text: str,
        output_dir: Path,
        *,
        voice: str,
        speed: float,
        instructions: str = "",
    ) -> GeneratedAsset:
        normalized = str(text or "").strip()
        if not normalized:
            raise ProviderError("speech text is empty")
        if len(normalized) > SPEECH_PROVIDER_INPUT_LIMIT:
            raise ProviderError("speech text exceeds provider input limit")
        normalized_voice = str(voice or "").strip().lower()
        if not speech_voice_allowed(normalized_voice):
            raise ProviderError("speech voice is not allowlisted")
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{asset_id}.wav"
        duration = max(1.0, min(60.0, len(normalized) / max(1.0, 4.0 * speed)))
        sample_rate = 16_000
        frame_count = int(duration * sample_rate)
        frequency = 180 + (int(hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:4], 16) % 120)
        amplitude = 2200
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            frames = bytearray()
            for index in range(frame_count):
                envelope = min(
                    1.0,
                    index / max(1, sample_rate // 12),
                    (frame_count - index) / max(1, sample_rate // 12),
                )
                value = int(amplitude * max(0.0, envelope) * math.sin(2 * math.pi * frequency * index / sample_rate))
                frames.extend(struct.pack("<h", value))
            audio.writeframes(frames)
        return GeneratedAsset(
            f"/assets/{path.name}",
            {
                "mode": "local-speech-preview",
                "provider": self.name,
                "model": self.model,
                "voice": normalized_voice,
                "speed": speed,
                "duration_seconds": round(duration, 3),
                "text_chars": len(normalized),
                "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                "instructions_configured": bool(instructions.strip()),
            },
        )


class OpenAISpeechProvider:
    """OpenAI Audio API TTS 适配器；SDK 和 key 仅在真实调用时加载。"""

    name = "openai"

    def __init__(self, base_url: str, model: str, api_key_env: str = "OPENAI_API_KEY", timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    def synthesize(
        self,
        asset_id: str,
        text: str,
        output_dir: Path,
        *,
        voice: str,
        speed: float,
        instructions: str = "",
    ) -> GeneratedAsset:
        normalized = str(text or "").strip()
        if not normalized:
            raise ProviderError("speech text is empty")
        if len(normalized) > SPEECH_PROVIDER_INPUT_LIMIT:
            raise ProviderError("speech text exceeds provider input limit")
        normalized_voice = str(voice or "").strip().lower()
        if not speech_voice_allowed(normalized_voice):
            raise ProviderError("speech voice is not allowlisted")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError("speech provider credential is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("speech provider SDK is not installed") from exc
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{asset_id}.mp3"
        partial = output_dir / f".{asset_id}.mp3.part"
        payload: dict[str, Any] = {
            "model": self.model,
            "voice": normalized_voice,
            "input": normalized,
            "response_format": "mp3",
            "speed": speed,
        }
        if instructions.strip() and self.model not in {"tts-1", "tts-1-hd"}:
            payload["instructions"] = instructions.strip()[:1000]
        try:
            client = OpenAI(api_key=api_key, base_url=self.base_url or None, timeout=self.timeout)
            with client.audio.speech.with_streaming_response.create(**payload) as response:
                response.stream_to_file(partial)
            max_bytes = min(max(int(os.environ.get("STUDIO_TTS_OUTPUT_MAX_BYTES", str(64 * 1024 * 1024))), 1024), 256 * 1024 * 1024)
            if not partial.is_file() or partial.stat().st_size <= 0:
                raise ProviderError("speech provider returned empty audio")
            if partial.stat().st_size > max_bytes:
                raise ProviderError("speech provider output exceeds size limit")
            partial.replace(path)
        except ProviderError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise ProviderError("speech provider request failed") from exc
        return GeneratedAsset(
            f"/assets/{path.name}",
            {
                "provider": self.name,
                "model": self.model,
                "voice": normalized_voice,
                "speed": speed,
                "text_chars": len(normalized),
                "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                "instructions_configured": bool(instructions.strip()),
            },
        )


class OpenAICompatibleTextProvider:
    """最小 Chat Completions 适配器，密钥只从环境变量读取。"""

    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, api_key_env: str = "STUDIO_TEXT_API_KEY", timeout: int = 90) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    def rewrite(self, source_text: str, mode: str) -> tuple[str, dict[str, Any]]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"text provider requires environment variable {self.api_key_env}")
        mode_instructions = {
            "faithful": "保留原事件、人物关系和叙事信息，只做适合分镜的轻量整理，不新增关键事实。",
            "condensed": "压缩冗余叙述为短视频可用的镜头表达，保留因果、人物动作和冲突，不把多个事件混成无法审核的一句。",
            "originalized": "在用户拥有或获授权使用原文的前提下，重组句式、叙事视角和镜头表达，避免逐句复述；不得声称规避版权审查，必须保留来源可追溯性并交给人工审核。",
        }
        instruction = mode_instructions.get(mode, "按当前改编模式处理，并保留来源可追溯性。")
        prompt = (
            "你是漫剧改编编辑。请在不规避版权审查的前提下，将用户有权使用的原文改编为可审核的镜头文字。"
            f"模式：{mode}。具体要求：{instruction}只返回改编文本，不要解释、免责声明或 Markdown。\n原文：{source_text}"
        )
        result, metadata = self.complete(prompt)
        metadata.update({"mode": mode, "preview": False})
        return result, metadata

    def complete(self, instruction: str, json_mode: bool = False) -> tuple[str, dict[str, Any]]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"text provider requires environment variable {self.api_key_env}")
        body_data: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": instruction}],
            "temperature": 0.4,
        }
        if json_mode:
            body_data["response_format"] = {"type": "json_object"}
        body = json.dumps(body_data).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(_read_provider_response(response, "text"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError("text provider request failed") from exc
        try:
            raw_content = payload["choices"][0]["message"].get("content")
            result = _normalize_message_content(raw_content if raw_content is not None else "", "text")
        except (KeyError, IndexError, AttributeError, TypeError) as exc:
            raise ProviderError("text provider returned an invalid response") from exc
        if not result:
            raise ProviderError("text provider returned empty text")
        return result, {"preview": False, "provider": self.name, "model": self.model}


class LocalPreviewVisionProvider:
    name = "local"

    def review(self, image_path: Path, prompt: str) -> dict[str, Any]:
        return {
            "status": "UNKNOWN",
            "issues": ["本地预览 provider 不具备视觉判断能力，需人工复核"],
            "raw": "",
            "provider": self.name,
            "model": "manual-review",
        }


class OpenAICompatibleVisionProvider:
    """OpenAI Chat Completions 兼容的图片审核适配器。"""

    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, api_key_env: str = "STUDIO_VISION_API_KEY", timeout: int = 90) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    def review(self, image_path: Path, prompt: str) -> dict[str, Any]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"vision provider requires environment variable {self.api_key_env}")
        mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        try:
            image_bytes = image_path.read_bytes()
            max_bytes = int(os.environ.get("STUDIO_VISION_INPUT_MAX_BYTES", str(50 * 1024 * 1024)))
        except (OSError, ValueError) as exc:
            raise ProviderError("vision input image is unavailable") from exc
        if not image_bytes:
            raise ProviderError("vision input image is empty")
        if len(image_bytes) > max(1, max_bytes):
            raise ProviderError("vision input image exceeds upload limit")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}]}],
                "temperature": 0,
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(_read_provider_response(response, "vision"))
            raw_content = payload["choices"][0]["message"].get("content")
            content = _normalize_message_content(raw_content if raw_content is not None else "", "vision")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("vision provider request failed") from exc
        status, issues = self._parse(content)
        return {"status": status, "issues": issues, "raw": content, "provider": self.name, "model": self.model}

    @staticmethod
    def _parse(content: str) -> tuple[str, list[str]]:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        pass_markers = {"PASS", "通过", "无问题", "未发现问题", "没有问题"}
        issues = [line for line in lines if line.upper() not in pass_markers and line not in pass_markers]
        if not lines:
            return "UNKNOWN", ["视觉模型空响应，需重审"]
        if not issues and any(line.upper() in pass_markers or line in pass_markers for line in lines):
            return "PASS", []
        return "FAIL", issues


class LocalPreviewImageProvider:
    name = "local"

    def generate(self, asset_id: str, description: str, prompt: str, output_dir: Path) -> GeneratedAsset:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{asset_id}.svg"
        title = html.escape(description[:48] or "AI 漫剧关键帧")
        subtitle = html.escape(prompt[:90])
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="576" viewBox="0 0 1024 576"><rect width="1024" height="576" fill="#161622"/><rect x="32" y="32" width="960" height="512" rx="22" fill="#2a2845" stroke="#b6a7ff" stroke-width="3"/><circle cx="512" cy="220" r="94" fill="#f0b7a4"/><path d="M380 470 Q512 300 644 470" fill="#4d78c4"/><text x="512" y="90" text-anchor="middle" fill="#b6a7ff" font-size="28" font-family="sans-serif">LOCAL PREVIEW</text><text x="512" y="390" text-anchor="middle" fill="white" font-size="24" font-family="sans-serif">{title}</text><text x="512" y="430" text-anchor="middle" fill="#c8c6d8" font-size="16" font-family="sans-serif">{subtitle}</text></svg>'''
        path.write_text(svg, encoding="utf-8")
        raster_name = f"{asset_id}.ppm"
        raster_path = output_dir / raster_name
        self._write_video_raster(raster_path)
        return GeneratedAsset(
            f"/assets/{path.name}",
            {
                "mode": "local-image-preview",
                "provider": self.name,
                "video_source_filename": raster_name,
            },
        )

    @staticmethod
    def _write_video_raster(path: Path) -> None:
        """写入与 SVG 预览同构的 PPM，避免本地 FFmpeg 依赖 SVG 解码器。"""
        width, height = 1024, 576
        pixels = bytearray([22, 22, 34]) * (width * height)

        def fill_rect(left: int, top: int, right: int, bottom: int, color: tuple[int, int, int]) -> None:
            left = max(0, left)
            top = max(0, top)
            right = min(width, right)
            bottom = min(height, bottom)
            row = bytes(color) * max(0, right - left)
            for y in range(top, bottom):
                start = (y * width + left) * 3
                pixels[start : start + len(row)] = row

        fill_rect(32, 32, 992, 544, (42, 40, 69))
        fill_rect(32, 32, 992, 35, (182, 167, 255))
        fill_rect(32, 541, 992, 544, (182, 167, 255))
        fill_rect(32, 32, 35, 544, (182, 167, 255))
        fill_rect(989, 32, 992, 544, (182, 167, 255))

        for y in range(126, 315):
            for x in range(418, 607):
                if (x - 512) ** 2 + (y - 220) ** 2 <= 94 ** 2:
                    offset = (y * width + x) * 3
                    pixels[offset : offset + 3] = bytes((240, 183, 164))
        fill_rect(380, 410, 644, 470, (77, 120, 196))
        path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


class ComfyUIImageProvider:
    """ComfyUI HTTP API 适配器。

    workflow JSON 必须由项目/运维提供，代码不会猜节点 ID；字符串模板可使用
    `{{PROMPT}}`、`{{NEGATIVE_PROMPT}}` 和 `{{CLIENT_ID}}` 占位符。
    """

    name = "comfyui"

    def __init__(self, base_url: str, workflow_path: str, timeout: int = 300, poll_interval: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow_path = Path(workflow_path)
        self.timeout = timeout
        self.poll_interval = poll_interval

    def _load_workflow(self, workflow_path: Path | None = None) -> dict[str, Any]:
        """读取部署提供的 workflow，并把文件系统细节留在服务端。"""
        selected_path = workflow_path or self.workflow_path
        if not selected_path.is_file():
            raise ProviderError("ComfyUI workflow is not available")
        try:
            workflow = json.loads(selected_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("ComfyUI workflow is invalid") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise ProviderError("ComfyUI workflow is invalid")
        return workflow

    @staticmethod
    def _read_json_response(response: Any, error_message: str) -> Any:
        """Decode a provider response without leaking parser or transport details."""
        try:
            return json.loads(_read_provider_response(response, "ComfyUI"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ProviderError, TypeError, ValueError) as exc:
            raise ProviderError(error_message) from exc

    @staticmethod
    def _prompt_id(payload: Any, error_message: str) -> str:
        if not isinstance(payload, dict):
            raise ProviderError(error_message)
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ProviderError(error_message)
        return prompt_id.strip()

    def generate(self, asset_id: str, description: str, prompt: str, output_dir: Path) -> GeneratedAsset:
        output_dir.mkdir(parents=True, exist_ok=True)
        workflow = self._load_workflow()
        workflow = self._replace(workflow, {"{{PROMPT}}": prompt, "{{NEGATIVE_PROMPT}}": "低清晰度，重复人物，畸形手指，水印", "{{CLIENT_ID}}": asset_id})
        payload = json.dumps({"prompt": workflow, "client_id": asset_id}).encode()
        request = urllib.request.Request(f"{self.base_url}/prompt", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                prompt_id = self._prompt_id(
                    self._read_json_response(response, "ComfyUI prompt submission returned invalid response"),
                    "ComfyUI prompt submission returned invalid response",
                )
        except urllib.error.URLError as exc:
            raise ProviderError("ComfyUI prompt submission failed") from exc

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/history/{urllib.parse.quote(prompt_id)}", timeout=30) as response:
                    history = self._read_json_response(response, "ComfyUI history response is invalid")
                if not isinstance(history, dict):
                    raise ProviderError("ComfyUI history response is invalid")
                item = history.get(prompt_id)
                if item is not None and not isinstance(item, dict):
                    raise ProviderError("ComfyUI history response is invalid")
                status = item.get("status") if isinstance(item, dict) else None
                if isinstance(status, dict) and status.get("status_str") == "error":
                    raise ProviderError("ComfyUI workflow failed")
                outputs = item.get("outputs") if isinstance(item, dict) else None
                if outputs:
                    image = self._find_image(outputs)
                    if image:
                        return self._download(image, asset_id, output_dir, prompt_id)
            except urllib.error.URLError:
                pass
            time.sleep(self.poll_interval)
        raise ProviderError("ComfyUI workflow timed out")

    @classmethod
    def _replace(cls, value: Any, replacements: dict[str, str]) -> Any:
        if isinstance(value, str):
            for source, target in replacements.items():
                value = value.replace(source, target)
            return value
        if isinstance(value, list):
            return [cls._replace(item, replacements) for item in value]
        if isinstance(value, dict):
            return {key: cls._replace(item, replacements) for key, item in value.items()}
        return value

    @staticmethod
    def _find_image(outputs: dict[str, Any]) -> dict[str, str] | None:
        if not isinstance(outputs, dict):
            return None
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            images = output.get("images", [])
            if not isinstance(images, list):
                continue
            for image in images:
                if not isinstance(image, dict):
                    continue
                filename = str(image.get("filename", "")).strip()
                if filename:
                    return {key: str(image.get(key, "")) for key in ("filename", "subfolder", "type")}
        return None

    def _download(self, image: dict[str, str], asset_id: str, output_dir: Path, prompt_id: str) -> GeneratedAsset:
        query = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image["subfolder"], "type": image["type"]})
        try:
            with urllib.request.urlopen(f"{self.base_url}/view?{query}", timeout=60) as response:
                content = _read_comfyui_binary_response(response, "ComfyUI image")
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError("ComfyUI image download failed") from exc
        if not content:
            raise ProviderError("ComfyUI image download returned empty output")
        extension = Path(image["filename"]).suffix or ".png"
        path = output_dir / f"{asset_id}{extension}"
        try:
            path.write_bytes(content)
        except OSError as exc:
            raise ProviderError("ComfyUI image download failed") from exc
        return GeneratedAsset(f"/assets/{path.name}", {"provider": self.name, "prompt_id": prompt_id, "source": "comfyui"})


class ComfyUIVideoProvider(ComfyUIImageProvider):
    """ComfyUI 视频工作流适配器，读取 gifs/videos 输出并保存为项目资产。

    文生视频 workflow 可使用 ``{{PROMPT}}``；图生视频 workflow 可使用 ``{{IMAGE_REF}}``（推荐）或
    ``{{IMAGE_FILENAME}}`` / ``{{IMAGE_SUBFOLDER}}`` / ``{{IMAGE_TYPE}}``。
    适配器会把采用的关键帧先上传到 ComfyUI ``/upload/image``，不把 API
    容器或宿主机的本地路径硬编码进远端 workflow。
    """

    name = "comfyui"

    def __init__(
        self,
        base_url: str,
        workflow_path: str,
        timeout: int = 300,
        poll_interval: float = 2.0,
        *,
        t2v_workflow_path: str | None = None,
        r2v_workflow_path: str | None = None,
        long_video_first_workflow_path: str | None = None,
        long_video_context_workflow_path: str | None = None,
    ) -> None:
        super().__init__(base_url, workflow_path, timeout, poll_interval)
        self.t2v_workflow_path = Path(t2v_workflow_path) if t2v_workflow_path else None
        self.r2v_workflow_path = Path(r2v_workflow_path) if r2v_workflow_path else None
        self.long_video_first_workflow_path = Path(long_video_first_workflow_path) if long_video_first_workflow_path else None
        self.long_video_context_workflow_path = Path(long_video_context_workflow_path) if long_video_context_workflow_path else None

    def _select_workflow(self, source_image_path: Path | None) -> Path:
        if source_image_path is not None and self.r2v_workflow_path:
            return self.r2v_workflow_path
        if source_image_path is None and self.t2v_workflow_path:
            return self.t2v_workflow_path
        return self.workflow_path

    def generate(
        self,
        asset_id: str,
        shot_id: str,
        output_dir: Path,
        source_image_path: Path | None = None,
        prompt: str | None = None,
    ) -> GeneratedAsset:
        output_dir.mkdir(parents=True, exist_ok=True)
        selected_workflow = self._select_workflow(source_image_path)
        raw_workflow = self._load_workflow(selected_workflow)
        serialized_workflow = json.dumps(raw_workflow, ensure_ascii=False)
        image_placeholders = ("{{IMAGE_REF}}", "{{IMAGE_FILENAME}}", "{{SOURCE_IMAGE_FILENAME}}", "{{IMAGE_SUBFOLDER}}", "{{IMAGE_TYPE}}")
        uploaded_image: dict[str, str] | None = None
        if any(placeholder in serialized_workflow for placeholder in image_placeholders):
            if source_image_path is None:
                raise ProviderError("ComfyUI video workflow requires a ready source image")
            uploaded_image = self._upload_source_image(source_image_path, asset_id)
        workflow = self._replace(
            raw_workflow,
            {
                "{{ASSET_ID}}": asset_id,
                "{{SHOT_ID}}": shot_id,
                "{{CLIENT_ID}}": asset_id,
                "{{IMAGE_REF}}": self._image_ref(uploaded_image),
                "{{IMAGE_FILENAME}}": str((uploaded_image or {}).get("name", "")),
                "{{SOURCE_IMAGE_FILENAME}}": str((uploaded_image or {}).get("name", "")),
                "{{IMAGE_SUBFOLDER}}": str((uploaded_image or {}).get("subfolder", "")),
                "{{IMAGE_TYPE}}": str((uploaded_image or {}).get("type", "input")),
                "{{PROMPT}}": str(prompt or ""),
                "{{NEGATIVE_PROMPT}}": "低清晰度，重复人物，畸形手指，五官崩坏，水印",
            },
        )
        body = json.dumps({"prompt": workflow, "client_id": asset_id}).encode()
        request = urllib.request.Request(f"{self.base_url}/prompt", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                prompt_id = self._prompt_id(
                    self._read_json_response(response, "ComfyUI video prompt submission returned invalid response"),
                    "ComfyUI video prompt submission returned invalid response",
                )
        except urllib.error.URLError as exc:
            raise ProviderError("ComfyUI video prompt submission failed") from exc

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/history/{urllib.parse.quote(prompt_id)}", timeout=30) as response:
                    history = self._read_json_response(response, "ComfyUI history response is invalid")
                if not isinstance(history, dict):
                    raise ProviderError("ComfyUI history response is invalid")
                item = history.get(prompt_id)
                if item is not None and not isinstance(item, dict):
                    raise ProviderError("ComfyUI history response is invalid")
                status = item.get("status") if isinstance(item, dict) else None
                if isinstance(status, dict) and status.get("status_str") == "error":
                    raise ProviderError("ComfyUI video workflow failed")
                outputs = item.get("outputs") if isinstance(item, dict) else None
                if outputs:
                    media = self._find_media(outputs)
                    if media:
                        generated = self._download_media(media, asset_id, output_dir, prompt_id)
                        metadata = dict(generated.metadata)
                        metadata.update(
                            {
                                "generation_mode": "r2v" if source_image_path is not None else "t2v",
                                "workflow_role": "r2v" if source_image_path is not None else "t2v",
                                "workflow_file": selected_workflow.name,
                            }
                        )
                        if uploaded_image:
                            metadata["input_image"] = {
                                key: uploaded_image.get(key, "")
                                for key in ("name", "subfolder", "type")
                            }
                        return GeneratedAsset(generated.relative_url, metadata)
            except urllib.error.URLError:
                pass
            time.sleep(self.poll_interval)
        raise ProviderError("ComfyUI video workflow timed out")

    def generate_segment(
        self,
        segment_id: str,
        shot_id: str,
        output_dir: Path,
        *,
        role: str,
        prompt: str,
        segment_index: int,
        width: int,
        height: int,
        frames: int,
        fps: float,
        steps: int,
        seed: int,
        sampler: str,
        context_length: int,
        audio_context_length: int,
        source_image_path: Path | None = None,
        context_latent_path: str | None = None,
        context_clip_index: int | None = None,
        latent_output_prefix: str | None = None,
        reference_image_paths: list[Path] | None = None,
    ) -> GeneratedAsset:
        """运行一个可恢复的 H3 长视频片段。

        Director 与 Motion Context 通过两个独立 API-format workflow 文件接入。
        这里不修改 ComfyUI 的 Python 运行时；后续段只把上一段保存的 latent
        引用和 clip index 填入专用 workflow。
        """
        if role not in {"director", "motion-context"}:
            raise ProviderError("unsupported long-video workflow role")
        selected_workflow = self.long_video_first_workflow_path if role == "director" else self.long_video_context_workflow_path
        if selected_workflow is None:
            raise ProviderError("H3 long-video workflow is not configured")
        if role == "motion-context" and (not context_latent_path or context_clip_index is None):
            raise ProviderError("Motion Context requires the previous latent reference")
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_workflow = self._load_workflow(selected_workflow)
        serialized_workflow = json.dumps(raw_workflow, ensure_ascii=False)
        image_placeholders = ("{{IMAGE_REF}}", "{{IMAGE_FILENAME}}", "{{SOURCE_IMAGE_FILENAME}}", "{{IMAGE_SUBFOLDER}}", "{{IMAGE_TYPE}}")
        uploaded_image: dict[str, str] | None = None
        if any(placeholder in serialized_workflow for placeholder in image_placeholders):
            if source_image_path is None:
                raise ProviderError("H3 first long-video workflow requires a ready source image")
            uploaded_image = self._upload_source_image(source_image_path, segment_id)
        reference_placeholders = tuple(f"{{{{REFERENCE_IMAGE_{index}_REF}}}}" for index in range(8))
        referenced_slots = [index for index, placeholder in enumerate(reference_placeholders) if placeholder in serialized_workflow]
        uploaded_references: dict[int, dict[str, str]] = {}
        reference_paths = list(reference_image_paths or [])
        if referenced_slots:
            if max(referenced_slots) >= len(reference_paths):
                raise ProviderError("H3 reference workflow requires all referenced images")
            for index in referenced_slots:
                uploaded_references[index] = self._upload_source_image(reference_paths[index], f"{segment_id}-ref-{index + 1}")
        latent_prefix = str(latent_output_prefix or f"long-video/{segment_id}/context/clip")
        # H3 Motion Context SaveLatent writes a fixed-slot file such as
        # ``context/clip_00001.safetensors``.  Its LoadLatent node accepts
        # either that exact file or the containing directory plus clip_index;
        # it does not interpret ``context/clip`` as a filename prefix.
        latent_folder = str(Path(latent_prefix).parent)
        context_latent_ref = self._motion_context_folder(context_latent_path or latent_folder)
        timeline_data = json.dumps(
            {
                "version": 4,
                "editMode": "segment",
                "timelineMode": "prompt_batch",
                "totalFrames": frames,
                "frameRate": fps,
                "width": width,
                "height": height,
                "refMaxSize": max(width, height),
                "output": {
                    "mode": "fixed",
                    "longEdge": max(width, height),
                    "width": width,
                    "height": height,
                    "maxExportFrames": 0,
                    "exportMode": "all",
                    "audioMode": "generate",
                    "continuityEnabled": True,
                    "continuityOverlapFrames": context_length,
                },
                "videoClips": [],
                "video": {"fileName": "", "videoFile": "", "subfolder": "", "type": "input", "frames": [], "frameMap": []},
                "global": {"taskType": "t2v — 文生视频(Text to Video)", "prompt": prompt, "refs": [], "referenceVideo": {}, "continuousReference": False, "genImage": {"imageFile": ""}},
                "segments": [{"id": f"segment-{segment_index}", "start": 0, "length": frames, "frameCount": frames, "durationSec": frames / fps, "prompt": prompt, "taskType": "", "refs": [], "referenceVideo": {}, "genImage": {"imageFile": ""}, "negativePrompt": ""}],
                "gen": {"defaultFrameCount": frames},
                "runSelectEnabled": False,
                "runSelection": [],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        replacements: dict[str, Any] = {
            "{{ASSET_ID}}": segment_id,
            "{{SHOT_ID}}": shot_id,
            "{{CLIENT_ID}}": segment_id,
            "{{IMAGE_REF}}": self._image_ref(uploaded_image),
            "{{IMAGE_FILENAME}}": str((uploaded_image or {}).get("name", "")),
            "{{SOURCE_IMAGE_FILENAME}}": str((uploaded_image or {}).get("name", "")),
            "{{IMAGE_SUBFOLDER}}": str((uploaded_image or {}).get("subfolder", "")),
            "{{IMAGE_TYPE}}": str((uploaded_image or {}).get("type", "input")),
            **{
                placeholder: self._image_ref(uploaded_references.get(slot))
                for slot, placeholder in enumerate(reference_placeholders)
            },
            "{{PROMPT}}": prompt,
            "{{SEGMENT_PROMPT}}": prompt,
            "{{TIMELINE_DATA}}": timeline_data,
            "{{NEGATIVE_PROMPT}}": "低清晰度，重复人物，畸形手指，五官崩坏，水印，硬切",
            "{{SEGMENT_INDEX}}": segment_index,
            "{{CONTEXT_LATENT_PATH}}": context_latent_ref if role == "motion-context" else "",
            "{{CONTEXT_CLIP_INDEX}}": int(context_clip_index or 0),
            "{{LATENT_OUTPUT_PREFIX}}": latent_prefix,
            "{{OUTPUT_PREFIX}}": f"long-video/{segment_id}/segment-{segment_index:04d}",
            "{{WIDTH}}": width,
            "{{HEIGHT}}": height,
            "{{FRAMES}}": frames,
            "{{FPS}}": fps,
            "{{STEPS}}": steps,
            "{{SEED}}": seed,
            "{{SAMPLER}}": sampler,
            # MiniMaxH3MotionContext exposes context_length as a combo of
            # strings ("22", "5", "39", "56"), while the audio window and
            # clip indexes are integer inputs. Preserve that node contract
            # instead of letting typed replacement turn 22 into an invalid
            # integer payload.
            "{{CONTEXT_LENGTH}}": str(context_length),
            "{{AUDIO_CONTEXT_LENGTH}}": audio_context_length,
        }
        workflow = self._replace_typed(raw_workflow, replacements)
        body = json.dumps({"prompt": workflow, "client_id": segment_id}).encode()
        request = urllib.request.Request(f"{self.base_url}/prompt", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                prompt_id = self._prompt_id(
                    self._read_json_response(response, "ComfyUI long-video prompt submission returned invalid response"),
                    "ComfyUI long-video prompt submission returned invalid response",
                )
        except urllib.error.URLError as exc:
            raise ProviderError("ComfyUI long-video prompt submission failed") from exc

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/history/{urllib.parse.quote(prompt_id)}", timeout=30) as response:
                    history = self._read_json_response(response, "ComfyUI long-video history response is invalid")
                if not isinstance(history, dict):
                    raise ProviderError("ComfyUI long-video history response is invalid")
                item = history.get(prompt_id)
                if item is not None and not isinstance(item, dict):
                    raise ProviderError("ComfyUI long-video history response is invalid")
                status = item.get("status") if isinstance(item, dict) else None
                if isinstance(status, dict) and status.get("status_str") == "error":
                    raise ProviderError("ComfyUI long-video workflow failed")
                outputs = item.get("outputs") if isinstance(item, dict) else None
                if outputs:
                    media = self._find_media(outputs)
                    if media:
                        generated = self._download_media(media, segment_id, output_dir, prompt_id)
                        metadata = dict(generated.metadata)
                        latent = self._find_latent(outputs)
                        if latent:
                            latent_asset = self._download_latent(latent, segment_id, output_dir, prompt_id)
                            metadata.update(latent_asset.metadata)
                            # The local copy is evidence for the platform, but
                            # the next ComfyUI prompt must load the remote
                            # Motion Context directory created by SaveLatent.
                            metadata.update({"context_latent_ref": latent_folder, "latent_discovery": "history-output"})
                        elif role == "motion-context":
                            # 某些 Motion Context 节点只在 ComfyUI output 目录落盘，
                            # 不把文件作为 history.outputs 返回；保留 workflow ref，
                            # 由隔离 GPU 运行器的文件检查门禁最终确认。
                            metadata.update({"context_latent_ref": context_latent_ref, "latent_discovery": "workflow-reference-unverified"})
                        else:
                            metadata.update({"context_latent_ref": latent_folder, "latent_discovery": "workflow-output-prefix"})
                        metadata.update({"segment_index": segment_index, "workflow_role": role, "workflow_file": selected_workflow.name, "generation_mode": "long-video"})
                        if uploaded_image:
                            metadata["input_image"] = {key: uploaded_image.get(key, "") for key in ("name", "subfolder", "type")}
                        if uploaded_references:
                            metadata["input_references"] = [
                                {key: uploaded_references[slot].get(key, "") for key in ("name", "subfolder", "type")}
                                for slot in referenced_slots
                            ]
                        return GeneratedAsset(generated.relative_url, metadata)
            except urllib.error.URLError:
                pass
            time.sleep(self.poll_interval)
        raise ProviderError("ComfyUI long-video workflow timed out")

    @staticmethod
    def _motion_context_folder(path: str | None) -> str:
        """Normalize a Motion Context reference to a file or load directory.

        The H3 node's indexed loader only scans a directory for
        ``*_NNNNN.safetensors``.  Accept the old ``.../clip`` prefix and a
        concrete safetensors file for compatibility, but return the directory
        that the current workflow contract expects.
        """
        value = str(path or "").strip().strip("'").strip('"')
        if not value:
            return value
        if value.lower().endswith(".safetensors"):
            return str(Path(value).parent)
        if value.endswith("/clip") or value.endswith("\\clip"):
            return str(Path(value).parent)
        return value

    @classmethod
    def _replace_typed(cls, value: Any, replacements: dict[str, Any]) -> Any:
        if isinstance(value, str):
            if value in replacements:
                return replacements[value]
            for source, target in replacements.items():
                if isinstance(target, str):
                    value = value.replace(source, target)
            return value
        if isinstance(value, list):
            return [cls._replace_typed(item, replacements) for item in value]
        if isinstance(value, dict):
            return {key: cls._replace_typed(item, replacements) for key, item in value.items()}
        return value

    @staticmethod
    def _find_media(outputs: dict[str, Any]) -> dict[str, str] | None:
        if not isinstance(outputs, dict):
            return None
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            for key in ("videos", "gifs", "images"):
                media_items = output.get(key, [])
                if not isinstance(media_items, list):
                    continue
                for media in media_items:
                    if not isinstance(media, dict):
                        continue
                    if str(media.get("filename", "")).strip():
                        return {name: str(media.get(name, "")) for name in ("filename", "subfolder", "type")}
        return None

    @staticmethod
    def _find_latent(outputs: dict[str, Any]) -> dict[str, str] | None:
        """识别自定义 SaveLatent 节点常见的 history 输出形态。"""
        if not isinstance(outputs, dict):
            return None
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            for key in ("latents", "latent", "safetensors", "files"):
                items = output.get(key, [])
                if isinstance(items, dict):
                    items = [items]
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    filename = str(item.get("filename", "")).strip()
                    if filename and Path(filename).suffix.lower() in {".safetensors", ".latent", ".pt", ".bin"}:
                        return {name: str(item.get(name, "")) for name in ("filename", "subfolder", "type")}
        return None

    @staticmethod
    def _image_ref(uploaded_image: dict[str, str] | None) -> str:
        if not uploaded_image:
            return ""
        subfolder = uploaded_image.get("subfolder", "").strip("/")
        name = uploaded_image.get("name", "")
        return f"{subfolder}/{name}" if subfolder else name

    def _upload_source_image(self, source_image_path: Path, asset_id: str) -> dict[str, str]:
        try:
            content = source_image_path.read_bytes()
            max_bytes = int(os.environ.get("COMFYUI_INPUT_MAX_BYTES", str(50 * 1024 * 1024)))
        except (OSError, ValueError) as exc:
            raise ProviderError("ComfyUI source image is unavailable") from exc
        if not content:
            raise ProviderError("ComfyUI source image is empty")
        if len(content) > max(1, max_bytes):
            raise ProviderError("ComfyUI source image exceeds upload limit")
        boundary = f"----ai-manhua-{uuid.uuid4().hex}"
        extension = source_image_path.suffix.lower() or ".png"
        filename = f"{asset_id}{extension}"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(
            f"{self.base_url}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(_read_provider_response(response, "ComfyUI"))
            if not isinstance(payload, dict):
                raise ProviderError("ComfyUI source image upload returned invalid response")
            uploaded = {
                key: payload.get(key, "").strip() if isinstance(payload.get(key, ""), str) else ""
                for key in ("name", "subfolder", "type")
            }
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ProviderError, AttributeError, TypeError) as exc:
            raise ProviderError("ComfyUI source image upload failed") from exc
        if not uploaded["name"]:
            raise ProviderError("ComfyUI source image upload returned no filename")
        return uploaded

    def _download_media(self, media: dict[str, str], asset_id: str, output_dir: Path, prompt_id: str) -> GeneratedAsset:
        query = urllib.parse.urlencode({"filename": media["filename"], "subfolder": media["subfolder"], "type": media["type"]})
        try:
            with urllib.request.urlopen(f"{self.base_url}/view?{query}", timeout=120) as response:
                content = _read_comfyui_binary_response(response, "ComfyUI video")
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError("ComfyUI video download failed") from exc
        if not content:
            raise ProviderError("ComfyUI video download returned empty output")
        extension = Path(media["filename"]).suffix or ".mp4"
        path = output_dir / f"{asset_id}{extension}"
        try:
            path.write_bytes(content)
        except OSError as exc:
            raise ProviderError("ComfyUI video download failed") from exc
        return GeneratedAsset(f"/assets/{path.name}", {"provider": self.name, "prompt_id": prompt_id, "source": "comfyui"})

    def _download_latent(self, latent: dict[str, str], asset_id: str, output_dir: Path, prompt_id: str) -> GeneratedAsset:
        query = urllib.parse.urlencode({"filename": latent["filename"], "subfolder": latent["subfolder"], "type": latent["type"]})
        try:
            with urllib.request.urlopen(f"{self.base_url}/view?{query}", timeout=120) as response:
                content = _read_comfyui_binary_response(response, "ComfyUI latent")
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError("ComfyUI latent download failed") from exc
        if not content:
            raise ProviderError("ComfyUI latent download returned empty output")
        extension = Path(latent["filename"]).suffix or ".safetensors"
        path = output_dir / f"{asset_id}.context{extension}"
        try:
            path.write_bytes(content)
        except OSError as exc:
            raise ProviderError("ComfyUI latent download failed") from exc
        return GeneratedAsset(
            f"/assets/{path.name}",
            {
                "context_latent_relative_url": f"/assets/{path.name}",
                "context_latent_ref": str(latent.get("filename") or path.name),
                "latent_discovery": "history-output",
                "latent_prompt_id": prompt_id,
            },
        )


class LocalPreviewVideoProvider:
    name = "local"

    def generate(
        self,
        asset_id: str,
        shot_id: str,
        output_dir: Path,
        source_image_path: Path | None = None,
        prompt: str | None = None,
    ) -> GeneratedAsset:
        output_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            path = output_dir / f"{asset_id}.mp4"
            if source_image_path and source_image_path.is_file():
                # 本地预览也必须消费当前采用的关键帧；这不是 AI 推理，
                # 只是用 FFmpeg 做轻微推近，方便无 GPU 环境验收来源链路。
                preview_filter = (
                    "scale=1024:576:force_original_aspect_ratio=decrease,"
                    "pad=1024:576:(ow-iw)/2:(oh-ih)/2:color=0x161622,"
                    "zoompan=z='min(zoom+0.0015,1.04)':d=25:s=1024x576:fps=25"
                )
                try:
                    subprocess.run(
                        [
                            ffmpeg,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-loop",
                            "1",
                            "-i",
                            str(source_image_path),
                            "-vf",
                            preview_filter,
                            "-t",
                            "1",
                            "-an",
                            "-pix_fmt",
                            "yuv420p",
                            "-movflags",
                            "+faststart",
                            "-y",
                            str(path),
                        ],
                        check=True,
                    )
                    return GeneratedAsset(
                        f"/assets/{path.name}",
                        {
                            "mode": "local-video-preview",
                            "provider": self.name,
                            "duration_seconds": 1,
                            "source_image_used": True,
                        },
                    )
                except (OSError, subprocess.CalledProcessError):
                    path.unlink(missing_ok=True)
            # 输入素材无法解码时保留可诊断的纯色 fallback，不把它冒充成关键帧视频。
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x292743:s=1024x576:d=1",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(path),
                ],
                check=True,
            )
            return GeneratedAsset(
                f"/assets/{path.name}",
                {
                    "mode": "local-video-preview-fallback",
                    "provider": self.name,
                    "duration_seconds": 1,
                    "source_image_used": False,
                },
            )
        path = output_dir / f"{asset_id}.video-plan.json"
        path.write_text(json.dumps({"shot_id": shot_id, "duration_seconds": 1, "mode": "local-video-plan"}, ensure_ascii=False, indent=2), encoding="utf-8")
        return GeneratedAsset(f"/assets/{path.name}", {"mode": "local-video-plan", "provider": self.name, "duration_seconds": 1})

    def generate_segment(
        self,
        segment_id: str,
        shot_id: str,
        output_dir: Path,
        *,
        role: str,
        prompt: str,
        segment_index: int,
        width: int,
        height: int,
        frames: int,
        fps: float,
        steps: int,
        seed: int,
        sampler: str,
        context_length: int,
        audio_context_length: int,
        source_image_path: Path | None = None,
        context_latent_path: str | None = None,
        context_clip_index: int | None = None,
        latent_output_prefix: str | None = None,
        reference_image_paths: list[Path] | None = None,
    ) -> GeneratedAsset:
        """确定性的分段预览，实现同一服务合同但不冒充 H3 推理。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        duration = max(1.0, frames / max(1.0, fps))
        path = output_dir / f"{segment_id}.mp4"
        if not ffmpeg:
            raise ProviderError("long-video preview requires ffmpeg")
        color = "#292743" if role == "director" else "#243b3b"
        try:
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:d={duration}", "-r", str(fps), "-an", "-pix_fmt", "yuv420p", "-y", str(path)],
                check=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderError("long-video preview generation failed") from exc
        return GeneratedAsset(
            f"/assets/{path.name}",
            {
                "mode": "local-long-video-preview",
                "provider": self.name,
                "workflow_role": role,
                "segment_index": segment_index,
                "duration_seconds": round(duration, 3),
                "width": width,
                "height": height,
                "fps": fps,
                "steps": steps,
                "seed": seed,
                "sampler": sampler,
                "continuation_simulated": role == "motion-context",
                "context_latent_ref": str(latent_output_prefix or context_latent_path or "local-preview-context"),
            },
        )


@dataclass
class ProviderRegistry:
    text: TextProvider
    image: ImageProvider
    video: VideoProvider
    vision: VisionProvider
    speech: SpeechProvider

    @classmethod
    def local(cls) -> "ProviderRegistry":
        return cls(
            LocalPreviewTextProvider(),
            LocalPreviewImageProvider(),
            LocalPreviewVideoProvider(),
            LocalPreviewVisionProvider(),
            LocalPreviewSpeechProvider(),
        )

    @classmethod
    def from_env(cls, preferences: dict[str, Any] | None = None) -> "ProviderRegistry":
        """根据进程环境和用户的非敏感偏好构造 provider。

        API key 仍然只从进程环境读取；preferences 只能覆盖 provider、model、
        base_url 这类非敏感路由信息。
        """
        registry = cls.local()
        preferences = preferences or {}

        def configured(kind: str, env_name: str, default: str) -> str:
            value = preferences.get(kind)
            if isinstance(value, dict):
                preferred = value.get("provider")
                if preferred:
                    return str(preferred)
            return os.environ.get(env_name, default)

        def preference(kind: str, key: str, env_name: str, default: str) -> str:
            value = preferences.get(kind)
            if isinstance(value, dict) and value.get(key):
                return str(value[key])
            return os.environ.get(env_name, default)

        image_provider = configured("image", "STUDIO_IMAGE_PROVIDER", "local")
        if image_provider == "comfyui":
            base_url = preference("image", "base_url", "COMFYUI_BASE_URL", "http://127.0.0.1:8188")
            workflow_path = os.environ.get("COMFYUI_IMAGE_WORKFLOW", "")
            if not workflow_path:
                raise ProviderError("COMFYUI_IMAGE_WORKFLOW is required when STUDIO_IMAGE_PROVIDER=comfyui")
            registry.image = ComfyUIImageProvider(base_url, workflow_path)
        elif image_provider != "local":
            raise ProviderError(f"unsupported image provider: {image_provider}")

        video_provider = configured("video", "STUDIO_VIDEO_PROVIDER", "local")
        if video_provider == "comfyui":
            base_url = preference("video", "base_url", "COMFYUI_BASE_URL", "http://127.0.0.1:8188")
            workflow_path = os.environ.get("COMFYUI_VIDEO_WORKFLOW", "")
            t2v_workflow_path = os.environ.get("COMFYUI_T2V_VIDEO_WORKFLOW", "")
            r2v_workflow_path = os.environ.get("COMFYUI_R2V_VIDEO_WORKFLOW", "")
            long_video_first_workflow_path = os.environ.get("COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW", "")
            long_video_context_workflow_path = os.environ.get("COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW", "")
            if not workflow_path and not t2v_workflow_path and not r2v_workflow_path:
                raise ProviderError(
                    "COMFYUI_VIDEO_WORKFLOW or COMFYUI_T2V_VIDEO_WORKFLOW/COMFYUI_R2V_VIDEO_WORKFLOW is required when STUDIO_VIDEO_PROVIDER=comfyui"
                )
            registry.video = ComfyUIVideoProvider(
                base_url,
                workflow_path or t2v_workflow_path or r2v_workflow_path,
                t2v_workflow_path=t2v_workflow_path or None,
                r2v_workflow_path=r2v_workflow_path or None,
                long_video_first_workflow_path=long_video_first_workflow_path or None,
                long_video_context_workflow_path=long_video_context_workflow_path or None,
            )
        elif video_provider != "local":
            raise ProviderError(f"unsupported video provider: {video_provider}")

        text_provider = configured("text", "STUDIO_TEXT_PROVIDER", "local")
        if text_provider == "openai-compatible":
            registry.text = OpenAICompatibleTextProvider(
                preference("text", "base_url", "STUDIO_TEXT_BASE_URL", "http://127.0.0.1:8000/v1"),
                preference("text", "model", "STUDIO_TEXT_MODEL", "local-model"),
                os.environ.get("STUDIO_TEXT_API_KEY_ENV", "STUDIO_TEXT_API_KEY"),
            )
        elif text_provider != "local":
            raise ProviderError(f"unsupported text provider: {text_provider}")

        vision_provider = configured("vision", "STUDIO_VISION_PROVIDER", "local")
        if vision_provider == "openai-compatible":
            registry.vision = OpenAICompatibleVisionProvider(
                preference("vision", "base_url", "STUDIO_VISION_BASE_URL", os.environ.get("STUDIO_TEXT_BASE_URL", "http://127.0.0.1:8000/v1")),
                preference("vision", "model", "STUDIO_VISION_MODEL", "local-vision-model"),
                os.environ.get("STUDIO_VISION_API_KEY_ENV", "STUDIO_VISION_API_KEY"),
            )
        elif vision_provider != "local":
            raise ProviderError(f"unsupported vision provider: {vision_provider}")

        speech_provider = configured("speech", "STUDIO_SPEECH_PROVIDER", "local")
        if speech_provider == "openai":
            registry.speech = OpenAISpeechProvider(
                preference("speech", "base_url", "STUDIO_SPEECH_BASE_URL", "https://api.openai.com/v1"),
                preference("speech", "model", "STUDIO_SPEECH_MODEL", "gpt-4o-mini-tts-2025-12-15"),
                os.environ.get("STUDIO_SPEECH_API_KEY_ENV", "OPENAI_API_KEY"),
            )
        elif speech_provider != "local":
            raise ProviderError(f"unsupported speech provider: {speech_provider}")
        return registry
