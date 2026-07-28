import { NextRequest, NextResponse } from 'next/server';
import { getRole, isAdminConfigured, isGuestEnabled } from '@/lib/auth';
import { capabilitiesFor } from '@/lib/permissions-server';
import { getPolicy } from '@/lib/policy';

/**
 * Lets the client restore its session on reload and learn what it may do.
 *
 * Capabilities are returned so the UI can grey out actions the user lacks —
 * presentation only. Enforcement happens in the proxies regardless.
 */
export async function GET(request: NextRequest) {
  const role = getRole(request);
  const policy = getPolicy();
  return NextResponse.json({
    role,
    capabilities: role ? [...capabilitiesFor(role)] : [],
    securityLevel: policy.securityLevel,
    // Guests are told what they may see so the UI can hide empty tabs; admins
    // see everything regardless.
    visibility: role === 'admin' ? null : policy.guestVisibility,
    adminAvailable: isAdminConfigured(),
    guestAvailable: isGuestEnabled(),
  });
}
