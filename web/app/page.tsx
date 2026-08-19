"use client";

import Link from "next/link";
import { ChangeEvent, Fragment, useEffect, useMemo, useState } from "react";

// Production is served behind an HTTPS ingress that routes API and media
// paths to the API service. Keep the browser same-origin by default; local
// development still gets a convenient direct API fallback.
const API = process.env.NEXT_PUBLIC_API_BASE ?? (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:8787");

function requestId(scope = "request") {
  const cryptoApi = globalThis.crypto;
  const uuid = cryptoApi && typeof cryptoApi.randomUUID === "function" ? cryptoApi.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${scope}-${uuid}`;
}

function mediaUrl(value?: string) {
  if (!value) return "";
  return /^https?:\/\//i.test(value) ? value : `${API}${value}`;
}

function audioAssetFor(project: Project | null, assetId: string) {
  return project?.audio_assets?.find((asset) => asset.id === assetId);
}

function imageCandidates(shot: Shot) {
  return shot.assets.filter((asset) => asset.kind === "image");
}

function previewAsset(shot: Shot) {
  const images = imageCandidates(shot);
  return (
    images.find((asset) => asset.status === "ready" && asset.selected && asset.url) ??
    images.find((asset) => asset.status === "ready" && asset.url) ??
    images.find((asset) => asset.url)
  );
}

type Asset = {
  id: string;
  kind: string;
  character_id?: string | null;
  status: string;
  url?: string;
  selected: boolean;
  consistency_confirmed: boolean;
  metadata?: { mode?: string; original_filename?: string };
  reviews?: Array<{ status: string; issues?: string[] }>;
};

type ImagePrompt = {
  id: string;
  shot_id: string;
  prompt: string;
  negative_prompt: string;
  provider?: string;
  model?: string;
};
type PromptJobResult = Partial<ImagePrompt> & { job?: Job | null };

type Shot = {
  id: string;
  sequence: number;
  scene: string;
  emotion: string;
  duration_seconds: number;
  description: string;
  adaptation_unit_ids?: string[];
  image_prompt?: ImagePrompt | null;
  assets: Asset[];
};

type AudioTrack = { asset_id: string; start_seconds: number; volume: number };
type Subtitle = { start_seconds: number; end_seconds: number; text: string };
type CompositionSettings = {
  episode_id?: string;
  audio_tracks: AudioTrack[];
  subtitles: Subtitle[];
  narration_text?: string;
  updated_at?: string | null;
};
type Character = {
  id: string;
  name: string;
  role: string;
  description: string;
  visual_lock?: { prompt?: string };
  status?: string;
  references?: Array<{
    front_url?: string;
    side_url?: string;
    back_url?: string;
    status?: string;
    provider?: string;
    job?: Job | null;
  }>;
};

type StoryEntity = {
  id: string;
  kind: "location" | "prop" | string;
  name: string;
  description: string;
  attributes?: Record<string, unknown>;
  source_segment_ids?: string[];
  status: string;
};
type StoryRelationship = {
  id: string;
  source_type: "character" | "entity" | string;
  source_id: string;
  target_type: "character" | "entity" | string;
  target_id: string;
  relation: string;
  description?: string;
  source_segment_ids?: string[];
  status: string;
};
type StoryBible = {
  project_id: string;
  entities: StoryEntity[];
  relationships: StoryRelationship[];
  entity_count: number;
  relationship_count: number;
  job?: Job | null;
  last_run?: {
    provider: string;
    model?: string;
    status: string;
    error?: string | null;
    output?: { fallback?: boolean; warnings?: string[] };
  } | null;
};
type StoryEntityDraft = { description: string; status: string };
type StoryRelationshipDraft = {
  relation: string;
  description: string;
  status: string;
};

type Episode = {
  id: string;
  number: number;
  title: string;
  summary: string;
  conflict?: string;
  hook?: string;
  target_duration_seconds?: number;
  shots: Shot[];
  compositions?: Array<{
    id: string;
    playlist_url?: string;
    final_video_url?: string;
    status: string;
  }>;
  composition_settings?: CompositionSettings;
};

type Project = {
  id: string;
  title: string;
  story: string;
  style: string;
  episode_length?: string;
  source_documents?: Array<{ filename: string; content_sha256: string }>;
  characters?: Character[];
  story_bible?: StoryBible;
  episodes?: Episode[];
  audio_assets?: Asset[];
  adaptation_unit_count?: number;
  character_count?: number;
  episode_count?: number;
  asset_count?: number;
};
type ReadinessStage = {
  key: string;
  label: string;
  status: "ready" | "needs-review" | "pending" | "missing" | string;
  ready: boolean;
  count: number;
  total: number;
  detail: string;
  required: boolean;
  needs_human_review: boolean;
};
type ProjectReadiness = {
  project_id: string;
  stages: ReadinessStage[];
  counts: Record<string, number>;
  ready_for_video: boolean;
  ready_for_composition: boolean;
  ready_for_delivery: boolean;
  needs_human_review: boolean;
};

type DiffPart = { type: "equal" | "delete" | "insert"; text: string };
type SessionInfo = {
  id: string;
  device: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
};
type AdaptationQuality = {
  sequence_ratio?: number;
  ngram_overlap?: number;
  risk?: "not_applicable" | "low" | "medium" | "high" | string;
  requires_human_review?: boolean;
};
type AdaptationRevision = {
  id: string;
  version: number;
  mode: string;
  source_text: string;
  adapted_text: string;
  diff?: DiffPart[];
  provider?: string;
  metadata?: {
    event?: string;
    preview?: boolean;
    model?: string;
    quality?: AdaptationQuality;
  };
  created_at?: string;
};
type AdaptationUnit = {
  id: string;
  source_text: string;
  adapted_text: string;
  mode: string;
  status: string;
  diff?: DiffPart[];
  revisions?: AdaptationRevision[];
  traceability?: { adaptation_quality?: AdaptationQuality };
};
type Job = {
  id: string;
  kind: string;
  target_id: string;
  status: string;
  cost_credits: number;
  attempts: number;
  max_attempts: number;
  progress_percent?: number;
  progress_message?: string;
  error?: string | null;
  provider?: string;
};
type JobEvent = {
  id: string;
  job_id: string;
  from_status?: string | null;
  to_status: string;
  event_type: string;
  message?: string;
  attempt: number;
  created_at: string;
};
type CreditTransaction = {
  id: string;
  kind: string;
  amount: number;
  balance_after: number;
  reason: string;
  created_at: string;
};
type BillingAdjustment = {
  id: string;
  kind: "refund" | "chargeback" | "chargeback_reinstated" | string;
  amount_cents: number;
  currency: string;
  credits_delta: number;
  created_at: string;
};
type CreditPackage = {
  code: string;
  label: string;
  credits: number;
  amount_cents: number;
  currency: string;
};
type BillingSnapshot = {
  provider: string;
  checkout_available?: boolean;
  packages: CreditPackage[];
};
type BillingOrder = {
  id: string;
  package_code: string;
  package_label?: string;
  credits: number;
  amount_cents: number;
  currency: string;
  status: "pending" | "paid" | "cancelled" | string;
  provider_order_id: string;
  created_at: string;
  paid_at?: string | null;
  duplicate?: boolean;
  checkout_required?: boolean;
  checkout_url?: string;
  adjustments?: BillingAdjustment[];
};
type CharacterReferenceBatch = {
  project_id: string;
  status: string;
  requested: number;
  submitted: number;
  skipped: number;
  failed: number;
  required_credits: number;
  items: Array<{
    character_id: string;
    character_name: string;
    action: string;
    status: string;
    error?: string;
  }>;
};
type ImageAssetsBatch = {
  project_id: string;
  status: string;
  requested: number;
  submitted: number;
  skipped: number;
  failed: number;
  required_credits: number;
  items: Array<{
    shot_id: string;
    episode_number: number;
    sequence: number;
    action: string;
    status: string;
    asset?: Asset;
    error?: string;
  }>;
};
type VideoAssetsBatch = {
  project_id: string;
  status: string;
  requested: number;
  submitted: number;
  skipped: number;
  failed: number;
  required_credits: number;
  items: Array<{
    image_asset_id: string;
    shot_id: string;
    action: string;
    status: string;
    asset?: Asset;
    error?: string;
  }>;
};
type CompositionsBatch = {
  project_id: string;
  status: string;
  requested: number;
  submitted: number;
  skipped: number;
  failed: number;
  items: Array<{
    episode_id: string;
    episode_number: number;
    title: string;
    action: string;
    status: string;
    skip_reason?: string;
    error?: string;
  }>;
};
type ProviderPreference = {
  provider: string;
  model: string;
  base_url?: string;
};
type ProviderKind = "text" | "image" | "video" | "vision" | "speech";
type StudioSettings = {
  settingsVersion: number;
  text: ProviderPreference;
  image: ProviderPreference;
  video: ProviderPreference;
  vision: ProviderPreference;
  speech: ProviderPreference;
};
type ProjectDraft = {
  title: string;
  story: string;
  style: string;
  episode_length: string;
};
type ProjectTemplate = {
  genre: string;
  title: string;
  story: string;
  style: string;
};
type CharacterDraft = {
  name: string;
  role: string;
  description: string;
  visual_lock_prompt: string;
};
type EpisodeDraft = {
  title: string;
  summary: string;
  conflict: string;
  hook: string;
  target_duration_seconds: number;
};
type ShotDraft = {
  scene: string;
  emotion: string;
  description: string;
  duration_seconds: number;
};
type PromptDraft = { prompt: string; negative_prompt: string };

const PROJECT_TEMPLATES: ProjectTemplate[] = [
  {
    genre: "都市情感",
    title: "雨夜合约",
    story: "雨夜里，普通骑手意外救下被家族追捕的年轻继承人。两人因一份遗失的合同卷入商业暗战，在一次次追车、谈判和反转中联手揭开真相。",
    style: "国漫写实",
  },
  {
    genre: "悬疑惊悚",
    title: "午夜便利店",
    story: "午夜便利店每晚都会出现一位没有影子的客人。新来的店员沿着遗留的收据追查，发现每张收据都指向一桩尚未结案的失踪案。",
    style: "黑白水墨",
  },
  {
    genre: "古风权谋",
    title: "替嫁医妃",
    story: "被迫替嫁的女医官发现病弱王爷的病并非天命，而是朝堂布局。她以医术换取时间，在宫门与战场之间寻找破局证据。",
    style: "古典工笔",
  },
  {
    genre: "逆袭爽剧",
    title: "废柴的回归",
    story: "人人都以为他只是被嘲笑的赘婿，只有他知道自己曾经守护过整座城市。一次家族危机迫使他重新拾起身份，逐层揭开幕后对手。",
    style: "赛博国风",
  },
];

const defaultSettings: StudioSettings = {
  settingsVersion: 1,
  text: { provider: "local", model: "template" },
  image: { provider: "local", model: "placeholder" },
  video: { provider: "local", model: "placeholder" },
  vision: { provider: "local", model: "manual-review" },
  speech: { provider: "local", model: "deterministic-wave-preview" },
};

const SPEECH_VOICES = [
  { value: "cedar", label: "cedar（推荐）" },
  { value: "marin", label: "marin（推荐）" },
  { value: "alloy", label: "alloy" },
  { value: "ash", label: "ash" },
  { value: "ballad", label: "ballad" },
  { value: "coral", label: "coral" },
  { value: "echo", label: "echo" },
  { value: "fable", label: "fable" },
  { value: "nova", label: "nova" },
  { value: "onyx", label: "onyx" },
  { value: "sage", label: "sage" },
  { value: "shimmer", label: "shimmer" },
  { value: "verse", label: "verse" },
] as const;
const DEFAULT_SPEECH_VOICE_VALUES = SPEECH_VOICES.map((voice) => voice.value);
const SPEECH_SPEEDS = [
  { value: 0.75, label: "0.75×" },
  { value: 1, label: "1×（默认）" },
  { value: 1.25, label: "1.25×" },
  { value: 1.5, label: "1.5×" },
] as const;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm =
    typeof FormData !== "undefined" && init?.body instanceof FormData;
  const method = (init?.method ?? "GET").toUpperCase();
  const csrfToken =
    typeof window !== "undefined"
      ? window.sessionStorage.getItem("studio_csrf")
      : null;
  const headers = new Headers(init?.headers);
  if (!isForm) headers.set("Content-Type", "application/json");
  if (method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${API}${path}`, {
    ...init,
    credentials: "include",
    headers,
  });
  const payload = await response.json();
  if (
    typeof window !== "undefined" &&
    payload &&
    typeof payload.csrf_token === "string"
  ) {
    window.sessionStorage.setItem("studio_csrf", payload.csrf_token);
  }
  if (!response.ok) throw new Error(payload.detail ?? "请求失败");
  return payload as T;
}

export default function StudioPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [readiness, setReadiness] = useState<ProjectReadiness | null>(null);
  const [credits, setCredits] = useState(0);
  const [title, setTitle] = useState("");
  const [story, setStory] = useState("");
  const [newProjectStyle, setNewProjectStyle] = useState("国漫写实");
  const [sourceText, setSourceText] = useState("");
  const [sourceName, setSourceName] = useState("novel.txt");
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [projectBundleFile, setProjectBundleFile] = useState<File | null>(null);
  const [adaptationMode, setAdaptationMode] = useState("originalized");
  const [adaptationUnits, setAdaptationUnits] = useState<AdaptationUnit[]>([]);
  const [showAllAdaptationUnits, setShowAllAdaptationUnits] = useState(false);
  const [copyrightAcknowledged, setCopyrightAcknowledged] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [projectDraft, setProjectDraft] = useState<ProjectDraft>({
    title: "",
    story: "",
    style: "国漫写实",
    episode_length: "1min",
  });
  const [isAuthed, setIsAuthed] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [billingNotice, setBillingNotice] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobEvents, setJobEvents] = useState<Record<string, JobEvent[]>>({});
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [billing, setBilling] = useState<BillingSnapshot>({
    provider: "disabled",
    packages: [],
  });
  const [billingOrders, setBillingOrders] = useState<BillingOrder[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [settings, setSettings] = useState<StudioSettings>(defaultSettings);
  const [subtitleDrafts, setSubtitleDrafts] = useState<Record<string, string>>(
    {},
  );
  const [narrationDrafts, setNarrationDrafts] = useState<Record<string, string>>(
    {},
  );
  const [narrationVoice, setNarrationVoice] = useState("cedar");
  const [narrationSpeed, setNarrationSpeed] = useState(1);
  const [narrationInstructions, setNarrationInstructions] = useState("");
  const [allowedSpeechVoiceValues, setAllowedSpeechVoiceValues] = useState<string[]>(
    DEFAULT_SPEECH_VOICE_VALUES,
  );
  const speechVoiceOptions = useMemo(
    () =>
      allowedSpeechVoiceValues.map((value) => ({
        value,
        label: SPEECH_VOICES.find((voice) => voice.value === value)?.label ?? value,
      })),
    [allowedSpeechVoiceValues],
  );
  const [audioTrackDrafts, setAudioTrackDrafts] = useState<
    Record<string, AudioTrack[]>
  >({});
  const [characterDrafts, setCharacterDrafts] = useState<
    Record<string, CharacterDraft>
  >({});
  const [storyEntityDrafts, setStoryEntityDrafts] = useState<
    Record<string, StoryEntityDraft>
  >({});
  const [storyRelationshipDrafts, setStoryRelationshipDrafts] = useState<
    Record<string, StoryRelationshipDraft>
  >({});
  const [episodeDrafts, setEpisodeDrafts] = useState<
    Record<string, EpisodeDraft>
  >({});
  const [shotDrafts, setShotDrafts] = useState<Record<string, ShotDraft>>({});
  const [promptDrafts, setPromptDrafts] = useState<Record<string, PromptDraft>>(
    {},
  );

  const episodes = project?.episodes ?? [];
  const visibleAdaptationUnits = showAllAdaptationUnits
    ? adaptationUnits
    : adaptationUnits.slice(0, 8);
  const pendingAdaptationCount = adaptationUnits.filter(
    (unit) => unit.status !== "approved",
  ).length;
  const reviewableAdaptationCount = adaptationUnits.filter(
    (unit) => unit.status !== "approved" && unit.status !== "rejected",
  ).length;
  const nextReadinessStage = readiness?.stages.find((item) => !item.ready);
  const shotCount = useMemo(
    () => episodes.reduce((sum, episode) => sum + episode.shots.length, 0),
    [episodes],
  );
  const externalBillingProvider =
    billing.provider === "signed-webhook" || billing.provider === "stripe";

  function billingStatusLabel(status: string) {
    return {
      pending: "待支付",
      paid: "已支付",
      cancelled: "已取消",
    }[status] ?? status;
  }

  function billingAdjustmentLabel(kind: string) {
    return {
      refund: "退款",
      chargeback: "拒付扣回",
      chargeback_reinstated: "拒付资金恢复",
    }[kind] ?? kind;
  }

  function creditTransactionLabel(kind: string) {
    return {
      purchase: "充值入账",
      billing_refund: "退款扣回",
      billing_chargeback: "拒付扣回",
      billing_chargeback_reinstated: "拒付恢复",
      usage_reserved: "制作预扣",
      usage_refunded: "失败退回",
      top_up: "本地充值",
    }[kind] ?? kind;
  }

  function applyProjectTemplate(template: ProjectTemplate) {
    setTitle(template.title);
    setStory(template.story);
    setNewProjectStyle(template.style);
    setError("");
  }

  function storyNodeLabel(type: string, id: string) {
    if (type === "character")
      return project?.characters?.find((character) => character.id === id)?.name ?? id;
    return project?.story_bible?.entities.find((entity) => entity.id === id)?.name ?? id;
  }

  function applyProject(nextProject: Project) {
    const projectChanged = project?.id !== nextProject.id;
    setProject(nextProject);
    if (projectChanged) setShowAllAdaptationUnits(false);
    setProjectDraft({
      title: nextProject.title,
      story: nextProject.story,
      style: nextProject.style,
      episode_length: nextProject.episode_length ?? "1min",
    });
    setCharacterDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const character of nextProject.characters ?? []) {
        if (!(character.id in next))
          next[character.id] = {
            name: character.name,
            role: character.role,
            description: character.description,
            visual_lock_prompt: character.visual_lock?.prompt ?? "",
          };
      }
      return next;
    });
    setStoryEntityDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const entity of nextProject.story_bible?.entities ?? []) {
        if (!(entity.id in next))
          next[entity.id] = {
            description: entity.description,
            status: entity.status,
          };
      }
      return next;
    });
    setStoryRelationshipDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const relationship of nextProject.story_bible?.relationships ?? []) {
        if (!(relationship.id in next))
          next[relationship.id] = {
            relation: relationship.relation,
            description: relationship.description ?? "",
            status: relationship.status,
          };
      }
      return next;
    });
    setEpisodeDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const episode of nextProject.episodes ?? []) {
        if (!(episode.id in next))
          next[episode.id] = {
            title: episode.title,
            summary: episode.summary,
            conflict: episode.conflict ?? "",
            hook: episode.hook ?? "",
            target_duration_seconds: episode.target_duration_seconds ?? 60,
          };
      }
      return next;
    });
    setShotDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const episode of nextProject.episodes ?? [])
        for (const shot of episode.shots) {
          if (!(shot.id in next))
            next[shot.id] = {
              scene: shot.scene,
              emotion: shot.emotion,
              description: shot.description,
              duration_seconds: shot.duration_seconds,
            };
        }
      return next;
    });
    setPromptDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const episode of nextProject.episodes ?? [])
        for (const shot of episode.shots)
          if (shot.image_prompt && !(shot.image_prompt.id in next)) {
            next[shot.image_prompt.id] = {
              prompt: shot.image_prompt.prompt,
              negative_prompt: shot.image_prompt.negative_prompt,
            };
          }
      return next;
    });
    setSubtitleDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const episode of nextProject.episodes ?? []) {
        const serverDraft = (episode.composition_settings?.subtitles ?? [])
          .map(
            (subtitle) =>
              `${subtitle.start_seconds}-${subtitle.end_seconds} | ${subtitle.text}`,
          )
          .join("\n");
        if (!(episode.id in next) || (!next[episode.id].trim() && serverDraft))
          next[episode.id] = serverDraft;
      }
      return next;
    });
    setNarrationDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const episode of nextProject.episodes ?? []) {
        const serverDraft = episode.composition_settings?.narration_text ?? "";
        if (!(episode.id in next) || (!next[episode.id].trim() && serverDraft))
          next[episode.id] = serverDraft;
      }
      return next;
    });
    setAudioTrackDrafts((current) => {
      const next = projectChanged ? {} : { ...current };
      for (const episode of nextProject.episodes ?? []) {
        if (!(episode.id in next))
          next[episode.id] = [
            ...(episode.composition_settings?.audio_tracks ?? []),
          ];
      }
      return next;
    });
  }

  async function refreshOperationalData(): Promise<BillingOrder[]> {
    const [account, jobItems, transactionItems, preference, billingSnapshot, orderItems] = await Promise.all(
      [
        api<{ balance: number }>("/api/credits"),
        api<Job[]>("/api/jobs"),
        api<CreditTransaction[]>("/api/credits/transactions"),
        api<StudioSettings>("/api/studio-settings"),
        api<BillingSnapshot>("/api/billing/packages"),
        api<BillingOrder[]>("/api/billing/orders"),
      ],
    );
    setCredits(account.balance);
    setJobs(jobItems);
    setTransactions(transactionItems);
    setBilling(billingSnapshot);
    setBillingOrders(orderItems);
    setSettings({
      ...defaultSettings,
      ...preference,
      text: { ...defaultSettings.text, ...preference.text },
      image: { ...defaultSettings.image, ...preference.image },
      video: { ...defaultSettings.video, ...preference.video },
      vision: { ...defaultSettings.vision, ...preference.vision },
      speech: { ...defaultSettings.speech, ...preference.speech },
    });
    return orderItems;
  }

  async function refreshSessions() {
    const items = await api<SessionInfo[]>("/api/auth/sessions");
    setSessions(items);
  }

  async function refreshProjects(selectId?: string) {
    const list = await api<Project[]>("/api/projects");
    setProjects(list);
    const id = selectId ?? project?.id ?? list[0]?.id;
    if (id) {
      const [nextProject, units, nextReadiness] = await Promise.all([
        api<Project>(`/api/projects/${id}`),
        api<AdaptationUnit[]>(`/api/projects/${id}/adaptation-units`).catch(
          () => [],
        ),
        api<ProjectReadiness>(`/api/projects/${id}/readiness`),
      ]);
      applyProject(nextProject);
      setAdaptationUnits(units);
      setReadiness(nextReadiness);
    }
  }

  async function refreshProject() {
    if (project) {
      const [nextProject, units, nextReadiness] = await Promise.all([
        api<Project>(`/api/projects/${project.id}`),
        api<AdaptationUnit[]>(
          `/api/projects/${project.id}/adaptation-units`,
        ).catch(() => []),
        api<ProjectReadiness>(`/api/projects/${project.id}/readiness`),
      ]);
      applyProject(nextProject);
      setAdaptationUnits(units);
      setReadiness(nextReadiness);
    }
    await refreshOperationalData();
  }

  async function selectProject(projectId: string) {
    setError("");
    try {
      const [nextProject, units, nextReadiness] = await Promise.all([
        api<Project>(`/api/projects/${projectId}`),
        api<AdaptationUnit[]>(
          `/api/projects/${projectId}/adaptation-units`,
        ).catch(() => []),
        api<ProjectReadiness>(`/api/projects/${projectId}/readiness`),
      ]);
      applyProject(nextProject);
      setAdaptationUnits(units);
      setReadiness(nextReadiness);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目加载失败");
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const billingReturn = params.get("billing");
    const billingOrderId = params.get("order_id");
    const isBillingReturn = billingReturn === "success" || billingReturn === "cancelled";
    let billingTimer: number | undefined;
    let billingPolls = 0;
    const reconcileBillingReturn = async () => {
      billingPolls += 1;
      try {
        const orders = await refreshOperationalData();
        const order = billingOrderId
          ? orders.find((item) => item.id === billingOrderId)
          : undefined;
        const settled = order && order.status !== "pending";
        if (settled || billingPolls >= 6 || billingReturn === "cancelled") {
          if (billingTimer !== undefined) window.clearInterval(billingTimer);
          if (order?.status === "paid") {
            setBillingNotice("支付已由 Webhook 验证，积分已入账。");
          } else if (order?.status === "cancelled") {
            setBillingNotice("支付未完成，订单已取消，积分未入账。");
          } else if (billingReturn === "cancelled") {
            setBillingNotice("支付未完成，订单不会入账。");
          } else {
            setBillingNotice("支付页面已返回，暂未收到 Webhook；订单状态仍以工作台为准。");
          }
        }
      } catch {
        if (billingPolls >= 6 && billingTimer !== undefined) {
          window.clearInterval(billingTimer);
          setBillingNotice("支付页面已返回，但暂时无法刷新订单状态，请稍后查看工作台。");
        }
      }
    };
    if (isBillingReturn) {
      setBillingNotice(
        billingReturn === "success"
          ? "支付页面已返回，正在等待支付 Webhook 验证。"
          : "支付未完成，正在确认订单状态。",
      );
      window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
      void reconcileBillingReturn();
      billingTimer = window.setInterval(() => void reconcileBillingReturn(), 2000);
    }
    api<{ csrf_token?: string | null }>("/api/auth/csrf").catch(
      () => undefined,
    );
    api<{ speech_voices?: string[] }>("/api/health")
      .then((health) => {
        const configured = Array.isArray(health.speech_voices)
          ? health.speech_voices.filter((value) => typeof value === "string" && value.trim())
          : [];
        if (!configured.length) return;
        setAllowedSpeechVoiceValues(configured);
        setNarrationVoice((current) =>
          configured.includes(current) ? current : configured[0],
        );
      })
      .catch(() => undefined);
    api<{ id: string }>("/api/auth/me")
      .then((profile) => {
        const authenticated = profile.id !== "local-user";
        setIsAuthed(authenticated);
        return Promise.all([
          refreshProjects(),
          authenticated ? refreshSessions() : Promise.resolve(),
        ]);
      })
      .catch((reason: Error) => {
        if (!reason.message.includes("authentication required"))
          setError(`API 未启动：${reason.message}`);
      });
    if (!isBillingReturn) refreshOperationalData().catch(() => undefined);
    return () => {
      if (billingTimer !== undefined) window.clearInterval(billingTimer);
    };
  }, []);

  useEffect(() => {
    if (!project?.id) return undefined;
    const projectId = project.id;
    const timer = window.setInterval(() => {
      refreshOperationalData().catch(() => undefined);
      if (expandedJobId) {
        api<JobEvent[]>(`/api/jobs/${expandedJobId}/events`)
          .then((events) =>
            setJobEvents((current) => ({ ...current, [expandedJobId]: events })),
          )
          .catch(() => undefined);
      }
      Promise.all([
        api<Project>(`/api/projects/${projectId}`),
        api<AdaptationUnit[]>(
          `/api/projects/${projectId}/adaptation-units`,
        ).catch(() => []),
        api<ProjectReadiness>(`/api/projects/${projectId}/readiness`),
      ])
        .then(([nextProject, units, nextReadiness]) => {
          applyProject(nextProject);
          setAdaptationUnits(units);
          setReadiness(nextReadiness);
        })
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [expandedJobId, project?.id]);

  async function authenticate(mode: "login" | "register") {
    setBusy(mode === "login" ? "登录账户" : "注册账户");
    setError("");
    try {
      await api<{ token: string }>(`/api/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setIsAuthed(true);
      await refreshSessions();
      await refreshProjects();
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账户操作失败");
    } finally {
      setBusy("");
    }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    window.sessionStorage.removeItem("studio_csrf");
    setIsAuthed(false);
    setSessions([]);
    setProject(null);
    setProjects([]);
    setSubtitleDrafts({});
    setNarrationDrafts({});
    setAudioTrackDrafts({});
  }

  async function logoutAll() {
    setBusy("退出全部设备");
    setError("");
    try {
      await api("/api/auth/logout-all", { method: "POST" });
      window.sessionStorage.removeItem("studio_csrf");
      setIsAuthed(false);
      setSessions([]);
      setProjects([]);
      setProject(null);
      setAdaptationUnits([]);
      setSubtitleDrafts({});
      setNarrationDrafts({});
      setAudioTrackDrafts({});
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出全部设备失败");
    } finally {
      setBusy("");
    }
  }

  async function revokeSession(session: SessionInfo) {
    setBusy(`撤销${session.device}`);
    setError("");
    try {
      await api(`/api/auth/sessions/${encodeURIComponent(session.id)}`, {
        method: "DELETE",
      });
      await refreshSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "撤销设备失败");
    } finally {
      setBusy("");
    }
  }

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await action();
      await refreshProject();
      await refreshProjects(project?.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  async function createProject() {
    if (!title.trim()) return setError("先输入项目标题");
    setBusy("创建项目");
    setError("");
    try {
      const created = await api<Project>("/api/projects", {
        method: "POST",
        headers: { "Idempotency-Key": requestId("project") },
        body: JSON.stringify({ title, story, style: newProjectStyle }),
      });
      setTitle("");
      setStory("");
      await refreshProjects(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy("");
    }
  }

  async function saveProjectSettings() {
    if (!project) return;
    await run("保存项目设置", async () => {
      const updated = await api<Project>(`/api/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify(projectDraft),
      });
      applyProject(updated);
    });
  }

  async function importSource() {
    if (!project || !sourceText.trim()) return setError("请输入小说内容");
    if (!copyrightAcknowledged)
      return setError("请确认你拥有或获授权使用该内容");
    await run("导入小说", async () => {
      await api(`/api/projects/${project.id}/source`, {
        method: "POST",
        headers: { "Idempotency-Key": `source:${project.id}:${requestId()}` },
        body: JSON.stringify({
          filename: sourceName,
          text: sourceText,
          mode: "faithful",
          copyrightAcknowledged,
        }),
      });
      setSourceText("");
    });
  }

  async function uploadSource() {
    if (!project || !sourceFile)
      return setError("请选择 TXT / Markdown / DOCX / EPUB / PDF 文件");
    if (!copyrightAcknowledged)
      return setError("请确认你拥有或获授权使用该内容");
    await run("上传小说文件", async () => {
      const form = new FormData();
      form.append("file", sourceFile);
      form.append("mode", "faithful");
      form.append("copyrightAcknowledged", "true");
      await api(`/api/projects/${project.id}/source-file`, {
        method: "POST",
        headers: { "Idempotency-Key": `source-file:${project.id}:${requestId()}` },
        body: form,
      });
      setSourceFile(null);
    });
  }

  async function rewriteSource() {
    if (!project) return;
    await run("运行改编预览", async () => {
      const result = await api<AdaptationUnit[] | { units: AdaptationUnit[] }>(
        `/api/projects/${project.id}/rewrite`,
        {
          method: "POST",
          headers: { "Idempotency-Key": `rewrite:${project.id}:${adaptationMode}:${requestId()}` },
          body: JSON.stringify({ mode: adaptationMode }),
        },
      );
      setAdaptationUnits(Array.isArray(result) ? result : result.units);
    });
  }

  async function reviewUnit(
    unit: AdaptationUnit,
    status: "approved" | "rejected" = "approved",
  ) {
    await run(status === "approved" ? "通过改编稿" : "驳回改编稿", async () => {
      const updated = await api<AdaptationUnit>(
        `/api/adaptation-units/${unit.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            adapted_text: unit.adapted_text,
            status,
          }),
        },
      );
      setAdaptationUnits((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
    });
  }

  async function saveAdaptationDraft(unit: AdaptationUnit) {
    await run("保存改编草稿", async () => {
      const updated = await api<AdaptationUnit>(
        `/api/adaptation-units/${unit.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            adapted_text: unit.adapted_text,
            status: "draft",
          }),
        },
      );
      setAdaptationUnits((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
    });
  }

  async function bulkReviewAdaptationUnits(status: "approved" | "rejected") {
    if (!project) return;
    const unitIds = adaptationUnits
      .filter((unit) =>
        status === "approved"
          ? unit.status !== "approved"
          : unit.status !== "approved" && unit.status !== "rejected",
      )
      .map((unit) => unit.id);
    if (!unitIds.length) return setError("没有待审核的改编单元");
    await run(status === "approved" ? "批量通过改编稿" : "批量驳回改编稿", async () => {
      const result = await api<{ units: AdaptationUnit[] }>(
        `/api/projects/${project.id}/adaptation-units/review`,
        {
          method: "POST",
          body: JSON.stringify({ unit_ids: unitIds, status }),
        },
      );
      const updated = new Map(result.units.map((unit) => [unit.id, unit]));
      setAdaptationUnits((items) =>
        items.map((item) => updated.get(item.id) ?? item),
      );
    });
  }

  function updateEpisodeDraft(
    episode: Episode,
    changes: Partial<EpisodeDraft>,
  ) {
    setEpisodeDrafts((current) => ({
      ...current,
      [episode.id]: {
        ...{
          title: episode.title,
          summary: episode.summary,
          conflict: episode.conflict ?? "",
          hook: episode.hook ?? "",
          target_duration_seconds: episode.target_duration_seconds ?? 60,
        },
        ...(current[episode.id] ?? {}),
        ...changes,
      },
    }));
  }

  function updateShotDraft(shot: Shot, changes: Partial<ShotDraft>) {
    setShotDrafts((current) => ({
      ...current,
      [shot.id]: {
        ...{
          scene: shot.scene,
          emotion: shot.emotion,
          description: shot.description,
          duration_seconds: shot.duration_seconds,
        },
        ...(current[shot.id] ?? {}),
        ...changes,
      },
    }));
  }

  async function saveCharacter(characterId: string) {
    const draft = characterDrafts[characterId];
    if (!draft) return;
    await run("保存角色卡", async () => {
      const updated = await api<Character>(`/api/characters/${characterId}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: draft.name,
          role: draft.role,
          description: draft.description,
          visual_lock: { prompt: draft.visual_lock_prompt },
        }),
      });
      setCharacterDrafts((current) => ({
        ...current,
        [characterId]: {
          name: updated.name,
          role: updated.role,
          description: updated.description,
          visual_lock_prompt: updated.visual_lock?.prompt ?? "",
        },
      }));
    });
  }

  async function createCharacterReferences() {
    if (!project || !(project.characters ?? []).length) return;
    await run("批量生成三视图", async () => {
      const result = await api<CharacterReferenceBatch>(
        `/api/projects/${project.id}/character-references`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `character-reference-batch:${project.id}:${requestId()}`,
          },
          body: JSON.stringify({
            character_ids: (project.characters ?? []).map(
              (character) => character.id,
            ),
          }),
        },
      );
      if (result.failed > 0)
        setError(`批量三视图部分失败：${result.failed} 个角色需要重试`);
    });
  }

  async function saveEpisode(episodeId: string) {
    const draft = episodeDrafts[episodeId];
    if (!draft) return;
    await run("保存分集大纲", async () => {
      const updated = await api<Episode>(`/api/episodes/${episodeId}`, {
        method: "PATCH",
        body: JSON.stringify(draft),
      });
      setEpisodeDrafts((current) => ({
        ...current,
        [episodeId]: {
          title: updated.title,
          summary: updated.summary,
          conflict: updated.conflict ?? "",
          hook: updated.hook ?? "",
          target_duration_seconds: updated.target_duration_seconds ?? 60,
        },
      }));
    });
  }

  async function saveShot(shotId: string) {
    const draft = shotDrafts[shotId];
    if (!draft) return;
    await run("保存分镜", async () => {
      const updated = await api<Shot>(`/api/shots/${shotId}`, {
        method: "PATCH",
        body: JSON.stringify(draft),
      });
      setShotDrafts((current) => ({
        ...current,
        [shotId]: {
          scene: updated.scene,
          emotion: updated.emotion,
          description: updated.description,
          duration_seconds: updated.duration_seconds,
        },
      }));
    });
  }

  async function generatePrompt(shotId: string) {
    await run("生成图片提示词", async () => {
      const updated = await api<PromptJobResult>(
        `/api/shots/${shotId}/image-prompts`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `prompt:${shotId}:${requestId()}`,
          },
        },
      );
      const promptId = updated.id;
      if (promptId) {
        setPromptDrafts((current) => ({
          ...current,
          [promptId]: {
            prompt: updated.prompt ?? "",
            negative_prompt: updated.negative_prompt ?? "",
          },
        }));
      }
    });
  }

  async function savePrompt(prompt: ImagePrompt) {
    const draft = promptDrafts[prompt.id];
    if (!draft) return;
    await run("保存图片提示词", async () => {
      const updated = await api<ImagePrompt>(
        `/api/image-prompts/${prompt.id}`,
        { method: "PATCH", body: JSON.stringify(draft) },
      );
      setPromptDrafts((current) => ({
        ...current,
        [prompt.id]: {
          prompt: updated.prompt,
          negative_prompt: updated.negative_prompt,
        },
      }));
    });
  }

  async function prepareStructure() {
    if (!project) return;
    await run("准备项目结构", async () => {
      await api<Project>(`/api/projects/${project.id}/structure`, {
        method: "POST",
        headers: { "Idempotency-Key": `structure:${project.id}` },
      });
    });
  }

  async function generateStoryBible() {
    if (!project) return;
    await run("抽取故事资产", async () => {
      await api<StoryBible>(`/api/projects/${project.id}/story-bible`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `story-bible:${project.id}:${requestId()}`,
        },
      });
    });
  }

  async function saveStoryEntity(entity: StoryEntity) {
    const draft = storyEntityDrafts[entity.id];
    if (!draft) return;
    await run("保存故事资产", async () => {
      await api<StoryEntity>(`/api/story-entities/${entity.id}`, {
        method: "PATCH",
        body: JSON.stringify(draft),
      });
    });
  }

  async function saveStoryRelationship(relationship: StoryRelationship) {
    const draft = storyRelationshipDrafts[relationship.id];
    if (!draft) return;
    await run("保存故事关系", async () => {
      await api<StoryRelationship>(
        `/api/story-relationships/${relationship.id}`,
        { method: "PATCH", body: JSON.stringify(draft) },
      );
    });
  }

  async function createImage(shot: Shot) {
    if (!project) return;
    await run(`生成镜头 ${shot.sequence}`, async () => {
      await api(`/api/shots/${shot.id}/image`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `image:${shot.id}:${requestId()}`,
        },
      });
    });
  }

  async function createEpisodeImages(episode: Episode) {
    if (!project) return;
    const shotIds = episode.shots
      .filter(
        (shot) =>
          !shot.assets.some(
            (asset) =>
              asset.kind === "image" &&
              ["ready", "pending", "generating"].includes(asset.status),
          ),
      )
      .map((shot) => shot.id);
    if (!shotIds.length) return setError(`第 ${episode.number} 集没有待生成的关键帧`);
    await run(`批量生成第 ${episode.number} 集关键帧`, async () => {
      const result = await api<ImageAssetsBatch>(
        `/api/projects/${project.id}/image-assets`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `image-batch:${episode.id}:${requestId()}`,
          },
          body: JSON.stringify({ shot_ids: shotIds }),
        },
      );
      if (result.failed > 0)
        setError(`批量关键帧部分失败：${result.failed} 个镜头需要重试`);
    });
  }

  async function createEpisodeVideos(episode: Episode) {
    if (!project) return;
    const assetIds = episode.shots.flatMap((shot) =>
      shot.assets
        .filter(
          (asset) =>
            asset.kind === "image" &&
            asset.status === "ready" &&
            asset.selected &&
            asset.consistency_confirmed,
        )
        .map((asset) => asset.id),
    );
    if (!assetIds.length)
      return setError(`第 ${episode.number} 集没有已确认的关键帧`);
    await run(`批量生成第 ${episode.number} 集视频`, async () => {
      const result = await api<VideoAssetsBatch>(
        `/api/projects/${project.id}/video-assets`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `video-batch:${episode.id}:${requestId()}`,
          },
          body: JSON.stringify({ asset_ids: assetIds }),
        },
      );
      if (result.failed > 0)
        setError(`批量视频部分失败：${result.failed} 个镜头需要重试`);
    });
  }

  async function toggleAsset(asset: Asset) {
    if (!project) return;
    await run("确认资产", async () => {
      await api(`/api/assets/${asset.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          selected: !asset.selected,
          consistency_confirmed: !asset.selected,
        }),
      });
    });
  }

  async function createVideo(asset: Asset) {
    await run("生成视频片段", async () => {
      await api(`/api/assets/${asset.id}/video`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `video:${asset.id}:${requestId()}`,
        },
      });
    });
  }

  async function reviewAsset(asset: Asset) {
    await run("视觉审核", async () => {
      await api(`/api/assets/${asset.id}/review`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `vision-review:${asset.id}:${requestId()}`,
        },
        body: JSON.stringify({ audit_type: "scene" }),
      });
    });
  }

  async function decideAssetReview(asset: Asset, status: "PASS" | "FAIL") {
    await run(status === "PASS" ? "人工通过视觉审核" : "人工驳回视觉审核", async () => {
      await api(`/api/assets/${asset.id}/review/decision`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `manual-review:${asset.id}:${status}:${requestId()}`,
        },
        body: JSON.stringify({
          status,
          issues: status === "FAIL" ? ["人工复核判定不通过"] : [],
        }),
      });
    });
  }

  async function reviewProjectAssets() {
    if (!project) return;
    await run("批量视觉审核", async () => {
      const result = await api<{ failed: number; reviewed: number }>(
        `/api/projects/${project.id}/asset-reviews`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `vision-review-batch:${project.id}:${requestId()}`,
          },
          body: JSON.stringify({ audit_type: "scene" }),
        },
      );
      if (result.failed) {
        setError(`批量视觉审核部分失败：${result.failed} 个素材需要检查`);
      }
    });
  }

  async function attachCharacterReference(asset: Asset, characterId: string) {
    await run("绑定角色母版", async () => {
      await api(`/api/assets/${asset.id}/character-reference`, {
        method: "POST",
        body: JSON.stringify(characterId ? { character_id: characterId } : {}),
      });
    });
  }

  async function createCharacterReference(characterId: string) {
    await run("生成角色三视图", async () => {
      await api(`/api/characters/${characterId}/reference-image`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `character-reference:${characterId}:${requestId()}`,
        },
      });
    });
  }

  async function uploadCharacterReference(
    characterId: string,
    view: "front" | "side" | "back",
    event: ChangeEvent<HTMLInputElement>,
  ) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 16 * 1024 * 1024)
      return setError("角色参考图不能超过 16 MB");
    const labels = { front: "正面", side: "侧面", back: "背面" };
    await run(`上传${labels[view]}参考图`, async () => {
      await api(`/api/characters/${characterId}/reference-upload`, {
        method: "POST",
        body: JSON.stringify({
          view,
          filename: file.name,
          data_base64: base64FromBytes(new Uint8Array(await file.arrayBuffer())),
        }),
      });
    });
  }

  async function compose(episode: Episode) {
    await run("合成播放清单", async () => {
      await api(`/api/episodes/${episode.id}/compose`, {
        method: "POST",
        headers: {
            "Idempotency-Key": `composition:${episode.id}:${requestId()}`,
        },
      });
    });
  }

  async function composeProject() {
    if (!project) return;
    await run("批量合成项目", async () => {
      const result = await api<CompositionsBatch>(
        `/api/projects/${project.id}/compositions`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `composition-batch:${project.id}:${requestId()}`,
          },
        },
      );
      if (result.failed > 0)
        setError(`批量合成部分失败：${result.failed} 个分集需要检查`);
    });
  }

  function base64FromBytes(bytes: Uint8Array) {
    let binary = "";
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
      binary += String.fromCharCode(
        ...bytes.subarray(index, index + chunkSize),
      );
    }
    return window.btoa(binary);
  }

  async function uploadEpisodeAudio(
    episode: Episode,
    event: ChangeEvent<HTMLInputElement>,
  ) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 16 * 1024 * 1024) return setError("音频文件不能超过 16 MB");
    await run("导入分集音频", async () => {
      const asset = await api<Asset>(`/api/episodes/${episode.id}/audio`, {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          data_base64: base64FromBytes(
            new Uint8Array(await file.arrayBuffer()),
          ),
        }),
      });
      const current = episode.composition_settings ?? {
        audio_tracks: [],
        subtitles: [],
      };
      const audio_tracks = [
        ...(audioTrackDrafts[episode.id] ?? current.audio_tracks),
        { asset_id: asset.id, start_seconds: 0, volume: 1 },
      ];
      await api(`/api/episodes/${episode.id}/composition-settings`, {
        method: "PATCH",
        body: JSON.stringify({
          audio_tracks,
          subtitles: current.subtitles,
        }),
      });
      setAudioTrackDrafts((items) => ({
        ...items,
        [episode.id]: audio_tracks,
      }));
    });
  }

  async function generateEpisodeNarration(episode: Episode) {
    await run("生成分集旁白", async () => {
      const asset = await api<Asset>(`/api/episodes/${episode.id}/narration`, {
        method: "POST",
        headers: {
          "Idempotency-Key": `narration:${episode.id}:${requestId()}`,
        },
        body: JSON.stringify({
          text: (narrationDrafts[episode.id] ?? episode.composition_settings?.narration_text ?? "").trim(),
          voice: narrationVoice,
          speed: narrationSpeed,
          instructions: narrationInstructions.trim(),
          attach_to_timeline: true,
          auto_subtitles: true,
        }),
      });
      const current =
        audioTrackDrafts[episode.id] ??
        episode.composition_settings?.audio_tracks ??
        [];
      if (asset.id && !current.some((track) => track.asset_id === asset.id)) {
        setAudioTrackDrafts((items) => ({
          ...items,
          [episode.id]: [
            ...current,
            { asset_id: asset.id, start_seconds: 0, volume: 1 },
          ],
        }));
      }
    });
  }

  async function saveNarrationDraft(episode: Episode) {
    const narration_text = (
      narrationDrafts[episode.id] ??
      episode.composition_settings?.narration_text ??
      ""
    ).trim();
    await run("保存旁白稿", async () => {
      await api(`/api/episodes/${episode.id}/composition-settings`, {
        method: "PATCH",
        body: JSON.stringify({
          audio_tracks:
            audioTrackDrafts[episode.id] ??
            episode.composition_settings?.audio_tracks ??
            [],
          subtitles: episode.composition_settings?.subtitles ?? [],
          narration_text,
        }),
      });
      setNarrationDrafts((items) => ({ ...items, [episode.id]: narration_text }));
    });
  }

  function updateAudioTrack(
    episodeId: string,
    index: number,
    changes: Partial<AudioTrack>,
  ) {
    setAudioTrackDrafts((current) => ({
      ...current,
      [episodeId]: (current[episodeId] ?? []).map((track, trackIndex) =>
        trackIndex === index ? { ...track, ...changes } : track,
      ),
    }));
  }

  function removeAudioTrack(episodeId: string, index: number) {
    setAudioTrackDrafts((current) => ({
      ...current,
      [episodeId]: (current[episodeId] ?? []).filter(
        (_, trackIndex) => trackIndex !== index,
      ),
    }));
  }

  async function saveEpisodeTimeline(episode: Episode) {
    const lines = (subtitleDrafts[episode.id] ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const subtitles: Subtitle[] = [];
    for (const line of lines) {
      const match = line.match(
        /^([0-9]+(?:\.[0-9]+)?)\s*-\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*(.+)$/,
      );
      if (!match || Number(match[2]) <= Number(match[1])) {
        setError(`字幕格式错误：${line}；请使用“开始-结束 | 文本”`);
        return;
      }
      subtitles.push({
        start_seconds: Number(match[1]),
        end_seconds: Number(match[2]),
        text: match[3].trim(),
      });
    }
    const audio_tracks =
      audioTrackDrafts[episode.id] ??
      episode.composition_settings?.audio_tracks ??
      [];
    if (
      audio_tracks.some(
        (track) =>
          !Number.isFinite(track.start_seconds) ||
          track.start_seconds < 0 ||
          track.start_seconds > 900 ||
          !Number.isFinite(track.volume) ||
          track.volume < 0 ||
          track.volume > 2,
      )
    ) {
      setError("音轨参数错误：起点需在 0-900 秒，音量需在 0-2");
      return;
    }
    await run("保存音频字幕时间线", async () => {
      await api(`/api/episodes/${episode.id}/composition-settings`, {
        method: "PATCH",
        body: JSON.stringify({ audio_tracks, subtitles }),
      });
    });
  }

  async function retryJob(job: Job) {
    await run("重试任务", async () => {
      await api(`/api/jobs/${job.id}/retry`, { method: "POST" });
    });
  }

  async function cancelJob(job: Job) {
    await run("取消任务", async () => {
      await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
    });
  }

  async function toggleJobEvents(job: Job) {
    if (expandedJobId === job.id) {
      setExpandedJobId(null);
      return;
    }
    setExpandedJobId(job.id);
    if (jobEvents[job.id]) return;
    try {
      const events = await api<JobEvent[]>(`/api/jobs/${job.id}/events`);
      setJobEvents((current) => ({ ...current, [job.id]: events }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务时间线加载失败");
    }
  }

  async function saveSettings() {
    setBusy("保存 Provider 偏好");
    setError("");
    try {
      const updated = await api<StudioSettings>("/api/studio-settings", {
        method: "PATCH",
        body: JSON.stringify(settings),
      });
      setSettings({
        ...defaultSettings,
        ...updated,
        text: { ...defaultSettings.text, ...updated.text },
        image: { ...defaultSettings.image, ...updated.image },
        video: { ...defaultSettings.video, ...updated.video },
        vision: { ...defaultSettings.vision, ...updated.vision },
        speech: { ...defaultSettings.speech, ...updated.speech },
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置保存失败");
    } finally {
      setBusy("");
    }
  }

  async function localTopUp() {
    setBusy("本地充值");
    setError("");
    try {
      await api("/api/credits/top-up", {
        method: "POST",
        body: JSON.stringify({
          amount: 20,
          reason: "local development top-up",
        }),
      });
      await refreshOperationalData();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "充值失败");
    } finally {
      setBusy("");
    }
  }

  async function createBillingOrder(packageCode: string) {
    const idempotencyKey = requestId(`billing-${packageCode}`);
    setBusy("创建充值订单");
    setError("");
    try {
      const order = await api<BillingOrder>("/api/billing/orders", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ package_code: packageCode }),
      });
      setBillingOrders((current) => [
        order,
        ...current.filter((item) => item.id !== order.id),
      ]);
      setError(
        order.status === "pending" && order.checkout_url
          ? billing.provider === "stripe"
            ? "订单已创建；请打开 Stripe Checkout 完成付款，Webhook 回调后自动入账。"
            : "订单已创建；请打开支付入口完成付款，签名回调后自动入账。"
          : order.status === "pending"
            ? "订单已创建；当前未配置 checkout 入口，完成外部支付后签名回调会自动入账。"
          : "订单状态已更新。",
      );
      await refreshOperationalData();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "充值订单创建失败");
    } finally {
      setBusy("");
    }
  }

  async function exportProject() {
    if (!project) return;
    setBusy("导出项目");
    setError("");
    try {
      const bundle = await api<Record<string, unknown>>(
        `/api/projects/${project.id}/export`,
      );
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(bundle, null, 2)], {
          type: "application/json",
        }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${project.title || project.id}-project-export.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setBusy("");
    }
  }

  async function downloadProjectArchive() {
    if (!project) return;
    setBusy("下载项目包");
    setError("");
    try {
      const response = await fetch(`${API}/api/projects/${project.id}/archive`, {
        credentials: "include",
        headers: { Accept: "application/zip" },
      });
      if (!response.ok) {
        let message = "项目包下载失败";
        try {
          const payload = (await response.json()) as { detail?: string };
          message = payload.detail ?? message;
        } catch {
          // Non-JSON proxy errors keep the stable fallback message.
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${(project.title || project.id).replace(/[\\/:*?"<>|]+/g, "_")}-project-bundle.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目包下载失败");
    } finally {
      setBusy("");
    }
  }

  async function importProjectBundle() {
    if (!projectBundleFile) return setError("请选择 project-export.json 或项目 ZIP");
    setBusy("导入项目交换包");
    setError("");
    try {
      const isArchive = projectBundleFile.name.toLowerCase().endsWith(".zip");
      let imported: Project;
      if (isArchive) {
        const csrfToken =
          typeof window !== "undefined"
            ? window.sessionStorage.getItem("studio_csrf")
            : null;
        const headers = new Headers({
          Accept: "application/json",
          "Content-Type": "application/zip",
        });
        if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
        const response = await fetch(`${API}/api/projects/import-archive`, {
          method: "POST",
          body: projectBundleFile,
          credentials: "include",
          headers,
        });
        const payload = (await response.json()) as Project & { detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? "项目归档导入失败");
        imported = payload;
      } else {
        const bundle = JSON.parse(await projectBundleFile.text()) as Record<
          string,
          unknown
        >;
        imported = await api<Project>("/api/projects/import", {
          method: "POST",
          body: JSON.stringify(bundle),
        });
      }
      setProjectBundleFile(null);
      await refreshProjects(imported.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目交换包导入失败");
    } finally {
      setBusy("");
    }
  }

  function nextActionForStage(stageKey: string) {
    switch (stageKey) {
      case "source":
      case "adaptation":
        return { label: "打开小说改编入口", target: "source-panel" };
      case "story-bible":
        return { label: "抽取故事资产", run: () => void generateStoryBible() };
      case "structure":
        return { label: "一键准备项目结构", run: () => void prepareStructure() };
      case "references":
        return { label: "批量生成角色三视图", run: () => void createCharacterReferences() };
      case "prompts":
      case "keyframes":
      case "visual-review":
      case "videos":
        return { label: "打开分镜素材画布", target: "shots-panel" };
      case "composition":
        return { label: "批量合成项目", run: () => void composeProject() };
      default:
        return { label: "查看制作状态", target: "readiness-panel" };
    }
  }

  function executeNextAction(action: ReturnType<typeof nextActionForStage>) {
    if (action.run) {
      action.run();
      return;
    }
    if (action.target) {
      document.getElementById(action.target)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">✦</span>
          <span>漫剧工作台</span>
        </div>
        <div className="side-label">项目</div>
        <div className="project-list">
          {projects.map((item) => (
            <button
              className={`project-item ${item.id === project?.id ? "active" : ""}`}
              key={item.id}
              onClick={() => void selectProject(item.id)}
            >
              <span>{item.title}</span>
              <small>{item.asset_count ?? 0} 素材</small>
            </button>
          ))}
          {!projects.length && <div className="empty-side">还没有项目</div>}
        </div>
        <div className="credit-card">
          <span>本地积分</span>
          <strong>{credits}</strong>
          <small>真实 provider 接入前使用本地预览</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">AI MANHUA STUDIO</span>
            <h1>{project?.title ?? "从小说开始制作一部漫剧"}</h1>
          </div>
          <div className="top-actions">
            {project && (
              <button
                className="ghost-button"
                disabled={!!busy}
                onClick={exportProject}
              >
                导出交换包
              </button>
            )}
            {project && (
              <button
                className="secondary-button"
                disabled={!!busy}
                onClick={() => void downloadProjectArchive()}
              >
                下载项目包
              </button>
            )}
            {isAuthed && (
              <button className="ghost-button" onClick={() => void logout()}>
                退出账户
              </button>
            )}
            {project && (
              <Link className="ghost-button" href={`/board/${project.id}`}>
                打开关系画布 ↗
              </Link>
            )}
          </div>
        </header>
        {billingNotice && <div className="alert" role="status">{billingNotice}</div>}
        {error && <div className="alert">{error}</div>}
        {!project ? (
          <section className="welcome-grid">
            <div className="hero-card">
              <span className="eyebrow">CREATE YOUR STORY</span>
              <h2>把文字变成可审核的视觉流水线。</h2>
              <p>
                小说导入、改编追溯、角色母版、分镜、关键帧和成片，都在同一个项目里留下证据。
              </p>
            </div>
            <div className="new-project panel">
              <h3>新建项目</h3>
              <div className="template-picker">
                <div>
                  <span className="eyebrow">QUICK START</span>
                  <p className="muted">选择一个题材模板，仍可在创建前继续修改。</p>
                </div>
                <div className="template-grid">
                  {PROJECT_TEMPLATES.map((template) => (
                    <button
                      className="template-card"
                      key={template.title}
                      type="button"
                      onClick={() => applyProjectTemplate(template)}
                    >
                      <strong>{template.genre}</strong>
                      <span>{template.title}</span>
                    </button>
                  ))}
                </div>
              </div>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="项目标题，例如：雨夜合约"
              />
              <label className="new-project-style">
                <span>视觉风格</span>
                <select
                  value={newProjectStyle}
                  onChange={(event) => setNewProjectStyle(event.target.value)}
                >
                  <option>国漫写实</option>
                  <option>黑白水墨</option>
                  <option>赛博国风</option>
                  <option>古典工笔</option>
                </select>
              </label>
              <textarea
                value={story}
                onChange={(event) => setStory(event.target.value)}
                placeholder="先写一段故事梗概，也可以创建后再导入小说"
              />
              <button
                className="primary-button"
                disabled={!!busy}
                onClick={createProject}
              >
                {busy || "创建项目"}
              </button>
              <div className="import-actions">
                <label className="file-field">
                  导入项目交换包
                  <input
                    type="file"
                    accept="application/json,.json,application/zip,.zip"
                    onChange={(event) =>
                      setProjectBundleFile(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
                <button
                  className="secondary-button compact"
                  disabled={!!busy || !projectBundleFile}
                  onClick={importProjectBundle}
                >
                  {projectBundleFile?.name.toLowerCase().endsWith(".zip")
                    ? "导入 ZIP"
                    : "导入 JSON"}
                </button>
              </div>
              <div className="auth-divider">
                <span>可选：登录账户后启用项目隔离</span>
              </div>
              <form
                className="auth-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void authenticate("login");
                }}
              >
                <input
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="邮箱"
                  type="email"
                  autoComplete="email"
                  required
                />
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="密码（至少 8 位）"
                  type="password"
                  autoComplete="current-password"
                  minLength={8}
                  required
                />
                <div className="auth-actions">
                  <button
                    type="submit"
                    className="secondary-button compact"
                    disabled={!!busy}
                  >
                    登录
                  </button>
                  <button
                    type="button"
                    className="ghost-button compact"
                    disabled={!!busy}
                    onClick={() => void authenticate("register")}
                  >
                    注册
                  </button>
                </div>
              </form>
            </div>
          </section>
        ) : (
          <>
            <section className="stats-row">
              <div>
                <span>改编单元</span>
                <strong>{project.adaptation_unit_count ?? 0}</strong>
              </div>
              <div>
                <span>角色</span>
                <strong>
                  {project.characters?.length ?? project.character_count ?? 0}
                </strong>
              </div>
              <div>
                <span>镜头</span>
                <strong>{shotCount}</strong>
              </div>
              <div>
                <span>素材</span>
                <strong>{project.asset_count ?? 0}</strong>
              </div>
              <button
                className="primary-button compact"
                disabled={!!busy}
                onClick={prepareStructure}
              >
                {busy || "一键准备项目结构"}
              </button>
              <button
                className="secondary-button compact"
                disabled={!!busy || !(project.episodes?.length ?? 0)}
                onClick={() => void composeProject()}
              >
                {busy === "批量合成项目" ? busy : "批量合成项目"}
              </button>
              <button
                className="ghost-button compact"
                disabled={!!busy || !(project.asset_count ?? 0)}
                onClick={() => void reviewProjectAssets()}
              >
                {busy === "批量视觉审核" ? busy : "批量视觉审核"}
              </button>
            </section>
            {readiness && (
              <section id="readiness-panel" className="panel readiness-panel" data-testid="project-readiness">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">PRODUCTION GATE</span>
                    <h2>制作就绪度</h2>
                  </div>
                  <div className="readiness-summary">
                    <span className={`status-pill ${readiness.ready_for_delivery ? "ready" : ""}`}>
                      {readiness.ready_for_delivery ? "可交付" : readiness.ready_for_composition ? "可合成" : readiness.ready_for_video ? "可出视频" : "继续准备"}
                    </span>
                    {readiness.needs_human_review && <span className="muted">含人工复核节点</span>}
                  </div>
                </div>
                <div className="readiness-grid">
                  {readiness.stages.map((item) => (
                    <div className={`readiness-item readiness-${item.status}`} key={item.key} data-readiness-stage={item.key}>
                      <div>
                        <strong>{item.label}</strong>
                        <span>{item.detail}</span>
                      </div>
                      <b>{item.status === "ready" ? "就绪" : item.status === "needs-review" ? "待人工复核" : item.status === "pending" ? "处理中" : "待准备"}</b>
                    </div>
                  ))}
                </div>
                <small className="muted">门禁由服务端项目数据计算；页面轮询、刷新或更换设备后仍保持一致。</small>
              </section>
            )}
            {readiness && (
              <section className="panel next-action-panel" data-testid="next-action">
                <div>
                  <span className="eyebrow">NEXT ACTION</span>
                  <h2>{nextReadinessStage ? `当前阶段：${nextReadinessStage.label}` : "项目已具备交付条件"}</h2>
                  <p className="muted">
                    {nextReadinessStage?.detail ?? "所有必需制作阶段已完成，可以下载项目包或打开最终成片。"}
                  </p>
                </div>
                {nextReadinessStage ? (
                  <button
                    className="primary-button compact"
                    disabled={!!busy}
                    onClick={() => executeNextAction(nextActionForStage(nextReadinessStage.key))}
                  >
                    {nextActionForStage(nextReadinessStage.key).label}
                  </button>
                ) : (
                  <button className="selected-button compact" onClick={downloadProjectArchive} disabled={!!busy}>
                    下载项目交付包
                  </button>
                )}
              </section>
            )}
            <section className="panel project-settings-panel">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">PROJECT SETTINGS</span>
                  <h2>项目设置</h2>
                </div>
                <span className="muted">保存后影响后续结构生成</span>
              </div>
              <div className="project-settings-grid">
                <label>
                  <span>标题</span>
                  <input
                    value={projectDraft.title}
                    onChange={(event) =>
                      setProjectDraft((draft) => ({
                        ...draft,
                        title: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  <span>视觉风格</span>
                  <select
                    value={projectDraft.style}
                    onChange={(event) =>
                      setProjectDraft((draft) => ({
                        ...draft,
                        style: event.target.value,
                      }))
                    }
                  >
                    <option>国漫写实</option>
                    <option>黑白水墨</option>
                    <option>赛博国风</option>
                    <option>古典工笔</option>
                  </select>
                </label>
                <label>
                  <span>目标时长</span>
                  <select
                    value={projectDraft.episode_length}
                    onChange={(event) =>
                      setProjectDraft((draft) => ({
                        ...draft,
                        episode_length: event.target.value,
                      }))
                    }
                  >
                    <option value="30s">30 秒</option>
                    <option value="1min">1 分钟</option>
                    <option value="3min">3 分钟</option>
                    <option value="5min">5 分钟</option>
                  </select>
                </label>
                <label className="project-story-field">
                  <span>故事梗概</span>
                  <textarea
                    value={projectDraft.story}
                    onChange={(event) =>
                      setProjectDraft((draft) => ({
                        ...draft,
                        story: event.target.value,
                      }))
                    }
                  />
                </label>
                <button
                  className="secondary-button compact project-save-button"
                  disabled={!!busy}
                  onClick={() => void saveProjectSettings()}
                >
                  {busy === "保存项目设置" ? busy : "保存项目设置"}
                </button>
              </div>
            </section>
            <section className="operations-grid">
              <div className="panel operation-panel">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">JOB CONTROL</span>
                    <h2>任务中心</h2>
                  </div>
                  <span className="muted">每 5 秒刷新</span>
                </div>
                {jobs.length === 0 ? (
                  <p className="muted">还没有生成任务。</p>
                ) : (
                  <div className="job-list">
                    {jobs.slice(0, 8).map((job) => (
                      <div className="job-row" key={job.id} data-job-id={job.id} data-job-kind={job.kind}>
                        <div>
                          <strong>
                            {job.kind === "image"
                              ? "关键帧"
                              : job.kind === "video"
                                ? "视频片段"
                                : job.kind === "character-reference"
                                  ? "角色三视图"
                              : job.kind === "compose"
                                    ? "合成"
                                    : job.kind === "story-bible"
                                      ? "故事资产"
                                      : job.kind === "structure"
                                        ? "项目结构"
                                      : job.kind === "vision-review"
                                        ? "视觉审核"
                                      : job.kind === "prompt"
                                        ? "图片提示词"
                                      : job.kind}
                          </strong>
                          <span>
                            {job.status} · {job.attempts}/{job.max_attempts} 次
                            · {job.cost_credits} 积分
                          </span>
                          <div className="job-progress" aria-label={`任务进度 ${job.progress_percent ?? 0}%`}>
                            <div
                              className="job-progress-track"
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={job.progress_percent ?? 0}
                              style={{ width: `${Math.max(0, Math.min(100, job.progress_percent ?? 0))}%` }}
                            />
                            <small>
                              {job.progress_percent ?? 0}% · {job.progress_message || "等待处理"}
                            </small>
                          </div>
                          {job.error && <small>{job.error}</small>}
                        </div>
                        <div className="job-actions">
                          <button
                            className="ghost-button compact"
                            disabled={!!busy}
                            onClick={() => void toggleJobEvents(job)}
                          >
                            {expandedJobId === job.id ? "收起时间线" : "时间线"}
                          </button>
                          {job.status === "failed" && (
                            <button
                              className="secondary-button compact"
                              disabled={!!busy}
                              onClick={() => retryJob(job)}
                            >
                              重试
                            </button>
                          )}
                          {job.status === "queued" && (
                            <button
                              className="ghost-button compact"
                              disabled={!!busy}
                              onClick={() => cancelJob(job)}
                            >
                              取消
                            </button>
                          )}
                        </div>
                        {expandedJobId === job.id && (
                          <div className="job-timeline">
                            {(jobEvents[job.id] ?? []).length === 0 ? (
                              <span className="muted">暂无事件</span>
                            ) : (
                              jobEvents[job.id].map((event) => (
                                <div className="job-event" key={event.id}>
                                  <strong>
                                    {event.from_status
                                      ? `${event.from_status} → `
                                      : ""}
                                    {event.to_status}
                                  </strong>
                                  <span>
                                    {event.event_type} · 第 {event.attempt} 次
                                  </span>
                                  {event.message && (
                                    <small>{event.message}</small>
                                  )}
                                  <time>{event.created_at}</time>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="panel operation-panel">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">ACCOUNT & PROVIDERS</span>
                    <h2>积分与 Provider</h2>
                  </div>
                  <div>
                    <strong className={`balance-number ${credits < 0 ? "charge" : ""}`}>
                      {credits}
                    </strong>
                    {credits < 0 ? (
                      <small className="muted balance-warning">退款/拒付已超过当前余额，暂不能继续消费</small>
                    ) : null}
                  </div>
                </div>
                <div className="account-actions">
                  {externalBillingProvider ? (
                    billing.packages.map((item) => (
                      <button
                        className="secondary-button compact"
                        disabled={!!busy || billing.checkout_available === false}
                        key={item.code}
                        onClick={() => void createBillingOrder(item.code)}
                      >
                        {item.label} · {item.credits} 积分 · ¥
                        {(item.amount_cents / 100).toFixed(2)}
                      </button>
                    ))
                  ) : (
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={localTopUp}
                    >
                      本地开发 +20
                    </button>
                  )}
                  <span className="muted">
                    {billing.provider === "stripe"
                      ? billing.checkout_available === false
                        ? "Stripe Checkout 尚未完成部署配置"
                        : "跳转 Stripe Checkout；Webhook 回调后自动入账"
                      : billing.provider === "signed-webhook"
                        ? "订单需由外部支付适配器完成；签名回调后自动入账"
                      : "本地预览模式；生产充值订单尚未启用"}
                  </span>
                </div>
                {billingOrders.length > 0 && (
                  <div className="transaction-list">
                    {billingOrders.slice(0, 3).map((order) => (
                      <div className="transaction-row" key={order.id}>
                        <span>{order.package_label ?? order.package_code}</span>
                        <b>{billingStatusLabel(order.status)}</b>
                        <small>
                          {order.credits} 积分 · ¥
                          {(order.amount_cents / 100).toFixed(2)} · {order.id}
                          {order.checkout_url ? (
                            <>
                              {" · "}
                              <a href={order.checkout_url} target="_blank" rel="noreferrer">
                                打开支付
                              </a>
                            </>
                          ) : null}
                          {order.adjustments?.map((adjustment) => (
                            <span className="billing-adjustment" key={adjustment.id}>
                              {billingAdjustmentLabel(adjustment.kind)} {adjustment.credits_delta > 0 ? "+" : ""}
                              {adjustment.credits_delta} 积分
                            </span>
                          ))}
                        </small>
                      </div>
                    ))}
                  </div>
                )}
                <div className="transaction-list">
                  {transactions.slice(0, 4).map((item) => (
                    <div className="transaction-row" key={item.id}>
                      <span>{creditTransactionLabel(item.kind)}</span>
                      <b className={item.amount < 0 ? "charge" : "refund"}>
                        {item.amount > 0 ? "+" : ""}
                        {item.amount}
                      </b>
                      <small>{item.reason}</small>
                    </div>
                  ))}
                </div>
                <details className="settings-box">
                  <summary>编辑非敏感 Provider 偏好</summary>
                  <div className="provider-form">
                    {(
                      [
                        "text",
                        "image",
                        "video",
                        "vision",
                        "speech",
                      ] as const satisfies readonly ProviderKind[]
                    ).map((kind) => (
                      <label key={kind}>
                        <span>
                          {kind === "text"
                            ? "文本"
                            : kind === "image"
                              ? "图片"
                            : kind === "video"
                              ? "视频"
                              : kind === "vision"
                                ? "视觉"
                                : "语音"}
                        </span>
                        <input
                          value={settings[kind].provider}
                          onChange={(event) =>
                            setSettings((current) => ({
                              ...current,
                              [kind]: {
                                ...current[kind],
                                provider: event.target.value,
                              },
                            }))
                          }
                          placeholder="provider"
                        />
                        <input
                          value={settings[kind].model}
                          onChange={(event) =>
                            setSettings((current) => ({
                              ...current,
                              [kind]: {
                                ...current[kind],
                                model: event.target.value,
                              },
                            }))
                          }
                          placeholder="model"
                        />
                        <input
                          value={settings[kind].base_url ?? ""}
                          onChange={(event) =>
                            setSettings((current) => ({
                              ...current,
                              [kind]: {
                                ...current[kind],
                                base_url: event.target.value,
                              },
                            }))
                          }
                          placeholder="base_url（可选）"
                        />
                      </label>
                    ))}
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={saveSettings}
                    >
                      保存偏好
                    </button>
                    <small className="muted">
                      API key、Cookie 和 secret 不会写入设置；base_url 仅接受
                      http(s)。
                    </small>
                  </div>
                </details>
              </div>
            </section>
            {isAuthed && (
              <section className="panel security-panel">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">SECURITY</span>
                    <h2>账户安全</h2>
                  </div>
                  <span className="muted">服务端撤销会话</span>
                </div>
                <div className="security-summary">
                  <p className="muted">
                    设备列表只显示短标签和活跃时间，不保存原始 User-Agent、IP 或认证信息。
                  </p>
                  <button
                    className="ghost-button compact"
                    disabled={!!busy}
                    onClick={() => void logoutAll()}
                  >
                    退出全部设备
                  </button>
                </div>
                <div className="session-list">
                  {sessions.length === 0 && (
                    <p className="muted">当前没有可展示的登录设备。</p>
                  )}
                  {sessions.map((session) => (
                    <div className="session-row" key={session.id}>
                      <div>
                        <strong>{session.device}</strong>
                        {session.current && <span className="status-pill">当前设备</span>}
                        <small>
                          最近活跃：{new Date(session.last_seen_at).toLocaleString("zh-CN")} ·
                          创建于：{new Date(session.created_at).toLocaleString("zh-CN")}
                        </small>
                      </div>
                      <button
                        className="ghost-button compact"
                        disabled={!!busy || session.current}
                        onClick={() => void revokeSession(session)}
                      >
                        {session.current ? "当前设备" : "撤销设备"}
                      </button>
                    </div>
                  ))}
                </div>
              </section>
            )}
            <section id="source-panel" className="panel import-panel">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">SOURCE & ADAPTATION</span>
                  <h2>小说改编入口</h2>
                </div>
                <div className="top-actions">
                  <span className="status-pill">
                    可追溯模式 · {adaptationUnits.length} 条
                  </span>
                  {adaptationUnits.length > 8 && (
                    <button
                      className="ghost-button compact"
                      disabled={!!busy}
                      onClick={() => setShowAllAdaptationUnits((value) => !value)}
                    >
                      {showAllAdaptationUnits ? "收起改编单元" : "显示全部改编单元"}
                    </button>
                  )}
                  {pendingAdaptationCount > 0 && (
                    <>
                      <button
                        className="selected-button compact"
                        disabled={!!busy}
                        onClick={() => void bulkReviewAdaptationUnits("approved")}
                      >
                        批量通过 {pendingAdaptationCount} 条
                      </button>
                      {reviewableAdaptationCount > 0 && (
                        <button
                          className="ghost-button compact"
                          disabled={!!busy}
                          onClick={() => void bulkReviewAdaptationUnits("rejected")}
                        >
                          批量驳回 {reviewableAdaptationCount} 条
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>
              <div className="import-grid">
                <input
                  value={sourceName}
                  onChange={(event) => setSourceName(event.target.value)}
                  placeholder="文件名"
                />
                <textarea
                  value={sourceText}
                  onChange={(event) => setSourceText(event.target.value)}
                  placeholder="粘贴 TXT / Markdown 内容；每个句子会保留来源字符区间和行号"
                />
                <label className="file-field">
                  上传文件
                  <input
                    type="file"
                    accept=".txt,.md,.markdown,.docx,.epub,.pdf"
                    onChange={(event) =>
                      setSourceFile(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
                <label className="checkline">
                  <input
                    type="checkbox"
                    checked={copyrightAcknowledged}
                    onChange={(event) =>
                      setCopyrightAcknowledged(event.target.checked)
                    }
                  />
                  我确认拥有或获授权使用此内容
                </label>
                <div className="import-actions">
                  <button
                    className="secondary-button"
                    disabled={!!busy}
                    onClick={importSource}
                  >
                    {busy === "导入小说" ? busy : "导入粘贴内容"}
                  </button>
                  <button
                    className="primary-button"
                    disabled={!!busy || !sourceFile}
                    onClick={uploadSource}
                  >
                    {busy === "上传小说文件" ? busy : "上传并解析文件"}
                  </button>
                </div>
                <div className="rewrite-row">
                  <select
                    value={adaptationMode}
                    onChange={(event) => setAdaptationMode(event.target.value)}
                  >
                    <option value="faithful">保真</option>
                    <option value="condensed">压缩</option>
                    <option value="originalized">原创化表达</option>
                  </select>
                  <button
                    className="secondary-button"
                    disabled={!!busy || !project?.adaptation_unit_count}
                    onClick={rewriteSource}
                  >
                    {busy === "运行改编预览" ? busy : "运行改编预览"}
                  </button>
                </div>
              </div>
              {adaptationUnits.length > 0 && (
                <div className="adaptation-table">
                  {visibleAdaptationUnits.map((unit) => (
                    <div className="adaptation-row" key={unit.id}>
                      <span>{unit.source_text}</span>
                      <b>→</b>
                      <div>
                        <input
                          value={unit.adapted_text}
                          onChange={(event) =>
                            setAdaptationUnits((items) =>
                              items.map((item) =>
                                item.id === unit.id
                                  ? {
                                      ...item,
                                      adapted_text: event.target.value,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                        <div
                          className="adaptation-diff"
                          aria-label="原文与改编稿差异"
                        >
                          {(unit.diff ?? []).map((part, index) => (
                            <span
                              className={`diff-${part.type}`}
                              key={`${unit.id}-diff-${index}`}
                            >
                              {part.text}
                            </span>
                          ))}
                        </div>
                        {unit.traceability?.adaptation_quality &&
                          unit.traceability.adaptation_quality.risk !==
                            "not_applicable" && (
                            <div
                              className={
                                "adaptation-quality risk-" +
                                (unit.traceability.adaptation_quality.risk ??
                                  "medium")
                              }
                              title="启发式相似度提示，不是法律意见或抄袭结论"
                            >
                              相似度提示{" "}
                              {Math.round(
                                (unit.traceability.adaptation_quality
                                  .sequence_ratio ?? 0) * 100,
                              )}
                              % ·{" "}
                              {unit.traceability.adaptation_quality.risk ===
                              "high"
                                ? "高风险"
                                : unit.traceability.adaptation_quality.risk ===
                                    "medium"
                                  ? "中风险"
                                  : "低风险"}{" "}
                              · 请人工复核
                            </div>
                          )}
                        {(unit.revisions?.length ?? 0) > 0 && (
                          <details className="revision-history">
                            <summary>
                              版本历史 · {unit.revisions?.length} 个版本
                            </summary>
                            <div className="revision-list">
                              {unit.revisions?.map((revision) => (
                                <div
                                  className="revision-item"
                                  key={revision.id}
                                >
                                  <div>
                                    <strong>v{revision.version}</strong>
                                    <span>
                                      {revision.metadata?.event ===
                                      "manual_edit"
                                        ? "人工编辑"
                                        : revision.metadata?.event ===
                                            "source_import"
                                          ? "导入初稿"
                                          : "模型重写"}{" "}
                                      · {revision.provider ?? "local"}
                                    </span>
                                  </div>
                                  <p>{revision.adapted_text}</p>
                                </div>
                              ))}
                            </div>
                          </details>
                        )}
                      </div>
                      <small>{unit.status}</small>
                      {unit.status !== "approved" && (
                        <div className="adaptation-actions">
                          <button
                            className="ghost-button compact"
                            disabled={!!busy}
                            onClick={() => void saveAdaptationDraft(unit)}
                          >
                            保存草稿
                          </button>
                          <button
                            className="selected-button compact"
                            disabled={!!busy}
                            onClick={() => void reviewUnit(unit)}
                          >
                            通过
                          </button>
                          {unit.status !== "rejected" && (
                            <button
                              className="ghost-button compact"
                              disabled={!!busy}
                              onClick={() => void reviewUnit(unit, "rejected")}
                            >
                              驳回
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
            {project.story_bible && (
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">STORY BIBLE · TRACEABLE ASSETS</span>
                    <h2>故事资产与关系</h2>
                  </div>
                  <div className="top-actions">
                    <span className="muted">
                      {project.story_bible.entity_count} 个地点/道具 · {project.story_bible.relationship_count} 条关系
                    </span>
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={() => void generateStoryBible()}
                    >
                      {busy === "抽取故事资产" ? busy : "重新抽取故事资产"}
                    </button>
                  </div>
                </div>
                <p className="muted">
                  抽取结果默认是草稿，来源段落 ID 会随结果保存；通过审核后才作为稳定设定参与关系画布和后续制作。
                </p>
                {project.story_bible.last_run?.output?.fallback && (
                  <div className="notice">
                    当前是本地预览文本 Provider，未调用外部模型；可配置真实文本 Provider 后重新抽取。
                  </div>
                )}
                {(project.story_bible.entities.length > 0 || project.story_bible.relationships.length > 0) ? (
                  <div className="story-bible-grid">
                    <div>
                      <h3>地点与道具</h3>
                      {project.story_bible.entities.map((entity) => (
                        <article className="story-entity-card" key={entity.id}>
                          <div className="section-heading">
                            <strong>{entity.name}</strong>
                            <small>{entity.kind === "location" ? "地点" : "道具"} · {entity.status}</small>
                          </div>
                          <p>{entity.description || "待补充设定"}</p>
                          <small className="muted">来源段落 {entity.source_segment_ids?.length ?? 0} 个</small>
                          <details className="editor-details">
                            <summary>审核/编辑资产</summary>
                            <div className="editor-grid">
                              <label className="editor-wide">
                                <span>设定描述</span>
                                <textarea
                                  value={storyEntityDrafts[entity.id]?.description ?? entity.description}
                                  onChange={(event) =>
                                    setStoryEntityDrafts((current) => ({
                                      ...current,
                                      [entity.id]: {
                                        description: event.target.value,
                                        status: current[entity.id]?.status ?? entity.status,
                                      },
                                    }))
                                  }
                                />
                              </label>
                              <label>
                                <span>审核状态</span>
                                <select
                                  value={storyEntityDrafts[entity.id]?.status ?? entity.status}
                                  onChange={(event) =>
                                    setStoryEntityDrafts((current) => ({
                                      ...current,
                                      [entity.id]: {
                                        description: current[entity.id]?.description ?? entity.description,
                                        status: event.target.value,
                                      },
                                    }))
                                  }
                                >
                                  <option value="draft">草稿</option>
                                  <option value="approved">通过</option>
                                  <option value="rejected">驳回</option>
                                </select>
                              </label>
                              <button
                                className="secondary-button compact"
                                disabled={!!busy}
                                onClick={() => void saveStoryEntity(entity)}
                              >
                                保存资产审核
                              </button>
                            </div>
                          </details>
                        </article>
                      ))}
                    </div>
                    <div>
                      <h3>关系</h3>
                      {project.story_bible.relationships.length === 0 && <p className="muted">暂未抽取到关系。</p>}
                      {project.story_bible.relationships.map((relationship) => (
                        <article className="story-entity-card" key={relationship.id}>
                          <strong>
                            {storyNodeLabel(relationship.source_type, relationship.source_id)} → {relationship.relation} → {storyNodeLabel(relationship.target_type, relationship.target_id)}
                          </strong>
                          {relationship.description && <p>{relationship.description}</p>}
                          <small className="muted">{relationship.status} · 来源段落 {relationship.source_segment_ids?.length ?? 0} 个</small>
                          <details className="editor-details">
                            <summary>审核/编辑关系</summary>
                            <div className="editor-grid">
                              <label>
                                <span>关系标签</span>
                                <input
                                  value={storyRelationshipDrafts[relationship.id]?.relation ?? relationship.relation}
                                  onChange={(event) =>
                                    setStoryRelationshipDrafts((current) => ({
                                      ...current,
                                      [relationship.id]: {
                                        relation: event.target.value,
                                        description: current[relationship.id]?.description ?? relationship.description ?? "",
                                        status: current[relationship.id]?.status ?? relationship.status,
                                      },
                                    }))
                                  }
                                />
                              </label>
                              <label>
                                <span>审核状态</span>
                                <select
                                  value={storyRelationshipDrafts[relationship.id]?.status ?? relationship.status}
                                  onChange={(event) =>
                                    setStoryRelationshipDrafts((current) => ({
                                      ...current,
                                      [relationship.id]: {
                                        relation: current[relationship.id]?.relation ?? relationship.relation,
                                        description: current[relationship.id]?.description ?? relationship.description ?? "",
                                        status: event.target.value,
                                      },
                                    }))
                                  }
                                >
                                  <option value="draft">草稿</option>
                                  <option value="approved">通过</option>
                                  <option value="rejected">驳回</option>
                                </select>
                              </label>
                              <label className="editor-wide">
                                <span>关系说明</span>
                                <textarea
                                  value={storyRelationshipDrafts[relationship.id]?.description ?? relationship.description ?? ""}
                                  onChange={(event) =>
                                    setStoryRelationshipDrafts((current) => ({
                                      ...current,
                                      [relationship.id]: {
                                        relation: current[relationship.id]?.relation ?? relationship.relation,
                                        description: event.target.value,
                                        status: current[relationship.id]?.status ?? relationship.status,
                                      },
                                    }))
                                  }
                                />
                              </label>
                              <button
                                className="secondary-button compact"
                                disabled={!!busy}
                                onClick={() => void saveStoryRelationship(relationship)}
                              >
                                保存关系审核
                              </button>
                            </div>
                          </details>
                        </article>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">导入小说后点击“重新抽取故事资产”，生成地点、道具和关系草稿。</div>
                )}
              </section>
            )}
            {project.characters && project.characters.length > 0 && (
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">CHARACTER LOCKS</span>
                    <h2>角色母版</h2>
                  </div>
                  <div className="top-actions">
                    <span className="muted">
                      {project.characters.length} 个角色 · 三视图 8 积分/个
                    </span>
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={() => void createCharacterReferences()}
                    >
                      {busy === "批量生成三视图" ? busy : "批量生成三视图"}
                    </button>
                  </div>
                </div>
                <div className="character-grid">
                  {project.characters.map((character) => {
                    const reference = character.references?.[0];
                    const ready = reference?.status === "ready";
                    const draft = characterDrafts[character.id];
                    return (
                      <article className="character-card" key={character.id}>
                        <div className="avatar-placeholder">
                          {character.name.slice(0, 1)}
                        </div>
                        <div className="card-content">
                          <strong>{character.name}</strong>
                          <span>
                            {character.role} · {character.status ?? "draft"}
                          </span>
                          <p>{character.description}</p>
                          <details className="editor-details">
                            <summary>编辑角色卡</summary>
                            <div className="editor-grid">
                              <label>
                                <span>名称</span>
                                <input
                                  value={draft?.name ?? character.name}
                                  onChange={(event) =>
                                    setCharacterDrafts((current) => ({
                                      ...current,
                                      [character.id]: {
                                        ...(current[character.id] ?? {
                                          name: character.name,
                                          role: character.role,
                                          description: character.description,
                                          visual_lock_prompt:
                                            character.visual_lock?.prompt ?? "",
                                        }),
                                        name: event.target.value,
                                      },
                                    }))
                                  }
                                />
                              </label>
                              <label>
                                <span>角色类型</span>
                                <select
                                  value={draft?.role ?? character.role}
                                  onChange={(event) =>
                                    setCharacterDrafts((current) => ({
                                      ...current,
                                      [character.id]: {
                                        ...(current[character.id] ?? {
                                          name: character.name,
                                          role: character.role,
                                          description: character.description,
                                          visual_lock_prompt:
                                            character.visual_lock?.prompt ?? "",
                                        }),
                                        role: event.target.value,
                                      },
                                    }))
                                  }
                                >
                                  <option value="protagonist">主角</option>
                                  <option value="supporting">配角</option>
                                  <option value="antagonist">对手</option>
                                </select>
                              </label>
                              <label className="editor-wide">
                                <span>视觉设定</span>
                                <textarea
                                  value={
                                    draft?.description ?? character.description
                                  }
                                  onChange={(event) =>
                                    setCharacterDrafts((current) => ({
                                      ...current,
                                      [character.id]: {
                                        ...(current[character.id] ?? {
                                          name: character.name,
                                          role: character.role,
                                          description: character.description,
                                          visual_lock_prompt:
                                            character.visual_lock?.prompt ?? "",
                                        }),
                                        description: event.target.value,
                                      },
                                    }))
                                  }
                                />
                              </label>
                              <label className="editor-wide">
                                <span>视觉锁定提示</span>
                                <textarea
                                  value={
                                    draft?.visual_lock_prompt ??
                                    character.visual_lock?.prompt ??
                                    ""
                                  }
                                  onChange={(event) =>
                                    setCharacterDrafts((current) => ({
                                      ...current,
                                      [character.id]: {
                                        ...(current[character.id] ?? {
                                          name: character.name,
                                          role: character.role,
                                          description: character.description,
                                          visual_lock_prompt:
                                            character.visual_lock?.prompt ?? "",
                                        }),
                                        visual_lock_prompt: event.target.value,
                                      },
                                    }))
                                  }
                                />
                              </label>
                              <button
                                className="secondary-button compact"
                                disabled={!!busy}
                                onClick={() => void saveCharacter(character.id)}
                              >
                                保存角色卡
                              </button>
                            </div>
                          </details>
                          {ready ? (
                            <div className="reference-strip">
                              {(
                                ["front_url", "side_url", "back_url"] as const
                              ).map((key) => (
                                <img
                                  key={key}
                                  src={mediaUrl(reference?.[key])}
                                  alt={`${character.name} ${key}`}
                                />
                              ))}
                            </div>
                          ) : (
                            <>
                              <button
                                className="secondary-button compact"
                                disabled={
                                  !!busy || reference?.status === "generating"
                                }
                                onClick={() =>
                                  createCharacterReference(character.id)
                                }
                              >
                                {reference?.status === "generating"
                                  ? "生成中…"
                                  : reference?.status === "failed"
                                    ? "重试三视图"
                                    : "生成三视图"}
                              </button>
                              {reference?.status && (
                                <small className="muted">
                                  状态：{reference.status}
                                </small>
                              )}
                            </>
                          )}
                          <div className="reference-upload-grid" aria-label={`${character.name} 用户参考图`}>
                            {(["front", "side", "back"] as const).map((view) => (
                              <label className="reference-upload" key={view}>
                                <span>
                                  {view === "front" ? "上传正面" : view === "side" ? "上传侧面" : "上传背面"}
                                </span>
                                <input
                                  type="file"
                                  accept="image/png,image/jpeg,image/webp"
                                  disabled={!!busy}
                                  onChange={(event) => void uploadCharacterReference(character.id, view, event)}
                                />
                              </label>
                            ))}
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            )}
            {episodes.map((episode) => (
              <section id={episode.id === episodes[0]?.id ? "shots-panel" : undefined} className="panel episode-panel" key={episode.id}>
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">
                      EPISODE {String(episode.number).padStart(2, "0")}
                    </span>
                    <h2>{episode.title}</h2>
                  </div>
                  <div className="top-actions">
                    {episode.compositions?.[0]?.final_video_url && (
                      <a
                        className="selected-button compact"
                        href={mediaUrl(episode.compositions[0].final_video_url)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开成片 ↗
                      </a>
                    )}
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={() => void createEpisodeImages(episode)}
                    >
                      批量生成关键帧
                    </button>
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={() => void createEpisodeVideos(episode)}
                    >
                      批量生成视频
                    </button>
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={() => compose(episode)}
                    >
                      合成清单
                    </button>
                  </div>
                </div>
                <p className="summary">{episode.summary}</p>
                <details className="editor-details episode-editor">
                  <summary>编辑分集大纲</summary>
                  <div className="editor-grid">
                    <label>
                      <span>标题</span>
                      <input
                        value={
                          episodeDrafts[episode.id]?.title ?? episode.title
                        }
                        onChange={(event) =>
                          updateEpisodeDraft(episode, {
                            title: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      <span>目标时长（秒）</span>
                      <input
                        type="number"
                        min="15"
                        max="900"
                        value={
                          episodeDrafts[episode.id]?.target_duration_seconds ??
                          episode.target_duration_seconds ??
                          60
                        }
                        onChange={(event) =>
                          updateEpisodeDraft(episode, {
                            target_duration_seconds: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="editor-wide">
                      <span>摘要</span>
                      <textarea
                        value={
                          episodeDrafts[episode.id]?.summary ?? episode.summary
                        }
                        onChange={(event) =>
                          updateEpisodeDraft(episode, {
                            summary: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      <span>核心冲突</span>
                      <textarea
                        value={
                          episodeDrafts[episode.id]?.conflict ??
                          episode.conflict ??
                          ""
                        }
                        onChange={(event) =>
                          updateEpisodeDraft(episode, {
                            conflict: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      <span>结尾钩子</span>
                      <textarea
                        value={
                          episodeDrafts[episode.id]?.hook ?? episode.hook ?? ""
                        }
                        onChange={(event) =>
                          updateEpisodeDraft(episode, {
                            hook: event.target.value,
                          })
                        }
                      />
                    </label>
                    <button
                      className="secondary-button compact"
                      disabled={!!busy}
                      onClick={() => void saveEpisode(episode.id)}
                    >
                      保存分集大纲
                    </button>
                  </div>
                </details>
                <div className="timeline-editor">
                  <div className="timeline-heading">
                    <div>
                      <strong>声音与字幕时间线</strong>
                      <span>
                        Remotion 合成时生效；音频上传后默认从 0 秒开始
                      </span>
                    </div>
                    <label className="secondary-button compact upload-audio">
                      上传音频
                      <input
                        type="file"
                        accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.webm"
                        disabled={!!busy}
                        onChange={(event) =>
                          void uploadEpisodeAudio(episode, event)
                        }
                      />
                    </label>
                    <button
                      className="secondary-button compact"
                      data-narration-button={episode.id}
                      disabled={!!busy}
                      onClick={() => void generateEpisodeNarration(episode)}
                    >
                      生成旁白
                    </button>
                    <label className="muted narration-voice-control">
                      旁白音色
                      <select
                        aria-label="旁白音色"
                        value={narrationVoice}
                        disabled={!!busy}
                        onChange={(event) => setNarrationVoice(event.target.value)}
                      >
                        {speechVoiceOptions.map((voice) => (
                          <option key={voice.value} value={voice.value}>
                            {voice.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="muted narration-voice-control">
                      旁白语速
                      <select
                        aria-label="旁白语速"
                        value={String(narrationSpeed)}
                        disabled={!!busy}
                        onChange={(event) =>
                          setNarrationSpeed(Number(event.target.value))
                        }
                      >
                        {SPEECH_SPEEDS.map((speed) => (
                          <option key={speed.value} value={speed.value}>
                            {speed.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="muted narration-voice-control narration-instructions-control">
                      表达指令
                      <input
                        type="text"
                        aria-label="旁白表达指令"
                        value={narrationInstructions}
                        maxLength={1000}
                        disabled={!!busy}
                        placeholder="如：沉稳、低声叙述"
                        onChange={(event) =>
                          setNarrationInstructions(event.target.value)
                        }
                      />
                    </label>
                    <span className="muted" data-narration-disclosure="true">
                      内置音色；可调整语速和表达指令；长旁白会自动分段合并并估算字幕；本地预览为确定性音频，真实 TTS 为 AI 生成语音
                    </span>
                  </div>
                  <div className="narration-script-editor">
                    <label>
                      <span>分集旁白稿</span>
                      <textarea
                        aria-label="分集旁白稿"
                        maxLength={48000}
                        value={
                          narrationDrafts[episode.id] ??
                          episode.composition_settings?.narration_text ??
                          ""
                        }
                        placeholder="留空则使用镜头描述/分集摘要"
                        disabled={!!busy}
                        onChange={(event) =>
                          setNarrationDrafts((current) => ({
                            ...current,
                            [episode.id]: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <div className="narration-script-actions">
                      <button
                        className="secondary-button compact"
                        data-narration-script-save={episode.id}
                        disabled={!!busy}
                        onClick={() => void saveNarrationDraft(episode)}
                      >
                        保存旁白稿
                      </button>
                      <span className="muted">
                        保存后刷新仍保留；生成旁白会使用当前稿件
                      </span>
                    </div>
                  </div>
                  <div className="timeline-summary">
                    <span>
                      音轨{" "}
                      {
                        (
                          audioTrackDrafts[episode.id] ??
                          episode.composition_settings?.audio_tracks ??
                          []
                        ).length
                      }
                    </span>
                    <span>
                      字幕 {episode.composition_settings?.subtitles.length ?? 0}
                    </span>
                    <span>
                      {(
                        audioTrackDrafts[episode.id] ??
                        episode.composition_settings?.audio_tracks ??
                        []
                      ).length || episode.composition_settings?.subtitles.length
                        ? "合成引擎需设为 remotion"
                        : "未配置附加轨道"}
                    </span>
                  </div>
                  <div className="audio-track-list">
                    {(
                      audioTrackDrafts[episode.id] ??
                      episode.composition_settings?.audio_tracks ??
                      []
                    ).length === 0 ? (
                      <span className="muted">
                        上传音频后可调整起点和音量。
                      </span>
                    ) : (
                      (
                        audioTrackDrafts[episode.id] ??
                        episode.composition_settings?.audio_tracks ??
                        []
                      ).map((track, index) => (
                        <div
                          className="audio-track-row"
                          key={`${episode.id}-${track.asset_id}-${index}`}
                        >
                          <span className="audio-track-name">
                            音轨 {index + 1}
                            <small>…{track.asset_id.slice(-8)}</small>
                          </span>
                          {audioAssetFor(project, track.asset_id)?.url ? (
                            <audio
                              className="audio-track-preview"
                              controls
                              preload="metadata"
                              src={mediaUrl(audioAssetFor(project, track.asset_id)?.url)}
                              aria-label={`试听音轨 ${index + 1}`}
                            />
                          ) : (
                            <small className="muted">音频处理中</small>
                          )}
                          <label>
                            <span>起点 (秒)</span>
                            <input
                              type="number"
                              min="0"
                              max="900"
                              step="0.1"
                              value={track.start_seconds}
                              onChange={(event) =>
                                updateAudioTrack(episode.id, index, {
                                  start_seconds: Number(event.target.value),
                                })
                              }
                            />
                          </label>
                          <label>
                            <span>音量</span>
                            <input
                              type="number"
                              min="0"
                              max="2"
                              step="0.1"
                              value={track.volume}
                              onChange={(event) =>
                                updateAudioTrack(episode.id, index, {
                                  volume: Number(event.target.value),
                                })
                              }
                            />
                          </label>
                          <button
                            className="ghost-button compact"
                            disabled={!!busy}
                            onClick={() => removeAudioTrack(episode.id, index)}
                          >
                            删除
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                  <textarea
                    value={subtitleDrafts[episode.id] ?? ""}
                    onChange={(event) =>
                      setSubtitleDrafts((current) => ({
                        ...current,
                        [episode.id]: event.target.value,
                      }))
                    }
                    placeholder="每行一条字幕，例如：0-3 | 她推开门。"
                  />
                  <button
                    className="ghost-button compact"
                    disabled={!!busy}
                    onClick={() => void saveEpisodeTimeline(episode)}
                  >
                    保存声音与字幕时间线
                  </button>
                </div>
                <div className="shot-grid">
                  {episode.shots.map((shot) => (
                    <article className="shot-card" key={shot.id}>
                      <div className="shot-meta">
                        <span>
                          镜头 {String(shot.sequence).padStart(2, "0")}
                        </span>
                        <small>{shot.emotion}</small>
                      </div>
                      {previewAsset(shot) ? (
                        <img
                          className="preview"
                          src={mediaUrl(previewAsset(shot)?.url)}
                          alt={shot.description}
                        />
                      ) : (
                        <div className="preview empty-preview">
                          <span>尚未生成关键帧</span>
                        </div>
                      )}
                      <p>{shot.description}</p>
                      {imageCandidates(shot).length > 0 && (
                        <div className="candidate-strip" aria-label="关键帧候选">
                          {imageCandidates(shot).map((asset, index) => (
                            <div
                              className={
                                asset.selected
                                  ? "candidate-thumb selected-candidate"
                                  : "candidate-thumb"
                              }
                              key={`${asset.id}-candidate`}
                              title={
                                asset.selected
                                  ? "当前采用的关键帧"
                                  : `关键帧候选 ${index + 1}`
                              }
                            >
                              {asset.url ? (
                                <img src={mediaUrl(asset.url)} alt={`关键帧候选 ${index + 1}`} />
                              ) : (
                                <span>{asset.status}</span>
                              )}
                              <small>
                                {asset.selected ? "已采用" : `候选 ${index + 1}`}
                              </small>
                            </div>
                          ))}
                        </div>
                      )}
                      <details className="editor-details">
                        <summary>编辑分镜</summary>
                        <div className="editor-grid">
                          <label>
                            <span>场景</span>
                            <input
                              value={shotDrafts[shot.id]?.scene ?? shot.scene}
                              onChange={(event) =>
                                updateShotDraft(shot, {
                                  scene: event.target.value,
                                })
                              }
                            />
                          </label>
                          <label>
                            <span>情绪</span>
                            <input
                              value={
                                shotDrafts[shot.id]?.emotion ?? shot.emotion
                              }
                              onChange={(event) =>
                                updateShotDraft(shot, {
                                  emotion: event.target.value,
                                })
                              }
                            />
                          </label>
                          <label>
                            <span>时长（秒）</span>
                            <input
                              type="number"
                              min="1"
                              max="30"
                              step="0.1"
                              value={
                                shotDrafts[shot.id]?.duration_seconds ??
                                shot.duration_seconds
                              }
                              onChange={(event) =>
                                updateShotDraft(shot, {
                                  duration_seconds: Number(event.target.value),
                                })
                              }
                            />
                          </label>
                          <label className="editor-wide">
                            <span>镜头描述</span>
                            <textarea
                              value={
                                shotDrafts[shot.id]?.description ??
                                shot.description
                              }
                              onChange={(event) =>
                                updateShotDraft(shot, {
                                  description: event.target.value,
                                })
                              }
                            />
                          </label>
                          <button
                            className="secondary-button compact"
                            disabled={!!busy}
                            onClick={() => void saveShot(shot.id)}
                          >
                            保存分镜
                          </button>
                        </div>
                      </details>
                      {shot.image_prompt ? (
                        <details className="editor-details">
                          <summary>编辑图片提示词</summary>
                          <div className="editor-grid">
                            <label className="editor-wide">
                              <span>正面提示词</span>
                              <textarea
                                value={
                                  promptDrafts[shot.image_prompt.id]?.prompt ??
                                  shot.image_prompt.prompt
                                }
                                onChange={(event) =>
                                  setPromptDrafts((current) => ({
                                    ...current,
                                    [shot.image_prompt!.id]: {
                                      prompt: event.target.value,
                                      negative_prompt:
                                        current[shot.image_prompt!.id]
                                          ?.negative_prompt ??
                                        shot.image_prompt!.negative_prompt,
                                    },
                                  }))
                                }
                              />
                            </label>
                            <label className="editor-wide">
                              <span>负面提示词</span>
                              <textarea
                                value={
                                  promptDrafts[shot.image_prompt.id]
                                    ?.negative_prompt ??
                                  shot.image_prompt.negative_prompt
                                }
                                onChange={(event) =>
                                  setPromptDrafts((current) => ({
                                    ...current,
                                    [shot.image_prompt!.id]: {
                                      prompt:
                                        current[shot.image_prompt!.id]
                                          ?.prompt ?? shot.image_prompt!.prompt,
                                      negative_prompt: event.target.value,
                                    },
                                  }))
                                }
                              />
                            </label>
                            <button
                              className="secondary-button compact"
                              disabled={!!busy}
                              onClick={() =>
                                void savePrompt(shot.image_prompt!)
                              }
                            >
                              保存提示词
                            </button>
                            <button
                              className="ghost-button compact"
                              disabled={!!busy}
                              onClick={() => void generatePrompt(shot.id)}
                            >
                              按当前分镜重生成
                            </button>
                          </div>
                        </details>
                      ) : (
                        <button
                          className="ghost-button compact"
                          disabled={!!busy}
                          onClick={() => void generatePrompt(shot.id)}
                        >
                          生成图片提示词
                        </button>
                      )}
                      <div className="shot-actions">
                        <button
                          className="secondary-button compact"
                          disabled={!!busy}
                          onClick={() => createImage(shot)}
                        >
                          生成预览
                        </button>
                        {imageCandidates(shot).map((asset) => (
                            <Fragment key={asset.id}>
                              <button
                                className={
                                  asset.selected
                                    ? "selected-button compact"
                                    : "ghost-button compact"
                                }
                                onClick={() => toggleAsset(asset)}
                              >
                                {asset.selected ? "已采用✓" : "采用关键帧"}
                              </button>
                              <button
                                className="ghost-button compact"
                                onClick={() => reviewAsset(asset)}
                              >
                                视觉审核
                              </button>
                              <label className="reference-picker">
                                <span>角色母版</span>
                                <select
                                  value={asset.character_id ?? ""}
                                  disabled={!!busy}
                                  onChange={(event) =>
                                    void attachCharacterReference(
                                      asset,
                                      event.target.value,
                                    )
                                  }
                                >
                                  <option value="">未绑定</option>
                                  {(project.characters ?? []).map(
                                    (character) => (
                                      <option
                                        value={character.id}
                                        key={character.id}
                                      >
                                        {character.name}
                                      </option>
                                    ),
                                  )}
                                </select>
                              </label>
                            </Fragment>
                          ))}
                        {shot.assets
                          .filter(
                            (asset) =>
                              asset.kind === "image" &&
                              asset.selected &&
                              asset.consistency_confirmed,
                          )
                          .map((asset) => (
                            <button
                              key={`${asset.id}-video`}
                              className="secondary-button compact"
                              onClick={() => createVideo(asset)}
                            >
                              生成视频
                            </button>
                          ))}
                      </div>
                      {shot.assets
                        .filter(
                          (asset) =>
                            asset.kind === "image" && asset.reviews?.[0],
                        )
                        .map((asset) => (
                          <div
                            className="asset-status"
                            key={`${asset.id}-review-status`}
                          >
                            视觉审核：{asset.reviews?.[0]?.status}
                            {asset.reviews?.[0]?.issues?.[0]
                              ? ` · ${asset.reviews[0].issues[0]}`
                              : ""}
                            {asset.reviews?.[0]?.status === "UNKNOWN" && (
                              <span className="review-decision-actions">
                                <button
                                  className="selected-button compact"
                                  disabled={!!busy}
                                  onClick={() => void decideAssetReview(asset, "PASS")}
                                >
                                  人工通过
                                </button>
                                <button
                                  className="ghost-button compact"
                                  disabled={!!busy}
                                  onClick={() => void decideAssetReview(asset, "FAIL")}
                                >
                                  人工驳回
                                </button>
                              </span>
                            )}
                          </div>
                        ))}
                      {shot.assets
                        .filter((asset) => asset.kind === "video")
                        .map((asset) => (
                          <div className="asset-status" key={asset.id}>
                            视频片段：{asset.status}{" "}
                            {asset.metadata?.mode === "local-video-plan"
                              ? "（本地计划）"
                              : ""}
                          </div>
                        ))}
                    </article>
                  ))}
                </div>
              </section>
            ))}
            {!episodes.length && (
              <div className="panel empty-state">
                <h2>下一步：准备项目结构</h2>
                <p>
                  先导入小说，或点击“一键准备项目结构”生成本地可编辑的角色、分集和分镜骨架。
                </p>
              </div>
            )}
          </>
        )}
      </section>
    </main>
  );
}
