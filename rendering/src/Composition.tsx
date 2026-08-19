import React from "react";
import {
  AbsoluteFill,
  Audio,
  Composition,
  Img,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  Video,
} from "remotion";

import { DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH, durationInFrames, type RenderAudioTrack, type RenderItem, type RenderManifest } from "./manifest.ts";

function itemSource(item: RenderItem): string | null {
  if (item.asset_file) {
    return staticFile(item.asset_file);
  }
  return item.url || null;
}

function audioSource(track: RenderAudioTrack): string | null {
  if (track.asset_file) return staticFile(track.asset_file);
  return track.url || null;
}

function ShotFrame({ item }: { item: RenderItem }) {
  const frame = useCurrentFrame();
  const { durationInFrames: shotFrames } = useVideoConfig();
  const source = itemSource(item);
  const opacity = Math.min(1, Math.max(0, frame / Math.max(1, Math.min(12, shotFrames / 3))));

  return (
    <AbsoluteFill style={{ backgroundColor: "#111827", opacity }}>
      {source && source.toLowerCase().endsWith(".mp4") ? (
        <Video src={source} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : source ? (
        <Img src={source} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : null}
      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          padding: 38,
          background: "linear-gradient(transparent 52%, rgba(0,0,0,0.72))",
          color: "white",
          fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 700 }}>{item.title || `镜头 ${item.sequence}`}</div>
        <div style={{ fontSize: 14, opacity: 0.72, marginTop: 8 }}>{item.shot_id}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
}

export function EpisodeComposition({ manifest }: { manifest: RenderManifest }) {
  const fps = manifest.fps ?? DEFAULT_FPS;
  let from = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: "#111827" }}>
      {manifest.items.map((item) => {
        const duration = Math.max(1, Math.ceil(item.duration_seconds * fps));
        const sequence = <Sequence key={item.asset_id} from={from} durationInFrames={duration} premountFor={Math.min(15, duration)}>
          <ShotFrame item={item} />
        </Sequence>;
        from += duration;
        return sequence;
      })}
      {(manifest.audio_tracks ?? []).map((track) => {
        const source = audioSource(track);
        if (!source) return null;
        const start = Math.max(0, Math.floor((track.start_seconds ?? 0) * fps));
        return (
          <Sequence key={track.id} from={start}>
            <Audio src={source} volume={track.volume ?? 1} />
          </Sequence>
        );
      })}
      {(manifest.subtitles ?? []).map((subtitle, index) => {
        const start = Math.max(0, Math.floor(subtitle.start_seconds * fps));
        const duration = Math.max(1, Math.ceil((subtitle.end_seconds - subtitle.start_seconds) * fps));
        return (
          <Sequence key={`subtitle-${index}`} from={start} durationInFrames={duration}>
            <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 72, pointerEvents: "none" }}>
              <div style={{ background: "rgba(0,0,0,0.72)", borderRadius: 8, color: "white", fontSize: 30, lineHeight: 1.35, maxWidth: "82%", padding: "8px 20px", textAlign: "center", fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif" }}>
                {subtitle.text}
              </div>
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
}

export function RemotionRoot() {
  const defaultManifest: RenderManifest = {
    title: "AI 漫剧分集",
    fps: DEFAULT_FPS,
    width: DEFAULT_WIDTH,
    height: DEFAULT_HEIGHT,
    items: [{ sequence: 1, shot_id: "preview-shot", asset_id: "preview-asset", duration_seconds: 3, url: null }],
    audio_tracks: [],
    subtitles: [],
  };
  return (
    <Composition
      id="Episode"
      component={EpisodeComposition}
      durationInFrames={durationInFrames(defaultManifest)}
      fps={defaultManifest.fps ?? DEFAULT_FPS}
      width={defaultManifest.width ?? DEFAULT_WIDTH}
      height={defaultManifest.height ?? DEFAULT_HEIGHT}
      defaultProps={{ manifest: defaultManifest }}
      calculateMetadata={({ props }) => {
        const runtimeManifest = (props as { manifest?: RenderManifest }).manifest ?? defaultManifest;
        return {
          durationInFrames: durationInFrames(runtimeManifest),
          fps: runtimeManifest.fps ?? DEFAULT_FPS,
          width: runtimeManifest.width ?? DEFAULT_WIDTH,
          height: runtimeManifest.height ?? DEFAULT_HEIGHT,
        };
      }}
    />
  );
}
