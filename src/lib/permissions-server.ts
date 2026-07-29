/**
 * Policy-aware helpers that need the policy file. Server-only.
 *
 * Role -> capability resolution used to live here. It now lives in
 * `backend/roles.py` and arrives with the session, so there is exactly one
 * authority instead of two implementations that could drift apart.
 *
 * What remains is guest visibility: which parts of the world an unauthenticated
 * viewer may see. That is a property of the server's policy rather than of any
 * account, so it is answered here from the policy file.
 */

import { getPolicy } from './policy';
import type { Feature } from './permissions';

/** Whether guests may see a particular kind of data on this server. */
export function guestMaySee(feature: Feature): boolean {
  return getPolicy().guestVisibility[feature] === true;
}

/** Every guest-visible feature, for hiding empty tabs in the UI. */
export function guestVisibility(): Record<string, boolean> {
  return getPolicy().guestVisibility;
}
