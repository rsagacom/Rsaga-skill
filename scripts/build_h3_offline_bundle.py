#!/usr/bin/env python3
"""Build a self-contained, file://-compatible MiniMax H3 evidence browser."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
RUN_ROOT = PROJECT / "test-runs"
DEST = PROJECT / "h3-evaluation-offline"
ALLOWED_EXTENSIONS = {
    ".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".json", ".jsonl", ".md", ".log", ".txt", ".csv", ".safetensors",
}
SOURCE_DOCS = [
    PROJECT / "AGENTS.md",
    PROJECT / "docs/H3_PRODUCTION_HANDOFF.md",
    PROJECT / "docs/AI_MANHUA_STUDIO_BLUEPRINT.md",
    PROJECT / "docs/H3_PRODUCTION_SELECTION.md",
    PROJECT / "docs/H3_REUSABLE_EXECUTION_BLUEPRINT.md",
    PROJECT / "docs/H3_COMPLETE_MODEL_TEST_PLAN_2026-08-16.md",
    PROJECT / "workflows/h3-production/README.md",
    PROJECT / "workflows/h3-production/production-profile.json",
    PROJECT / "scripts/run_h3_production_job.py",
    PROJECT / "web/app/h3-evaluation/page.tsx",
    PROJECT / "web/app/h3-evaluation/page.module.css",
    Path("/Users/rsaga/Desktop/H3.md"),
]

RUN_NOTES: dict[str, dict[str, Any]] = {
    "2026-08-12-h3-baseline": {"family": "基线 / A-B", "note": "早期 4v4a 基线与无 LoRA 对照；用于判断音视频链路是否工作。"},
    "2026-08-12-h3-int8-t8": {"family": "T8 Turbo 基线", "note": "非剪枝 INT8 + INT4 Qwen + T8 双时钟采样，4 步与 8 步对照。"},
    "2026-08-12-h3-official-int8": {"family": "官方 INT8 基线", "note": "官方 20 步无 Turbo LoRA 基线；保留用于对照，不作为当前生产质量基线。"},
    "2026-08-13-h3-blockcache": {"family": "BlockCache", "note": "官方 INT8 + BlockCache 路径；验证缓存加速与画面稳定边界。"},
    "2026-08-13-h3-director-aimixer": {"family": "Director", "note": "AIMixer / MiniMax H3 Director 两段时间轴接力，验证生产编排入口。"},
    "2026-08-13-h3-director-package": {"family": "社区工作流包", "note": "夸克整合包解包和工作流留档，作为社区部署参考，不等同于当前正式链路。"},
    "2026-08-13-h3-hd-matrix": {"family": "高清分辨率矩阵", "note": "统一 seed、统一工作流的 360p–1MP、真人/3D/水墨/动画风格矩阵；含 8 步与 1MP 20 步样本。"},
    "2026-08-13-h3-hq-matrix": {"family": "高清质量矩阵", "note": "1088×608 与 1344×768（约 1MP）质量档，包含真人、3D 国风和水墨 CG。"},
    "2026-08-13-h3-larry-int8": {"family": "LoRA A/B", "note": "Larry LoRA INT8 6 步对照。"},
    "2026-08-13-h3-longvideo": {"family": "长段时长矩阵", "note": "T8 8 步 10 秒、12 秒、15 秒可运行样本，记录 RTX 3060 12GB 的长度上限。"},
    "2026-08-13-h3-motion-context": {"family": "Motion Context", "note": "独立 Motion Context 的 latent + 音频上下文接力，固定 context_length=22。"},
    "2026-08-13-h3-multirate": {"family": "多速率采样", "note": "视频/音频不同步数的多速率采样对照。"},
    "2026-08-13-h3-multirate-4v10a": {"family": "多速率采样", "note": "4 video / 10 audio 多速率路径。"},
    "2026-08-13-h3-official-1mp-recheck": {"family": "官方 1MP 复测", "note": "官方链路的 1MP 复测留档。"},
    "2026-08-13-h3-pruned-int8": {"family": "剪枝 INT8", "note": "剪枝 INT8 与官方 20 步路径对照；用于确认剪枝对画面和显存的影响。"},
    "2026-08-13-h3-pruned-larry": {"family": "剪枝 + Larry", "note": "剪枝底座配 Larry LoRA 的速度/画面对照。"},
    "2026-08-13-h3-quark4step": {"family": "4 步 Turbo", "note": "社区夸克 4 步快速路径，作为预演速度参考。"},
    "2026-08-13-h3-spectrum": {"family": "Spectrum", "note": "Spectrum 跳步节点路径；留档用于与 T8、BlockCache 比较。"},
    "2026-08-13-h3-t8-1mp": {"family": "T8 1MP", "note": "T8 LoRA 1MP 8 步高分辨率样本。"},
    "2026-08-13-h3-t8-1mp-20steps": {"family": "T8 1MP 20 步", "note": "T8 LoRA + 1MP + 20 步；记录高负载和高质量边界。"},
    "2026-08-14-h3-5s-concat-30s": {"family": "独立 5 秒拼接", "note": "6 段独立 5 秒片段拼成 30 秒；可做剪辑式长视频，但硬切处没有 latent/音频上下文接力。"},
    "2026-08-14-h3-long-director-audio": {"family": "Director 长视频", "note": "Director 内置上下文接力，国风雨巷 + 普通话独白 + 环境音，30/60/120 秒三档。"},
    "2026-08-14-h3-long-director-r2v-character": {"family": "Director 三视图 R2V", "note": "角色三视图参考主体生视频，测试远景到近景的人物一致性。"},
    "2026-08-14-h3-lora-ab": {"family": "LoRA A/B", "note": "T8、Larry、Realism People、Drbaph Ref2V、Kijai LightX2V Ref2V 同 seed 对照。"},
    "2026-08-14-h3-r2v-3view-832x480-5s": {"family": "三视图 R2V", "note": "三视图参考、832×480、5 秒、8 步，T8 + SageAttention 基线。"},
    "2026-08-15-h3-3dcg-identity-ab": {"family": "3DCG 身份 A/B", "note": "3DCG 三视图与面部增强参考包对照。"},
    "2026-08-15-h3-3dcg-long-continuity": {"family": "3DCG 长连续性", "note": "Director 与 Motion Context 两套连续接力，三视图/面部参考包 30 秒对照。"},
    "2026-08-15-h3-identity-reference-ab": {"family": "面部参考 A/B", "note": "近景人物面部参考包强度对照。"},
    "2026-08-15-h3-live-i2v-5s": {"family": "真人 I2V", "note": "成功真人 T2V 基线只切换为首帧 R2V/I2V，5 秒复测；实际 584.98 秒，真人脸型与场景保持稳定。"},
    "2026-08-16-h3-nf4-diffsynth": {"family": "DiffSynth NF4 T2VA", "note": "官方 DiffSynth-Studio Pruned-NF4 FL2VA；512×288、50 步、Torch SDPA；3060 12GB 完成 5 秒音视频基线，耗时 734.13 秒。"},
    "2026-08-17-h3-reference-audio-gate": {"family": "选定底座 / 插件专项", "note": "剪枝 INT8 + drbaph 专用 LoRA 的三视图 R2V；对比 Director 与 Motion Context，并接入 CosyVoice3 独立对白封装。"},
    "2026-08-17-h3-pruned-lora-15s": {"family": "选定底座 / 15 秒", "note": "剪枝 INT8 + drbaph 剪枝专用 raw-key LoRA；Director 与 Motion Context 15 秒专项，作为当前长期生产主候选证据。"},
    "2026-08-17-h3-pruned-long-continuity": {"family": "Motion Context 长连续性", "note": "剪枝 INT8 无 LoRA 的 10/15/30 秒连续性控制链；用于确认上下文接力的硬件与时长边界。"},
    "2026-08-17-h3-pruned-resolution-gate": {"family": "剪枝 INT8 高清门禁", "note": "832×480 与 1088×608 单段 5 秒分辨率边界；不把无 LoRA 高清结果外推为长视频生产结论。"},
    "2026-08-17-h3-gguf-q4-baseline": {"family": "GGUF Q4_K_M", "note": "FL2VA/REF2VA/Qwen 官方 GGUF 工作流 T2V/R2V 兼容性基线；能跑但速度和 Sage 回退不适合长期生产。"},
    "2026-08-17-h3-q4km-gate": {"family": "GGUF Q4_K_M", "note": "Q4_K_M 低显存兼容性与资源门禁；与官方 GGUF Loader 链路分开记录。"},
    "2026-08-17-h3-int4-gate": {"family": "剪枝 INT4", "note": "剪枝 INT4 4/8/20 步与景别/画风门禁；作为低资源备用，不替代 INT8 质量结论。"},
    "2026-08-17-h3-full-int8-gate": {"family": "非剪枝 INT8", "note": "非剪枝 INT8 质量上限/备用底座门禁；资源占用高，不作为 RTX 3060 长链默认底座。"},
    "2026-08-17-h3-destiny-ch01-pilot": {"family": "《命运模型》第一章", "note": "第一章分镜与人物一致性试片；用于生产流程演练，不替代统一模型矩阵。"},
    "2026-08-18-h3-destiny-ch01-plugin-ab": {"family": "《命运模型》插件 A/B", "note": "选定生产底座在第一章正面对白场景中的 Director / Motion Context 公平 A/B。"},
    "2026-08-18-h3-destiny-ch01-director-gate": {"family": "Director 插件门禁", "note": "《命运模型》第一章正面对白 Director 15 秒链；检查时间轴连续性、音画封装和人物脸部。"},
    "2026-08-18-h3-destiny-ch01-motion-8step-gate": {"family": "Motion Context 插件门禁", "note": "《命运模型》第一章 Motion Context 8 steps 接力；逐段保留 AV latent、日志、解码和接缝证据。"},
    "2026-08-18-h3-destiny-ch01-hd-motion-8step-gate": {"family": "Motion Context 高清门禁", "note": "选定底座的高清 Motion Context 8 steps 代表性镜头；确认高分辨率不直接替代低分辨率长链结论。"},
    "2026-08-18-h3-destiny-ch01-hd-lora-gate": {"family": "选定底座 / 高清 LoRA", "note": "剪枝 INT8 + drbaph 专用 LoRA 高清单段门禁；作为后续对白近景和发行画质的入口。"},
    "2026-08-18-h3-destiny-ch01-audio-gate": {"family": "音频与对白门禁", "note": "《命运模型》第一章独立对白、TTS、音频混音与 H3 画面封装；口型同步仍需单独人工门禁。"},
}


def safe_copy_tree() -> list[Path]:
    marker = DEST / "BUNDLE_MARKER.txt"
    if DEST.exists():
        if not marker.exists():
            raise RuntimeError(f"Refusing to overwrite non-bundle directory: {DEST}")
        shutil.rmtree(DEST)
    (DEST / "runs").mkdir(parents=True)
    copied: list[Path] = []
    for source_run in sorted(RUN_ROOT.glob("2026-08-1[2-8]-h3-*")):
        if not source_run.is_dir():
            continue
        target_run = DEST / "runs" / source_run.name
        for source in sorted(source_run.rglob("*")):
            if not source.is_file():
                continue
            if any(part in {".git", "__pycache__"} for part in source.parts):
                continue
            if source.name.startswith("."):
                continue
            if source.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            relative = source.relative_to(source_run)
            target = target_run / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target.relative_to(DEST))
    sources = DEST / "sources"
    sources.mkdir()
    for source in SOURCE_DOCS:
        if source.exists():
            shutil.copy2(source, sources / source.name)
    marker.write_text(
        "This directory is generated by scripts/build_h3_offline_bundle.py.\n"
        "All files under runs/ are copied evidence from test-runs dated 2026-08-12 through 2026-08-18.\n",
        encoding="utf-8",
    )
    return copied


def json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def ffprobe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            check=True, capture_output=True, text=True,
        )
        raw = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"status": "ffprobe_failed"}
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    def number(value: Any) -> Any:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return {
        "status": "passed",
        "durationSeconds": number(raw.get("format", {}).get("duration")),
        "sizeBytes": int(raw.get("format", {}).get("size", path.stat().st_size)),
        "videoCodec": video.get("codec_name", ""),
        "width": video.get("width"), "height": video.get("height"),
        "fps": video.get("r_frame_rate", ""), "frames": video.get("nb_frames", ""),
        "audioCodec": audio.get("codec_name", ""),
        "audioSampleRate": audio.get("sample_rate", ""), "audioChannels": audio.get("channels", ""),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def all_artifacts(run_dir: Path, suffixes: set[str] | None = None) -> list[Path]:
    items = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        items.append(path)
    return items


def relative_link(path: Path) -> str:
    return path.relative_to(DEST).as_posix()


def find_related_json(run_dir: Path, video: Path) -> list[Path]:
    candidates = all_artifacts(run_dir, {".json", ".jsonl"})
    stem_tokens = set(re.findall(r"[a-z0-9]+", video.stem.lower()))
    scored: list[tuple[int, Path]] = []
    for item in candidates:
        tokens = set(re.findall(r"[a-z0-9]+", item.stem.lower()))
        score = len(stem_tokens & tokens)
        if "workflow" in item.name.lower():
            score += 4
        if item.name in {"review_manifest.json", "matrix.json"}:
            score += 1
        scored.append((score, item))
    return [item for _, item in sorted(scored, key=lambda row: (-row[0], row[1].as_posix()))[:8]]


def extract_settings(json_files: list[Path]) -> dict[str, Any]:
    settings: dict[str, Any] = {"models": [], "loras": [], "nodes": [], "prompts": [], "values": {}}
    for path in json_files:
        data = json_load(path)
        if data is None:
            continue
        for obj in walk_values(data):
            if not isinstance(obj, dict):
                continue
            if obj.get("class_type") and obj["class_type"] not in settings["nodes"]:
                settings["nodes"].append(obj["class_type"])
            for key, value in obj.items():
                if isinstance(value, str) and value.endswith(".safetensors"):
                    if "lora" in key.lower() or "lora" in value.lower():
                        if value not in settings["loras"]:
                            settings["loras"].append(value)
                    elif value not in settings["models"]:
                        settings["models"].append(value)
                lower = key.lower()
                if lower in {"steps", "seed", "width", "height", "frame_rate", "fps", "sampler", "scheduler", "cfg", "task_type", "context_length", "audio_context_length", "shift_video", "shift_audio", "strength_model", "duration_sec", "total_frames"}:
                    if isinstance(value, (str, int, float, bool)):
                        settings["values"].setdefault(key, value)
                if "prompt" in lower and isinstance(value, str) and len(value.strip()) > 10:
                    clean = value.strip()
                    if clean not in settings["prompts"]:
                        settings["prompts"].append(clean)
    return settings


def find_elapsed(run_dir: Path, video: Path, run_name: str) -> float | None:
    exact = video.name
    # The Director long-video report contains the exact end-to-end totals;
    # use them before scanning a shared log, otherwise the 120s log total
    # would be incorrectly repeated for the 30s and 60s files in the batch.
    if run_name == "2026-08-14-h3-long-director-audio":
        known = {"30": 2065.61, "60": 4438.23, "120": 8667.43}
        match = re.search(r"(?:^|_)(30|60|120)s(?:_|\.)", video.name)
        if match:
            return known[match.group(1)]
    for path in all_artifacts(run_dir, {".json"}):
        data = json_load(path)
        for obj in walk_values(data):
            if not isinstance(obj, dict):
                continue
            out = str(obj.get("output", obj.get("video", "")))
            if exact not in out and video.stem not in out and str(obj.get("id", "")) not in video.stem:
                continue
            for key in ("elapsed_seconds", "elapsedSeconds", "elapsed"):
                value = obj.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in all_artifacts(run_dir, {".md", ".log", ".txt"}))
    for pattern in (r"Prompt executed in\s+(\d+):(\d+):(\d+)", r"total elapsed[^0-9]*(\d+(?:\.\d+)?)\s*(?:s|秒)", r"耗时[^0-9]*(\d+(?:\.\d+)?)\s*(?:s|秒)"):
        match = re.search(pattern, text, re.I)
        if match:
            if len(match.groups()) == 3:
                h, m, s = map(float, match.groups())
                return h * 3600 + m * 60 + s
            return float(match.group(1))
    # The long Director report gives exact totals in its table, even when the
    # corresponding output file is named independently from the workflow.
    return None


def poster_for(video: Path, run_dir: Path, index: int) -> Path | None:
    stem = video.stem
    candidates = [
        p for p in all_artifacts(run_dir, {".png", ".jpg", ".jpeg", ".webp"})
        if stem in p.stem or p.parent.name == stem or "contact" in p.name.lower()
    ]
    preferred = next((p for p in candidates if "frame_02" in p.name or "middle" in p.name.lower()), None)
    if preferred is None and candidates:
        preferred = candidates[0]
    if preferred:
        return preferred
    poster_dir = DEST / "posters"
    poster_dir.mkdir(exist_ok=True)
    poster = poster_dir / f"{index:04d}_{sha256(video)[:10]}.jpg"
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.5", "-i", str(video), "-frames:v", "1", "-q:v", "4", str(poster)], check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return poster if poster.exists() else None


def make_video_item(video: Path, index: int) -> dict[str, Any]:
    run_dir = next(parent for parent in [video.parent, *video.parents] if parent.parent == DEST / "runs")
    run_name = run_dir.name
    note = RUN_NOTES.get(run_name, {"family": "H3 测试", "note": "已纳入近日报告素材包。"})
    related = find_related_json(run_dir, video)
    settings = extract_settings(related)
    media = ffprobe(video)
    poster = poster_for(video, run_dir, index)
    reports = [relative_link(p) for p in all_artifacts(run_dir, {".md", ".log", ".txt"})]
    json_links = [relative_link(p) for p in related]
    prompt = settings["prompts"][0] if settings["prompts"] else ""
    return {
        "id": f"{run_name}::{video.relative_to(run_dir).as_posix()}",
        "date": run_name[:10], "run": run_name, "family": note["family"], "runNote": note["note"],
        "title": video.stem,
        "video": relative_link(video), "poster": relative_link(poster) if poster else "",
        "sourceFile": video.name, "sourceRunPath": str(RUN_ROOT / run_name / video.relative_to(run_dir)),
        "sizeBytes": video.stat().st_size, "sha256": sha256(video), "media": media,
        "elapsedSeconds": find_elapsed(run_dir, video, run_name),
        "settings": settings, "prompt": prompt[:5000], "reports": reports, "jsonLinks": json_links,
        "runFiles": [relative_link(p) for p in all_artifacts(run_dir) if p.stat().st_size < 12 * 1024 * 1024][:120],
    }


def fmt_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024
    return str(value)


def build_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for run_dir in sorted((DEST / "runs").iterdir()):
        if not run_dir.is_dir():
            continue
        videos = all_artifacts(run_dir, {".mp4", ".webm", ".mov"})
        for video in videos:
            items.append(make_video_item(video, len(items) + 1))
        note = RUN_NOTES.get(run_dir.name, {"family": "H3 测试", "note": "已纳入近日报告素材包。"})
        files = all_artifacts(run_dir)
        runs.append({
            "name": run_dir.name, "date": run_dir.name[:10], "family": note["family"], "note": note["note"],
            "videoCount": len(videos), "fileCount": len(files), "sizeBytes": sum(p.stat().st_size for p in files),
            "reports": [relative_link(p) for p in files if p.suffix.lower() in {".md", ".log", ".txt"}],
        })
    return items, runs


def js_data(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>MiniMax H3 · 离线评测资料台</title>
<style>
:root{--bg:#0d0f0e;--panel:#151816;--panel2:#1b1f1c;--ink:#f5f0e6;--paper:#d9d1bf;--muted:#8f968e;--line:#2b302d;--amber:#e6a15b;--mint:#9cd3bb;--red:#e58b79;--mono:"SFMono-Regular",Consolas,monospace}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;color:var(--ink);background:var(--bg);font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif}a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}button,input,select{font:inherit}button{cursor:pointer;color:inherit}.top{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;min-height:62px;padding:0 clamp(18px,4vw,64px);background:rgba(13,15,14,.92);border-bottom:1px solid var(--line);backdrop-filter:blur(16px)}.brand{display:flex;gap:11px;align-items:center;font-size:12px;letter-spacing:.12em}.mark{color:var(--amber);font-size:22px}.topmeta{color:var(--muted);font-size:11px}.hero{padding:64px clamp(18px,7vw,108px) 58px;display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:48px;border-bottom:1px solid var(--line);background:radial-gradient(circle at 76% 10%,#694c2c33,transparent 31%),radial-gradient(circle at 100% 100%,#346b5930,transparent 28%)}.eyebrow{color:var(--amber);font:10px/1.5 var(--mono);letter-spacing:.18em;text-transform:uppercase}.hero h1{max-width:800px;margin:20px 0 20px;font-size:clamp(42px,7vw,92px);line-height:.92;letter-spacing:-.075em;font-weight:700}.hero h1 em{color:var(--amber);font-style:normal}.lead{max-width:700px;color:var(--paper);font-size:15px;line-height:1.8}.heroaside{align-self:end;color:var(--muted);font-size:11px;line-height:1.75}.heroaside strong{display:block;margin:10px 0;color:var(--ink);font-size:23px;line-height:1.15}.rule{height:1px;background:var(--amber)}.stats{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid var(--line)}.stat{min-height:112px;padding:19px clamp(15px,3vw,38px);border-right:1px solid var(--line)}.stat:last-child{border:0}.stat small{display:block;color:var(--muted);font-size:10px}.stat strong{display:block;margin-top:12px;font-size:28px;letter-spacing:-.05em}.controls{display:flex;flex-wrap:wrap;align-items:end;gap:12px;padding:18px clamp(18px,4vw,64px);background:#111311;border-bottom:1px solid var(--line)}.control{display:grid;gap:6px;color:var(--muted);font-size:10px;letter-spacing:.04em}.control.grow{flex:1;min-width:240px}.control input,.control select{width:100%;padding:10px 11px;color:var(--paper);background:var(--panel);border:1px solid var(--line);border-radius:2px;outline:0}.control input:focus,.control select:focus{border-color:var(--amber)}.count{margin-left:auto;margin-bottom:10px;color:var(--amber);font:11px var(--mono);white-space:nowrap}.layout{max-width:1700px;margin:auto;padding:38px clamp(18px,4vw,64px) 68px;display:grid;grid-template-columns:340px minmax(0,1fr);gap:42px}.sidehead,.detailhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.sidehead h2,.detailhead h2{margin:7px 0 0;font-size:20px;font-weight:500;letter-spacing:-.04em}.sidehead small{color:var(--muted);font:20px var(--mono)}.list{margin-top:20px;border-top:1px solid var(--line)}.row{width:100%;display:grid;grid-template-columns:25px minmax(0,1fr) auto;gap:8px;align-items:center;padding:12px 8px;text-align:left;background:transparent;border:0;border-bottom:1px solid #ffffff0e;color:var(--muted);transition:.18s}.row:hover,.row.active{color:var(--ink);background:#e6a15b12;box-shadow:inset 2px 0 var(--amber)}.rowno{color:var(--amber);font:10px var(--mono)}.rowmain{min-width:0}.rowmain b,.rowmain span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rowmain b{font-size:12px;font-weight:500}.rowmain span{margin-top:5px;color:var(--muted);font-size:10px}.rowtime{font:10px var(--mono);color:var(--paper);white-space:nowrap}.detailhead{align-items:center;margin-bottom:16px}.badge{padding:6px 10px;color:var(--mint);border:1px solid #9cd3bb59;border-radius:999px;font-size:10px;white-space:nowrap}.stage{overflow:hidden;background:#070807;border:1px solid var(--line)}.stage video{width:100%;display:block;max-height:65vh;aspect-ratio:16/9;object-fit:contain;background:#070807}.facts{display:grid;grid-template-columns:repeat(6,1fr);border:1px solid var(--line);border-top:0}.fact{min-height:70px;padding:12px;border-right:1px solid var(--line)}.fact:last-child{border:0}.fact small{display:block;color:var(--muted);font-size:9px}.fact b{display:block;margin-top:7px;color:var(--paper);font:12px var(--mono);white-space:nowrap}.section{margin-top:32px;border-top:1px solid var(--line)}.sectionhead{display:flex;align-items:baseline;gap:12px;padding-top:15px}.sectionhead h3{margin:0;font-size:15px;font-weight:500}.sectionhead span{color:var(--muted);font-size:11px}.runnote{margin:11px 0 0;color:var(--muted);font-size:12px;line-height:1.7}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:15px}.info{min-width:0;padding:13px 14px;background:var(--panel);border:1px solid var(--line)}.info h4{margin:0 0 11px;color:var(--paper);font-size:12px;font-weight:500}.kv{display:grid;grid-template-columns:120px minmax(0,1fr);gap:8px 12px;color:var(--muted);font-size:10px;line-height:1.6}.kv b{overflow-wrap:anywhere;color:var(--paper);font:10px/1.6 var(--mono);font-weight:400}.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{padding:5px 7px;color:var(--paper);background:#ffffff0b;border:1px solid #ffffff13;font:10px var(--mono);overflow-wrap:anywhere}.links{display:flex;flex-wrap:wrap;gap:8px 14px;padding:12px 0;font-size:11px}.prompt{white-space:pre-wrap;max-height:260px;overflow:auto;color:var(--paper);background:#0b0d0c;border:1px solid var(--line);padding:14px;font:11px/1.7 var(--mono)}details{margin-top:12px;border-bottom:1px solid #ffffff12}summary{padding:12px 0;color:var(--paper);cursor:pointer;font-size:12px}summary::marker{color:var(--amber)}.long{max-width:1700px;margin:auto;padding:0 clamp(18px,4vw,64px) 70px}.longhead{padding-top:28px;border-top:1px solid var(--line)}.longhead h2{margin:10px 0;font-size:clamp(28px,4vw,48px);letter-spacing:-.06em;font-weight:500}.longhead p{max-width:780px;color:var(--muted);font-size:12px;line-height:1.7}.runlist{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:18px}.runitem{padding:14px;background:var(--panel);border:1px solid var(--line)}.runitem b{display:block;color:var(--paper);font-size:12px}.runitem span{display:block;margin-top:7px;color:var(--muted);font:10px var(--mono)}.runitem p{min-height:37px;margin:10px 0 0;color:var(--muted);font-size:11px;line-height:1.6}.footer{padding:20px clamp(18px,4vw,64px);color:var(--muted);border-top:1px solid var(--line);font-size:10px;line-height:1.8}.empty{padding:30px 0;color:var(--muted);font-size:12px;text-align:center}@media(max-width:1000px){.hero{grid-template-columns:1fr}.heroaside{max-width:420px}.layout{grid-template-columns:260px minmax(0,1fr);gap:24px}.facts{grid-template-columns:repeat(3,1fr)}.fact:nth-child(3){border-right:0}.fact:nth-child(-n+3){border-bottom:1px solid var(--line)}.runlist{grid-template-columns:repeat(2,1fr)}}@media(max-width:720px){.topmeta{display:none}.hero{padding:48px 18px}.stats{grid-template-columns:repeat(2,1fr)}.stat:nth-child(even){border-right:0}.stat:nth-child(-n+4){border-bottom:1px solid var(--line)}.controls{padding:16px 18px}.count{width:100%;margin-left:0}.layout{display:block;padding:28px 18px 55px}.side{margin-bottom:38px}.list{display:grid;grid-template-columns:repeat(2,1fr)}.row{grid-template-columns:22px minmax(0,1fr);min-width:0}.rowtime{display:none}.grid2{grid-template-columns:1fr}.facts{grid-template-columns:repeat(2,1fr)}.fact:nth-child(odd){border-right:1px solid var(--line)}.fact:nth-child(even){border-right:0}.fact:nth-child(n){border-bottom:1px solid var(--line)}.fact:last-child{border-bottom:0}.long{padding:0 18px 55px}.runlist{grid-template-columns:1fr}}
</style></head>
<body>
<header class="top"><div class="brand"><span class="mark">✦</span><span>MINIMAX H3 / 离线评测资料台</span></div><div class="topmeta">2026.08.12 — 2026.08.18 · FILE:// READY</div></header>
<section class="hero"><div><p class="eyebrow">LOCAL EVIDENCE / VIDEO GENERATION</p><h1>把每一次<br><em>H3 生成</em>留在现场。</h1><p class="lead">近四天真实运行的 H3 视频、工作流、日志、抽帧、媒体探针和报告，被整理成一个无需部署的本地核对包。双击本页即可筛选、播放、查看生成组件与原始证据。</p></div><aside class="heroaside"><div class="rule"></div><strong>一页看清<br>速度 × 画质 × 连续性</strong><p>这是工程评测资料，不把“文件生成成功”误写成“生产级成片”。页面保留机器验收、抽帧审阅和报告中的边界。</p></aside></section>
<section class="stats"><div class="stat"><small>样本视频</small><strong id="statVideos">—</strong></div><div class="stat"><small>测试批次</small><strong id="statRuns">—</strong></div><div class="stat"><small>覆盖日期</small><strong>7 天</strong></div><div class="stat"><small>核心底座</small><strong>INT8</strong></div><div class="stat"><small>生产候选</small><strong>Motion</strong></div></section>
<section class="controls"><label class="control grow">查找视频 / LoRA / 节点 / 分辨率<input id="search" placeholder="例如：真人、Drbaph、704×416、Motion Context"></label><label class="control">测试日期<select id="dateFilter"><option value="all">全部日期</option></select></label><label class="control">测试家族<select id="familyFilter"><option value="all">全部家族</option></select></label><label class="control">模式<select id="modeFilter"><option value="all">全部模式</option><option value="短段">短段</option><option value="长视频">长视频</option><option value="三视图 / R2V">三视图 / R2V</option><option value="I2V">I2V</option><option value="矩阵">矩阵</option></select></label><span class="count" id="resultCount">—</span></section>
<main class="layout"><aside class="side"><div class="sidehead"><div><p class="eyebrow">INDEX / SAMPLES</p><h2>逐条样本</h2></div><small id="indexCount">—</small></div><div class="list" id="resultList"></div></aside><section class="detail" id="detail"></section></main>
<section class="long"><div class="longhead"><p class="eyebrow">RUNS / REPORTS / RAW EVIDENCE</p><h2>批次与原始资料</h2><p>每个测试批次都保留在 <code>runs/</code> 下：原始 MP4、抽帧、工作流 JSON、ffprobe JSON、运行日志、测试报告和拼接清单。点击文件名可直接打开或下载；复制整个文件夹到笔记本后链接仍然有效。</p></div><div class="runlist" id="runList"></div></section>
<footer class="footer">离线包由 <code>scripts/build_h3_offline_bundle.py</code> 生成。<span>所有结论以页面中的报告、日志、媒体解码和抽帧证据为准；未记录的耗时明确显示为“未单独记录”。</span></footer>
<script>
const DATA = __DATA__;
const RUNS = __RUNS__;
const $ = (id) => document.getElementById(id);
let filtered = DATA.slice();
let selected = DATA.find((x) => x.run === '2026-08-14-h3-long-director-audio' && x.title.includes('final_30s')) || DATA[0];
function fmtTime(seconds){ if(seconds === null || seconds === undefined) return '未单独记录'; const s=Math.round(seconds); const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60; return h ? `${h}时${m}分${sec}秒` : `${m}分${sec}秒`; }
function fmtDuration(seconds){ if(seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '—'; return `${Number(seconds).toFixed(3)}s`; }
function fmtBytes(bytes){ const units=['B','KB','MB','GB']; let n=Number(bytes); let u=0; while(n>=1024&&u<units.length-1){n/=1024;u++} return `${n.toFixed(n>=100?0:1)} ${units[u]}`; }
function val(x){ return x === undefined || x === null || x === '' ? '未记录' : String(x); }
function link(path,label){ return path ? `<a href="${encodeURI(path)}" target="_blank" rel="noreferrer">${label || path.split('/').pop()}</a>` : ''; }
function searchable(x){ return [x.title,x.run,x.family,x.runNote,x.sourceFile,x.settings.models.join(' '),x.settings.loras.join(' '),x.settings.nodes.join(' '),x.prompt].join(' ').toLowerCase(); }
function mode(x){ const text=searchable(x); if(text.includes('i2v')||text.includes('r2v')) return text.includes('i2v') ? 'I2V' : '三视图 / R2V'; if(text.includes('motion context')||text.includes('director')) return '长视频'; if(text.includes('matrix')||x.run.includes('matrix')) return '矩阵'; return '短段'; }
function setOptions(){ const dates=[...new Set(DATA.map(x=>x.date))].sort(); dates.forEach(d=>$('dateFilter').insertAdjacentHTML('beforeend',`<option>${d}</option>`)); const fam=[...new Set(DATA.map(x=>x.family))].sort(); fam.forEach(f=>$('familyFilter').insertAdjacentHTML('beforeend',`<option>${f}</option>`)); }
function apply(){ const q=$('search').value.trim().toLowerCase(), d=$('dateFilter').value, f=$('familyFilter').value, m=$('modeFilter').value; filtered=DATA.filter(x=>(!q||searchable(x).includes(q))&&(d==='all'||x.date===d)&&(f==='all'||x.family===f)&&(m==='all'||mode(x)===m)); $('resultCount').textContent=`${filtered.length} / ${DATA.length} 样本`; renderList(); if(!filtered.includes(selected)) selected=filtered[0]; renderDetail(); }
function renderList(){ const el=$('resultList'); if(!filtered.length){el.innerHTML='<div class="empty">没有匹配样本</div>';return} el.innerHTML=filtered.map((x,i)=>`<button class="row ${selected===x?'active':''}" data-id="${encodeURIComponent(x.id)}"><span class="rowno">${String(DATA.indexOf(x)+1).padStart(3,'0')}</span><span class="rowmain"><b>${x.title}</b><span>${x.family} · ${x.media.width||'?'}×${x.media.height||'?'} · ${fmtDuration(x.media.durationSeconds)}</span></span><span class="rowtime">${x.elapsedSeconds?fmtTime(x.elapsedSeconds):'—'}</span></button>`).join(''); el.querySelectorAll('.row').forEach(btn=>btn.addEventListener('click',()=>{selected=DATA.find(x=>x.id===decodeURIComponent(btn.dataset.id));renderList();renderDetail();window.scrollTo({top:document.querySelector('.detail').offsetTop-90,behavior:'smooth'});})); }
function chips(values){ return values && values.length ? `<div class="chips">${values.map(v=>`<span class="chip">${escapeHtml(v)}</span>`).join('')}</div>` : '<span class="muted">未从工作流 JSON 提取</span>'; }
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function renderDetail(){ const x=selected, el=$('detail'); if(!x){el.innerHTML='<div class="empty">请先选择样本</div>';return} const media=x.media||{}; const values=x.settings.values||{}; const reportLinks=x.reports.map(p=>link(p)).join(' · ')||'无'; const jsonLinks=x.jsonLinks.map(p=>link(p)).join(' · ')||'无'; const runFiles=x.runFiles.map(p=>link(p)).join(' · ')||'无'; el.innerHTML=`<div class="detailhead"><div><p class="eyebrow">SELECTED SAMPLE / ${x.date}</p><h2>${escapeHtml(x.title)}</h2></div><span class="badge">${escapeHtml(x.family)}</span></div><div class="stage">${x.video?`<video controls preload="metadata" poster="${encodeURI(x.poster)}" src="${encodeURI(x.video)}"></video>`:'<div class="empty">无视频文件</div>'}</div><div class="facts"><div class="fact"><small>实际时长</small><b>${fmtDuration(media.durationSeconds)}</b></div><div class="fact"><small>生成耗时</small><b>${fmtTime(x.elapsedSeconds)}</b></div><div class="fact"><small>分辨率</small><b>${val(media.width)}×${val(media.height)}</b></div><div class="fact"><small>帧率 / 帧数</small><b>${val(media.fps)} / ${val(media.frames)}</b></div><div class="fact"><small>视频 / 音频</small><b>${val(media.videoCodec)} / ${val(media.audioCodec)}</b></div><div class="fact"><small>文件大小</small><b>${fmtBytes(x.sizeBytes)}</b></div></div><div class="section"><div class="sectionhead"><h3>这一条测试说明</h3><span>${escapeHtml(x.run)}</span></div><p class="runnote">${escapeHtml(x.runNote)}</p></div><div class="grid2"><div class="info"><h4>媒体与机器验收</h4><div class="kv"><span>完整解码</span><b>${val(media.status)}</b><span>采样率</span><b>${val(media.audioSampleRate)}</b><span>声道</span><b>${val(media.audioChannels)}</b><span>SHA-256</span><b>${x.sha256}</b><span>原始文件</span><b>${escapeHtml(x.sourceRunPath)}</b></div></div><div class="info"><h4>工作流关键值</h4><div class="kv"><span>steps</span><b>${val(values.steps)}</b><span>采样器</span><b>${val(values.sampler)}</b><span>scheduler</span><b>${val(values.scheduler)}</b><span>CFG / seed</span><b>${val(values.cfg)} / ${val(values.seed)}</b><span>任务类型</span><b>${val(values.task_type)}</b><span>尺寸 / 帧率</span><b>${val(values.width)}×${val(values.height)} / ${val(values.frame_rate||values.fps)}</b></div></div></div><div class="section"><div class="sectionhead"><h3>模型、LoRA 与节点</h3><span>从关联工作流 JSON 提取</span></div><div class="grid2"><div class="info"><h4>模型资产</h4>${chips(x.settings.models)}</div><div class="info"><h4>LoRA</h4>${chips(x.settings.loras)}</div><div class="info"><h4>节点链路</h4>${chips(x.settings.nodes)}</div><div class="info"><h4>上下文 / 长度参数</h4><div class="kv">${Object.entries(values).filter(([k])=>/context|duration|total_frames|shift|strength/.test(k)).map(([k,v])=>`<span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b>`).join('')||'<span>未提取</span>'}</div></div></div></div><div class="section"><div class="sectionhead"><h3>提示词与原始证据</h3><span>不截断原始文件；此处展示提示词预览</span></div>${x.prompt?`<details open><summary>提示词预览</summary><div class="prompt">${escapeHtml(x.prompt)}</div></details>`:''}<div class="links"><strong>报告 / 日志：</strong>${reportLinks}</div><div class="links"><strong>工作流 / JSON：</strong>${jsonLinks}</div><div class="links"><strong>本批次其他证据：</strong>${runFiles}</div></div>`; }
function renderRuns(){ $('runList').innerHTML=RUNS.map(r=>`<article class="runitem"><b>${escapeHtml(r.name)}</b><span>${escapeHtml(r.family)} · ${r.videoCount} 个视频 · ${r.fileCount} 个证据文件 · ${fmtBytes(r.sizeBytes)}</span><p>${escapeHtml(r.note)}</p><div class="links">${r.reports.map(p=>link(p)).join(' · ')||'本批次无独立报告文件'}</div></article>`).join(''); }
setOptions(); $('search').addEventListener('input',apply); $('dateFilter').addEventListener('change',apply); $('familyFilter').addEventListener('change',apply); $('modeFilter').addEventListener('change',apply); $('statVideos').textContent=DATA.length; $('statRuns').textContent=RUNS.length; $('indexCount').textContent=String(DATA.length).padStart(3,'0'); renderRuns(); apply();
</script></body></html>'''


def write_outputs(items: list[dict[str, Any]], runs: list[dict[str, Any]], copied: list[Path]) -> None:
    payload = {"generatedAt": datetime.now().isoformat(timespec="seconds"), "scope": "2026-08-12 through 2026-08-18", "items": items, "runs": runs}
    (DEST / "catalog.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = f"""MiniMax H3 离线评测资料台

打开方式：双击 index.html。不要运行 npm、Next.js、Python 服务或 ComfyUI。

范围：2026-08-12 至 2026-08-18
视频：{len(items)} 个
批次：{len(runs)} 个
打包体积（runs）：{fmt_bytes(sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file()))}

目录：
- index.html：离线评测页，数据已内嵌，视频和证据均用相对路径
- catalog.json：机器可读索引
- runs/：原始 MP4、抽帧、工作流 JSON、日志、报告和拼接清单
- sources/：H3 生产交接手册、项目蓝图和原评测台源码
- posters/：页面自动生成的本地视频封面（若原目录没有中间帧）

复制：复制整个 h3-evaluation-offline 文件夹到笔记本，不要只复制 index.html。
当前生产选择：剪枝 INT8 FL2VA + INT4 ConvRot Qwen + drbaph 剪枝专用 raw-key Turbo LoRA + SageAttention 启动参数 + 8 steps；Director 负责编排，Motion Context 负责 AV latent 接力。Q4_K_M、剪枝 INT4、非剪枝 INT8 均保留为兼容/质量对照，不进入默认长链。
证据边界：媒体 ffprobe / 完整解码和抽帧文件均保留；每个样本是否通过视觉人工审阅，以相应报告中的文字为准。
"""
    (DEST / "README.txt").write_text(readme, encoding="utf-8")
    inventory = {"fileCount": len(copied), "videoCount": len(items), "runCount": len(runs), "bundleBytes": sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())}
    (DEST / "BUNDLE_MANIFEST.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    page = HTML_TEMPLATE.replace("__DATA__", js_data(items)).replace("__RUNS__", js_data(runs))
    (DEST / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    copied = safe_copy_tree()
    items, runs = build_catalog()
    write_outputs(items, runs, copied)
    print(json.dumps({"dest": str(DEST), "videos": len(items), "runs": len(runs), "copiedFiles": len(copied)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
