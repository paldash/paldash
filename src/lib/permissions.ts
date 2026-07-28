/**
 * Capability and feature names.
 *
 * Pure constants and pure functions only — this module is imported by client
 * components, so it must never touch `fs` or the policy file. Policy-aware
 * resolution lives in `permissions-server.ts`.
 */

export const CAPABILITIES = {
  /** Read live server status, map, bases. */
  VIEW_BASIC: 'view.basic',
  /** Read player saves, inventories, item totals, breeding. */
  VIEW_DETAIL: 'view.detail',
  /** Kick/ban/announce/save/shutdown through the game's REST API. */
  SERVER_CONTROL: 'server.control',
  /** Read and write PalWorldSettings.ini. */
  SETTINGS_WRITE: 'settings.write',
  /** Create, restore and delete save backups. */
  BACKUP_MANAGE: 'backup.manage',
  /** Change the access policy itself. */
  POLICY_MANAGE: 'policy.manage',

  /** Sort/merge plain stackable items. Cannot touch equipment. */
  SAVE_SORT_STACKABLES: 'save.sort.stackables',
  /** Sort/merge every item including durability-bearing equipment. */
  SAVE_SORT_ALL: 'save.sort.all',
  /** Arbitrary edits to players, Pals and container slots. */
  SAVE_EDIT_FULL: 'save.edit.full',
} as const;

export type Capability = (typeof CAPABILITIES)[keyof typeof CAPABILITIES];

/** Write capabilities that the security level gates. */
export const POLICY_GATED: Capability[] = [
  CAPABILITIES.SETTINGS_WRITE,
  CAPABILITIES.BACKUP_MANAGE,
  CAPABILITIES.SAVE_SORT_STACKABLES,
  CAPABILITIES.SAVE_SORT_ALL,
  CAPABILITIES.SAVE_EDIT_FULL,
];

/**
 * Visibility features a guest can be granted or denied individually. Admins
 * always see everything.
 */
export const FEATURES = {
  SERVER_STATUS: 'serverStatus',
  ONLINE_PLAYERS: 'onlinePlayers',
  BASES: 'bases',
  GUILDS: 'guilds',
  MAP_OBJECTS: 'mapObjects',
  CHESTS: 'chests',
  ITEMS: 'items',
  BREEDING: 'breeding',
} as const;

export type Feature = (typeof FEATURES)[keyof typeof FEATURES];

/**
 * Save-backend path -> { capability, feature }.
 *
 * `feature` is the guest visibility toggle; a path with no feature is
 * admin-only. First match wins, and anything unmatched is admin-only by
 * default so a new endpoint is never accidentally public.
 */
const SAVE_PATHS: [RegExp, Capability, Feature | null][] = [
  [/^health$/, CAPABILITIES.VIEW_BASIC, FEATURES.SERVER_STATUS],
  [/^bases$/, CAPABILITIES.VIEW_BASIC, FEATURES.BASES],
  [/^guilds$/, CAPABILITIES.VIEW_BASIC, FEATURES.GUILDS],
  [/^mapobjects$/, CAPABILITIES.VIEW_BASIC, FEATURES.MAP_OBJECTS],
  [/^items$/, CAPABILITIES.VIEW_DETAIL, FEATURES.ITEMS],
  [/^breeding\//, CAPABILITIES.VIEW_DETAIL, FEATURES.BREEDING],

  [/^edit\/sort\/stackables$/, CAPABILITIES.SAVE_SORT_STACKABLES, null],
  [/^edit\/sort\/all$/, CAPABILITIES.SAVE_SORT_ALL, null],
  [/^edit\//, CAPABILITIES.SAVE_EDIT_FULL, null],

  [/^policy$/, CAPABILITIES.POLICY_MANAGE, null],
  [/^settings\//, CAPABILITIES.SETTINGS_WRITE, null],
  [/^backup/, CAPABILITIES.BACKUP_MANAGE, null],
  [/^restore\//, CAPABILITIES.BACKUP_MANAGE, null],
  [/^server\//, CAPABILITIES.SERVER_CONTROL, null],
];

export function describeSavePath(path: string): {
  capability: Capability;
  feature: Feature | null;
} {
  for (const [pattern, capability, feature] of SAVE_PATHS) {
    if (pattern.test(path)) return { capability, feature };
  }
  return { capability: CAPABILITIES.VIEW_DETAIL, feature: null };
}

/** Palworld REST paths a guest may read, subject to their visibility toggles. */
export const REST_GUEST_FEATURES: Record<string, Feature> = {
  info: FEATURES.SERVER_STATUS,
  metrics: FEATURES.SERVER_STATUS,
  players: FEATURES.ONLINE_PLAYERS,
};
