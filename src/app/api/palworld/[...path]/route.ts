import { NextRequest, NextResponse } from 'next/server';
import { getSession } from '@/lib/auth';
import { CAPABILITIES, REST_GUEST_FEATURES } from '@/lib/permissions';
import { guestMaySee } from '@/lib/permissions-server';

const PALWORLD_REST_URL = process.env.PALWORLD_REST_URL || 'http://127.0.0.1:8212';
const PALWORLD_ADMIN_PASSWORD = process.env.PALWORLD_ADMIN_PASSWORD || '';

/** Fields that must never reach a guest. */
const PLAYER_PII = ['ip', 'userId', 'playerId', 'accountName', 'odlerlookup'];

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

async function handle(request: NextRequest, path: string[], method: 'GET' | 'POST') {
  const endpoint = path[0] ?? '';
  if (!/^[a-z0-9_-]+$/i.test(endpoint) || path.length > 2) {
    return NextResponse.json({ error: 'Unknown endpoint' }, { status: 404 });
  }

  const session = await getSession(request);
  const signedIn = session.user !== null;

  if (!signedIn) {
    // Every POST here is a privileged action (kick/ban/announce/save/shutdown).
    const feature = REST_GUEST_FEATURES[endpoint];
    if (method !== 'GET' || !feature) {
      return NextResponse.json({ error: 'Sign in to do this.' }, { status: 401 });
    }
    if (!guestMaySee(feature)) {
      return NextResponse.json(
        { error: 'This information is not available to guests on this server' },
        { status: 403 }
      );
    }
  } else if (method === 'POST' && !session.capabilities.includes(CAPABILITIES.SERVER_CONTROL)) {
    // Kick, ban, announce, save, shutdown: Moderator and above.
    return NextResponse.json(
      { error: 'Your role does not allow controlling the server.' },
      { status: 403 }
    );
  } else if (!session.capabilities.includes(CAPABILITIES.VIEW_BASIC)) {
    return NextResponse.json({ error: 'Your role does not allow this.' }, { status: 403 });
  }

  return proxyRequest(`/v1/api/${path.join('/')}`, request, method, signedIn, endpoint);
}

async function proxyRequest(
  apiPath: string,
  request: NextRequest,
  method: 'GET' | 'POST',
  signedIn: boolean,
  endpoint: string
) {
  try {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Authorization: `Basic ${Buffer.from(`admin:${PALWORLD_ADMIN_PASSWORD}`).toString('base64')}`,
    };

    const init: RequestInit = { method, headers };

    if (method === 'POST') {
      const body = await request.text().catch(() => '');
      if (body) init.body = body;
    }

    const res = await fetch(`${PALWORLD_REST_URL}${apiPath}`, init);

    if (!res.ok) {
      return NextResponse.json(
        { error: `Palworld API returned ${res.status}` },
        { status: res.status }
      );
    }

    let data = await res.json().catch(() => ({}));

    // Guests get to see who is online and where, but not their IPs or IDs.
    if (!signedIn && endpoint === 'players' && data && Array.isArray(data.players)) {
      data = {
        ...data,
        players: data.players.map((player: Record<string, unknown>) => {
          const safe = { ...player };
          for (const field of PLAYER_PII) delete safe[field];
          return safe;
        }),
      };
    }

    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Connection failed';
    return NextResponse.json({ error: message, offline: true }, { status: 503 });
  }
}
