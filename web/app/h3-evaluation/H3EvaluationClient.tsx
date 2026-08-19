'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import styles from './page.module.css';

export type EvaluationItem = {
  id: string;
  style: string;
  resolution: string;
  width: number;
  height: number;
  steps: number;
  seed: number;
  elapsedSeconds: number;
  durationSeconds: number;
  frames: number;
  fps: string;
  videoCodec: string;
  audioCodec: string;
  audioSampleRate: string;
  audioChannels: number;
  sizeBytes: number;
  sha256: string;
  promptId: string;
  videoUrl: string;
  frameUrls: string[];
  workflowUrl: string;
};

type Props = {
  items: EvaluationItem[];
  summary: { sampleCount: number; styleCount: number; resolutionCount: number; totalGpuHours: number };
  longVideos: LongVideoItem[];
};

type LongVideoItem = {
  id: string;
  label: string;
  method: string;
  resolution: string;
  steps: number;
  elapsedSeconds: number;
  durationSeconds: number;
  frames: number;
  videoUrl: string;
  posterUrl: string;
  note: string;
};

const styleLabels: Record<string, string> = {
  live_action: '真人写实',
  '3d_guofeng': '3D 国风',
  '3d_xianxia': '3D 仙侠',
  anime_cel: '赛璐璐动画',
  ink_cg: '水墨 CG',
};

const resolutionLabels: Record<string, string> = {
  r360: '640 × 384',
  r480: '832 × 480',
  r540: '960 × 544',
  r660: '1088 × 608',
  r800: '1216 × 672',
  r1mp: '1344 × 768 · 1MP',
};

const resolutionOrder = ['r360', 'r480', 'r540', 'r660', 'r800', 'r1mp'];
const styleOrder = ['live_action', '3d_guofeng', '3d_xianxia', 'anime_cel', 'ink_cg'];

function formatMinutes(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
}

function formatSize(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function recommendation(item: EvaluationItem) {
  if (item.resolution === 'r660' && item.steps === 8) return '平衡推荐';
  if (item.resolution === 'r1mp' && item.steps === 8) return '高清候选';
  if (item.resolution === 'r1mp' && item.steps === 20) return '关键镜头';
  return '基线样本';
}

export default function H3EvaluationClient({ items, summary, longVideos }: Props) {
  const [resolution, setResolution] = useState('all');
  const [style, setStyle] = useState('all');
  const [steps, setSteps] = useState('all');
  const [selectedId, setSelectedId] = useState(items.find((item) => item.id === 'r1mp_3d_guofeng_8s')?.id ?? items[0]?.id ?? '');

  const filteredItems = useMemo(
    () => items.filter((item) => (resolution === 'all' || item.resolution === resolution) && (style === 'all' || item.style === style) && (steps === 'all' || String(item.steps) === steps)),
    [items, resolution, style, steps],
  );
  const selected = filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0] ?? items[0];
  const peerItems = selected ? items.filter((item) => item.resolution === selected.resolution && item.steps === selected.steps) : [];

  function selectItem(id: string) {
    setSelectedId(id);
  }

  function resetFilters() {
    setResolution('all');
    setStyle('all');
    setSteps('all');
  }

  return (
    <main className={styles.page}>
      <header className={styles.nav}>
        <Link className={styles.brand} href="/">
          <span className={styles.brandMark}>✦</span>
          <span>AI 漫剧工作台</span>
        </Link>
        <div className={styles.navRight}>
          <span className={styles.liveDot}><i /> 本地评测存档</span>
          <Link className={styles.navLink} href="/">返回工作台 ↗</Link>
        </div>
      </header>

      <section className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.kicker}>MINIMAX H3 · LOCAL BENCHMARK / 2026.08</p>
          <h1>高清生成<br /><em>评测台</em></h1>
          <p className={styles.heroLead}>把 35 次真实生成变成可比较的画面证据。按风格、分辨率与采样步数逐项筛选，直接播放原始输出，判断哪一档值得进入生产。</p>
        </div>
        <div className={styles.heroNote}>
          <span className={styles.noteLine} />
          <p>统一底座</p>
          <strong>INT8 FL2VA<br />+ T8 Turbo LoRA</strong>
          <small>SageAttention · DualClock<br />24fps · 原生音频</small>
        </div>
      </section>

      <section className={styles.metrics} aria-label="测试摘要">
        <div><span>可审阅样本</span><strong>{summary.sampleCount}</strong><small>全部完整解码通过</small></div>
        <div><span>画面风格</span><strong>{summary.styleCount}</strong><small>真人 / 3D / 动画 / 水墨</small></div>
        <div><span>分辨率阶梯</span><strong>{summary.resolutionCount}</strong><small>640×384 → 1344×768</small></div>
        <div><span>累计 GPU 时间</span><strong>{summary.totalGpuHours.toFixed(2)}h</strong><small>RTX 3060 12GB 实测</small></div>
      </section>

      <section className={styles.controlBar} aria-label="筛选条件">
        <div className={styles.controlIntro}>
          <span className={styles.kicker}>COMPARE OUTPUTS</span>
          <strong>选择审阅样本</strong>
        </div>
        <label>分辨率<select value={resolution} onChange={(event) => setResolution(event.target.value)}><option value="all">全部分辨率</option>{resolutionOrder.map((key) => <option key={key} value={key}>{resolutionLabels[key]}</option>)}</select></label>
        <label>风格<select value={style} onChange={(event) => setStyle(event.target.value)}><option value="all">全部风格</option>{styleOrder.map((key) => <option key={key} value={key}>{styleLabels[key]}</option>)}</select></label>
        <label>采样<select value={steps} onChange={(event) => setSteps(event.target.value)}><option value="all">全部步数</option><option value="8">8 steps</option><option value="20">20 steps</option></select></label>
        <button className={styles.resetButton} onClick={resetFilters}>重置</button>
        <span className={styles.resultCount}>{filteredItems.length} 个结果</span>
      </section>

      <section className={styles.workspace}>
        <aside className={styles.resultsPanel}>
          <div className={styles.panelHeading}><div><span className={styles.kicker}>OUTPUT INDEX</span><h2>样本目录</h2></div><span className={styles.indexCount}>{filteredItems.length.toString().padStart(2, '0')}</span></div>
          <div className={styles.resultList}>
            {filteredItems.map((item) => (
              <button key={item.id} className={`${styles.resultRow} ${selected?.id === item.id ? styles.resultRowActive : ''}`} onClick={() => selectItem(item.id)}>
                <span className={styles.rowIndex}>{String(items.indexOf(item) + 1).padStart(2, '0')}</span>
                <span className={styles.rowMain}><strong>{styleLabels[item.style]}</strong><small>{resolutionLabels[item.resolution]} · {item.steps} steps</small></span>
                <span className={styles.rowTime}>{formatMinutes(item.elapsedSeconds)}</span>
              </button>
            ))}
            {!filteredItems.length && <div className={styles.empty}>没有匹配样本<br /><button onClick={resetFilters}>清除筛选</button></div>}
          </div>
        </aside>

        {selected && (
          <article className={styles.detailPanel}>
            <div className={styles.detailHeader}>
              <div><p className={styles.kicker}>SELECTED OUTPUT / {selected.id}</p><h2>{styleLabels[selected.style]} <span>·</span> {resolutionLabels[selected.resolution]}</h2></div>
              <span className={`${styles.recommendation} ${selected.steps === 20 ? styles.recommendationWarm : ''}`}>{recommendation(selected)}</span>
            </div>
            <div className={styles.videoStage}>
              <video key={selected.videoUrl} className={styles.video} src={selected.videoUrl} controls playsInline preload="metadata" poster={selected.frameUrls[1]} />
              <div className={styles.videoTag}>H3 / {selected.steps} STEPS</div>
            </div>
            <div className={styles.quickFacts}>
              <div><span>生成耗时</span><strong>{formatMinutes(selected.elapsedSeconds)}</strong></div>
              <div><span>视频时长</span><strong>{selected.durationSeconds.toFixed(3)}s</strong></div>
              <div><span>输出规格</span><strong>{selected.frames} 帧 · {selected.fps} fps</strong></div>
              <div><span>文件大小</span><strong>{formatSize(selected.sizeBytes)}</strong></div>
            </div>

            <div className={styles.reviewStrip}>
              <div className={styles.subHeading}><span className={styles.kicker}>FRAME CHECK</span><strong>首帧 / 中帧 / 尾帧</strong><small>快速看构图、人物和连续性</small></div>
              <div className={styles.frames}>
                {selected.frameUrls.map((url, index) => <a href={url} target="_blank" rel="noreferrer" key={url}><img src={url} alt={`${selected.id} 抽帧 ${index + 1}`} /><span>F{index + 1}</span></a>)}
              </div>
            </div>

            <details className={styles.settings}>
              <summary><span>查看生成设置与链路</span><b>＋</b></summary>
              <div className={styles.settingsGrid}>
                <div><span>扩散底座</span><strong>非剪枝 INT8 FL2VA</strong></div>
                <div><span>文本编码器</span><strong>INT4 Qwen</strong></div>
                <div><span>加速 LoRA</span><strong>T8 Turbo v4</strong></div>
                <div><span>注意力 / 采样器</span><strong>SageAttention · DualClock</strong></div>
                <div><span>随机种子</span><strong>{selected.seed}</strong></div>
                <div><span>音频轨</span><strong>{selected.audioCodec} · {selected.audioSampleRate}Hz · {selected.audioChannels}ch</strong></div>
                <div className={styles.hash}><span>SHA-256</span><strong>{selected.sha256}</strong></div>
              </div>
              <a className={styles.workflowLink} href={selected.workflowUrl} target="_blank" rel="noreferrer">打开对应 ComfyUI 工作流 JSON ↗</a>
            </details>

            <div className={styles.peerSection}>
              <div className={styles.subHeading}><span className={styles.kicker}>SAME PASS</span><strong>同档风格对比</strong><small>{resolutionLabels[selected.resolution]} · {selected.steps} steps</small></div>
              <div className={styles.peerGrid}>{peerItems.map((item) => <button key={item.id} className={`${styles.peer} ${item.id === selected.id ? styles.peerActive : ''}`} onClick={() => selectItem(item.id)}><img src={item.frameUrls[1]} alt={styleLabels[item.style]} /><span>{styleLabels[item.style]}</span><small>{formatMinutes(item.elapsedSeconds)}</small></button>)}</div>
            </div>
          </article>
        )}
      </section>

      <section className={styles.longSection}>
        <div className={styles.longHeading}>
          <div><p className={styles.kicker}>LONG-RUN EVIDENCE / 3060 12GB</p><h2>长视频实测</h2><p>这里单独收录真实长时长链路。成功样本和 OOM 边界分开看，避免把 5 秒矩阵的速度外推成 15 秒生产能力。</p></div>
          <span className={styles.longStamp}>5 个成功产物<br /><b>+ 1 个 OOM 边界</b></span>
        </div>
        <div className={styles.longGrid}>
          {longVideos.map((item) => <article className={styles.longCard} key={item.id}>
            <div className={styles.longVideoWrap}><video className={styles.longVideo} src={item.videoUrl} poster={item.posterUrl} controls playsInline preload="metadata" /><span className={styles.longDuration}>{item.durationSeconds.toFixed(3)}s</span></div>
            <div className={styles.longCardBody}>
              <div className={styles.longCardTitle}><div><span className={styles.kicker}>{item.method}</span><h3>{item.label}</h3></div><span className={styles.longStep}>{item.steps} steps</span></div>
              <p>{item.note}</p>
              <div className={styles.longFacts}><span><b>生成</b>{formatMinutes(item.elapsedSeconds)}</span><span><b>画面</b>{item.resolution}</span><span><b>帧数</b>{item.frames}</span></div>
              <a className={styles.longOpen} href={item.videoUrl} target="_blank" rel="noreferrer">单独打开视频 ↗</a>
            </div>
          </article>)}
        </div>
        <div className={styles.oomBoundary}><span className={styles.oomMark}>!</span><div><strong>明确失败边界：832 × 480 / 15.083 秒 / 8 steps + T8 LoRA</strong><p>实测触发 CUDA OOM，未产出可验收视频。15 秒应切换到 704 × 416，或改用分段接力。</p></div><span className={styles.oomLabel}>OOM</span></div>
      </section>

      <footer className={styles.footer}><span>机器验收：35/35 短视频完整解码通过 · 长视频成功档已单独列出 · 峰值显存约 11,033MiB / 12GB</span><span><Link href="/">工作台</Link><span className={styles.footerDivider}>/</span><a href="/api/h3-evaluation/media/review_manifest.json" target="_blank" rel="noreferrer">原始验收清单</a></span></footer>
    </main>
  );
}
