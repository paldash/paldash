import { NextRequest, NextResponse } from 'next/server';
import {
  SESSION_COOKIE, login, sessionCookieOptions, isGuestEnabled, publicUser,
} from '@/lib/auth';

/**
 * POST { username, password } -> a session
 * POST { guest: true }        -> a guest view, if the operator allows it
 *
 * Guest access issues no cookie at all: a guest is simply an unauthenticated
 * caller, and what they may see is decided by the visibility policy. That keeps
 * "no credential" and "a credential naming nobody" from being two separate
 * things to reason about.
 *
 * Rate limiting lives in the backend, per-IP and per-username, persisted so a
 * restart does not reset an attacker's budget.
 */
export async function POST(request: NextRequest) {
  let body: { username?: string; password?: string; guest?: boolean };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  if (body.guest) {
    if (!isGuestEnabled()) {
      return NextResponse.json({ error: 'Guest access is disabled' }, { status: 403 });
    }
    const res = NextResponse.json({ role: 'guest' });
    // Clear any existing session so "continue as guest" really drops privileges.
    res.cookies.set(SESSION_COOKIE, '', { ...sessionCookieOptions(request), maxAge: 0 });
    return res;
  }

  if (!body.username || !body.password) {
    return NextResponse.json(
      { error: 'Username and password are required' },
      { status: 400 }
    );
  }

  const forwardedFor =
    request.headers.get('x-forwarded-for') ?? request.headers.get('x-real-ip') ?? '';
  const result = await login(
    body.username,
    body.password,
    forwardedFor,
    request.headers.get('user-agent') ?? ''
  );

  if (!result.ok) {
    const res = NextResponse.json({ error: result.error }, { status: result.status });
    if (result.retryAfter) res.headers.set('Retry-After', result.retryAfter);
    return res;
  }

  const res = NextResponse.json({
    role: result.user.role,
    // Shared with /api/auth/session. This list used to be spelled out here and
    // was missing `steamUid`, so a freshly signed-in account read as having no
    // linked character until the page was reloaded.
    user: publicUser(result.user),
    capabilities: result.capabilities,
  });
  res.cookies.set(SESSION_COOKIE, result.token, sessionCookieOptions(request));
  return res;
}
