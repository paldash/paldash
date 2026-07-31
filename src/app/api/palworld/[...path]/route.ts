import { NextRequest, NextResponse } from 'next/server';
import { getSession, getSessionToken } from '@/lib/auth';
import { CAPABILITIES, REST_GUEST_FEATURES } from '@/lib/permissions';
import { guestMaySee } from '@/lib/permissions-server';

const PALWORLD_REST_URL = process.env.PALWORLD_REST_URL || 'http://127.0.0.1:8212';
const PALWORLD_ADMIN_PASSWORD = process.env.PALWORLD_ADMIN_PASSWORD || '';

/** Fields that must never reach a guest. */
const PLAYER_PII = ['ip', 'userId', 'playerId', 'accountName', 'odlerlookup'];

const BACKEND = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8400';

/**
 * Player uids this session must not see, from the backend's privacy rules.
 *
 * Live positions come from the game's REST API through this proxy, not from the
 * save backend — so per-player privacy has to be applied here as well, or a
 * hidden player would vanish from the save-derived map and still show up as a
 * live dot on it.
 *
 * A failure returns an empty set, which shows everyone. That is the wrong way to
 * fail for a privacy feature, so it is deliberately paired with the backend
 * being on the same loopback interface as this process: if it is unreachable,
 * the map has no data to draw anyway.
 */
async function hiddenPlayerUids(token: string | undefined): Promise<Set<string>> {
  if (!token) return new Set();
  try {
    const res = await fetch(`${BACKEND}/api/privacy/hidden`, {
      headers: { 'X-Session-Token': token },
      cache: 'no-store',
    });
    if (!res.ok) return new Set();
    const data = await res.json();
    return new Set<string>((data.players ?? []).map((u: string) => u.toLowerCase()));
  } catch {
    return new Set();
  }
}

/** The proxy sees dashed or undashed ids depending on the field; normalise. */
function normaliseUid(value: unknown): string {
  return String(value ?? '').replace(/-/g, '').toLowerCase();
}

/**
 * Whether a live player row is one of the hidden uids.
 *
 * **The two sides speak different dialects, and comparing them with `===`
 * matched nothing — so live-position privacy never worked.** The save (and
 * therefore `/api/privacy/hidden`) gives a full Palworld uid,
 * `22b22b02-0000-0000-0000-000000000000`, which normalises to 32 characters.
 * The game's REST API gives `playerId` as just the leading `22B22B02`, and
 * `userId` as a Steam id in `steam_1100001...` form — a different identifier
 * entirely.
 *
 * A Palworld player uid is a Steam ID32 followed by zeros, so the short form is
 * a genuine prefix of the long one and a prefix test is exact rather than fuzzy.
 * `userId` is checked too, since a hidden account may be keyed either way.
 *
 * This failed **open**: a player who asked to be hidden vanished from the
 * save-derived map and went on showing as a live dot on the same screen, which
 * is precisely the failure `backend/privacy.py` says filtering in two places
 * exists to prevent.
 */
function isHidden(hidden: Set<string>, player: Record<string, unknown>): boolean {
  if (!hidden.size) return false;
  const short = normaliseUid(player.playerId);
  const account = normaliseUid(player.userId);
  for (const uid of hidden) {
    if (short && uid.startsWith(short)) return true;
    if (account && account.endsWith(uid)) return true;
  }
  return false;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return handle(request, path, 'GET');
}

/**
 * Deliberately refuses everything.
 *
 * This proxy used to forward POSTs — kick, ban, announce, save, shutdown — to the
 * game server, gated on `server.control`. They worked, and they left **no audit
 * record**, because the audit log lives in SQLite and only the Python backend
 * opens that file. An unaudited ban is precisely the action an operator later
 * needs to look up.
 *
 * Those commands now live on the backend (`/api/save/moderate/*`,
 * `/api/save/server/*`), which issues them itself and records the actor, the
 * target, the reason and the outcome. This handler stays, rather than being
 * deleted, so that a stale caller gets a clear 405 pointing at the right route
 * instead of a 404 that looks like the feature was removed.
 */
export async function POST() {
  return NextResponse.json(
    {
      error:
        'Commands are not sent through this proxy. Use the backend moderation and ' +
        'server routes, which audit the action.',
    },
    { status: 405 }
  );
}

/** Reads only. Commands go to the backend so they can be audited — see POST above. */
async function handle(request: NextRequest, path: string[], method: 'GET') {
  const endpoint = path[0] ?? '';
  if (!/^[a-z0-9_-]+$/i.test(endpoint) || path.length > 2) {
    return NextResponse.json({ error: 'Unknown endpoint' }, { status: 404 });
  }

  const session = await getSession(request);
  const signedIn = session.user !== null;

  if (!signedIn) {
    const feature = REST_GUEST_FEATURES[endpoint];
    if (!feature) {
      return NextResponse.json({ error: 'Sign in to do this.' }, { status: 401 });
    }
    if (!guestMaySee(feature)) {
      return NextResponse.json(
        { error: 'This information is not available to guests on this server' },
        { status: 403 }
      );
    }
  } else if (!session.capabilities.includes(CAPABILITIES.VIEW_BASIC)) {
    return NextResponse.json({ error: 'Your role does not allow this.' }, { status: 403 });
  }

  return proxyRequest(`/v1/api/${path.join('/')}`, request, method, signedIn, endpoint);
}

async function proxyRequest(
  apiPath: string,
  request: NextRequest,
  method: 'GET',
  signedIn: boolean,
  endpoint: string
) {
  try {
    const init: RequestInit = {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Basic ${Buffer.from(`admin:${PALWORLD_ADMIN_PASSWORD}`).toString('base64')}`,
      },
    };

    // The privacy lookup does not depend on the game's answer, so it is started
    // alongside rather than after it. Sequentially it added ~1 ms of loopback to
    // every live-position poll; concurrently it adds nothing, because the game
    // server's own round trip is the longer of the two and always was.
    const needsPrivacy = endpoint === 'players';
    const [res, hidden] = await Promise.all([
      fetch(`${PALWORLD_REST_URL}${apiPath}`, init),
      needsPrivacy ? hiddenPlayerUids(getSessionToken(request)) : Promise.resolve(new Set<string>()),
    ]);

    if (!res.ok) {
      return NextResponse.json(
        { error: `Palworld API returned ${res.status}` },
        { status: res.status }
      );
    }

    let data = await res.json().catch(() => ({}));

    if (endpoint === 'players' && data && Array.isArray(data.players)) {
      let players = data.players as Record<string, unknown>[];

      // Per-player privacy applies to live positions too. Without this a player
      // who hid themselves would disappear from the save-derived map and keep
      // showing as a live dot on the same screen.
      if (hidden.size) {
        players = players.filter((p) => !isHidden(hidden, p));
      }

      // Guests get to see who is online and where, but not their IPs or IDs.
      if (!signedIn) {
        players = players.map((player) => {
          const safe = { ...player };
          for (const field of PLAYER_PII) delete safe[field];
          return safe;
        });
      }

      data = { ...data, players };
    }

    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Connection failed';
    return NextResponse.json({ error: message, offline: true }, { status: 503 });
  }
}
