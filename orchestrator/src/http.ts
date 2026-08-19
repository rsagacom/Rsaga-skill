export const DEFAULT_RESPONSE_MAX_BYTES = 64 * 1024;

export class ResponseBodyTooLargeError extends Error {
  constructor() {
    super('response body exceeds the configured limit');
    this.name = 'ResponseBodyTooLargeError';
  }
}

/**
 * Read a fetch response without allowing an untrusted body to grow memory
 * without bound. Callers should decide whether the decoded text is safe to
 * expose; worker errors intentionally discard the text instead.
 */
export async function readBoundedResponseText(
  response: Response,
  maxBytes = DEFAULT_RESPONSE_MAX_BYTES,
): Promise<string> {
  if (!Number.isInteger(maxBytes) || maxBytes < 1) {
    throw new RangeError('maxBytes must be a positive integer');
  }
  if (!response.body) return '';

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      const chunk = result.value;
      if (!chunk) continue;
      if (chunk.byteLength > maxBytes - total) {
        try {
          await reader.cancel();
        } catch {
          // The size-limit error is the stable contract; cancellation is best effort.
        }
        throw new ResponseBodyTooLargeError();
      }
      chunks.push(chunk);
      total += chunk.byteLength;
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(bytes);
}
