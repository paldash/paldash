import { NextRequest, NextResponse } from 'next/server';
import { crossSiteReason, SESSION_COOKIE, getSessionToken, logout, sessionCookieOptions } from '@/lib/auth';

/**
 * Signing out now actually revokes the session server-side rather than only
 * clearing the browser's copy, so a token captured beforehand stops working
 * immediately instead of lasting its full lifetime.
 */
export async function POST(request: NextRequest) {
  // Cross-site logout is only a nuisance, but the gate is one line and
  // leaving a single mutating route outside it invites the next one to
  // copy the exception.
  const crossSite = crossSiteReason(request.headers);
  if (crossSite) {
    return NextResponse.json({ error: crossSite }, { status: 403 });
  }

  await logout(getSessionToken(request));

  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, '', { ...sessionCookieOptions(request), maxAge: 0 });
  return res;
}
