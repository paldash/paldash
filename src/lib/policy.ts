/**
 * Server-side reader for the access policy.
 *
 * The Python backend owns `policy.json`; this process reads the same file
 * directly (both run in the same container) rather than making an HTTP hop on
 * every proxied request. A short cache keeps it to roughly one stat per second
 * under load, and the backend rewrites the file atomically so a partial read is
 * not possible.
 *
 * If the file cannot be read we fall back to the environment defaults, which are
 * the conservative ones — a missing policy file never opens anything up.
 */

import { readFileSync } from 'fs';
import path from 'path';

export type SecurityLevel = 'readonly' | 'safe' | 'full';

export interface AccessPolicy {
  securityLevel: SecurityLevel;
  guestVisibility: Record<string, boolean>;
}

/** Capabilities unlocked at each level. Mirrors backend/policy.py. */
const LEVEL_CAPABILITIES: Record<SecurityLevel, string[]> = {
  readonly: [],
  safe: ['backup.manage', 'settings.write', 'save.sort.stackables'],
  full: [
    'backup.manage',
    'settings.write',
    'save.sort.stackables',
    'save.sort.all',
    'save.edit.full',
  ],
};

const CACHE_MS = 2000;
let cached: { at: number; policy: AccessPolicy } | null = null;

function envBool(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (raw === undefined) return fallback;
  return ['1', 'true', 'yes', 'on'].includes(raw.trim().toLowerCase());
}

function envLevel(): SecurityLevel {
  const level = (process.env.SECURITY_LEVEL ?? 'safe').trim().toLowerCase();
  return level === 'readonly' || level === 'full' ? level : 'safe';
}

function defaults(): AccessPolicy {
  return {
    securityLevel: envLevel(),
    guestVisibility: {
      serverStatus: envBool('GUEST_SEE_SERVER_STATUS', true),
      onlinePlayers: envBool('GUEST_SEE_PLAYERS', true),
      bases: envBool('GUEST_SEE_BASES', true),
      guilds: envBool('GUEST_SEE_GUILDS', true),
      mapObjects: envBool('GUEST_SEE_MAP_OBJECTS', false),
      chests: envBool('GUEST_SEE_CHESTS', false),
      items: envBool('GUEST_SEE_ITEMS', false),
      breeding: envBool('GUEST_SEE_BREEDING', false),
    },
  };
}

function policyPath(): string {
  return (
    process.env.POLICY_FILE ||
    path.join(process.env.CACHE_DIR || '/app/cache', 'policy.json')
  );
}

export function getPolicy(): AccessPolicy {
  const now = Date.now();
  if (cached && now - cached.at < CACHE_MS) return cached.policy;

  const policy = defaults();
  try {
    const stored = JSON.parse(readFileSync(policyPath(), 'utf8'));
    if (['readonly', 'safe', 'full'].includes(stored.securityLevel)) {
      policy.securityLevel = stored.securityLevel;
    }
    if (stored.guestVisibility && typeof stored.guestVisibility === 'object') {
      for (const [key, value] of Object.entries(stored.guestVisibility)) {
        if (typeof value === 'boolean') policy.guestVisibility[key] = value;
      }
    }
  } catch {
    // No policy file yet, or unreadable — environment defaults stand.
  }

  // The environment is a ceiling, never a floor: an operator who sets
  // SECURITY_LEVEL=readonly cannot have it raised from the web UI.
  const order: SecurityLevel[] = ['readonly', 'safe', 'full'];
  if (order.indexOf(policy.securityLevel) > order.indexOf(envLevel())) {
    policy.securityLevel = envLevel();
  }

  cached = { at: now, policy };
  return policy;
}

/** Write capabilities permitted by the current security level. */
export function policyCapabilities(): string[] {
  return LEVEL_CAPABILITIES[getPolicy().securityLevel] ?? [];
}

export function guestCanSee(feature: string): boolean {
  return getPolicy().guestVisibility[feature] === true;
}
