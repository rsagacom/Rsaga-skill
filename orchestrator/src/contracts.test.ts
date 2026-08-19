import assert from 'node:assert/strict';
import test from 'node:test';
import { assertOrchestratorAuthConfig, isAuthorized, orchestratorAuthRequired, parseBooleanEnv } from './auth.js';
import { GenerationRequestError, parseGenerationPayload } from './contracts.js';
import { readBoundedResponseText, ResponseBodyTooLargeError } from './http.js';

test('orchestrator auth defaults to local compatibility mode', () => {
  assert.equal(parseBooleanEnv(undefined), false);
  assert.equal(parseBooleanEnv('true'), true);
  assert.equal(parseBooleanEnv(' YES '), true);
  assert.equal(parseBooleanEnv('0'), false);
  assert.equal(orchestratorAuthRequired(undefined, 'development'), false);
  assert.equal(orchestratorAuthRequired(undefined, ' production '), true);
  assert.equal(orchestratorAuthRequired('true', 'development'), true);
  assert.doesNotThrow(() => assertOrchestratorAuthConfig(undefined, false));
  assert.equal(isAuthorized(undefined, false, undefined), true);
});

test('production orchestrator auth requires a token and compares it safely', () => {
  assert.throws(
    () => assertOrchestratorAuthConfig(undefined, true),
    /ORCHESTRATOR_TOKEN is required/,
  );
  assert.equal(isAuthorized(undefined, true, undefined), false);
  assert.equal(isAuthorized('orchestrator-test-token', true, undefined), false);
  assert.equal(isAuthorized('orchestrator-test-token', true, 'wrong-token'), false);
  assert.equal(isAuthorized('orchestrator-test-token', true, 'orchestrator-test-token'), true);
});

test('parseGenerationPayload normalizes a valid internal job payload', () => {
  assert.deepEqual(parseGenerationPayload({
    jobId: ' job-1 ',
    userId: ' user-1 ',
    kind: 'text',
    targetId: ' project-1 ',
    ignored: 'field',
  }), {
    jobId: 'job-1',
    userId: 'user-1',
    kind: 'text',
    targetId: 'project-1',
  });
});

test('parseGenerationPayload accepts story bible extraction jobs', () => {
  assert.equal(parseGenerationPayload({
    jobId: 'story-job-1',
    userId: 'user-1',
    kind: 'story-bible',
    targetId: 'project-1',
  }).kind, 'story-bible');
});

test('parseGenerationPayload accepts project structure jobs', () => {
  assert.equal(parseGenerationPayload({
    jobId: 'structure-job-1',
    userId: 'user-1',
    kind: 'structure',
    targetId: 'project-1',
  }).kind, 'structure');
});

test('parseGenerationPayload accepts asynchronous visual review jobs', () => {
  assert.equal(parseGenerationPayload({
    jobId: 'review-job-1',
    userId: 'user-1',
    kind: 'vision-review',
    targetId: 'asset-1',
  }).kind, 'vision-review');
});

test('parseGenerationPayload accepts asynchronous image prompt jobs', () => {
  assert.equal(parseGenerationPayload({
    jobId: 'prompt-job-1',
    userId: 'user-1',
    kind: 'prompt',
    targetId: 'shot-1',
  }).kind, 'prompt');
});

test('parseGenerationPayload rejects missing and unsupported fields', () => {
  assert.throws(() => parseGenerationPayload({ jobId: 'job-1', userId: 'user-1', kind: 'unknown', targetId: 'target-1' }), (error: unknown) => {
    return error instanceof GenerationRequestError && error.statusCode === 400;
  });
  assert.throws(() => parseGenerationPayload({ jobId: 'job-1', userId: 'user-1', kind: 'text' }), /targetId/);
});

test('parseGenerationPayload rejects oversized identifiers', () => {
  assert.throws(() => parseGenerationPayload({
    jobId: 'job-1',
    userId: 'user-1',
    kind: 'image',
    targetId: 'x'.repeat(257),
  }), /too long/);
});

test('bounded response reader accepts a response within the limit', async () => {
  const response = new Response('worker-ok');
  assert.equal(await readBoundedResponseText(response, 32), 'worker-ok');
});

test('bounded response reader rejects an oversized response before retaining it', async () => {
  const response = new Response('x'.repeat(128));
  await assert.rejects(
    readBoundedResponseText(response, 16),
    (error: unknown) => error instanceof ResponseBodyTooLargeError,
  );
});
