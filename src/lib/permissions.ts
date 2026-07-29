/**
 * Capability and feature names, and the backend route allowlist.
 *
 * Pure constants and pure functions only — this module is imported by client
 * components, so it must never touch `fs` or the policy file.
 *
 * `backend/roles.py` is the authority for who gets what; this mirrors the names
 * for the UI and for routing decisions. The backend re-checks every capability
 * itself, so a mistake here cannot grant access it should not.
 */

export const CAPABILITIES = {
  /** Read live server status, map, bases. */
  VIEW_BASIC: 'view.basic',
  /** Read player saves, inventories, item totals, breeding. */
  VIEW_DETAIL: 'view.detail',
  /** Read one's own character only. */
  VIEW_SELF: 'view.self',
  /** Kick/ban/announce/restart through the game's REST API. */
  SERVER_CONTROL: 'server.control',
  /** Read and write PalWorldSettings.ini. */
  SETTINGS_WRITE: 'settings.write',
  /** Create, restore and delete save backups. */
  BACKUP_MANAGE: 'backup.manage',
  /** Change the access policy itself. */
  POLICY_MANAGE: 'policy.manage',
  /** Create and modify accounts. */
  USERS_MANAGE: 'users.manage',
  /** Read the audit log. */
  AUDIT_VIEW: 'audit.view',

  /** Sort/merge plain stackable items. Cannot touch equipment. */
  SAVE_SORT_STACKABLES: 'save.sort.stackables',
  /** Sort/merge every item including durability-bearing equipment. */
  SAVE_SORT_ALL: 'save.sort.all',
  /** Arbitrary edits to players, Pals and container slots. */
  SAVE_EDIT_FULL: 'save.edit.full',
} as const;

export type Capability = (typeof CAPABILITIES)[keyof typeof CAPABILITIES];

/** Write capabilities that the security level gates, on top of the role. */
export const POLICY_GATED: Capability[] = [
  CAPABILITIES.SETTINGS_WRITE,
  CAPABILITIES.BACKUP_MANAGE,
  CAPABILITIES.SAVE_SORT_STACKABLES,
  CAPABILITIES.SAVE_SORT_ALL,
  CAPABILITIES.SAVE_EDIT_FULL,
];

/**
 * Visibility features a guest can be granted or denied individually. Signed-in
 * users are governed by their role instead.
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
 * Backend routes reachable through the save proxy.
 *
 * This is an ALLOWLIST, not a pattern match with a default. Previously an
 * unmatched path fell through to a default capability, which meant a new backend
 * route was reachable the moment it existed — and a path such as
 * `..%2F..%2Fauth%2Flogin` matched nothing and took the default branch. Anything
 * not named here is now refused outright.
 *
 * `feature` is the guest visibility toggle; `null` means signed-in only.
 */
interface RouteRule {
  pattern: RegExp;
  methods: string[];
  capability: Capability;
  feature: Feature | null;
}

const ROUTES: RouteRule[] = [
  // ─── Reads ───
  { pattern: /^health$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^bases$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.BASES },
  { pattern: /^guilds$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.GUILDS },
  { pattern: /^mapobjects$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  { pattern: /^world\/fasttravel$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  { pattern: /^world\/reference$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^roles$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },

  // Storage is VIEW_DETAIL, unlike plain `bases` above — the base list is a map
  // pin, its storage is a full inventory readout.
  { pattern: /^bases\/storage$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^bases\/[A-Za-z0-9-]+\/storage$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^reports$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^reports\/[a-z-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^items$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^pals$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.BREEDING },
  { pattern: /^breeding\/[a-z]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.BREEDING },
  { pattern: /^players$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^players\/[A-Za-z0-9-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^progress$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^inventory\/[A-Za-z0-9-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },

  // ─── Writes ───
  { pattern: /^refresh$/, methods: ['POST'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  { pattern: /^policy$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^policy$/, methods: ['POST'], capability: CAPABILITIES.POLICY_MANAGE, feature: null },

  { pattern: /^settings\/ini$/, methods: ['GET'], capability: CAPABILITIES.SETTINGS_WRITE, feature: null },
  { pattern: /^settings\/ini$/, methods: ['POST'], capability: CAPABILITIES.SETTINGS_WRITE, feature: null },
  { pattern: /^settings\/preset\/[a-z0-9_]+$/, methods: ['POST'], capability: CAPABILITIES.SETTINGS_WRITE, feature: null },

  { pattern: /^backups$/, methods: ['GET'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^backup$/, methods: ['POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  // Literal sub-paths come first so `prune` and `schedule` are never captured
  // by the `{backup_id}` patterns below.
  { pattern: /^backups\/prune$/, methods: ['POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^backups\/schedule\/config$/, methods: ['GET', 'POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^backups\/[A-Za-z0-9]+$/, methods: ['GET', 'PATCH', 'DELETE'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^backups\/[A-Za-z0-9]+\/verify$/, methods: ['POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^backups\/[A-Za-z0-9]+\/preview$/, methods: ['GET'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^backups\/[A-Za-z0-9]+\/download$/, methods: ['GET'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^restore\/[A-Za-z0-9]+$/, methods: ['POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },

  { pattern: /^edit\/sort\/stackables$/, methods: ['POST'], capability: CAPABILITIES.SAVE_SORT_STACKABLES, feature: null },
  { pattern: /^edit\/sort\/all$/, methods: ['POST'], capability: CAPABILITIES.SAVE_SORT_ALL, feature: null },
  { pattern: /^edit$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },

  { pattern: /^server\/(note-shutdown|restart|start-container|stop-container)$/, methods: ['POST'], capability: CAPABILITIES.SERVER_CONTROL, feature: null },

  // ─── Accounts & audit ───
  { pattern: /^users$/, methods: ['GET', 'POST'], capability: CAPABILITIES.USERS_MANAGE, feature: null },
  { pattern: /^users\/[A-Za-z0-9._-]+$/, methods: ['PATCH', 'DELETE'], capability: CAPABILITIES.USERS_MANAGE, feature: null },
  { pattern: /^audit$/, methods: ['GET'], capability: CAPABILITIES.AUDIT_VIEW, feature: null },
  { pattern: /^auth\/password$/, methods: ['POST'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
];

export interface RouteVerdict {
  allowed: boolean;
  capability?: Capability;
  feature?: Feature | null;
  reason?: string;
}

/**
 * Look up a backend path.
 *
 * Rejects anything containing a path traversal attempt before matching, so an
 * encoded `..` cannot reach the backend even if some future rule would have
 * matched the decoded form.
 */
export function describeSavePath(path: string, method: string): RouteVerdict {
  if (!path || path.includes('..') || path.includes('//') || path.startsWith('/')) {
    return { allowed: false, reason: 'Invalid path' };
  }

  const candidates = ROUTES.filter((r) => r.pattern.test(path));
  if (candidates.length === 0) {
    return { allowed: false, reason: 'Unknown endpoint' };
  }

  const match = candidates.find((r) => r.methods.includes(method));
  if (!match) {
    return { allowed: false, reason: `${method} is not allowed on this endpoint` };
  }

  return { allowed: true, capability: match.capability, feature: match.feature };
}

/** Palworld REST paths a guest may read, subject to their visibility toggles. */
export const REST_GUEST_FEATURES: Record<string, Feature> = {
  info: FEATURES.SERVER_STATUS,
  metrics: FEATURES.SERVER_STATUS,
  players: FEATURES.ONLINE_PLAYERS,
};
