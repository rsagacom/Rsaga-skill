#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器验收：注册 -> 创建项目 -> 选择来源文件 -> multipart 解析
# -> 来源媒体类型/文本回读 -> 改编单元出现 -> 批量通过。
# 临时文件和临时账户只存在于本次浏览器会话，不写入快照或报告。

WEB_URL="${STUDIO_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
API_URL="${STUDIO_SMOKE_API_URL:-http://127.0.0.1:8787}"
SESSION_NAME="${STUDIO_BROWSER_SESSION:-ai-manhua-browser-source-file-import-smoke}"
EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-source-file-import-smoke}"
TEMP_SOURCE_DIR=""
SOURCE_FIXTURE="${STUDIO_SOURCE_FIXTURE:-}"
SOURCE_FILENAME="${STUDIO_SOURCE_FILENAME:-}"
EXPECTED_MEDIA_TYPE="${STUDIO_EXPECTED_MEDIA_TYPE:-}"

if [[ -z "${SOURCE_FIXTURE}" ]]; then
  TEMP_SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/studio-source-file.XXXXXX")"
  SOURCE_FIXTURE="${TEMP_SOURCE_DIR}/chapter.txt"
  printf '%s\n%s\n%s\n' \
    '雨夜里，林默推开旧公寓的门。' \
    '门后传来一声陌生的呼唤。' \
    '她握紧银色怀表，回头看向走廊。' > "${SOURCE_FIXTURE}"
fi

if [[ -z "${SOURCE_FILENAME}" ]]; then
  SOURCE_FILENAME="$(basename "${SOURCE_FIXTURE}")"
fi
if [[ -z "${EXPECTED_MEDIA_TYPE}" ]]; then
  case "${SOURCE_FILENAME##*.}" in
    txt|text) EXPECTED_MEDIA_TYPE="text/plain" ;;
    md|markdown) EXPECTED_MEDIA_TYPE="text/markdown" ;;
    docx) EXPECTED_MEDIA_TYPE="application/vnd.openxmlformats-officedocument.wordprocessingml.document" ;;
    epub) EXPECTED_MEDIA_TYPE="application/epub+zip" ;;
    pdf) EXPECTED_MEDIA_TYPE="application/pdf" ;;
    *) echo "unsupported smoke fixture extension: ${SOURCE_FILENAME}" >&2; exit 2 ;;
  esac
fi

if [[ -n "${PLAYWRIGHT_CLI:-}" ]]; then
  read -r -a PLAYWRIGHT_COMMAND <<< "${PLAYWRIGHT_CLI}"
elif [[ -x "/Users/rsaga/.codex/skills/playwright/scripts/playwright_cli.sh" ]]; then
  PLAYWRIGHT_COMMAND=(/Users/rsaga/.codex/skills/playwright/scripts/playwright_cli.sh)
else
  PLAYWRIGHT_COMMAND=(npx --yes --package=@playwright/cli -- playwright-cli)
fi

mkdir -p "${EVIDENCE_DIR}"

pw() {
  "${PLAYWRIGHT_COMMAND[@]}" --session "${SESSION_NAME}" "$@"
}

pw_code() {
  local output
  if ! output=$(pw run-code "$@"); then
    printf '%s\n' "$output" >&2
    return 1
  fi
  if [[ "$output" == *"### Error"* || "$output" == *"Error:"* ]]; then
    printf '%s\n' "$output" >&2
    return 1
  fi
  printf '%s\n' "$output"
}

cleanup() {
  pw close >/dev/null 2>&1 || true
  if [[ -n "${TEMP_SOURCE_DIR}" ]]; then
    rm -f "${SOURCE_FIXTURE}"
    rmdir "${TEMP_SOURCE_DIR}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

pw open "${WEB_URL}" >/dev/null
pw_code 'async (page) => {
  const logout = page.getByRole("button", { name: "退出账户" });
  if (await logout.count()) {
    await logout.click();
    await page.locator("input[placeholder=\"邮箱\"]").waitFor();
  }
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/01-initial.yml"

pw_code 'async (page) => {
  const stamp = Date.now();
  const email = `browser-source-file-${stamp}@example.test`;
  const password = `SourceFile-${stamp}!`;
  await page.locator("input[placeholder=\"邮箱\"]").fill(email);
  await page.locator("input[placeholder^=\"密码\"]").fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await page.getByText("新建项目", { exact: true }).waitFor();
  await page.locator("input[placeholder=\"邮箱\"]").fill("");
  await page.locator("input[placeholder^=\"密码\"]").fill("");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/02-registered.yml"

pw_code 'async (page) => {
  await page.locator("input[placeholder^=\"项目标题\"]").fill("浏览器文件导入验收");
  await page.locator("textarea[placeholder^=\"先写一段故事梗概\"]").fill("用于文件导入验收的短篇故事。");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByText("浏览器文件导入验收", { exact: true }).first().waitFor();
  await page.locator(".file-field input[type=file]").waitFor();
}' >/dev/null

pw_code 'async (page) => {
  await page.locator(".file-field input[type=file]").setInputFiles(`'"${SOURCE_FIXTURE}"'`);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "上传并解析文件" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".adaptation-row").length >= 3, { timeout: 30000 });
  const audit = await page.evaluate(async (apiUrl) => {
    const projects = await fetch(`${apiUrl}/api/projects`, { credentials: "include" }).then((response) => response.json());
    const project = projects.find((item) => item.title === "浏览器文件导入验收");
    if (!project) throw new Error("source-file project not found");
    const detail = await fetch(`${apiUrl}/api/projects/${project.id}`, { credentials: "include" }).then((response) => response.json());
    const source = detail.source_documents?.[0];
    const units = await fetch(`${apiUrl}/api/projects/${project.id}/adaptation-units`, { credentials: "include" }).then((response) => response.json());
    return { filename: source?.filename, media_type: source?.media_type, units: units.length };
  }, `'"${API_URL}"'`);
  if (audit.filename !== `'"${SOURCE_FILENAME}"'` || audit.media_type !== `'"${EXPECTED_MEDIA_TYPE}"'` || audit.units < 3) {
    throw new Error(`source-file audit failed: ${JSON.stringify(audit)}`);
  }
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/03-file-imported.yml"

pw_code 'async (page) => {
  await page.getByRole("button", { name: /批量通过/ }).first().click();
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".adaptation-row small")];
    return rows.length >= 3 && rows.every((item) => item.textContent?.trim() === "approved");
  });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/04-adaptation-approved.yml"
pw screenshot --filename "${EVIDENCE_DIR}/04-adaptation-approved.png" --full-page >/dev/null

echo "status=passed"
echo "browser=playwright"
echo "flow=register/create/select-${SOURCE_FILENAME##*.}/multipart-extract/media-type/adaptation/bulk-approve"
echo "evidence_dir=${EVIDENCE_DIR}"
