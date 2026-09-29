import { NextResponse } from 'next/server';

/** Container readiness includes the backend, without exposing world metadata. */
export async function GET() {
  try {
    const url = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8400';
    const res = await fetch(`${url}/api/ready`, { cache: 'no-store', signal: AbortSignal.timeout(3000) });
    if (!res.ok) throw new Error('Backend not ready');
    return NextResponse.json({ status: 'ok' });
  } catch {
    return NextResponse.json({ status: 'unavailable' }, { status: 503 });
  }
}
