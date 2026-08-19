import test from "node:test";
import assert from "node:assert/strict";

import { durationInFrames, normalizeManifest } from "./manifest.js";

test("normalizes a composition manifest into deterministic frame durations", () => {
  const manifest = normalizeManifest({
    composition_id: "composition_1",
    fps: 24,
    items: [{ sequence: 1, shot_id: "shot_1", asset_id: "asset_1", duration_seconds: 2.5 }],
  });
  assert.equal(manifest.fps, 24);
  assert.equal(durationInFrames(manifest), 60);
});

test("rejects empty or unsafe durations before invoking Chromium", () => {
  assert.throws(() => normalizeManifest({ items: [] }), /at least one item/);
  assert.throws(() => normalizeManifest({ items: [{ shot_id: "s", asset_id: "a", duration_seconds: 0 }] }), /invalid duration/);
});

test("normalizes audio tracks and extends the timeline for subtitles", () => {
  const manifest = normalizeManifest({
    fps: 10,
    items: [{ sequence: 1, shot_id: "s", asset_id: "a", duration_seconds: 1, url: null }],
    audio_tracks: [{ source_path: "/tmp/narration.wav", start_seconds: 0, volume: 0.8 }],
    subtitles: [{ start_seconds: 1, end_seconds: 2.5, text: "字幕" }],
  });
  assert.equal(manifest.audio_tracks?.[0].volume, 0.8);
  assert.equal(durationInFrames(manifest), 25);
});

test("rejects subtitle overlap with invalid timing", () => {
  assert.throws(() => normalizeManifest({
    items: [{ shot_id: "s", asset_id: "a", duration_seconds: 1 }],
    subtitles: [{ start_seconds: 2, end_seconds: 1, text: "bad" }],
  }), /invalid timing/);
});
