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
  /**
   * Restart / stop / start the server, and force a world save.
   *
   * Split from moderation on purpose: taking the server down is an operations
   * decision, banning a player is a social one, and an operator should be able to
   * grant either without the other.
   */
  SERVER_CONTROL: 'server.control',
  /** Kick, ban, unban, broadcast. */
  PLAYERS_MODERATE: 'players.moderate',
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
  // Static pak-derived world objects, queried by viewport. Bundled data with no
  // player content in it, so the gate is the same one the map itself uses.
  { pattern: /^world\/objects$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // Whether the bundled data still matches the installed game build. A read at
  // VIEW_BASIC because it qualifies the map everyone is looking at; acknowledging
  // it is a server-wide statement and needs POLICY_MANAGE.
  { pattern: /^world\/build$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // Installed mods — the innocent explanation for unrecognised species ids.
  { pattern: /^world\/mods$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^world\/build\/acknowledge$/, methods: ['POST'], capability: CAPABILITIES.POLICY_MANAGE, feature: null },
  // Re-reads the bundled data files from disk. It reloads, it does not
  // regenerate — see the backend route. POLICY_MANAGE because it changes what
  // every session sees.
  { pattern: /^world\/packs\/reload$/, methods: ['POST'], capability: CAPABILITIES.POLICY_MANAGE, feature: null },
  { pattern: /^world\/objects\/categories$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  { pattern: /^world\/fasttravel$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  { pattern: /^world\/reference$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // Discoveries are VIEW_BASIC because a Player must be able to see their OWN
  // progress. The backend decides what a given role may see of the undiscovered
  // half — the proxy cannot, since that depends on the discoveryVisibility
  // policy and on which character the account is linked to.
  { pattern: /^world\/discoveries$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // A player's own privacy setting. VIEW_BASIC because it is about themselves —
  // gating it higher would mean only staff could choose to hide, which is
  // backwards.
  { pattern: /^privacy\/me$/, methods: ['GET', 'POST'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  { pattern: /^privacy\/hidden$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  // Per-base visibility. Also VIEW_BASIC, because the gate that matters is
  // ownership — the backend checks the caller is the base's guild master.
  { pattern: /^privacy\/bases$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  { pattern: /^privacy\/bases\/[A-Za-z0-9-]+$/, methods: ['POST'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  { pattern: /^roles$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },

  // Storage is VIEW_DETAIL, unlike plain `bases` above — the base list is a map
  // pin, its storage is a full inventory readout.
  { pattern: /^bases\/storage$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^bases\/[A-Za-z0-9-]+\/storage$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  // Literal `export/verify` before the `export/{kind}` pattern: verify is a POST
  // and must not be reachable as a GET export of a kind called "verify".
  { pattern: /^export\/verify$/, methods: ['POST'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^import\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  // Apply requires a planHash query param the backend checks against a fresh
  // re-plan, so this route cannot be used to write without a preview first.
  { pattern: /^import\/apply$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  // The schema is a read: the UI renders its editor from it, so it must not
  // need the write capability just to show what the bounds are.
  { pattern: /^edit\/schema\/(player|pal)$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  // Preview before apply, and apply carries a planHash the backend re-checks.
  { pattern: /^edit\/pal\/[A-Za-z0-9-]+\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/pal\/[A-Za-z0-9-]+$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/player\/[A-Za-z0-9-]+\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/player\/[A-Za-z0-9-]+$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  // Bulk: `pals/bulk` rather than `pal/{id}`, so it can never be reached by a
  // request that looks like a single-Pal edit of a Pal called "bulk".
  { pattern: /^edit\/pals\/bulk\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/pals\/bulk$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/container\/[A-Za-z0-9-]+\/slots\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/container\/[A-Za-z0-9-]+\/slots$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  // Clone routes are literal, and they come BEFORE the `edit/pal/{id}` patterns
  // above would ever be consulted for them — `clone` is not a GUID, so it cannot
  // match `[A-Za-z0-9-]+` ambiguously in a way that matters, but keeping them
  // explicit means a Pal can never be addressed as if it were the clone verb.
  { pattern: /^edit\/pal-containers$/, methods: ['GET'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/pal\/clone\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/pal\/clone$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/pal\/import\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/pal\/import$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  // Scanning for illegal Pals is a read — it is how an admin finds out whether
  // anyone has been cheating, and that must not require the write capability.
  { pattern: /^palcheck\/scan$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^palcheck\/repair\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^palcheck\/repair$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^export\/(world|player|guild|base|container|pal)$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
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

  { pattern: /^server\/(note-shutdown|restart|start-container|stop-container|save|shutdown|force-stop)$/, methods: ['POST'], capability: CAPABILITIES.SERVER_CONTROL, feature: null },
  // Moderation goes through the backend rather than the game-REST proxy, because
  // the backend owns the audit log and these are exactly the actions an operator
  // needs a record of. See backend/moderate.py.
  { pattern: /^moderate\/(announce|kick|ban|unban)$/, methods: ['POST'], capability: CAPABILITIES.PLAYERS_MODERATE, feature: null },
  { pattern: /^moderate\/bans$/, methods: ['GET'], capability: CAPABILITIES.PLAYERS_MODERATE, feature: null },
  // Recurring announcements. Same gate as sending one by hand — the schedule is
  // just a broadcast with a timer in front of it.
  { pattern: /^announcements$/, methods: ['GET', 'POST'], capability: CAPABILITIES.PLAYERS_MODERATE, feature: null },
  { pattern: /^announcements\/\d+$/, methods: ['PATCH', 'DELETE'], capability: CAPABILITIES.PLAYERS_MODERATE, feature: null },
  { pattern: /^announcements\/\d+\/send$/, methods: ['POST'], capability: CAPABILITIES.PLAYERS_MODERATE, feature: null },
  // History is a read at the same gate as the live server status it extends.
  { pattern: /^metrics\/(history|summary)$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },

  // ─── Accounts & audit ───
  { pattern: /^users$/, methods: ['GET', 'POST'], capability: CAPABILITIES.USERS_MANAGE, feature: null },
  { pattern: /^users\/[A-Za-z0-9._-]+$/, methods: ['PATCH', 'DELETE'], capability: CAPABILITIES.USERS_MANAGE, feature: null },
  { pattern: /^audit$/, methods: ['GET'], capability: CAPABILITIES.AUDIT_VIEW, feature: null },
  { pattern: /^auth\/password$/, methods: ['POST'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  // A remapped copy of the world. BACKUP_MANAGE because the output contains every
  // player's data — the same disclosure a backup is — even though it only reads
  // the live world and never writes to it.
  // Teleport is a save edit, not a game command — the game's own teleport is
  // anchored to an admin's in-game character, which a dashboard does not have.
  { pattern: /^teleport$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^teleport\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^teleport\/destinations$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^export\/world-copy$/, methods: ['POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  { pattern: /^export\/world-copy\/preview$/, methods: ['POST'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
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
