import { NextRequest, NextResponse } from 'next/server';
import { SESSION_COOKIE, getSessionToken, logout, sessionCookieOptions } from '@/lib/auth';

/**
 * Signing out now actually revokes the session server-side rather than only
 * clearing the browser's copy, so a token captured beforehand stops working
 * immediately instead of lasting its full lifetime.
 */
export async function POST(request: NextRequest) {
  await logout(getSessionToken(request));

  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, '', { ...sessionCookieOptions(request), maxAge: 0 });
  return res;
}
