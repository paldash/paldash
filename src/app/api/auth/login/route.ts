import { NextRequest, NextResponse } from 'next/server';
import {
  SESSION_COOKIE,
  checkAdminPassword,
  createSessionToken,
  isAdminConfigured,
  isGuestEnabled,
  sessionCookieOptions,
} from '@/lib/auth';

/**
 * POST { password } -> admin session
 * POST { guest: true } -> guest session
 */
export async function POST(request: NextRequest) {
  let body: { password?: string; guest?: boolean };
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
    res.cookies.set(SESSION_COOKIE, createSessionToken('guest'), sessionCookieOptions);
    return res;
  }

  if (!isAdminConfigured()) {
    return NextResponse.json(
      { error: 'Admin login is disabled because PANEL_PASSWORD is not set on the server.' },
      { status: 503 }
    );
  }

  if (!body.password || !checkAdminPassword(body.password)) {
    // Blunt the obvious online guessing attack without pretending this is
    // real rate limiting.
    await new Promise((resolve) => setTimeout(resolve, 400));
    return NextResponse.json({ error: 'Incorrect password' }, { status: 401 });
  }

  const res = NextResponse.json({ role: 'admin' });
  res.cookies.set(SESSION_COOKIE, createSessionToken('admin'), sessionCookieOptions);
  return res;
}
