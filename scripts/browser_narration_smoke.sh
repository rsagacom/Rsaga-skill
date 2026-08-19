#!/usr/bin/env bash

set -euo pipefail

# 真实浏览器验收：注册 -> 创建项目 -> 导入小说 -> 改编通过 -> 准备结构
# -> 编辑/保存分集旁白稿 -> 点击“生成旁白” -> 检查本地 WAV 音频资产、音轨挂载和积分扣减。
# 本地预览不调用真实 TTS Provider；脚本只输出脱敏状态和证据路径。

WEB_URL="${STUDIO_SMOKE_WEB_URL:-http://127.0.0.1:3000}"
API_URL="${STUDIO_SMOKE_API_URL:-http://127.0.0.1:8787}"
SESSION_NAME="${STUDIO_BROWSER_SESSION:-ai-manhua-narration-smoke}"
EVIDENCE_DIR="${STUDIO_BROWSER_EVIDENCE_DIR:-output/playwright/browser-narration-smoke}"
SMOKE_VOICE="${STUDIO_SMOKE_VOICE:-marin}"
EXPECTED_VOICE_COUNT="${STUDIO_SMOKE_EXPECTED_VOICE_COUNT:-0}"
SMOKE_SPEED="${STUDIO_SMOKE_SPEED:-1.25}"
SMOKE_INSTRUCTIONS="${STUDIO_SMOKE_INSTRUCTIONS:-沉稳、低声叙述}"
SMOKE_INSTRUCTIONS_JSON="$(SMOKE_INSTRUCTIONS="${SMOKE_INSTRUCTIONS}" node -p 'JSON.stringify(process.env.SMOKE_INSTRUCTIONS)')"

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

pw_code 'async (page) => {
  const stamp = Date.now();
  await page.locator("input[placeholder=\"邮箱\"]").fill(`narration-smoke-${stamp}@example.test`);
  await page.locator("input[placeholder^=\"密码\"]").fill(`Narration-${stamp}!`);
  await page.getByRole("button", { name: "注册" }).click();
  await page.getByText("新建项目", { exact: true }).waitFor();
  await page.locator("input[placeholder=\"邮箱\"]").fill("");
  await page.locator("input[placeholder^=\"密码\"]").fill("");
}' >/dev/null

pw_code 'async (page) => {
  await page.locator("input[placeholder^=\"项目标题\"]").fill("浏览器旁白生成验收");
  await page.locator("textarea[placeholder^=\"先写一段故事梗概\"]").fill("用于验证旁白生成和音频时间线挂载的短篇故事。");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByText("浏览器旁白生成验收", { exact: true }).first().waitFor();
  await page.locator("textarea[placeholder^=\"粘贴 TXT\"]").fill("雨夜里，她推开旧宅的门。\n门后传来陌生的呼唤。");
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
  await page.waitForFunction(() => document.querySelectorAll("[data-narration-button]").length >= 1, { timeout: 30000 });
}' >/dev/null
pw snapshot > "${EVIDENCE_DIR}/02-structure.yml"

pw_code 'async (page) => {
  const apiUrl = "'"${API_URL}"'";
  const smokeVoice = "'"${SMOKE_VOICE}"'";
  const expectedVoiceCount = Number("'"${EXPECTED_VOICE_COUNT}"'") || 0;
  const smokeSpeed = Number("'"${SMOKE_SPEED}"'");
  const smokeInstructions = '"${SMOKE_INSTRUCTIONS_JSON}"';
  const narrationDraft = "雨夜里，她推开旧宅的门。\n门后传来陌生的呼唤。";
  const before = await page.evaluate(async (url) => (await (await fetch(`${url}/api/credits`, { credentials: "include" })).json()).balance, apiUrl);
  const narrationDraftInput = page.locator("textarea[aria-label=\"分集旁白稿\"]");
  await narrationDraftInput.first().waitFor();
  await narrationDraftInput.first().fill(narrationDraft);
  await page.locator("[data-narration-script-save]").first().click();
  if (await narrationDraftInput.first().inputValue() !== narrationDraft) throw new Error("narration draft was not preserved after save");
  await page.reload();
  await page.locator("[data-narration-button]").first().waitFor();
  const reloadedNarrationDraftInput = page.locator("textarea[aria-label=\"分集旁白稿\"]");
  if (await reloadedNarrationDraftInput.first().inputValue() !== narrationDraft) throw new Error("narration draft was not restored after browser reload");
  const voiceSelector = page.locator("select[aria-label=\"旁白音色\"]");
  await voiceSelector.first().waitFor();
  const availableVoices = await voiceSelector.first().locator("option").evaluateAll((options) => options.map((option) => option.value));
  if (expectedVoiceCount > 0 && availableVoices.length !== expectedVoiceCount) {
    throw new Error(`unexpected speech voice option count: ${availableVoices.length}`);
  }
  if (!availableVoices.includes(smokeVoice)) throw new Error(`speech voice is not exposed by Web: ${smokeVoice}`);
  await voiceSelector.first().selectOption(smokeVoice);
  const speedSelector = page.locator("select[aria-label=\"旁白语速\"]");
  await speedSelector.first().waitFor();
  await speedSelector.first().selectOption(String(smokeSpeed));
  const instructionsInput = page.locator("input[aria-label=\"旁白表达指令\"]");
  await instructionsInput.first().waitFor();
  await instructionsInput.first().fill(smokeInstructions);
  await page.locator("[data-narration-button]").first().click();
  await page.waitForFunction(() => document.querySelectorAll(".audio-track-row").length >= 1, { timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll(".audio-track-preview").length >= 1, { timeout: 30000 });
  const audioPreview = await page.locator(".audio-track-preview").count();
  await page.waitForFunction(() => {
    const editor = [...document.querySelectorAll("textarea")].find((item) => item.getAttribute("placeholder")?.startsWith("每行一条字幕"));
    return Boolean(editor && editor.value.trim());
  }, { timeout: 30000 });
  const subtitleText = await page.locator("textarea[placeholder^=\"每行一条字幕\"]").inputValue();
  const result = await page.evaluate(async ({ url, before, draftChars }) => {
    const projects = await (await fetch(`${url}/api/projects`, { credentials: "include" })).json();
    const project = projects.find((item) => item.title === "浏览器旁白生成验收");
    if (!project) throw new Error("narration smoke project not found");
    const detail = await (await fetch(`${url}/api/projects/${project.id}`, { credentials: "include" })).json();
    const episode = detail.episodes?.[0];
    const track = episode?.composition_settings?.audio_tracks?.[0];
    const asset = (detail.audio_assets ?? []).find((item) => item.id === track?.asset_id);
    const after = await (await fetch(`${url}/api/credits`, { credentials: "include" })).json();
    return {
      provider: asset?.provider,
      status: asset?.status,
      voice: asset?.metadata?.voice,
      speed: asset?.metadata?.speed,
      instructions_configured: asset?.metadata?.instructions_configured,
      text_chars: asset?.metadata?.text_chars,
      draft_persisted: episode?.composition_settings?.narration_text,
      url_suffix: String(asset?.url ?? "").split(".").pop(),
      tracks: episode?.composition_settings?.audio_tracks?.length ?? 0,
      subtitle_lines: episode?.composition_settings?.subtitles?.length ?? 0,
      audio_preview: 0,
      credit_delta: before - after.balance,
    };
  }, { url: apiUrl, before, draftChars: narrationDraft.length });
  result.audio_preview = audioPreview;
  result.subtitle_lines = Math.max(result.subtitle_lines, subtitleText.split("\n").filter(Boolean).length);
  if (result.provider !== "local" || result.status !== "ready" || result.voice !== smokeVoice || result.speed !== smokeSpeed || result.instructions_configured !== Boolean(smokeInstructions.trim()) || result.text_chars !== narrationDraft.length || result.draft_persisted !== narrationDraft || result.url_suffix !== "wav" || result.tracks < 1 || result.subtitle_lines < 1 || result.audio_preview < 1 || result.credit_delta !== 1) {
    throw new Error(`narration smoke contract failed: ${JSON.stringify(result)}`);
  }
  return JSON.stringify(result);
}'
pw snapshot > "${EVIDENCE_DIR}/03-narration-mounted.yml"
pw screenshot --filename "${EVIDENCE_DIR}/03-narration-mounted.png" --full-page >/dev/null

echo "status=passed"
echo "browser=playwright"
echo "flow=register/create/import/rewrite/approve/structure/save-narration-draft/generate-narration/attach-audio-track"
echo "evidence_dir=${EVIDENCE_DIR}"
