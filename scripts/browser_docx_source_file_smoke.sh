#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器 DOCX 文件导入验收；DOCX fixture 只写入临时目录。
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/studio-docx-source.XXXXXX")"
SOURCE_FIXTURE="${TEMP_SOURCE_DIR}/chapter.docx"

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
xml = (
    '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>浏览器 DOCX 导入正文</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>第二段用于改编审校。</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>第三段用于批量通过。</w:t></w:r></w:p></w:body></w:document>"
)
with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("word/document.xml", xml.encode("utf-8"))
PY

cd "${PROJECT_ROOT}"
export STUDIO_SOURCE_FIXTURE="${SOURCE_FIXTURE}"
export STUDIO_SOURCE_FILENAME="chapter.docx"
export STUDIO_EXPECTED_MEDIA_TYPE="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
export STUDIO_BROWSER_SESSION="${STUDIO_BROWSER_SESSION:-ai-manhua-browser-docx-source-file-smoke}"
export STUDIO_BROWSER_EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-docx-source-file-smoke}"
exec "${PROJECT_ROOT}/scripts/browser_source_file_import_smoke.sh"
