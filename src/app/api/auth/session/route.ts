import { NextRequest, NextResponse } from 'next/server';
import { getSession, isGuestEnabled } from '@/lib/auth';

/**
 * Lets the client restore its session on reload and learn what it may do.
 *
 * Capabilities are returned so the UI can hide actions the user lacks — that is
 * presentation only. Enforcement happens in the backend on every request,
 * regardless of what the UI believes.
 */
export async function GET(request: NextRequest) {
  const session = await getSession(request);

  return NextResponse.json({
    role: session.role,
    user: session.user
      ? {
          username: session.user.username,
          displayName: session.user.displayName,
          role: session.user.role,
          steamUid: session.user.steamUid,
          mustChangePassword: session.user.mustChangePassword,
        }
      : null,
    capabilities: session.capabilities,
    securityLevel: session.securityLevel,
    // Guests are told what they may see so the UI can hide empty tabs; signed-in
    // users are governed by their role instead.
    visibility: session.visibility,
    guestAvailable: isGuestEnabled(),
    /**
     * False on a brand-new deployment with no accounts. The login screen uses it
     * to explain that PANEL_PASSWORD needs setting rather than showing a form
     * that cannot succeed.
     */
    anyUsers: session.anyUsers,
  });
}
