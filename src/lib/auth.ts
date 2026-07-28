/**
 * Session handling. Server-side only — never import this from a client component.
 *
 * The previous login was decorative: `password.length > 0` granted admin, and
 * the API proxies did no checking at all, so anyone who could reach port 3000
 * could POST /api/palworld/shutdown regardless of what the UI showed them.
 *
 * Sessions are stateless HMAC-signed cookies. There is no session store to keep
 * in sync between the two processes, and nothing sensitive lives in the cookie.
 */

import { createHash, createHmac, randomBytes, timingSafeEqual } from 'crypto';
import type { NextRequest } from 'next/server';

export type { Role } from './auth-types';
import type { Role } from './auth-types';

export const SESSION_COOKIE = 'pw_session';
const SESSION_TTL_SECONDS = 60 * 60 * 12;

let ephemeralSecret: string | null = null;

/**
 * Signing key. Prefers an explicit SESSION_SECRET; otherwise derives one from
 * PANEL_PASSWORD so that changing the password invalidates existing sessions.
 * With neither set, only guests can log in, and a random per-process key means
 * their sessions simply end on restart.
 */
function getSecret(): string {
  const explicit = process.env.SESSION_SECRET?.trim();
  if (explicit) return explicit;

  const password = process.env.PANEL_PASSWORD?.trim();
  if (password) {
    return createHash('sha256').update(`palworld-dashboard:${password}`).digest('hex');
  }

  if (!ephemeralSecret) {
    ephemeralSecret = randomBytes(32).toString('hex');
    console.warn(
      '[auth] Neither SESSION_SECRET nor PANEL_PASSWORD is set. Admin login is ' +
        'disabled and guest sessions will not survive a restart.'
    );
  }
  return ephemeralSecret;
}

export function isAdminConfigured(): boolean {
  return Boolean(process.env.PANEL_PASSWORD?.trim());
}

export function isGuestEnabled(): boolean {
  return process.env.GUEST_VIEW_ENABLED?.toLowerCase() !== 'false';
}

/** Constant-time password comparison over fixed-length digests. */
export function checkAdminPassword(candidate: string): boolean {
  const expected = process.env.PANEL_PASSWORD?.trim();
  if (!expected) return false;

  const a = createHash('sha256').update(candidate).digest();
  const b = createHash('sha256').update(expected).digest();
  return timingSafeEqual(a, b);
}

function base64url(input: Buffer | string): string {
  return Buffer.from(input).toString('base64url');
}

function sign(payload: string): string {
  return createHmac('sha256', getSecret()).update(payload).digest('base64url');
}

export function createSessionToken(role: Role): string {
  const body = base64url(
    JSON.stringify({ role, exp: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS })
  );
  return `${body}.${sign(body)}`;
}

export function verifySessionToken(token: string | undefined): Role | null {
  if (!token) return null;

  const [body, signature] = token.split('.');
  if (!body || !signature) return null;

  const expected = sign(body);
  // Compare as fixed-length digests so length differences cannot throw.
  const a = createHash('sha256').update(signature).digest();
  const b = createHash('sha256').update(expected).digest();
  if (!timingSafeEqual(a, b)) return null;

  try {
    const claims = JSON.parse(Buffer.from(body, 'base64url').toString());
    if (typeof claims.exp !== 'number' || claims.exp < Date.now() / 1000) return null;
    if (claims.role !== 'admin' && claims.role !== 'guest') return null;
    return claims.role as Role;
  } catch {
    return null;
  }
}

export function getRole(request: NextRequest): Role | null {
  return verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value);
}

export const sessionCookieOptions = {
  httpOnly: true,
  sameSite: 'lax' as const,
  path: '/',
  maxAge: SESSION_TTL_SECONDS,
  // Only set Secure when actually served over HTTPS — forcing it breaks the
  // common LAN deployment over plain http://.
  secure: process.env.COOKIE_SECURE?.toLowerCase() === 'true',
};
