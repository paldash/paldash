import { NextResponse } from 'next/server';

/**
 * Which build is serving this response.
 *
 * Unauthenticated and deliberately so: a build id is not a secret, and the
 * clients that most need it are the ones with a stale bundle — which may
 * predate whatever the current session shape is. Gating it behind auth would
 * make the mechanism fail exactly when it is needed.
 *
 * Kept separate from `/api/health`, whose docstring promises it "reveals
 * nothing" and which Docker's HEALTHCHECK depends on. Two probes with two
 * contracts is clearer than one with a footnote.
 *
 * `no-store` is set here AND in `next.config.ts`. A cached version probe
 * reports the running build as current forever, which is precisely the failure
 * this exists to prevent — belt and braces is cheap for a 40-byte response.
 */
export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json(
    { build: process.env.NEXT_PUBLIC_BUILD_ID ?? 'unknown' },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
