/** Read bounded bytes before decoding text/JSON, including chunked uploads. */
export class BodyLimitError extends Error {}
export async function boundedText(request: Request, limit: number): Promise<string> {
  const declared = Number(request.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > limit) throw new BodyLimitError('Request body is too large');
  if (!request.body) return '';
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; void reader.cancel('Request body timed out'); }, 30_000);
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (timedOut) throw new Error('Request body timed out');
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new BodyLimitError('Request body is too large');
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } finally { clearTimeout(timeout); reader.releaseLock(); }
}
