import { Redis } from 'ioredis';
import { Worker } from 'bullmq';
import { QUEUE_NAME, type GenerationPayload } from './contracts.js';
import { readBoundedResponseText, ResponseBodyTooLargeError } from './http.js';

const redisUrl = process.env.REDIS_URL ?? 'redis://127.0.0.1:6379';
const studioApiUrl = (process.env.STUDIO_API_URL ?? 'http://127.0.0.1:8787').replace(/\/$/, '');
const internalToken = process.env.STUDIO_INTERNAL_TOKEN;
if (!internalToken) throw new Error('STUDIO_INTERNAL_TOKEN is required for the worker');

const MAX_API_RESPONSE_BYTES = 64 * 1024;
const MAX_ERROR_RESPONSE_BYTES = 4 * 1024;

async function discardErrorResponse(response: Response): Promise<void> {
  try {
    await readBoundedResponseText(response, MAX_ERROR_RESPONSE_BYTES);
  } catch {
    // Never expose or retain an untrusted API error body in the worker log.
  }
}

async function readApiResponse(response: Response): Promise<{ status?: string; job?: { status?: string; error?: string } }> {
  if (!response.ok) {
    await discardErrorResponse(response);
    throw new Error(`studio API returned ${response.status}`);
  }
  let raw: string;
  try {
    raw = await readBoundedResponseText(response, MAX_API_RESPONSE_BYTES);
  } catch (error) {
    if (error instanceof ResponseBodyTooLargeError) {
      throw new Error('studio API response exceeded the worker size limit');
    }
    throw new Error('studio API response could not be read');
  }
  try {
    return JSON.parse(raw) as { status?: string; job?: { status?: string; error?: string } };
  } catch {
    throw new Error('studio API returned invalid JSON');
  }
}

const redis = new Redis(redisUrl, { maxRetriesPerRequest: null });
const worker = new Worker<GenerationPayload>(
  QUEUE_NAME,
  async (job) => {
    const response = await fetch(`${studioApiUrl}/api/internal/jobs/${job.data.jobId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Studio-Internal-Token': internalToken },
      body: JSON.stringify(job.data),
    });
    const result = await readApiResponse(response);
    const jobStatus = result.job?.status;
    if (jobStatus === 'queued' || jobStatus === 'running') {
      throw new Error(`studio API left job ${job.data.jobId} in ${jobStatus}; BullMQ will retry`);
    }
    if (result.status === 'failed' || result.job?.status === 'failed') {
      const retry = await fetch(`${studioApiUrl}/api/internal/jobs/${job.data.jobId}/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Studio-Internal-Token': internalToken },
      });
      if (!retry.ok) {
        await discardErrorResponse(retry);
        throw new Error(`provider failed and retry reservation was rejected: ${retry.status}`);
      }
      await readApiResponse(retry);
      throw new Error('studio API marked job failed; BullMQ will retry');
    }
    if (jobStatus && !['completed', 'cancelled'].includes(jobStatus)) {
      throw new Error(`studio API returned unexpected job status ${jobStatus}`);
    }
    return result;
  },
  { connection: redis, concurrency: Number(process.env.STUDIO_WORKER_CONCURRENCY ?? 2) },
);

worker.on('completed', (job) => console.log(`completed ${job.id}`));
worker.on('failed', (job, error) => console.error(`failed ${job?.id ?? 'unknown'}: ${error.message}`));
