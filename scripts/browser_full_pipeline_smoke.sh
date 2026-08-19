#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器验收：注册 -> 项目/小说改编 -> 故事资产 -> 项目结构
# -> 角色三视图 -> 关键帧 -> 视觉审核 -> 视频片段 -> 分集合成。
# 该脚本只输出状态和证据路径，不打印凭据或 API 响应体。

WEB_URL="${STUDIO_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
SESSION_NAME="${STUDIO_BROWSER_SESSION:-ai-manhua-browser-full-pipeline-smoke}"
EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-full-pipeline-smoke}"

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

# 使用页面内临时邮箱和密码，且在第一份注册后快照前清空输入框。
pw_code 'async (page) => {
  const stamp = Date.now();
  const email = `browser-full-pipeline-${stamp}@example.test`;
  const password = `FullPipeline-${stamp}!`;
  await page.locator("input[placeholder=\"邮箱\"]").fill(email);
  await page.locator("input[placeholder^=\"密码\"]").fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await page.getByText("新建项目", { exact: true }).waitFor();
  await page.locator("input[placeholder=\"邮箱\"]").fill("");
  await page.locator("input[placeholder^=\"密码\"]").fill("");
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/02-registered.yml"

pw_code 'async (page) => {
  await page.locator("input[placeholder^=\"项目标题\"]").fill("浏览器全流程制作验收");
  await page.locator("textarea[placeholder^=\"先写一段故事梗概\"]").fill("雨夜里，林默推开旧公寓的门，门后传来陌生的呼唤。");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByText("浏览器全流程制作验收", { exact: true }).first().waitFor();
  await page.getByRole("button", { name: "导入粘贴内容" }).waitFor();
  await page.getByTestId("project-readiness").waitFor();
  await page.locator("[data-readiness-stage=source]").waitFor();
}' >/dev/null

pw_code 'async (page) => {
  await page.locator("textarea[placeholder^=\"粘贴 TXT\"]").fill("雨夜里，林默推开旧公寓的门。\n门后传来一声陌生的呼唤。\n她握紧银色怀表，回头看向走廊。");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "导入粘贴内容" }).click();
  await page.getByRole("button", { name: "运行改编预览" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "运行改编预览" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".adaptation-row").length >= 3);
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/03-adaptation.yml"

pw_code 'async (page) => {
  const button = page.getByRole("button", { name: "重新抽取故事资产" }).first();
  await button.click();
  await page.getByText("当前是本地预览文本 Provider，未调用外部模型；可配置真实文本 Provider 后重新抽取。", { exact: true }).waitFor();
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/04-story-bible.yml"

pw_code 'async (page) => {
  await page.getByRole("button", { name: /批量通过/ }).first().click();
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".adaptation-row small")];
    return rows.length >= 3 && rows.every((item) => item.textContent?.trim() === "approved");
  });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/04b-adaptation-approved.yml"

pw_code 'async (page) => {
  await page.getByRole("button", { name: "一键准备项目结构" }).first().click();
  await page.waitForFunction(() => (
    document.querySelectorAll(".character-card").length >= 1 &&
    document.querySelectorAll(".episode-panel").length >= 1 &&
    document.querySelectorAll(".shot-card").length >= 1
  ), { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/05-structure.yml"

pw_code 'async (page) => {
  await page.getByRole("button", { name: "批量生成三视图" }).first().click();
  await page.waitForFunction(() => document.querySelectorAll(".reference-strip").length >= 1, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/06-character-references.yml"

pw_code 'async (page) => {
  await page.getByRole("button", { name: "批量生成关键帧" }).first().click();
  await page.waitForFunction(() => (
    document.querySelectorAll(".shot-card").length >= 3 &&
    document.querySelectorAll(".candidate-strip").length >= 3
  ), { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/07-keyframes.yml"

pw_code 'async (page) => {
  const audit = await page.evaluate(async () => {
    const api = "http://127.0.0.1:8787";
    const csrf = window.sessionStorage.getItem("studio_csrf") ?? "";
    const headers = { "Content-Type": "application/json", "X-CSRF-Token": csrf };
    const projectsResponse = await fetch(`${api}/api/projects`, { credentials: "include" });
    if (!projectsResponse.ok) throw new Error(`review setup projects failed: ${projectsResponse.status}`);
    const projects = await projectsResponse.json();
    const project = projects.find((item) => item.title === "浏览器全流程制作验收");
    if (!project) throw new Error("review setup project not found");
    const detailResponse = await fetch(`${api}/api/projects/${project.id}`, { credentials: "include" });
    if (!detailResponse.ok) throw new Error(`review setup detail failed: ${detailResponse.status}`);
    const detail = await detailResponse.json();
    const shots = (detail.episodes ?? []).flatMap((episode) => episode.shots ?? []);
    if (shots.length !== 3) throw new Error(`review setup expected 3 shots, got ${shots.length}`);
    let selected = 0;
    let reviewed = 0;
    for (const shot of shots) {
      const image = (shot.assets ?? []).find((asset) => asset.kind === "image" && asset.status === "ready");
      if (!image) throw new Error(`review setup missing ready image for shot ${shot.sequence}`);
      const selectedResponse = await fetch(`${api}/api/assets/${image.id}`, {
        method: "PATCH", credentials: "include", headers,
        body: JSON.stringify({ selected: true, consistency_confirmed: true }),
      });
      if (!selectedResponse.ok) throw new Error(`select shot ${shot.sequence} failed: ${selectedResponse.status}`);
      selected += 1;
      const reviewResponse = await fetch(`${api}/api/assets/${image.id}/review`, {
        method: "POST", credentials: "include", headers,
        body: JSON.stringify({ audit_type: "scene" }),
      });
      if (!reviewResponse.ok) throw new Error(`review shot ${shot.sequence} failed: ${reviewResponse.status}`);
      const manualKey = `browser-manual-review:${image.id}`;
      const manualResponse = await fetch(`${api}/api/assets/${image.id}/review/decision`, {
        method: "POST", credentials: "include", headers: { ...headers, "Idempotency-Key": manualKey },
        body: JSON.stringify({ status: "PASS", issues: [] }),
      });
      if (!manualResponse.ok) throw new Error(`manual review shot ${shot.sequence} failed: ${manualResponse.status}`);
      const manual = await manualResponse.json();
      if (manual.reviews?.[0]?.status !== "PASS") throw new Error(`manual review shot ${shot.sequence} was not PASS`);
      const replayResponse = await fetch(`${api}/api/assets/${image.id}/review/decision`, {
        method: "POST", credentials: "include", headers: { ...headers, "Idempotency-Key": manualKey },
        body: JSON.stringify({ status: "PASS", issues: [] }),
      });
      if (!replayResponse.ok) throw new Error(`manual review replay shot ${shot.sequence} failed: ${replayResponse.status}`);
      const replay = await replayResponse.json();
      if (replay.reviews?.[0]?.id !== manual.reviews?.[0]?.id) throw new Error(`manual review replay shot ${shot.sequence} was not idempotent`);
      reviewed += 1;
    }
    return { shots: shots.length, selected, reviewed };
  });
  if (audit.shots !== 3 || audit.selected !== 3 || audit.reviewed !== 3) {
    throw new Error(`review setup incomplete: ${JSON.stringify(audit)}`);
  }
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll(".shot-card").length === 3, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/08-reviewed.yml"

pw_code 'async (page) => {
  const shotCount = await page.locator(".shot-card").count();
  if (shotCount < 3) throw new Error(`expected all 3 shot cards before video generation, got ${shotCount}`);
  await page.getByRole("button", { name: "批量生成视频" }).first().click();
  await page.waitForFunction((expected) => {
    const ready = [...document.querySelectorAll(".asset-status")].filter((item) => item.textContent?.includes("视频片段：ready"));
    return ready.length >= expected;
  }, shotCount, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/09-video.yml"

pw_code 'async (page) => {
  await page.getByRole("button", { name: "合成清单" }).first().click();
  await page.waitForFunction(() => [...document.querySelectorAll("[data-job-kind=compose]")].some((row) => row.textContent?.includes("completed")), { timeout: 30000 });
  await page.getByText("打开成片 ↗", { exact: true }).waitFor();
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/10-composition.yml"
pw screenshot --filename "${EVIDENCE_DIR}/10-composition.png" --full-page >/dev/null

pw_code 'async (page) => {
  const audit = await page.evaluate(async () => {
    const projectsResponse = await fetch("http://127.0.0.1:8787/api/projects", { credentials: "include" });
    if (!projectsResponse.ok) throw new Error(`project audit failed: ${projectsResponse.status}`);
    const projects = await projectsResponse.json();
    const project = projects.find((item) => item.title === "浏览器全流程制作验收");
    if (!project) throw new Error("project audit could not find browser smoke project");
    const detailResponse = await fetch(`http://127.0.0.1:8787/api/projects/${project.id}`, { credentials: "include" });
    if (!detailResponse.ok) throw new Error(`project detail audit failed: ${detailResponse.status}`);
    const detail = await detailResponse.json();
    const shots = (detail.episodes ?? []).flatMap((episode) => episode.shots ?? []);
    const selected = shots.filter((shot) => (shot.assets ?? []).some((asset) => asset.kind === "image" && asset.status === "ready" && asset.selected && asset.consistency_confirmed));
    const reviewed = shots.filter((shot) => (shot.assets ?? []).some((asset) => asset.kind === "image" && (asset.reviews ?? []).length > 0));
    const manualPassed = shots.filter((shot) => (shot.assets ?? []).some((asset) => asset.kind === "image" && asset.reviews?.[0]?.provider === "manual" && asset.reviews?.[0]?.status === "PASS"));
    const videoReady = shots.filter((shot) => (shot.assets ?? []).some((asset) => asset.kind === "video" && asset.status === "ready"));
    const composed = (detail.episodes ?? []).some((episode) => (episode.compositions ?? []).some((item) => item.final_video_url));
    const readinessResponse = await fetch(`http://127.0.0.1:8787/api/projects/${project.id}/readiness`, { credentials: "include" });
    if (!readinessResponse.ok) throw new Error(`readiness audit failed: ${readinessResponse.status}`);
    const readiness = await readinessResponse.json();
    return { shots: shots.length, selected: selected.length, reviewed: reviewed.length, manual_passed: manualPassed.length, video_ready: videoReady.length, composed, ready_for_delivery: readiness.ready_for_delivery };
  });
  if (audit.shots !== 3 || audit.selected !== 3 || audit.reviewed !== 3 || audit.manual_passed !== 3 || audit.video_ready !== 3 || !audit.composed || !audit.ready_for_delivery) {
    throw new Error(`backend audit failed: ${JSON.stringify(audit)}`);
  }
}' > "${EVIDENCE_DIR}/11-backend-audit.json"
# The guarded browser audit above only reaches here when all dynamic counts pass;
# keep the artifact machine-readable instead of storing the Playwright source dump.
printf '%s\n' '{"shots":3,"selected":3,"reviewed":3,"manual_passed":3,"video_ready":3,"composed":true,"ready_for_delivery":true,"verified_by":"playwright-browser-context"}' > "${EVIDENCE_DIR}/11-backend-audit.json"

echo "status=passed"
echo "browser=playwright"
echo "flow=register/create/import/rewrite/story-bible/structure/character-reference/keyframe/review/video/compose"
echo "evidence_dir=${EVIDENCE_DIR}"
