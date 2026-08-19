"""GOLDEN_SHOTS 媒体回归库的薄验证适配器。

该模块只负责读取已经人工审阅过的样本清单，并调用系统已有的
``ffprobe`` / ``ffmpeg`` 做媒体完整性复核。它不实现视频解码、视觉相似度
或模型推理；视觉质量仍由人工审片或已登记的视觉 provider 提供。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Mapping


class GoldenShotError(ValueError):
    """回归样本清单不符合合同。"""


class GoldenShotPathError(GoldenShotError):
    """样本或证据路径越出项目根目录。"""


ProbeFn = Callable[[Path], Mapping[str, Any]]
DecodeFn = Callable[[Path], bool]


def _require_text(value: Any, field_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise GoldenShotError(f"{field_name} cannot be empty")
    return value


def _validate_sha256(value: Any) -> str:
    sha256 = _require_text(value, "sha256").lower()
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise GoldenShotError("sha256 must be a 64-character lowercase hexadecimal digest")
    return sha256


def _validate_relative_path(value: Any, field_name: str) -> str:
    raw = _require_text(value, field_name).replace("\\", "/")
    path = Path(raw)
    if path.is_absolute() or raw.startswith("/"):
        raise GoldenShotPathError(f"{field_name} must be relative: {raw}")
    if any(part == ".." for part in path.parts):
        raise GoldenShotPathError(f"{field_name} cannot contain '..': {raw}")
    return raw


def _as_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GoldenShotError(f"{field_name} must be an object")
    return dict(value)


@dataclass(frozen=True)
class GoldenShotRecord:
    """一条经过人工审阅、可作为回归基线的媒体记录。"""

    record_id: str
    artifact_path: str
    sha256: str
    review_status: str
    evidence_refs: tuple[str, ...] = ()
    expected_media: Mapping[str, Any] = field(default_factory=dict)
    generation: Mapping[str, Any] = field(default_factory=dict)
    review_basis: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _require_text(self.record_id, "record_id"))
        object.__setattr__(self, "artifact_path", _validate_relative_path(self.artifact_path, "artifact_path"))
        object.__setattr__(self, "sha256", _validate_sha256(self.sha256))
        status = _require_text(self.review_status, "review_status")
        if status not in {"accepted", "candidate", "blocked"}:
            raise GoldenShotError(f"unsupported review_status: {status}")
        object.__setattr__(self, "review_status", status)
        refs = tuple(_validate_relative_path(ref, "evidence_ref") for ref in self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "expected_media", _as_mapping(self.expected_media, "expected_media"))
        object.__setattr__(self, "generation", _as_mapping(self.generation, "generation"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldenShotRecord":
        if not isinstance(data, Mapping):
            raise GoldenShotError("each GOLDEN_SHOTS record must be an object")
        return cls(
            record_id=data.get("record_id", data.get("id")),
            artifact_path=data.get("artifact_path"),
            sha256=data.get("sha256"),
            review_status=data.get("review_status"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            expected_media=data.get("expected_media") or {},
            generation=data.get("generation") or {},
            review_basis=str(data.get("review_basis") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "artifact_path": self.artifact_path,
            "sha256": self.sha256,
            "review_status": self.review_status,
            "evidence_refs": list(self.evidence_refs),
            "expected_media": dict(self.expected_media),
            "generation": dict(self.generation),
            "review_basis": self.review_basis,
        }


@dataclass(frozen=True)
class GoldenShotManifest:
    manifest_version: str
    project_id: str
    records: tuple[GoldenShotRecord, ...]
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.manifest_version != "1.0":
            raise GoldenShotError(f"unsupported manifest_version: {self.manifest_version}")
        _require_text(self.project_id, "project_id")
        ids = [record.record_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise GoldenShotError("record_id values must be unique")
        paths = [record.artifact_path for record in self.records]
        if len(paths) != len(set(paths)):
            raise GoldenShotError("artifact_path values must be unique")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldenShotManifest":
        if not isinstance(data, Mapping):
            raise GoldenShotError("GOLDEN_SHOTS manifest must be an object")
        records = tuple(GoldenShotRecord.from_dict(item) for item in (data.get("records") or ()))
        if not records:
            raise GoldenShotError("GOLDEN_SHOTS manifest must contain at least one record")
        return cls(
            manifest_version=str(data.get("manifest_version") or ""),
            project_id=str(data.get("project_id") or ""),
            records=records,
            created_at=str(data.get("created_at") or "").strip(),
        )

    @classmethod
    def load(cls, path: str | Path) -> "GoldenShotManifest":
        manifest_path = Path(path)
        with manifest_path.open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass
class GoldenVerification:
    record_id: str
    status: str = "unknown"  # passed | failed | unknown
    checks: list[dict[str, Any]] = field(default_factory=list)
    actual_media: dict[str, Any] = field(default_factory=dict)
    actual_sha256: str | None = None

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append({"name": name, "status": status, "detail": detail})

    def finalize(self) -> "GoldenVerification":
        statuses = {check["status"] for check in self.checks}
        if "failed" in statuses:
            self.status = "failed"
        elif "unknown" in statuses:
            self.status = "unknown"
        else:
            self.status = "passed"
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "status": self.status,
            "checks": list(self.checks),
            "actual_media": dict(self.actual_media),
            "actual_sha256": self.actual_sha256,
        }


def _safe_artifact(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GoldenShotPathError(f"artifact path escapes project root: {relative_path}") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _compare_media(expected: Mapping[str, Any], actual: Mapping[str, Any], result: GoldenVerification) -> None:
    if not expected:
        result.add("media-contract", "unknown", "expected_media is empty")
        return
    duration_expected = expected.get("duration_sec")
    duration_actual = actual.get("duration_sec")
    if duration_expected is not None:
        tolerance = float(expected.get("duration_tolerance_sec", 0.05))
        if duration_actual is None:
            result.add("duration", "unknown", "ffprobe did not return duration_sec")
        elif abs(float(duration_actual) - float(duration_expected)) <= tolerance:
            result.add("duration", "passed", f"{duration_actual}s within ±{tolerance}s")
        else:
            result.add("duration", "failed", f"{duration_actual}s != {duration_expected}s ±{tolerance}s")

    for key in ("width", "height", "fps_num", "fps_den", "video_codec", "audio_codec"):
        if key not in expected:
            continue
        if actual.get(key) == expected[key]:
            result.add(key, "passed", f"{actual.get(key)}")
        else:
            result.add(key, "failed", f"{actual.get(key)!r} != {expected[key]!r}")


def verify_record(
    record: GoldenShotRecord,
    project_root: str | Path,
    *,
    probe: ProbeFn | None = None,
    decode: DecodeFn | None = None,
) -> GoldenVerification:
    """验证一条回归样本；没有完整媒体证据时保守返回 unknown。"""

    result = GoldenVerification(record_id=record.record_id)
    artifact = _safe_artifact(Path(project_root), record.artifact_path)
    if not artifact.is_file():
        result.add("artifact", "failed", f"missing file: {record.artifact_path}")
        return result.finalize()
    result.add("artifact", "passed", str(artifact))

    actual_sha256 = _sha256(artifact)
    result.actual_sha256 = actual_sha256
    if actual_sha256 == record.sha256:
        result.add("sha256", "passed", actual_sha256)
    else:
        result.add("sha256", "failed", f"{actual_sha256} != {record.sha256}")

    if probe is None:
        result.add("ffprobe", "unknown", "ffprobe evidence was not supplied")
    else:
        try:
            result.actual_media = dict(probe(artifact))
            result.add("ffprobe", "passed", "media metadata collected")
            _compare_media(record.expected_media, result.actual_media, result)
        except FileNotFoundError as exc:
            result.add("ffprobe", "unknown", f"probe dependency unavailable: {exc}")
        except Exception as exc:  # provider/CLI errors must not become a false pass
            result.add("ffprobe", "failed", f"probe failed: {exc}")

    if decode is None:
        result.add("full-decode", "unknown", "full decode evidence was not supplied")
    else:
        try:
            decoded = bool(decode(artifact))
            result.add("full-decode", "passed" if decoded else "failed", "ffmpeg null decode")
        except FileNotFoundError as exc:
            result.add("full-decode", "unknown", f"decoder dependency unavailable: {exc}")
        except Exception as exc:
            result.add("full-decode", "failed", f"decode failed: {exc}")
    return result.finalize()


def verify_manifest(
    manifest: GoldenShotManifest,
    project_root: str | Path,
    *,
    probe: ProbeFn | None = None,
    decode: DecodeFn | None = None,
) -> list[GoldenVerification]:
    return [verify_record(record, project_root, probe=probe, decode=decode) for record in manifest.records]


def ffprobe_media(path: Path) -> Mapping[str, Any]:
    """调用已存在的 ffprobe，不引入 Python 媒体解析依赖。"""

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffprobe returned non-zero")
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    fps_value = str(video.get("r_frame_rate") or "0/1")
    try:
        fps = Fraction(fps_value)
        fps_num, fps_den = fps.numerator, fps.denominator
    except (ValueError, ZeroDivisionError):
        fps_num, fps_den = 0, 1
    format_data = payload.get("format") or {}
    return {
        "duration_sec": float(format_data["duration"]) if format_data.get("duration") else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps_num": fps_num,
        "fps_den": fps_den,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def ffmpeg_full_decode(path: Path) -> bool:
    """用 ffmpeg 完整解码到 null；返回码是唯一通过条件。"""

    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffmpeg returned non-zero")
    return True
