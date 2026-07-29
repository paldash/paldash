/**
 * Role names, split out from auth.ts so client components and the permission
 * model can import them without pulling in server-only code.
 *
 * These mirror `backend/roles.py`, which is the authority — the backend
 * re-checks every capability regardless of what the UI believes.
 */
export type Role =
  | 'guest'
  | 'readonly'
  | 'player'
  | 'trusted'
  | 'moderator'
  | 'admin'
  | 'owner';

/** Least to most privileged. Used for "may I manage this account" checks in the UI. */
export const ROLE_RANK: Record<Role, number> = {
  guest: 0,
  readonly: 1,
  player: 2,
  trusted: 3,
  moderator: 4,
  admin: 5,
  owner: 6,
};

export const ROLE_LABEL: Record<Role, string> = {
  guest: 'Guest',
  readonly: 'Read only',
  player: 'Player',
  trusted: 'Trusted player',
  moderator: 'Moderator',
  admin: 'Administrator',
  owner: 'Owner',
};

/** Roles that can be assigned to a real account. `guest` is the absence of one. */
export const ASSIGNABLE_ROLES: Role[] = [
  'readonly',
  'player',
  'trusted',
  'moderator',
  'admin',
  'owner',
];

export function isAtLeast(role: Role, minimum: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minimum];
}
