import { afterEach, describe, expect, it, vi } from 'vitest';
import { boundedText, BodyLimitError } from './request-body';

function request(body: ReadableStream<Uint8Array>) {
  return new Request('http://localhost/upload', { method: 'POST', body, duplex: 'half' } as RequestInit);
}

afterEach(() => vi.useRealTimers());
describe('bounded uploads', () => {
  it('rejects chunked bodies once their byte limit is exceeded', async () => {
    const stream = new ReadableStream<Uint8Array>({ start(c) {
      c.enqueue(new Uint8Array(4)); c.enqueue(new Uint8Array(4)); c.close();
    }});
    await expect(boundedText(request(stream), 6)).rejects.toBeInstanceOf(BodyLimitError);
  });
  it('does not return a partial body when an upload times out', async () => {
    vi.useFakeTimers();
    const stream = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new TextEncoder().encode('{')); } });
    const result = boundedText(request(stream), 1024);
    const failure = expect(result).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(30_000);
    await failure;
  });
  it('decodes multibyte text only after counting its bytes', async () => {
    const stream = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new TextEncoder().encode('é')); c.close(); } });
    await expect(boundedText(request(stream), 1)).rejects.toBeInstanceOf(BodyLimitError);
  });
});
