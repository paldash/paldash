import { NextResponse } from 'next/server';

/** Unauthenticated liveness probe for Docker's HEALTHCHECK. Reveals nothing. */
export async function GET() {
  return NextResponse.json({ status: 'ok' });
}
