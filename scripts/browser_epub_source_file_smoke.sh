#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器 EPUB 文件导入验收；EPUB fixture 只写入临时目录。
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/studio-epub-source.XXXXXX")"
SOURCE_FIXTURE="${TEMP_SOURCE_DIR}/chapter.epub"

cleanup() {
  rm -f "${SOURCE_FIXTURE}"
  rmdir "${TEMP_SOURCE_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

PYTHON_BIN="${STUDIO_PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
"${PYTHON_BIN}" - "${SOURCE_FIXTURE}" <<'PY'
import sys
import zipfile

path = sys.argv[1]
with zipfile.ZipFile(path, "w") as archive:
    archive.writestr(
        "META-INF/container.xml",
        '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles></container>',
    )
    archive.writestr(
        "OEBPS/package.opf",
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><manifest>'
        '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="chapter"/></spine></package>',
    )
    archive.writestr(
        "OEBPS/chapter.xhtml",
        "<html><body><p>EPUB 第一段。</p><p>EPUB 第二段。</p><p>EPUB 第三段。</p></body></html>",
    )
PY

cd "${PROJECT_ROOT}"
export STUDIO_SOURCE_FIXTURE="${SOURCE_FIXTURE}"
export STUDIO_SOURCE_FILENAME="chapter.epub"
export STUDIO_EXPECTED_MEDIA_TYPE="application/epub+zip"
export STUDIO_BROWSER_SESSION="${STUDIO_BROWSER_SESSION:-ai-manhua-browser-epub-source-file-smoke}"
export STUDIO_BROWSER_EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-epub-source-file-smoke}"
exec "${PROJECT_ROOT}/scripts/browser_source_file_import_smoke.sh"
