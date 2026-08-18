/**
 * Session handling. Server-side only — never import this from a client component.
 *
 * WHAT CHANGED AND WHY
 * --------------------
 * This used to be a stateless HMAC-signed cookie carrying a role. Three problems
 * with that, all of them security findings:
 *
 *   - It could not be revoked. Logging out cleared the browser's copy while the
 *     token stayed valid for its full 12 hours, and disabling a user did nothing
 *     until their cookie expired.
 *   - There was one shared password and two roles, so nothing could be
 *     attributed to a person.
 *   - There was no throttling, so the single password could be guessed at
 *     network speed.
 *
 * The cookie now holds an opaque random token issued by the Python backend,
 * which stores only its hash. Every request resolves it against the database, so
 * logout, "disable account" and role changes all take effect immediately.
 *
 * The extra loopback call per request is a fraction of a millisecond, and it
 * buys real revocation — worth it for a tool whose whole job is guarding
 * something irreplaceable.
 */

import type { NextRequest } from 'next/server';
import type { Role } from './auth-types';

export type { Role } from './auth-types';

export const SESSION_COOKIE = 'pw_session';
export const SESSION_HEADER = 'X-Session-Token';

/**
 * CSRF: why a cross-site mutating request is refused, or null (AUDIT S7).
 *
 * The session rides an httpOnly SameSite=Lax cookie, which already blocks the
 * classic cross-site form POST — this is the defence-in-depth layer on top,
 * because Lax is a browser default this code does not control and a
 * subdomain-hosted attacker is same-SITE to the cookie.
 *
 * Two signals, strongest first:
 *
 * - `Sec-Fetch-Site` is the browser's own verdict and cannot be forged from a
 *   page. `cross-site` is refused outright; `same-origin`/`same-site`/`none`
 *   (address bar, bookmarks) pass.
 * - `Origin`, compared against the host the request arrived at
 *   (`X-Forwarded-Host` first — behind a reverse proxy the plain Host is the
 *   internal one and would fail every legitimate request). `Origin: null` — a
 *   sandboxed iframe or file:// page — is refused: no legitimate dashboard
 *   request has it.
 *
 * A request with NEITHER header passes. That is curl and every non-browser
 * client — CSRF needs a victim's browser to attach the cookie, and a client
 * that manufactures its own requests is not confused-deputising anything.
 *
 * Takes Headers, not a NextRequest, so tests exercise it without standing up
 * the framework.
 */
export function crossSiteReason(headers: Headers): string | null {
  const fetchSite = headers.get('sec-fetch-site');
  if (fetchSite === 'cross-site') {
    return 'cross-site request refused (Sec-Fetch-Site: cross-site)';
  }

  const origin = headers.get('origin');
  if (!origin) return null;
  if (origin === 'null') {
    return 'cross-site request refused (Origin: null)';
  }

  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return `cross-site request refused (unparseable Origin: ${origin})`;
  }
  const requestHost = (
    headers.get('x-forwarded-host') ?? headers.get('host') ?? ''
  ).split(',')[0].trim();
  if (requestHost && originHost.toLowerCase() !== requestHost.toLowerCase()) {
    return `cross-site request refused (Origin ${originHost} != host ${requestHost})`;
  }
  return null;
}

const BACKEND = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8400';
const SESSION_TTL_SECONDS = Number(process.env.SESSION_TTL_HOURS || 12) * 3600;

export interface SessionUser {
  id: number;
  username: string;
  role: Role;
  steamUid: string;
  displayName: string;
  disabled: boolean;
  mustChangePassword: boolean;
}

/**
 * The subset of a `SessionUser` the browser is given.
 *
 * **One function because there were two copies and they drifted.** The session
 * route listed `steamUid`; the login route did not — so signing in left
 * `store.user.steamUid` undefined for the whole session, and the Account tab
 * said "not linked" about an account that plainly was, since its bases and Pals
 * were right there. Reloading the page fixed it, which is why it read as
 * flakiness rather than as a missing field.
 *
 * `my-pals.tsx` keys its "linked" check on the same field and broke the same
 * way. Adding a field to `SessionUser` must not require remembering two places.
 *
 * The password hash and internal id are deliberately not included.
 */
export function publicUser(user: SessionUser) {
  return {
    username: user.username,
    displayName: user.displayName,
    role: user.role,
    steamUid: user.steamUid,
    mustChangePassword: user.mustChangePassword,
  };
}

export interface SessionInfo {
  user: SessionUser | null;
  role: Role;
  capabilities: string[];
  securityLevel: string;
  visibility: Record<string, boolean> | null;
  anyUsers: boolean;
}

export function getSessionToken(request: NextRequest): string {
  return request.cookies.get(SESSION_COOKIE)?.value ?? '';
}

async function backend(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BACKEND}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
    cache: 'no-store',
  });
}

/**
 * Resolve the caller's session.
 *
 * Deliberately not cached: caching would reintroduce a revocation window, which
 * is the exact problem this replaced.
 */
export async function getSession(request: NextRequest): Promise<SessionInfo> {
  const token = getSessionToken(request);
  try {
    const res = await backend('/api/auth/session', {
      headers: token ? { [SESSION_HEADER]: token } : {},
    });
    if (!res.ok) throw new Error(`session lookup failed: ${res.status}`);
    return (await res.json()) as SessionInfo;
  } catch {
    // Backend unreachable. Fail closed: no user, no capabilities.
    return {
      user: null,
      role: 'guest',
      capabilities: [],
      securityLevel: 'readonly',
      visibility: {},
      anyUsers: false,
    };
  }
}

export async function login(
  username: string,
  password: string,
  forwardedFor: string,
  userAgent: string
): Promise<
  | { ok: true; token: string; user: SessionUser; capabilities: string[] }
  | { ok: false; status: number; error: string; retryAfter?: string }
> {
  const res = await backend('/api/auth/login', {
    method: 'POST',
    headers: { 'X-Forwarded-For': forwardedFor, 'User-Agent': userAgent },
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return {
      ok: false,
      status: res.status,
      error: body.detail || body.error || 'Sign-in failed',
      retryAfter: res.headers.get('Retry-After') ?? undefined,
    };
  }

  const data = await res.json();
  return { ok: true, token: data.token, user: data.user, capabilities: data.capabilities };
}

export async function logout(token: string): Promise<void> {
  if (!token) return;
  await backend('/api/auth/logout', {
    method: 'POST',
    headers: { [SESSION_HEADER]: token },
  }).catch(() => undefined);
}

export function isGuestEnabled(): boolean {
  return process.env.GUEST_VIEW_ENABLED?.toLowerCase() !== 'false';
}

/**
 * Cookie flags.
 *
 * `secure` is inferred from the request rather than hard-coded: forcing it
 * breaks the common LAN deployment over plain http, and leaving it off behind a
 * TLS reverse proxy leaks the session cookie over any accidental plaintext hop.
 * COOKIE_SECURE overrides when the operator knows better.
 */
export function sessionCookieOptions(request?: NextRequest) {
  const override = process.env.COOKIE_SECURE?.toLowerCase();
  let secure = override === 'true';

  if (!override && request) {
    const proto =
      request.headers.get('x-forwarded-proto')?.split(',')[0].trim() ??
      new URL(request.url).protocol.replace(':', '');
    secure = proto === 'https';
  }

  return {
    httpOnly: true,
    sameSite: 'lax' as const,
    path: '/',
    maxAge: SESSION_TTL_SECONDS,
    secure,
  };
}
