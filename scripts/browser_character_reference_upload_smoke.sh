#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器验收：注册 -> 创建项目 -> 导入小说 -> 改编通过 -> 准备结构
# -> 通过 Web 三次文件选择上传角色正/侧/背参考图。
# 不打印注册凭据或 API 响应体，只输出脱敏状态和证据目录。

WEB_URL="${STUDIO_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
API_URL="${STUDIO_SMOKE_API_URL:-http://127.0.0.1:8787}"
SESSION_NAME="${STUDIO_BROWSER_SESSION:-ai-manhua-character-reference-upload-smoke}"
EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-character-reference-upload-smoke}"
TEMP_REFERENCE_FIXTURE=""
TEMP_REFERENCE_DIR=""
REFERENCE_FIXTURE="${STUDIO_REFERENCE_FIXTURE:-}"

if [[ -z "${REFERENCE_FIXTURE}" ]]; then
  TEMP_REFERENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/studio-reference-upload.XXXXXX")"
  TEMP_REFERENCE_FIXTURE="${TEMP_REFERENCE_DIR}/reference.png"
  # The API intentionally validates the PNG signature before storing the file;
  # this minimal fixture keeps the smoke deterministic and avoids repository assets.
  printf '\x89PNG\r\n\x1a\n' > "${TEMP_REFERENCE_FIXTURE}"
  REFERENCE_FIXTURE="${TEMP_REFERENCE_FIXTURE}"
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
  if [[ -n "${TEMP_REFERENCE_FIXTURE}" ]]; then
    rm -f "${TEMP_REFERENCE_FIXTURE}"
  fi
  if [[ -n "${TEMP_REFERENCE_DIR}" ]]; then
    rmdir "${TEMP_REFERENCE_DIR}" 2>/dev/null || true
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
  const email = `character-reference-upload-${stamp}@example.test`;
  const password = `CharacterUpload-${stamp}!`;
  await page.locator("input[placeholder=\"邮箱\"]").fill(email);
  await page.locator("input[placeholder^=\"密码\"]").fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await page.getByText("新建项目", { exact: true }).waitFor();
  await page.locator("input[placeholder=\"邮箱\"]").fill("");
  await page.locator("input[placeholder^=\"密码\"]").fill("");
}' >/dev/null

pw_code 'async (page) => {
  await page.locator("input[placeholder^=\"项目标题\"]").fill("角色参考图上传验收");
  await page.locator("textarea[placeholder^=\"先写一段故事梗概\"]").fill("用于验证用户角色参考图上传的短篇故事。");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByText("角色参考图上传验收", { exact: true }).first().waitFor();
  await page.locator("textarea[placeholder^=\"粘贴 TXT\"]").fill("林默站在旧宅门前。\n她抬头看向走廊。");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "导入粘贴内容" }).click();
  await page.getByRole("button", { name: "运行改编预览" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".adaptation-row").length >= 2);
  await page.getByRole("button", { name: /批量通过/ }).first().click();
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".adaptation-row small")];
    return rows.length >= 2 && rows.every((item) => item.textContent?.trim() === "approved");
  });
  await page.getByRole("button", { name: "一键准备项目结构" }).first().click();
  await page.waitForFunction(() => document.querySelectorAll(".character-card").length >= 1, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/02-structure.yml"

pw_code 'async (page) => {
  const before = await page.evaluate(async (apiUrl) => (await (await fetch(`${apiUrl}/api/credits`, { credentials: "include" })).json()).balance, "'"${API_URL}"'");
  const inputs = page.locator(".reference-upload input");
  for (const [index, view] of ["front", "side", "back"].entries()) {
    await inputs.nth(index).setInputFiles("'"${REFERENCE_FIXTURE}"'");
    await page.waitForTimeout(500);
  }
  await page.waitForFunction(() => document.querySelectorAll(".reference-strip img").length >= 3, { timeout: 30000 });
  const after = await page.evaluate(async (apiUrl) => (await (await fetch(`${apiUrl}/api/credits`, { credentials: "include" })).json()).balance, "'"${API_URL}"'");
  const views = await page.locator(".reference-strip img").count();
  return JSON.stringify({ views, credit_unchanged: before === after });
}'
pw snapshot > "${EVIDENCE_DIR}/03-uploaded-three-views.yml"
pw screenshot --filename "${EVIDENCE_DIR}/03-uploaded-three-views.png" >/dev/null

echo "status=passed"
echo "browser=playwright"
echo "flow=register/create/import/rewrite/approve/structure/upload-front-side-back"
echo "evidence_dir=${EVIDENCE_DIR}"
