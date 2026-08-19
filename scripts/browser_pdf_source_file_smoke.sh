#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器 PDF 文件导入验收；PDF fixture 只写入临时目录。
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/studio-pdf-source.XXXXXX")"
SOURCE_FIXTURE="${TEMP_SOURCE_DIR}/chapter.pdf"

cleanup() {
  rm -f "${SOURCE_FIXTURE}"
  rmdir "${TEMP_SOURCE_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

PYTHON_BIN="${STUDIO_PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
"${PYTHON_BIN}" - "${SOURCE_FIXTURE}" <<'PY'
import sys

path = sys.argv[1]
stream = (
    b"BT /F1 18 Tf 20 160 Td (PDF first;) Tj "
    b"0 -24 Td (PDF second;) Tj 0 -24 Td (PDF third;) Tj ET\n"
)
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 220] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
document = bytearray(b"%PDF-1.4\n")
offsets = [0]
for number, body in enumerate(objects, 1):
    offsets.append(len(document))
    document.extend(f"{number} 0 obj\n".encode("ascii"))
    document.extend(body)
    document.extend(b"\nendobj\n")
xref = len(document)
document.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
for offset in offsets[1:]:
    document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
document.extend(
    f"trailer\n<< /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
)
with open(path, "wb") as file:
    file.write(document)
PY

cd "${PROJECT_ROOT}"
export STUDIO_SOURCE_FIXTURE="${SOURCE_FIXTURE}"
export STUDIO_SOURCE_FILENAME="chapter.pdf"
export STUDIO_EXPECTED_MEDIA_TYPE="application/pdf"
export STUDIO_BROWSER_SESSION="${STUDIO_BROWSER_SESSION:-ai-manhua-browser-pdf-source-file-smoke}"
export STUDIO_BROWSER_EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-pdf-source-file-smoke}"
exec "${PROJECT_ROOT}/scripts/browser_source_file_import_smoke.sh"
