import { createServer } from 'node:http';
import { Queue } from 'bullmq';
import { Redis } from 'ioredis';
import { assertOrchestratorAuthConfig, isAuthorized, orchestratorAuthRequired } from './auth.js';
import { GenerationRequestError, parseGenerationPayload, QUEUE_NAME, type GenerationPayload } from './contracts.js';

const port = Number(process.env.ORCHESTRATOR_PORT ?? 8790);
const host = process.env.ORCHESTRATOR_HOST ?? '127.0.0.1';
const redisUrl = process.env.REDIS_URL ?? 'redis://127.0.0.1:6379';
const orchestratorToken = process.env.ORCHESTRATOR_TOKEN?.trim() || undefined;
const requireOrchestratorAuth = orchestratorAuthRequired(process.env.ORCHESTRATOR_REQUIRE_AUTH, process.env.STUDIO_ENV);
const configuredMaxBodyBytes = Number(process.env.ORCHESTRATOR_MAX_BODY_BYTES ?? 65536);
const maxBodyBytes = Number.isFinite(configuredMaxBodyBytes) ? Math.max(1024, configuredMaxBodyBytes) : 65536;
assertOrchestratorAuthConfig(orchestratorToken, requireOrchestratorAuth);
const redis = new Redis(redisUrl, { maxRetriesPerRequest: null });
const queue = new Queue<GenerationPayload>(QUEUE_NAME, { connection: redis });

function send(response: import('node:http').ServerResponse, status: number, payload: unknown) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify(payload));
}

function authorized(request: import('node:http').IncomingMessage): boolean {
  const provided = request.headers['x-orchestrator-token'];
  return isAuthorized(orchestratorToken, requireOrchestratorAuth, typeof provided === 'string' ? provided : undefined);
}

async function redisHealthy(): Promise<boolean> {
  let timer: NodeJS.Timeout | undefined;
  try {
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('redis health timeout')), 1500);
      timer.unref();
    });
    await Promise.race([redis.ping(), timeout]);
    return true;
  } catch {
    return false;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function body(request: import('node:http').IncomingMessage): Promise<GenerationPayload> {
  const contentLength = Number(request.headers['content-length'] ?? 0);
  if (!Number.isFinite(contentLength) || contentLength < 0) throw new GenerationRequestError('invalid content length', 400);
  if (contentLength > maxBodyBytes) throw new GenerationRequestError('request body too large', 413);
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    size += buffer.byteLength;
    if (size > maxBodyBytes) throw new GenerationRequestError('request body too large', 413);
    chunks.push(buffer);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new GenerationRequestError('request body must be valid JSON', 400);
  }
  return parseGenerationPayload(parsed);
}

const server = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/health') {
    const ready = await redisHealthy();
    send(response, ready ? 200 : 503, { status: ready ? 'ok' : 'degraded', queue: QUEUE_NAME, redis: ready ? 'ok' : 'unavailable' });
    return;
  }
  if (request.method !== 'POST' || request.url !== '/jobs') {
    send(response, 404, { detail: 'not found' });
    return;
  }
  if (!authorized(request)) {
    send(response, 403, { detail: 'orchestrator authorization required' });
    return;
  }
  const contentType = request.headers['content-type'] ?? '';
  if (!contentType.toLowerCase().startsWith('application/json')) {
    send(response, 415, { detail: 'application/json is required' });
    return;
  }
  try {
    const payload = await body(request);
    const job = await queue.add(payload.kind, payload, {
      jobId: payload.jobId,
      attempts: 3,
      backoff: { type: 'exponential', delay: 1000 },
      removeOnComplete: false,
      removeOnFail: false,
    });
    send(response, 202, { accepted: true, queue: QUEUE_NAME, bullmq_job_id: job.id });
  } catch (error) {
    const statusCode = error && typeof error === 'object' && 'statusCode' in error ? Number((error as GenerationRequestError).statusCode) : 500;
    send(response, statusCode >= 400 && statusCode < 600 ? statusCode : 500, { detail: error instanceof Error ? error.message : 'queue error' });
  }
});

server.listen(port, host, () => {
  console.log(`orchestrator listening on http://${host}:${port}`);
});
