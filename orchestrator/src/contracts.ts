export const QUEUE_NAME = process.env.STUDIO_QUEUE_NAME ?? 'manhua-generation';

export type GenerationPayload = {
  jobId: string;
  userId: string;
  kind: 'image' | 'video' | 'text' | 'prompt' | 'compose' | 'character-reference' | 'story-bible' | 'structure' | 'vision-review' | 'narration';
  targetId: string;
};

export class GenerationRequestError extends Error {
  constructor(message: string, readonly statusCode = 400) {
    super(message);
    this.name = 'GenerationRequestError';
  }
}

const allowedKinds = new Set<GenerationPayload['kind']>(['image', 'video', 'text', 'prompt', 'compose', 'character-reference', 'story-bible', 'structure', 'vision-review', 'narration']);

export function parseGenerationPayload(value: unknown): GenerationPayload {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new GenerationRequestError('request body must be a JSON object');
  }
  const payload = value as Record<string, unknown>;
  const jobId = typeof payload.jobId === 'string' ? payload.jobId.trim() : '';
  const userId = typeof payload.userId === 'string' ? payload.userId.trim() : '';
  const targetId = typeof payload.targetId === 'string' ? payload.targetId.trim() : '';
  const kind = typeof payload.kind === 'string' ? payload.kind.trim() : '';
  if (!jobId || !userId || !targetId || !kind) {
    throw new GenerationRequestError('jobId, userId, kind and targetId are required');
  }
  if (jobId.length > 128 || userId.length > 128 || targetId.length > 256) {
    throw new GenerationRequestError('job payload field is too long');
  }
  if (!allowedKinds.has(kind as GenerationPayload['kind'])) {
    throw new GenerationRequestError('unsupported job kind');
  }
  return { jobId, userId, kind: kind as GenerationPayload['kind'], targetId };
}
