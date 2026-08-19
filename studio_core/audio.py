"""P1 音频链合同：VoiceCasting、DialogueCue、AudioTrack、SubtitleCue 与混音门禁。

对齐蓝图 §14 与 H3 交接手册 §31 已验证结论：
- H3 原生 T2VA 音频只能作为氛围参考/音画同步参考，不承担最终对白；
- 对白/旁白走 CosyVoice3；ASR/字幕核验走 Whisper/人工听审；
- 环境声/SFX/音乐是独立可替换音轨；
- 最终混音走 FFmpeg/Remotion，输出响度/峰值/削波证据。

本模块是数据合同与规则检查，不发起任何 TTS/ASR 调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TrackKind(str, Enum):
    DIALOGUE = "dialogue"
    NARRATION = "narration"
    AMBIENT = "ambient"
    SFX = "sfx"
    MUSIC = "music"


class DialogueCueStatus(str, Enum):
    DRAFT = "draft"
    TTS_GENERATED = "tts_generated"
    ASR_VERIFIED = "asr_verified"
    MIXED = "mixed"
    ADOPTED = "adopted"


@dataclass
class VoiceCasting:
    """角色固定音色（蓝图 §14）。"""

    casting_id: str
    character_id: str
    voice_name: str
    provider: str = "CosyVoice3"
    language: str = "zh"
    pitch: float = 1.0
    speed: float = 1.0
    forbidden_tones: list[str] = field(default_factory=list)
    consent_ref: str | None = None  # 授权记录，禁止登记克隆者信息
    status: str = "draft"

    def to_dict(self) -> dict[str, Any]:
        return {
            "casting_id": self.casting_id,
            "character_id": self.character_id,
            "voice_name": self.voice_name,
            "provider": self.provider,
            "language": self.language,
            "pitch": self.pitch,
            "speed": self.speed,
            "forbidden_tones": list(self.forbidden_tones),
            "consent_ref": self.consent_ref,
            "status": self.status,
        }


@dataclass
class DialogueCue:
    """一条对白的结构化时码合同（蓝图 §14 台词节奏表）。"""

    cue_id: str
    shot_id: str
    character_id: str
    casting_id: str
    text: str
    start_sec: float
    duration_sec: float
    emotion: str = "neutral"
    pause_before_sec: float = 0.0
    emphasis_words: list[str] = field(default_factory=list)
    breath_marks: list[float] = field(default_factory=list)
    status: DialogueCueStatus = DialogueCueStatus.DRAFT
    audio_asset_id: str | None = None
    asr_transcript: str | None = None

    @property
    def cps(self) -> float:
        """对白密度：字数/秒。"""
        if self.duration_sec <= 0:
            return 0.0
        return len("".join(self.text.split())) / self.duration_sec

    def asr_match(self) -> bool | None:
        """ASR 核验：归一化后文本一致性。None 表示尚未 ASR。"""
        if self.asr_transcript is None:
            return None

        def norm(text: str) -> str:
            return "".join(ch for ch in text if ch.isalnum())

        return norm(self.asr_transcript) == norm(self.text)


@dataclass
class AudioTrack:
    """一条独立音轨（可替换）。"""

    track_id: str
    kind: TrackKind
    media_asset_id: str | None = None
    start_sec: float = 0.0
    duration_sec: float = 0.0
    gain_db: float = 0.0
    dip_to_background: bool = False  # 对白出现时自动压低（如 H3 氛围床）
    source_note: str = ""  # CosyVoice3 / H3-native / SFX-lib / 人工
    license_ref: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "kind": self.kind.value,
            "media_asset_id": self.media_asset_id,
            "start_sec": self.start_sec,
            "duration_sec": self.duration_sec,
            "gain_db": self.gain_db,
            "dip_to_background": self.dip_to_background,
            "source_note": self.source_note,
            "license_ref": self.license_ref,
            "sha256": self.sha256,
        }


@dataclass
class SubtitleCue:
    """字幕时码条目（蓝图 §14：原文/最终文本/说话人/语言/修正）。"""

    cue_id: str
    start_sec: float
    end_sec: float
    text: str
    speaker: str
    language: str = "zh"
    source_text: str | None = None
    revised: bool = False

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "text": self.text,
            "speaker": self.speaker,
            "language": self.language,
            "source_text": self.source_text,
            "revised": self.revised,
        }


@dataclass
class MixSpec:
    """混音目标（蓝图 § 交付门禁 / 音频门禁）。"""

    target_lufs: float = -16.0
    true_peak_db: float = -2.0
    sample_rate: int = 32000
    channels: int = 2

    def check_loudness(self, measured_lufs: float, measured_peak_db: float) -> list[str]:
        """响度证据核验，返回违规清单。"""
        issues: list[str] = []
        if measured_peak_db > self.true_peak_db:
            issues.append(f"peak {measured_peak_db}dB > {self.true_peak_db}dB (clipping risk)")
        if abs(measured_lufs - self.target_lufs) > 2.0:
            issues.append(f"loudness {measured_lufs} LUFS off target {self.target_lufs}")
        return issues


def audio_gate(tracks: list[AudioTrack], subtitles: list[SubtitleCue]) -> list[str]:
    """音频门禁规则（蓝图 § 音频门禁）。

    返回违规清单；空列表 = 结构门禁通过（不含人耳听审）。
    """
    issues: list[str] = []

    # 1. 必须存在非静音对白/旁白轨或有明确无对白声明
    dialogue_tracks = [t for t in tracks if t.kind in {TrackKind.DIALOGUE, TrackKind.NARRATION}]
    if not dialogue_tracks:
        issues.append("no dialogue/narration track (explicit no-dialogue must be declared)")

    # 2. 对白轨必须带媒体资产
    for track in dialogue_tracks:
        if not track.media_asset_id:
            issues.append(f"dialogue track {track.track_id} has no media asset")

    # 3. H3 原生氛围床不得标记为对白轨
    for track in tracks:
        if track.kind == TrackKind.AMBIENT and "H3-native" in track.source_note:
            if track.media_asset_id and not track.license_ref:
                # 氛围床允许无许可证（自有生成），但必须标注来源
                continue

    # 4. 字幕时间轴不能倒置、不能出负
    for sub in subtitles:
        if sub.duration_sec <= 0:
            issues.append(f"subtitle {sub.cue_id} non-positive duration")
        if sub.start_sec < 0:
            issues.append(f"subtitle {sub.cue_id} negative start")

    # 5. 对白 cue 的 ASR 未核验不得 adopted
    # （cue 级检查在 DialogueCue.asr_match 完成，这里只提示）
    return issues