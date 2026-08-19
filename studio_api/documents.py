"""来源文件文本抽取，不把格式解析逻辑塞进 API 路由。"""

from __future__ import annotations

import io
import os
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree


class DocumentExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    media_type: str


_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
}


def media_type_for_filename(filename: str) -> str:
    return _MEDIA_TYPES.get(PurePosixPath(filename or "source.txt").suffix.lower(), "text/plain")


def _max_extracted_chars() -> int:
    try:
        configured = int(os.environ.get("STUDIO_MAX_EXTRACTED_SOURCE_CHARS", "8000000"))
    except ValueError:
        configured = 8_000_000
    return max(1_000, min(configured, 32_000_000))


def _bounded_text(text: str) -> str:
    value = ensure_source_text(text).strip()
    if not value:
        raise DocumentExtractionError("source document contains no readable text")
    return value


def ensure_source_text(text: str) -> str:
    value = str(text or "")
    if "\x00" in value:
        raise DocumentExtractionError("source document contains a NUL character")
    if len(value) > _max_extracted_chars():
        raise DocumentExtractionError("extracted source is too large")
    return value


def _zip_member_name(name: str) -> str:
    """Return a safe POSIX member name; format parsers never follow host paths."""

    normalized = str(name or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("/") or ".." in path.parts or "\x00" in normalized:
        raise DocumentExtractionError("document archive contains an unsafe member path")
    return str(path)


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    total_uncompressed = 0
    maximum = _max_extracted_chars() * 4
    for info in archive.infolist():
        name = _zip_member_name(info.filename)
        if not name or name == "." or info.is_dir():
            continue
        mode = (int(info.external_attr) >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise DocumentExtractionError("document archive contains a symbolic link")
        if name in members:
            raise DocumentExtractionError("document archive contains duplicate members")
        total_uncompressed += max(0, int(info.file_size))
        if total_uncompressed > maximum:
            raise DocumentExtractionError("document archive expands beyond the import limit")
        members[name] = info
    return members


def _read_zip_member(archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], name: str) -> bytes:
    normalized = _zip_member_name(name)
    info = members.get(normalized)
    if info is None:
        raise KeyError(normalized)
    return archive.read(info)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1


def extract_text(filename: str, payload: bytes) -> str:
    return extract_document(filename, payload).text


def extract_document(filename: str, payload: bytes) -> ExtractedDocument:
    suffix = PurePosixPath(filename or "source.txt").suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".text"}:
        text = _decode(payload)
    elif suffix == ".docx":
        text = _extract_docx(payload)
    elif suffix == ".epub":
        text = _extract_epub(payload)
    elif suffix == ".pdf":
        text = _extract_pdf(payload)
    else:
        raise DocumentExtractionError(f"unsupported source format: {suffix or 'unknown'}")
    return ExtractedDocument(_bounded_text(text), media_type_for_filename(filename))


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentExtractionError("source text encoding is not supported")


def _extract_docx(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = _zip_members(archive)
            xml = _read_zip_member(archive, members, "word/document.xml")
        root = ElementTree.fromstring(xml)
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, DocumentExtractionError) as exc:
        raise DocumentExtractionError("invalid DOCX document") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n".join(paragraphs)


def _extract_epub(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = _zip_members(archive)
            container = ElementTree.fromstring(_read_zip_member(archive, members, "META-INF/container.xml"))
            rootfile = next(
                (node.attrib.get("full-path") for node in container.iter() if node.tag.rsplit("}", 1)[-1] == "rootfile"),
                None,
            )
            if not rootfile:
                raise DocumentExtractionError("EPUB container has no package document")
            opf_name = _zip_member_name(rootfile)
            opf = ElementTree.fromstring(_read_zip_member(archive, members, opf_name))
            manifest: dict[str, str] = {}
            for item in opf.iter():
                if item.tag.rsplit("}", 1)[-1] != "item":
                    continue
                item_id = item.attrib.get("id")
                href = item.attrib.get("href")
                media_type = item.attrib.get("media-type", "")
                if item_id and href and (media_type.startswith("application/xhtml") or media_type in {"text/html", "application/xml"}):
                    href = unquote(href.split("#", 1)[0].split("?", 1)[0])
                    manifest[item_id] = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), href))
            spine_names: list[str] = []
            for itemref in opf.iter():
                if itemref.tag.rsplit("}", 1)[-1] != "itemref":
                    continue
                href = manifest.get(itemref.attrib.get("idref", ""))
                if href and href in members:
                    spine_names.append(href)
            if not spine_names:
                spine_names = [
                    name
                    for name in members
                    if PurePosixPath(name).suffix.lower() in {".xhtml", ".html", ".htm"}
                ]
            texts = []
            for name in spine_names:
                parser = _TextParser()
                parser.feed(_decode(_read_zip_member(archive, members, name)))
                if parser.parts:
                    texts.append(" ".join(parser.parts))
            return "\n\n".join(texts)
    except (KeyError, ElementTree.ParseError, zipfile.BadZipFile, DocumentExtractionError) as exc:
        raise DocumentExtractionError("invalid EPUB document") from exc


def _extract_pdf(payload: bytes) -> str:
    reader = None
    try:
        from pypdf import PdfReader
        reader = PdfReader
    except ImportError:
        pass
    if reader is not None:
        try:
            pages: list[str] = []
            for page in reader(io.BytesIO(payload)).pages:
                pages.append(page.extract_text() or "")
                if len("\n\n".join(pages)) > _max_extracted_chars():
                    raise DocumentExtractionError("extracted source is too large")
            return "\n\n".join(pages).strip()
        except Exception as exc:  # pypdf raises format-specific exceptions
            if isinstance(exc, DocumentExtractionError):
                raise
            raise DocumentExtractionError("invalid or unreadable PDF document") from exc
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise DocumentExtractionError("PDF import requires optional pypdf or the system pdftotext command")
    with tempfile.TemporaryDirectory(prefix="studio-pdf-") as directory:
        source = f"{directory}/source.pdf"
        output = f"{directory}/source.txt"
        with open(source, "wb") as file:
            file.write(payload)
        try:
            subprocess.run([pdftotext, "-layout", source, output], check=True, capture_output=True)
            with open(output, encoding="utf-8", errors="replace") as file:
                text = file.read().strip()
            return _bounded_text(text)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DocumentExtractionError("invalid or unreadable PDF document") from exc
