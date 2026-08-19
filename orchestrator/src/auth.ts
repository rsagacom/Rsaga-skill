import { timingSafeEqual } from 'node:crypto';

export function parseBooleanEnv(value: string | undefined): boolean {
  return /^(1|true|yes|on)$/i.test(value?.trim() ?? '');
}

export function orchestratorAuthRequired(configured: string | undefined, studioEnvironment: string | undefined): boolean {
  return parseBooleanEnv(configured) || studioEnvironment?.trim().toLowerCase() === 'production';
}

export function assertOrchestratorAuthConfig(token: string | undefined, required: boolean): void {
  if (required && !token) {
    throw new Error('ORCHESTRATOR_TOKEN is required when ORCHESTRATOR_REQUIRE_AUTH is enabled');
  }
}

export function isAuthorized(token: string | undefined, required: boolean, provided: string | undefined): boolean {
  if (!token) return !required;
  if (!provided) return false;
  const expected = Buffer.from(token);
  const actual = Buffer.from(provided);
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}
