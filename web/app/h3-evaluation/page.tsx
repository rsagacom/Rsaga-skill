import fs from 'node:fs';
import path from 'node:path';
import H3EvaluationClient, { type EvaluationItem } from './H3EvaluationClient';

const RUN_ROOT = '/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix';
const MANIFEST = path.join(RUN_ROOT, 'review_manifest.json');
const MEDIA_BASE = '/api/h3-evaluation/media';

export const metadata = {
  title: 'H3 高清评测台 · AI 漫剧工作台',
  description: 'MiniMax H3 高清矩阵与长视频实测证据浏览器',
};

type ManifestRow = {
  id: string;
  style: string;
  res: string;
  steps: number;
  seed: number;
  elapsed_seconds: number;
  prompt_id: string;
  output: string;
  verification: {
    status: string;
    sha256: string;
    frames: string[];
    media: {
      duration_seconds: number;
      size_bytes: number;
      video_codec: string;
      video_width: number;
      video_height: number;
      video_frames: string;
      fps: string;
      audio_codec: string;
      audio_sample_rate: string;
      audio_channels: number;
    };
  };
};

function mediaPath(...parts: string[]) {
  return `${MEDIA_BASE}/${parts.map((part) => encodeURIComponent(part)).join('/')}`;
}

function basename(value: string) {
  return path.basename(value);
}

function buildItem(row: ManifestRow): EvaluationItem {
  const media = row.verification.media;
  return {
    id: row.id,
    style: row.style,
    resolution: row.res,
    width: media.video_width,
    height: media.video_height,
    steps: row.steps,
    seed: row.seed,
    elapsedSeconds: row.elapsed_seconds,
    durationSeconds: media.duration_seconds,
    frames: Number(media.video_frames),
    fps: media.fps,
    videoCodec: media.video_codec,
    audioCodec: media.audio_codec,
    audioSampleRate: media.audio_sample_rate,
    audioChannels: media.audio_channels,
    sizeBytes: media.size_bytes,
    sha256: row.verification.sha256,
    promptId: row.prompt_id,
    videoUrl: mediaPath('outputs', basename(row.output)),
    frameUrls: row.verification.frames.map((frame) => {
      const frameName = basename(frame);
      return mediaPath('frames', row.id, frameName);
    }),
    workflowUrl: mediaPath('workflows', `workflow_${row.id}.json`),
  };
}

function longMediaPath(root: string, ...parts: string[]) {
  return `${MEDIA_BASE}/${root}/${parts.map((part) => encodeURIComponent(part)).join('/')}`;
}

function longVideoItems() {
  return [
    {
      id: 'long_direct_10s_832x480',
      label: '10 秒稳定档',
      method: '直接 T2VA 文生视频',
      resolution: '832 × 480',
      steps: 8,
      elapsedSeconds: 753,
      durationSeconds: 10.125,
      frames: 243,
      videoUrl: longMediaPath('longvideo', 'h3_s04a_t8_dualclock_int8_int4clip_audio_480p_10s_8steps_00001_.mp4'),
      posterUrl: longMediaPath('longvideo', 'middle_frame.png'),
      note: '质量与长度平衡候选；适合作为 3060 12GB 的长镜头起点。',
    },
    {
      id: 'long_direct_12s_832x480',
      label: '12 秒质量档',
      method: '直接 T2VA 文生视频',
      resolution: '832 × 480',
      steps: 8,
      elapsedSeconds: 888,
      durationSeconds: 12.25,
      frames: 294,
      videoUrl: longMediaPath('longvideo', 'h3_s04a_t8_dualclock_int8_int4clip_audio_480p_12s_8steps_00001_.mp4'),
      posterUrl: longMediaPath('longvideo', '12s_middle_frame.png'),
      note: '画质优先的长镜头上限候选；15 秒时长不要沿用此分辨率。',
    },
    {
      id: 'long_direct_15s_704x416',
      label: '15 秒可行档',
      method: '直接 T2VA 文生视频',
      resolution: '704 × 416',
      steps: 8,
      elapsedSeconds: 846,
      durationSeconds: 15.083,
      frames: 362,
      videoUrl: longMediaPath('longvideo', 'h3_s04a_t8_dualclock_int8_int4clip_audio_704x416_15s_8steps_00001_.mp4'),
      posterUrl: longMediaPath('longvideo', '15s_704x416_middle_frame.png'),
      note: '3060 12GB 实测 15 秒成功档；建议后期再做超分。',
    },
    {
      id: 'long_motion_context_9s',
      label: 'Motion Context 接力',
      method: '两段 latent + 音频上下文接力',
      resolution: '832 × 480',
      steps: 8,
      elapsedSeconds: 986.53,
      durationSeconds: 9.449,
      frames: 226,
      videoUrl: longMediaPath('motion-context', 'chain_concat.mp4'),
      posterUrl: longMediaPath('motion-context', 'frames', 'clip2_head.png'),
      note: '第二段承接第一段尾部上下文，去除重叠后合并；适合手工控制段间连续性。',
    },
    {
      id: 'long_director_10s',
      label: 'Director 段间引导',
      method: 'AIMixer Director 两段时间轴',
      resolution: '832 × 480',
      steps: 8,
      elapsedSeconds: 1108,
      durationSeconds: 10.334,
      frames: 248,
      videoUrl: longMediaPath('director', 'aimixer_director_2segments.mp4'),
      posterUrl: longMediaPath('director', 'frames', 'frame_04.png'),
      note: '自动管理分段、上下文与显存清理；适合生产时间轴和局部重跑。',
    },
  ];
}

export default function H3EvaluationPage() {
  const rows = JSON.parse(fs.readFileSync(MANIFEST, 'utf-8')) as ManifestRow[];
  const items = rows.map(buildItem);
  const totalSeconds = items.reduce((sum, item) => sum + item.elapsedSeconds, 0);

  return (
    <H3EvaluationClient
      items={items}
      summary={{
        sampleCount: items.length,
        styleCount: new Set(items.map((item) => item.style)).size,
        resolutionCount: new Set(items.map((item) => item.resolution)).size,
        totalGpuHours: totalSeconds / 3600,
      }}
      longVideos={longVideoItems()}
    />
  );
}
