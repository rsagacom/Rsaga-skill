#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器验收：注册 -> 创建项目 -> 导入 TXT -> 改编预览 -> 单条驳回
# -> reload 保持状态 -> 批量通过。脚本只输出 UI 状态，不打印凭据或网络响应体。

WEB_URL="${STUDIO_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
SESSION_NAME="${STUDIO_BROWSER_SESSION:-ai-manhua-browser-smoke}"
EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-adaptation-smoke}"

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

cleanup() {
  pw close >/dev/null 2>&1 || true
}
trap cleanup EXIT

pw open "${WEB_URL}" >/dev/null
pw snapshot > "${EVIDENCE_DIR}/01-initial.yml"

# 使用页面内临时邮箱和密码，避免把凭据写入 shell 输出、仓库或证据文件。
pw run-code 'async (page) => {
  const stamp = Date.now();
  const email = `browser-smoke-${stamp}@example.test`;
  const password = `Browser-${stamp}!`;
  await page.locator("input[placeholder=\"邮箱\"]").fill(email);
  await page.locator("input[placeholder^=\"密码\"]").fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await page.getByText("新建项目", { exact: true }).waitFor();
  await page.locator("input[placeholder=\"邮箱\"]").fill("");
  await page.locator("input[placeholder^=\"密码\"]").fill("");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/02-registered.yml"

pw run-code 'async (page) => {
  await page.locator("input[placeholder^=\"项目标题\"]").fill("浏览器改编验收");
  await page.locator("textarea[placeholder^=\"先写一段故事梗概\"]").fill("用于浏览器验收的短篇故事。");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByText("浏览器改编验收", { exact: true }).first().waitFor();
}' >/dev/null

pw run-code 'async (page) => {
  await page.locator("textarea[placeholder^=\"粘贴 TXT\"]").fill("雨夜里，林默推开旧公寓的门。\n门后传来一声陌生的呼唤。");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "导入粘贴内容" }).click();
  await page.getByRole("button", { name: "运行改编预览" }).waitFor({ state: "visible" });
}' >/dev/null

pw run-code 'async (page) => {
  await page.getByRole("button", { name: "运行改编预览" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".adaptation-row").length >= 2);
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/03-adaptation-preview.yml"

pw run-code 'async (page) => {
  const row = page.locator(".adaptation-row").first();
  await row.getByRole("button", { name: "驳回" }).click();
  await page.waitForFunction(() => document.querySelector(".adaptation-row small")?.textContent?.trim() === "rejected");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/04-rejected.yml"

pw reload >/dev/null
pw run-code 'async (page) => {
  await page.waitForFunction(() => document.querySelector(".adaptation-row small")?.textContent?.trim() === "rejected");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/05-reloaded-rejected.yml"

pw run-code 'async (page) => {
  const button = page.getByRole("button", { name: /批量通过/ });
  await button.click();
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".adaptation-row small")];
    return rows.length >= 2 && rows.every((item) => item.textContent?.trim() === "approved");
  });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/06-bulk-approved.yml"
pw screenshot --filename "${EVIDENCE_DIR}/06-bulk-approved.png" --full-page >/dev/null

echo "status=passed"
echo "browser=playwright"
echo "flow=register/create/import/rewrite/reject/reload/bulk-approve"
echo "evidence_dir=${EVIDENCE_DIR}"
