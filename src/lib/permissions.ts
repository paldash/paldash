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
  // The Paldeck is bundled reference data about the game, not about this
  // server, so it reveals nothing a wiki would not and needs no parsed world.
  // The merged roster: everyone in the save, annotated with who is online and
  // (for callers who could act on it) who already has a dashboard account.
  // VIEW_DETAIL like the plain player list — it is the same population.
  { pattern: /^players\/roster$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^world\/paldeck$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/passives$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/research$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/passives\/effects$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/paldeck\/[A-Za-z0-9_]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/objects\/categories$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  { pattern: /^world\/fasttravel$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // The effigy counterpart, and it carries the same caveat: VIEW_BASIC here, with
  // the backend applying `discoveryVisibility` per category. It is the map's
  // fallback when /world/discoveries is unavailable — which it is for every
  // guest, since that route requires a real account.
  { pattern: /^world\/effigies$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // Guild-scoped server-side: the endpoint returns only the caller's own guilds'
  // markers (staff excepted), so VIEW_BASIC here is the gate on reaching it at
  // all, not on what comes back.
  { pattern: /^world\/guildmarkers$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // Field bosses. VIEW_BASIC, and deliberately NOT discovery-filtered: effigies
  // and fast travel are collectables the save tracks per player, so hiding the
  // undiscovered ones is a meaningful setting. A field boss respawns and is
  // never collected — there is no per-player record to filter against, and
  // inventing one would be worse than showing them all.
  { pattern: /^world\/bosses$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // Placed NPCs by role. Same gate and the same non-filtering as bosses: a
  // spawn point is not a collectable, so there is no per-player discovery
  // state to hide and inventing one would be worse than showing them.
  { pattern: /^world\/npcs$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.MAP_OBJECTS },
  // Raid bosses. Reference data about the game like the Paldeck, and NOT a map
  // layer — they are altar-summoned and have no world position, so SERVER_STATUS
  // rather than MAP_OBJECTS.
  { pattern: /^world\/raidbosses$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // Base raid groups. Reference data too, and for a stronger reason than raid
  // bosses: it makes no per-base claim at all, because neither the grade nor
  // the biome can be joined to a save.
  { pattern: /^world\/invaders$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/reference$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // Every boss in one list with its counters. Catalogue data like the map
  // layers it draws from, so the same gate.
  { pattern: /^world\/encounters$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // Ranking the GAME at a chosen build. Catalogue data, so the same gate as the
  // rest of `world/` — `optimise/*` is the roster-scoped version and is gated
  // on what somebody may see of this world instead.
  { pattern: /^world\/builds$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  { pattern: /^world\/builds\/compare$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // What a passive set resists. Describes passives and the type chart rather
  // than this world, so the same catalogue gate — nothing about a Pal somebody
  // owns travels through it.
  { pattern: /^world\/resistances$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // The item *catalogue* — what the game has. Not /items, which reports what
  // this world holds and is privacy-filtered per guild.
  { pattern: /^world\/items$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // Structures are not items — separate table, separate catalogue. Same gate:
  // reference data about what the game has, needing no parsed world.
  { pattern: /^world\/structures$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // Where one item comes from — recipes, drops, chests, merchants. Same
  // catalogue, same gate: it describes the game, not this world.
  { pattern: /^world\/items\/[A-Za-z0-9_]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
  // ...and the recursive version of the same answer, all the way to raw
  // materials. Listed separately rather than widening the pattern above: the
  // allowlist is explicit by design, and a trailing segment matched by a `.*`
  // is how an unintended route becomes reachable.
  { pattern: /^world\/items\/[A-Za-z0-9_]+\/tree$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: FEATURES.SERVER_STATUS },
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

  // VIEW_SELF, scoped by the backend to the caller's own guild's bases. Your
  // own base's contents are something you can walk up to in game, and gating
  // them at VIEW_DETAIL left a Player seeing their guild's *total* Wood on the
  // Items tab but not which of their own chests it was in.
  //
  // Still narrower than the plain `bases` list above, which is a map pin —
  // `baseVisibility` opening the map does NOT hand out other guilds' contents.
  { pattern: /^bases\/storage$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.ITEMS },
  { pattern: /^bases\/[A-Za-z0-9-]+\/storage$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.ITEMS },
  // Same capability and feature as the storage routes above, because it is the
  // same disclosure in a different shape — per-base container contents.
  { pattern: /^bases\/supply$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.ITEMS },
  // And so is "what could this guild craft" — it is derived from exactly those
  // contents, so it discloses them in aggregate and takes the same two gates.
  { pattern: /^bases\/craftable$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.ITEMS },
  // Work assignment discloses two things at once — which structures stand at a
  // base, and which Pals the caller owns — so it takes the stricter of the two
  // gates each half already has: VIEW_SELF, and the backend scopes bases through
  // the base-privacy filter and Pals through `_scope_pals`. Gated on BASES
  // rather than ITEMS because it names no container contents.
  { pattern: /^bases\/assign$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.BASES },
  // Who the game has ACTUALLY assigned, from WorkSaveData. The sibling of
  // /bases/assign — that one ranks who should work where, this reports who is.
  { pattern: /^bases\/working$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.BASES },
  // Rankings over the caller's own Pals — same scope and same feature gate as
  // the breeding planner, which reads the same list.
  { pattern: /^optimise\/work$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.BREEDING },
  { pattern: /^optimise\/combat$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.BREEDING },
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
  // Creating equipment or an egg. Same capability as a slot edit, but a distinct
  // route and a distinct audit action — this is the one operation that puts an
  // item into the world that was never obtained in it.
  // Pals that need attention — sick, starving, injured, low sanity. Same
  // capability as /pals because it is the same data narrowed to a problem, and
  // it is scoped through the same helper.
  { pattern: /^welfare$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  { pattern: /^edit\/container\/[A-Za-z0-9-]+\/create\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/container\/[A-Za-z0-9-]+\/create$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  // Guild membership. SAVE_EDIT_FULL because it rewrites Level.sav and can
  // re-home a guild's bases — a heavier thing than it sounds, which is why the
  // preview is a separate route and the apply requires its hash.
  { pattern: /^edit\/guild\/move\/preview$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
  { pattern: /^edit\/guild\/move$/, methods: ['POST'], capability: CAPABILITIES.SAVE_EDIT_FULL, feature: null },
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
  // `pal` and `player` sit at VIEW_SELF because exporting *your own* character
  // is the same class of act as reading your own palbox. The backend does the
  // ownership check — this allowlist only decides whether the request may reach
  // it at all, and it cannot tell whose id is in the query string.
  { pattern: /^export\/(player|pal)$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  { pattern: /^export\/(world|guild|base|container)$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  { pattern: /^reports$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^reports\/[a-z-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: FEATURES.ITEMS },
  { pattern: /^items\/scopes$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.ITEMS },
  { pattern: /^items$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.ITEMS },
  // VIEW_SELF, not VIEW_DETAIL: a Player must be able to see their own palbox.
  // The backend pins them to their own character below the allPalsVisibility
  // threshold, so the wider gate here does not widen what comes back.
  { pattern: /^pals$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.BREEDING },
  // Same reasoning as `pals`: the planner scoped to your own box is a Player
  // feature, and the backend decides whose Pals it actually reads.
  { pattern: /^breeding\/[a-z]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: FEATURES.BREEDING },
  { pattern: /^players$/, methods: ['GET'], capability: CAPABILITIES.VIEW_DETAIL, feature: null },
  // VIEW_SELF for your own uid only; the backend rejects anyone else's.
  { pattern: /^players\/[A-Za-z0-9-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  // A player's own item containers. Same scoping — own at VIEW_SELF, anyone
  // else's at VIEW_DETAIL, enforced by the backend which knows whose uid it is.
  { pattern: /^players\/[A-Za-z0-9-]+\/containers$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  // VIEW_SELF: the backend narrows this to the caller's own row below
  // VIEW_DETAIL rather than refusing, so a Player can see their own
  // progression. The denominators are still computed across everyone —
  // narrowing those would leak how much the players you cannot see have found.
  { pattern: /^progress$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  // Named progression checklists — WHICH bosses and regions are left, not just
  // how many. Same gate as the summary it details, feature gate included: a
  // stricter one here would make the detail unreachable wherever the summary
  // works, which reads as a broken tab rather than as a policy.
  { pattern: /^progress\/detail$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  // The Paldeck checklist, split from `detail` because it is 204 rows with a
  // route each. Same gate as the summary it details — a stricter one makes the
  // tab unreachable wherever the counts work, which reads as broken.
  { pattern: /^progress\/paldeck$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },
  // Same scoping as base storage above: a Player reaches containers belonging
  // to their own guild's bases and nothing else. The backend enforces it — this
  // allowlist cannot tell whose container an id names.
  { pattern: /^inventory\/[A-Za-z0-9-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_SELF, feature: null },

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
  // The guilds an export could keep or drop. Same capability as the export
  // itself: the list names every guild on the server, which is the same
  // disclosure the copy is.
  { pattern: /^export\/world-copy\/guilds$/, methods: ['GET'], capability: CAPABILITIES.BACKUP_MANAGE, feature: null },
  // The game's own display names. Reference data like the catalogues beside
  // it — what Palworld has, not what this world holds — so VIEW_BASIC.
  { pattern: /^world\/languages$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
  { pattern: /^world\/language\/[A-Za-z-]+$/, methods: ['GET'], capability: CAPABILITIES.VIEW_BASIC, feature: null },
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
