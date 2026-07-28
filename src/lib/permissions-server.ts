/**
 * Policy-aware permission resolution. Server-only — reads the policy file.
 *
 * Two questions get answered here:
 *   - may this session perform this write?  (security level)
 *   - may this session see this data?       (guest visibility)
 *
 * Admins are never restricted by visibility toggles; those exist to limit what
 * guests see. Admins *are* restricted by the security level, because that is
 * about protecting the world from mistakes, not about trust.
 */

import type { Role } from './auth-types';
import { getPolicy, policyCapabilities } from './policy';
import {
  CAPABILITIES,
  POLICY_GATED,
  describeSavePath,
  type Capability,
  type Feature,
} from './permissions';

/**
 * Capabilities for a session.
 *
 * `userId` is unused today and exists so per-user overrides can be introduced
 * without changing any caller.
 */
export function capabilitiesFor(role: Role, _userId?: string): Set<Capability> {
  if (role !== 'admin') {
    const policy = getPolicy();
    const granted: Capability[] = [];
    // A guest's "view" capabilities depend on what they are allowed to see.
    if (Object.values(policy.guestVisibility).some(Boolean)) {
      granted.push(CAPABILITIES.VIEW_BASIC);
    }
    return new Set(granted);
  }

  const allowed = new Set(policyCapabilities());
  const granted: Capability[] = [
    CAPABILITIES.VIEW_BASIC,
    CAPABILITIES.VIEW_DETAIL,
    CAPABILITIES.SERVER_CONTROL,
    CAPABILITIES.POLICY_MANAGE,
  ];
  for (const capability of POLICY_GATED) {
    if (allowed.has(capability)) granted.push(capability);
  }
  return new Set(granted);
}

export function can(role: Role, capability: Capability, userId?: string): boolean {
  return capabilitiesFor(role, userId).has(capability);
}

export function guestMaySee(feature: Feature): boolean {
  return getPolicy().guestVisibility[feature] === true;
}

/** Whether a session may touch a save-backend path. */
export function mayAccessSavePath(
  role: Role,
  path: string,
  method: string
): { allowed: true } | { allowed: false; reason: string; status: number } {
  const { capability, feature } = describeSavePath(path);

  if (role !== 'admin') {
    // Guests are read-only, full stop.
    if (method !== 'GET') {
      return { allowed: false, reason: 'Administrator access required', status: 403 };
    }
    if (!feature) {
      return { allowed: false, reason: 'Administrator access required', status: 403 };
    }
    if (!guestMaySee(feature)) {
      return {
        allowed: false,
        reason: 'This information is not available to guests on this server',
        status: 403,
      };
    }
    return { allowed: true };
  }

  if (!can(role, capability)) {
    return {
      allowed: false,
      reason:
        `'${capability}' is blocked by the current security level ` +
        `('${getPolicy().securityLevel}'). Raise it in the Access tab.`,
      status: 403,
    };
  }

  return { allowed: true };
}
