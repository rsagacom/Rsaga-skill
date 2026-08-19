"""小说/剧本来源导入与可追溯改编单元。

这里不假装完成 AI 改写：默认模式是保真拆分，改写由上层 provider 通过
transform 回调显式注入。这样每个输出单元始终能回指原文字符区间。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import AdaptationMode, AdaptationUnit, SourceDocument, SourceSegment


CHAPTER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>(?:第\s*\d+\s*[章节回]|Chapter\s+\d+\b).*)$",
    re.IGNORECASE | re.MULTILINE,
)
SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class AdaptationBundle:
    document: SourceDocument
    chapters: tuple[Chapter, ...]
    segments: tuple[SourceSegment, ...]
    units: tuple[AdaptationUnit, ...]


def load_source_document(path: str | Path, document_id: str = "source-1") -> SourceDocument:
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8")
    return SourceDocument(
        id=document_id,
        filename=source_path.name,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        media_type="text/markdown" if source_path.suffix.lower() in {".md", ".markdown"} else "text/plain",
    )


def split_chapters(text: str) -> list[Chapter]:
    matches = list(CHAPTER_RE.finditer(text))
    if not matches:
        return [Chapter(1, "第1章", text, 0, len(text))]

    chapters: list[Chapter] = []
    first_body_start = matches[0].start()
    preface = text[:first_body_start]
    if preface.strip():
        chapters.append(Chapter(0, "序章", preface, 0, first_body_start))

    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body_start = match.end()
        title = match.group("title").strip().lstrip("# ").strip()
        number_match = re.search(r"(?:第\s*)?(\d+)", title)
        number = int(number_match.group(1)) if number_match else index + 1
        chapters.append(Chapter(number, title, text[body_start:next_start], body_start, next_start))
    return chapters


def _trimmed_match(text: str, match: re.Match[str]) -> tuple[str, int, int]:
    raw = match.group(0)
    left_trimmed = len(raw) - len(raw.lstrip())
    right_trimmed = len(raw.rstrip())
    start = match.start() + left_trimmed
    end = match.start() + right_trimmed
    value = text[start:end]
    value = re.sub(r"^#{1,6}\s+", "", value)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL).strip()
    return value, start, end


def adapt_source(
    document: SourceDocument,
    mode: AdaptationMode = AdaptationMode.FAITHFUL,
    transform: Callable[[str, SourceSegment], str] | None = None,
) -> AdaptationBundle:
    chapters = split_chapters(document.text)
    segments: list[SourceSegment] = []
    units: list[AdaptationUnit] = []

    for chapter in chapters:
        local_sequence = 0
        chapter_text = chapter.text
        for match in SENTENCE_RE.finditer(chapter_text):
            sentence, local_start, local_end = _trimmed_match(chapter_text, match)
            if not sentence:
                continue
            start = chapter.start_offset + local_start
            end = chapter.start_offset + local_end
            local_sequence += 1
            segment_id = f"seg-{document.id}-{chapter.number:02d}-{local_sequence:03d}"
            unit_id = f"adapt-{document.id}-{chapter.number:02d}-{local_sequence:03d}"
            segment = SourceSegment(
                id=segment_id,
                source_document_id=document.id,
                chapter_no=chapter.number,
                sequence=local_sequence,
                text=sentence,
                start_offset=start,
                end_offset=end,
                line_start=document.text.count("\n", 0, start) + 1,
                line_end=document.text.count("\n", 0, max(start, end - 1)) + 1,
            )
            adapted_text = transform(sentence, segment) if transform else sentence
            segments.append(segment)
            units.append(
                AdaptationUnit(
                    id=unit_id,
                    source_segment_id=segment_id,
                    chapter_no=chapter.number,
                    sequence=local_sequence,
                    source_text=sentence,
                    adapted_text=adapted_text,
                    mode=mode,
                    traceability={
                        "source_document_id": document.id,
                        "start_offset": start,
                        "end_offset": end,
                        "content_sha256": document.content_sha256,
                    },
                )
            )
    return AdaptationBundle(document, tuple(chapters), tuple(segments), tuple(units))
