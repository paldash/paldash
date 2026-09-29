import { NextRequest, NextResponse } from 'next/server';
import { getSession, getSessionToken, isGuestEnabled } from '@/lib/auth';
import { CAPABILITIES, REST_GUEST_FEATURES } from '@/lib/permissions';
import { guestMaySee } from '@/lib/permissions-server';

const PALWORLD_REST_URL = process.env.PALWORLD_REST_URL || 'http://127.0.0.1:8212';
const PALWORLD_ADMIN_PASSWORD = process.env.PALWORLD_ADMIN_PASSWORD || '';
const BACKEND = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8400';
// Selecting a constant also prevents authorization and URL normalization from
// disagreeing about which resource is requested (including encoded separators).
const READ_PATHS: Record<string, string> = {
  info: '/v1/api/info', metrics: '/v1/api/metrics', players: '/v1/api/players',
};
const PUBLIC_PLAYER_FIELDS = ['name', 'ping', 'location_x', 'location_y', 'level'] as const;

function normaliseUid(value: unknown): string {
  return String(value ?? '').replace(/-/g, '').toLowerCase();
}

function isHidden(hidden: Set<string>, player: Record<string, unknown>): boolean {
  if (!hidden.size) return false;
  const candidate = normaliseUid(player.playerId);
  const short = /^[0-9a-f]{1,32}$/.test(candidate) ? candidate : '';
  const account = normaliseUid(player.userId).match(/[0-9a-f]{32}$/)?.[0] || '';
  // An unidentifiable row cannot be checked against the privacy policy.
  if (!short && !account) return true;
  for (const uid of hidden) {
    if (short && uid.startsWith(short)) return true;
    if (account && account.endsWith(uid)) return true;
  }
  return false;
}

async function hiddenPlayerUids(token: string, signal: AbortSignal): Promise<Set<string>> {
  // Guests need the same policy lookup. Any unavailable/malformed policy must
  // withhold the response, never interpret failure as "nobody is hidden".
  const res = await fetch(`${BACKEND}/api/privacy/hidden`, {
    headers: token ? { 'X-Session-Token': token } : {}, cache: 'no-store', signal,
  });
  if (!res.ok) throw new Error('Player visibility is unavailable');
  const data = await res.json();
  if (!Array.isArray(data.players) || !data.players.every((u: unknown) => typeof u === 'string' && /^[a-f0-9]{32}$/i.test(normaliseUid(u)))) {
    throw new Error('Player visibility is unavailable');
  }
  return new Set(data.players.map(normaliseUid));
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const endpoint = path[0];
  if (path.length !== 1 || !Object.hasOwn(READ_PATHS, endpoint)) {
    return NextResponse.json({ error: 'Unknown endpoint' }, { status: 404 });
  }
  const session = await getSession(request);
  const signedIn = session.user !== null;
  if (!session.capabilities.includes(CAPABILITIES.VIEW_BASIC)) {
    return NextResponse.json({ error: 'Sign in to view this information.' }, { status: signedIn ? 403 : 401 });
  }
  if (!signedIn && (!isGuestEnabled() || !guestMaySee(REST_GUEST_FEATURES[endpoint]))) {
    return NextResponse.json({ error: 'This information is not available to guests on this server' }, { status: 403 });
  }

  try {
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]);
    const [res, hidden] = await Promise.all([
      fetch(`${PALWORLD_REST_URL}${READ_PATHS[endpoint]}`, {
        headers: { Authorization: `Basic ${Buffer.from(`admin:${PALWORLD_ADMIN_PASSWORD}`).toString('base64')}` },
        cache: 'no-store', redirect: 'error', signal,
      }),
      endpoint === 'players' ? hiddenPlayerUids(getSessionToken(request), signal) : Promise.resolve(new Set<string>()),
    ]);
    if (!res.ok) return NextResponse.json({ error: `Palworld API returned ${res.status}` }, { status: res.status });
    let data = await res.json();
    if (endpoint === 'players') {
      if (!Array.isArray(data?.players) || !data.players.every((p: unknown) => p !== null && typeof p === 'object' && !Array.isArray(p))) {
        throw new Error('Player data is unavailable');
      }
      let players = (data.players as Record<string, unknown>[]).filter(p => !isHidden(hidden, p));
      if (!session.capabilities.includes(CAPABILITIES.VIEW_DETAIL)) {
        players = players.map(p => Object.fromEntries(PUBLIC_PLAYER_FIELDS
          .filter(k => k === 'name' ? typeof p[k] === 'string' : typeof p[k] === 'number' && Number.isFinite(p[k]))
          .map(k => [k, p[k]])));
      }
      // Do not forward unexpected top-level fields from a privileged API.
      data = { players };
    }
    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json({ error: 'Live server data or its visibility policy is unavailable', offline: true }, { status: 503 });
  }
}

// Keep the explicit refusal for stale clients; commands belong to the audited backend.
export async function POST() {
  return NextResponse.json({ error: 'Use the backend moderation and server routes, which audit commands.' }, { status: 405 });
}
