import { NextRequest, NextResponse } from 'next/server';
import { getRole } from '@/lib/auth';
import { mayAccessSavePath, guestMaySee } from '@/lib/permissions-server';
import { FEATURES } from '@/lib/permissions';

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8400';

/**
 * The Python backend has no auth of its own and is bound to loopback, so this
 * proxy is the only thing standing between a guest and the save editor.
 *
 * Access is decided per capability rather than per role, so granting one user
 * "may sort chests" without "may edit players" is a change to the capability
 * map alone.
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

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return handle(request, path, 'DELETE');
}

async function handle(request: NextRequest, path: string[], method: string) {
  const role = getRole(request);
  if (!role) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  const joined = path.join('/');
  const verdict = mayAccessSavePath(role, joined, method);

  if (!verdict.allowed) {
    return NextResponse.json({ error: verdict.reason }, { status: verdict.status });
  }

  return proxyToBackend(
    `/api/${joined}${request.nextUrl.search}`,
    request,
    method,
    role !== 'admin' ? joined : null
  );
}

async function proxyToBackend(
  apiPath: string,
  request: NextRequest,
  method: string,
  guestPath: string | null
) {
  try {
    const init: RequestInit = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };

    if (method === 'POST') {
      const body = await request.text().catch(() => '');
      if (body) init.body = body;
    }

    const res = await fetch(`${PYTHON_BACKEND_URL}${apiPath}`, init);
    let data = await res.json().catch(() => ({}));

    // Chests are a separate visibility toggle from the rest of the map, so a
    // guest allowed to see farms and palboxes is not automatically handed a
    // treasure map.
    if (
      guestPath === 'mapobjects' &&
      Array.isArray(data) &&
      !guestMaySee(FEATURES.CHESTS)
    ) {
      data = data.filter((o: { category?: string }) => o.category !== 'chest');
    }

    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Backend connection failed';
    return NextResponse.json({ error: message, backendOffline: true }, { status: 503 });
  }
}
