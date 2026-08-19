import { copyFile, mkdir, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

import { bundle } from "@remotion/bundler";
import { ensureBrowser, getCompositions, renderMedia } from "@remotion/renderer";

import { normalizeManifest, type RenderManifest } from "./manifest.js";

type PreparedManifest = RenderManifest & {
  items: Array<RenderManifest["items"][number]>;
};

function argument(name: string): string {
  const index = process.argv.indexOf(name);
  const value = index >= 0 ? process.argv[index + 1] : undefined;
  if (!value || value.startsWith("--")) {
    throw new Error(`missing ${name}`);
  }
  return value;
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function prepareAssets(manifest: RenderManifest): Promise<{ manifest: PreparedManifest; publicDir: string }> {
  const publicDir = await mkdtemp(join(tmpdir(), "ai-manhua-remotion-"));
  const items = [];
  for (const [index, item] of manifest.items.entries()) {
    const sourcePath = item.source_path ? resolve(item.source_path) : null;
    if (!sourcePath) {
      items.push(item);
      continue;
    }
    if (!(await pathExists(sourcePath))) {
      throw new Error(`source_path does not exist: ${sourcePath}`);
    }
    const assetFile = join("assets", `${String(index + 1).padStart(4, "0")}-${basename(sourcePath)}`);
    const targetPath = join(publicDir, assetFile);
    await mkdir(dirname(targetPath), { recursive: true });
    await copyFile(sourcePath, targetPath);
    items.push({ ...item, asset_file: assetFile });
  }
  const audioTracks = [];
  for (const [index, track] of (manifest.audio_tracks ?? []).entries()) {
    const sourcePath = track.source_path ? resolve(track.source_path) : null;
    if (!sourcePath) {
      audioTracks.push(track);
      continue;
    }
    if (!(await pathExists(sourcePath))) throw new Error(`audio source_path does not exist: ${sourcePath}`);
    const assetFile = join("assets", `audio-${String(index + 1).padStart(4, "0")}-${basename(sourcePath)}`);
    const targetPath = join(publicDir, assetFile);
    await mkdir(dirname(targetPath), { recursive: true });
    await copyFile(sourcePath, targetPath);
    audioTracks.push({ ...track, asset_file: assetFile });
  }
  return { manifest: { ...manifest, items, audio_tracks: audioTracks }, publicDir };
}

async function render(manifestPath: string, outputPath: string): Promise<void> {
  const raw = JSON.parse(await readFile(resolve(manifestPath), "utf8")) as unknown;
  const manifest = normalizeManifest(raw);
  const prepared = await prepareAssets(manifest);
  const entryPoint = fileURLToPath(new URL("./entry.tsx", import.meta.url));
  const configuredBrowser = process.env.STUDIO_REMOTION_BROWSER_EXECUTABLE?.trim();
  const browserOptions = configuredBrowser ? { browserExecutable: configuredBrowser, logLevel: "error" as const } : { logLevel: "error" as const };
  try {
    await ensureBrowser(browserOptions);
    const serveUrl = await bundle({ entryPoint, publicDir: prepared.publicDir });
    const compositions = await getCompositions(serveUrl, { inputProps: { manifest: prepared.manifest }, browserExecutable: configuredBrowser ?? null });
    const composition = compositions.find((item) => item.id === "Episode");
    if (!composition) {
      throw new Error("Remotion composition Episode was not registered");
    }
    await renderMedia({
      composition,
      serveUrl,
      codec: "h264",
      outputLocation: resolve(outputPath),
      inputProps: { manifest: prepared.manifest },
      browserExecutable: configuredBrowser ?? null,
    });
  } finally {
    await rm(prepared.publicDir, { recursive: true, force: true });
  }
}

const manifestPath = argument("--manifest");
const outputPath = argument("--output");
await render(manifestPath, outputPath);
