import { NextRequest, NextResponse } from 'next/server';
import { getSession, getSessionToken, SESSION_HEADER } from '@/lib/auth';
import { describeSavePath, FEATURES } from '@/lib/permissions';
import { guestMaySee } from '@/lib/permissions-server';

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8400';

/**
 * Proxy to the save backend.
 *
 * Two things changed here, both security findings:
 *
 *   - Routing is now an ALLOWLIST. Anything not explicitly named is refused,
 *     rather than falling through to a default capability. A new backend route
 *     is unreachable until it is listed, and traversal attempts are rejected
 *     before matching.
 *   - The caller's session token is forwarded, and the backend resolves it
 *     itself. The proxy no longer asserts who the caller is — it passes along a
 *     credential the backend verifies, so a bug here cannot fabricate an
 *     identity.
 *
 * This layer still applies guest visibility toggles and strips personal data,
 * because those are presentation concerns tied to the policy rather than to a
 * capability.
 */

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return handle(request, path, 'GET');
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return handle(request, path, 'POST');
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return handle(request, path, 'PATCH');
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return handle(request, path, 'DELETE');
}

async function handle(request: NextRequest, path: string[], method: string) {
  const joined = path.join('/');
  const route = describeSavePath(joined, method);

  if (!route.allowed) {
    return NextResponse.json({ error: route.reason }, { status: 404 });
  }

  const session = await getSession(request);
  const signedIn = session.user !== null;

  // Guests: read-only, and only what the visibility policy exposes.
  if (!signedIn) {
    if (method !== 'GET') {
      return NextResponse.json({ error: 'Sign in to do this.' }, { status: 401 });
    }
    if (!route.feature) {
      return NextResponse.json({ error: 'Sign in to view this.' }, { status: 401 });
    }
    if (!guestMaySee(route.feature)) {
      return NextResponse.json(
        { error: 'This information is not available to guests on this server' },
        { status: 403 }
      );
    }
  } else if (route.capability && !session.capabilities.includes(route.capability)) {
    // A fast, friendly rejection. The backend enforces this again regardless.
    return NextResponse.json(
      { error: `Your role does not allow '${route.capability}'.` },
      { status: 403 }
    );
  }

  return proxyToBackend(
    `/api/${joined}${request.nextUrl.search}`,
    request,
    method,
    signedIn ? null : joined
  );
}

async function proxyToBackend(
  apiPath: string,
  request: NextRequest,
  method: string,
  guestPath: string | null
) {
  try {
    const token = getSessionToken(request);
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers[SESSION_HEADER] = token;

    const forwardedFor =
      request.headers.get('x-forwarded-for') ?? request.headers.get('x-real-ip');
    if (forwardedFor) headers['X-Forwarded-For'] = forwardedFor;

    const init: RequestInit = { method, headers };

    if (method === 'POST' || method === 'PATCH') {
      const body = await request.text().catch(() => '');
      if (body) init.body = body;
    }

    const res = await fetch(`${PYTHON_BACKEND_URL}${apiPath}`, init);

    // Downloads stream straight through instead of being parsed and re-encoded:
    // backup archives (gzip) and report exports (CSV/TXT/JSON). Content-Disposition
    // is the test rather than the content type, because a JSON *report* is still a
    // download and would otherwise lose its filename and render in the browser.
    const contentType = res.headers.get('content-type') ?? '';
    const isDownload = (res.headers.get('content-disposition') ?? '').includes('attachment');
    if (isDownload || !contentType.includes('application/json')) {
      const headers = new Headers();
      for (const header of ['content-type', 'content-length', 'content-disposition']) {
        const value = res.headers.get(header);
        if (value) headers.set(header, value);
      }
      return new NextResponse(res.body, { status: res.status, headers });
    }

    let data = await res.json().catch(() => ({}));

    // Chests are a separate visibility toggle from the rest of the map, so a
    // guest allowed to see farms and palboxes is not automatically handed a
    // treasure map.
    if (
      guestPath === 'mapobjects' &&
      Array.isArray(data) &&
      !guestMaySee(FEATURES.CHESTS)
    ) {
      data = data.filter(
        (o: { category?: string }) =>
          o.category !== 'chest' &&
          o.category !== 'oilrigChest' &&
          o.category !== 'fishingJunk'
      );
    }

    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Backend connection failed';
    return NextResponse.json({ error: message, backendOffline: true }, { status: 503 });
  }
}
