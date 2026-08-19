export type RenderItem = {
  sequence: number;
  shot_id: string;
  asset_id: string;
  duration_seconds: number;
  url?: string | null;
  source_path?: string | null;
  asset_file?: string | null;
  title?: string | null;
};

export type RenderAudioTrack = {
  id?: string;
  url?: string | null;
  source_path?: string | null;
  asset_file?: string | null;
  start_seconds?: number;
  volume?: number;
};

export type RenderSubtitle = {
  start_seconds: number;
  end_seconds: number;
  text: string;
};

export type RenderManifest = {
  composition_id?: string;
  title?: string;
  fps?: number;
  width?: number;
  height?: number;
  items: RenderItem[];
  audio_tracks?: RenderAudioTrack[];
  subtitles?: RenderSubtitle[];
};

export const DEFAULT_FPS = 30;
export const DEFAULT_WIDTH = 1024;
export const DEFAULT_HEIGHT = 576;

export function normalizeManifest(input: unknown): RenderManifest {
  if (!input || typeof input !== "object") {
    throw new Error("render manifest must be an object");
  }
  const candidate = input as Partial<RenderManifest>;
  if (!Array.isArray(candidate.items) || candidate.items.length === 0) {
    throw new Error("render manifest must contain at least one item");
  }
  const items = candidate.items.map((item, index) => {
    if (!item || typeof item !== "object") {
      throw new Error(`render item ${index + 1} must be an object`);
    }
    const current = item as Partial<RenderItem>;
    if (!current.shot_id || !current.asset_id) {
      throw new Error(`render item ${index + 1} is missing shot_id or asset_id`);
    }
    const duration = Number(current.duration_seconds ?? 3);
    if (!Number.isFinite(duration) || duration <= 0 || duration > 900) {
      throw new Error(`render item ${index + 1} has invalid duration_seconds`);
    }
    return {
      sequence: Number(current.sequence ?? index + 1),
      shot_id: String(current.shot_id),
      asset_id: String(current.asset_id),
      duration_seconds: duration,
      url: current.url == null ? null : String(current.url),
      source_path: current.source_path == null ? null : String(current.source_path),
      asset_file: current.asset_file == null ? null : String(current.asset_file),
      title: current.title == null ? null : String(current.title),
    } satisfies RenderItem;
  });
  const audioTracks = Array.isArray(candidate.audio_tracks)
    ? candidate.audio_tracks.map((track, index) => {
        if (!track || typeof track !== "object") throw new Error(`audio track ${index + 1} must be an object`);
        const current = track as RenderAudioTrack;
        const start = Number(current.start_seconds ?? 0);
        const volume = Number(current.volume ?? 1);
        if (!Number.isFinite(start) || start < 0 || start > 900) throw new Error(`audio track ${index + 1} has invalid start_seconds`);
        if (!Number.isFinite(volume) || volume < 0 || volume > 2) throw new Error(`audio track ${index + 1} has invalid volume`);
        if (!current.url && !current.source_path && !current.asset_file) throw new Error(`audio track ${index + 1} has no source`);
        return {
          id: current.id == null ? `audio-${index + 1}` : String(current.id),
          url: current.url == null ? null : String(current.url),
          source_path: current.source_path == null ? null : String(current.source_path),
          asset_file: current.asset_file == null ? null : String(current.asset_file),
          start_seconds: start,
          volume,
        } satisfies RenderAudioTrack;
      })
    : [];
  const subtitles = Array.isArray(candidate.subtitles)
    ? candidate.subtitles.map((subtitle, index) => {
        if (!subtitle || typeof subtitle !== "object") throw new Error(`subtitle ${index + 1} must be an object`);
        const current = subtitle as RenderSubtitle;
        const start = Number(current.start_seconds);
        const end = Number(current.end_seconds);
        const text = String(current.text ?? "").trim();
        if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > 900) throw new Error(`subtitle ${index + 1} has invalid timing`);
        if (!text || text.length > 500) throw new Error(`subtitle ${index + 1} has invalid text`);
        return { start_seconds: start, end_seconds: end, text } satisfies RenderSubtitle;
      })
    : [];
  return {
    composition_id: candidate.composition_id == null ? undefined : String(candidate.composition_id),
    title: candidate.title == null ? "AI 漫剧分集" : String(candidate.title),
    fps: Number(candidate.fps ?? DEFAULT_FPS),
    width: Number(candidate.width ?? DEFAULT_WIDTH),
    height: Number(candidate.height ?? DEFAULT_HEIGHT),
    items,
    audio_tracks: audioTracks,
    subtitles,
  };
}

export function durationInFrames(manifest: RenderManifest): number {
  const fps = manifest.fps ?? DEFAULT_FPS;
  const videoSeconds = manifest.items.reduce((total, item) => total + item.duration_seconds, 0);
  const subtitleSeconds = (manifest.subtitles ?? []).reduce((latest, subtitle) => Math.max(latest, subtitle.end_seconds), 0);
  return Math.max(1, Math.ceil(Math.max(videoSeconds, subtitleSeconds) * fps));
}
