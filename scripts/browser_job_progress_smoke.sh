#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器验收：注册 -> 创建项目 -> 导入 TXT -> 同步建立改编基线
# -> 通过 ?queued=true 创建文本 Job -> 等待本地 worker 完成
# -> 检查服务端 progressbar -> reload 后再次检查进度恢复。
# 脚本只输出状态和证据路径，不打印凭据或 API 响应体。

WEB_URL="${STUDIO_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
SESSION_NAME="${STUDIO_BROWSER_SESSION:-ai-manhua-job-progress-smoke}"
EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-job-progress-smoke}"

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
  const email = `job-progress-smoke-${stamp}@example.test`;
  const password = `JobProgress-${stamp}!`;
  await page.locator("input[placeholder=\"邮箱\"]").fill(email);
  await page.locator("input[placeholder^=\"密码\"]").fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await page.getByText("新建项目", { exact: true }).waitFor();
  await page.locator("input[placeholder=\"邮箱\"]").fill("");
  await page.locator("input[placeholder^=\"密码\"]").fill("");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/02-registered.yml"

pw run-code 'async (page) => {
  await page.locator("input[placeholder^=\"项目标题\"]").fill("浏览器任务进度验收");
  await page.locator("textarea[placeholder^=\"先写一段故事梗概\"]").fill("用于任务进度浏览器验收的短篇故事。");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByText("浏览器任务进度验收", { exact: true }).first().waitFor();
}' >/dev/null

pw run-code 'async (page) => {
  await page.locator("textarea[placeholder^=\"粘贴 TXT\"]").fill("雨夜里，林默推开旧公寓的门。\n门后传来一声陌生的呼唤。");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "导入粘贴内容" }).click();
  await page.getByRole("button", { name: "运行改编预览" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "运行改编预览" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".adaptation-row").length >= 2);
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/03-adaptation-baseline.yml"

# 直接走公开的 queued API 合同，认证 cookie/CSRF 仍由真实浏览器上下文提供；
# 本地预览栈的持久化 worker 会处理该 queued Job。
pw run-code 'async (page) => {
  const result = await page.evaluate(async () => {
    const projects = await fetch("/api/projects", { credentials: "include" }).then((response) => response.json());
    const project = projects[0];
    const csrf = window.sessionStorage.getItem("studio_csrf") || "";
    const response = await fetch(`/api/projects/${project.id}/rewrite?queued=true`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": `browser-job-progress:${project.id}:${Date.now()}`,
        "X-CSRF-Token": csrf,
      },
      body: JSON.stringify({ mode: "condensed" }),
    });
    const payload = await response.json();
    if (!response.ok || !payload?.job?.id) {
      throw new Error(`queued rewrite failed: ${response.status}`);
    }
    window.sessionStorage.setItem("browser_job_progress_id", payload.job.id);
    return { status: payload.job.status };
  });
  if (!result?.status) throw new Error("queued job was not created");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/04-queued-requested.yml"

pw run-code 'async (page) => {
  await page.waitForFunction(() => {
    const jobId = window.sessionStorage.getItem("browser_job_progress_id");
    const row = jobId ? document.querySelector(`[data-job-id="${jobId}"]`) : null;
    const progress = row?.querySelector("[role=\"progressbar\"]");
    return Boolean(
      row &&
      row.textContent?.includes("completed") &&
      progress?.getAttribute("aria-valuenow") === "100" &&
      row.textContent?.includes("已完成"),
    );
  }, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/05-completed-progress.yml"
pw screenshot --filename "${EVIDENCE_DIR}/05-completed-progress.png" --full-page >/dev/null

pw reload >/dev/null
pw run-code 'async (page) => {
  await page.waitForFunction(() => {
    const jobId = window.sessionStorage.getItem("browser_job_progress_id");
    const row = jobId ? document.querySelector(`[data-job-id="${jobId}"]`) : null;
    const progress = row?.querySelector("[role=\"progressbar\"]");
    return Boolean(
      row &&
      row.textContent?.includes("completed") &&
      progress?.getAttribute("aria-valuenow") === "100" &&
      row.textContent?.includes("已完成"),
    );
  }, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/06-reloaded-progress.yml"
pw screenshot --filename "${EVIDENCE_DIR}/06-reloaded-progress.png" --full-page >/dev/null

echo "status=passed"
echo "browser=playwright"
echo "flow=register/create/import/sync-rewrite/queued-job/completed-progress/reload"
echo "evidence_dir=${EVIDENCE_DIR}"
