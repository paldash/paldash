// ─── Palworld REST API Types ────────────────────────────

export interface ServerInfo {
  version: string;
  servername: string;
  description: string;
  worldguid: string;
}

export interface ServerMetrics {
  serverfps: number;
  currentplayernum: number;
  maxplayernum: number;
  uptime: number;
  frametime: number;
}

export interface Player {
  name: string;
  accountName: string;
  playerId: string;
  odlerlookup: string;
  userId: string;
  ip: string;
  ping: number;
  location_x: number;
  location_y: number;
  level: number;
}

export interface ServerSettings {
  [key: string]: string | number | boolean;
}

/**
 * The game's own ban list, read from its file.
 *
 * `found: false` with a `note` rather than an empty array, because an empty list
 * and "we could not find the file" look identical to a reader and mean very
 * different things.
 */
export interface BanList {
  found: boolean;
  path: string;
  bans: string[];
  note: string;
}

/** One bucket of history. Nulls are real: they mean nothing was recorded. */
export interface MetricsPoint {
  ts: number;
  samples: number;
  serverFps: number | null;
  frameTime: number | null;
  playersPeak: number | null;
  playersAvg: number | null;
  cpuPercent: number | null;
  memUsedMb: number | null;
  memTotalMb: number | null;
  diskFreeMb: number | null;
  worldSizeMb: number | null;
  palCount: number | null;
  baseCount: number | null;
  /**
   * Fraction of the bucket the game answered in, 0..1 — not a boolean.
   * Anything below 1 is a partial outage, which is what an intermittently
   * crashing server looks like and would be invisible as a flag.
   */
  reachable: number | null;
  /**
   * The GAME process's resident memory.
   *
   * **Not `memUsedMb`**, which is the cgroup's figure — this container, the
   * dashboard. Palworld's server leaks, and the leak happens in a process that
   * number does not describe.
   *
   * `null` whenever the dashboard cannot see the process, which is the ordinary
   * container deployment: no shared PID namespace, so the game's `/proc` entries
   * are simply not there. It must render as absent rather than 0.
   */
  gameMemMb: number | null;
  /** Swap in use. `swapTotalMb` of 0 means the box has none. */
  swapUsedMb: number | null;
  swapTotalMb: number | null;
  /**
   * Percentage of CPU time the hypervisor gave to someone else.
   *
   * The signal nothing else here substitutes for: on a rented VPS a high figure
   * says the stutter is the host being oversubscribed rather than the operator's
   * doing. 0 on bare metal is a real answer; `null` means unmeasured.
   */
  cpuSteal: number | null;
  netRxKbs: number | null;
  netTxKbs: number | null;
  /** Hottest CPU thermal zone. `null` under most virtualisation — never 0. */
  cpuTempC: number | null;
}

export interface MetricsHistory {
  hours: number;
  bucketSeconds: number;
  retentionDays: number;
  intervalSeconds: number;
  enabled: boolean;
  points: MetricsPoint[];
}

export interface MetricsSummary {
  enabled: boolean;
  intervalSeconds: number;
  retentionDays: number;
  samples: number;
  oldest: number | null;
  newest: number | null;
  /** Over the retained window only. Never present this as an all-time figure. */
  uptimeFraction: number | null;
}

// ─── Save Data Types ────────────────────────────────────

export interface BaseCamp {
  id: string;
  name: string;
  /** False when `name` is our positional fallback for the game's placeholder. */
  playerNamed?: boolean;
  guildId: string;
  guildName: string;
  x: number;
  y: number;
  z: number;
  radius: number;
  /**
   * Pals actually **working at this base**, from its own worker container.
   *
   * Safe to sum across bases. 165 of 1,905 on the reference world — most Pals
   * sit in a palbox, which is a guild-level thing rather than a base one.
   */
  palCount?: number;
  /**
   * Pals in this base's **guild**, not at this base.
   *
   * A guild total repeated on each of its bases — never sum it across bases, or
   * a guild with three bases reports three times its Pals. Optional because a
   * cache written before the field existed does not carry it; the backend
   * discards such a cache now, and `?? 0` is the belt to that braces.
   */
  guildPalCount?: number;
  /**
   * How many workers this base can hold — the denominator for `palCount`.
   *
   * `SlotNum` on the base's own worker container, so it is the game's answer
   * for **this** base after applying both the server's `BaseCampWorkerMaxNum`
   * and the base's level. It is not derived from a setting, and a server-wide
   * setting could not answer it: measured capacities run 8–25 across four real
   * worlds.
   *
   * **Absent means "not known", never zero.** The worker container may fail to
   * resolve, and a base rendered as `n/0` reads as infinitely full. Show the
   * bare count when this is missing.
   */
  workerCapacity?: number;
  containerIds: string[];
  storedItemCount?: number;
  usedSlots?: number;
  totalSlots?: number;
}

/** One container owned by a base, as summarised during the parse. */
export interface BaseContainer {
  containerId: string;
  kind: string;
  kindName: string;
  category: string | null;
  usedSlots: number;
  totalSlots: number;
  itemCount: number;
}

export interface BaseStorage {
  baseId: string;
  baseName: string;
  guildId: string;
  guildName: string;
  containerCount: number;
  usedSlots: number;
  totalSlots: number;
  fillPercent: number;
  itemCount: number;
  uniqueItems: number;
  items: { itemId: string; itemName: string; count: number }[];
  containers: BaseContainer[];
}

export interface GuildInfo {
  id: string;
  name: string;
  members: GuildMember[];
  baseCampIds: string[];
  /**
   * The guild's `base_camp_level`, straight from the save.
   *
   * **A GUILD figure, not a per-base one**, and it must stay that way. Base
   * level is not stored anywhere per base — neither `BaseCampSaveData` nor the
   * palbox it points at carries one, checked on 11 of 11 — so dividing this by
   * the base count or stamping it on each base would invent a number. That is
   * the `guildPalCount` mistake, which this project already made once.
   *
   * What it MEANS is not established: it scales with base count, so "sum of the
   * guild's base levels" is the obvious reading and exactly the kind of
   * inference that needs evidence. Shown as the game's own number, unlabelled
   * by interpretation.
   */
  baseCampLevel?: number;
  /**
   * The server's `GuildPlayerMaxNum`, from its INI.
   *
   * **A SETTING, not a game constraint** — it reads like a rule of the game and
   * is not, so it comes from the operator's INI rather than any bundled table.
   * `null`/absent means the INI could not be read, which is the common
   * deployment: show no denominator rather than a guessed one.
   */
  memberCap?: number | null;
  /** Rank indices allowed to open the guild chest, from the save. */
  chestAllowedRoles?: number[];
  /** Those indices named — "Sub Master", "Member". Never a bare number. */
  chestAllowedRoleNames?: string[];
  /** How many ranks the game has, so "2 of 4" does not imply a total. */
  roleCount?: number | null;
  /**
   * Per-rank permission indices, straight from the save and **deliberately
   * unnamed**. The game has eight permissions and the save uses indices 0-7, so
   * the count agrees — but nothing establishes the ORDER, and a guessed mapping
   * would tell an operator a rank can kick players when it cannot.
   */
  rolePermissions?: { role: number; permissions: number[] }[];
}

export interface GuildMember {
  uid: string;
  name: string;
  level: number;
  isOnline: boolean;
}

/**
 * One row of the merged roster: everyone in the save, annotated with live state.
 *
 * The live REST list only knows who is connected right now, which is the wrong
 * population for account management — the player you want to add an account for
 * is usually the one who logged off.
 */
export interface RosterPlayer {
  uid: string;
  name: string;
  level: number;
  online: boolean;
  /** The id kick/ban take, which is not always spelled like the save's uid. */
  restUserId: string;
  ping: number | null;
  /** Only present for callers who could act on it (`users.manage`). */
  hasAccount?: boolean;
  accountUsername?: string;
}

export interface PlayerRoster {
  players: RosterPlayer[];
  onlineCount: number;
  gameApiReachable: boolean;
  canManageAccounts: boolean;
}

export interface PlayerSaveData {
  uid: string;
  name: string;
  level: number;
  hp: number;
  maxHp: number;
  stamina: number;
  maxStamina: number;
  hunger: number;
  sanity: number;
  exp: number;
  inventoryInfo: InventoryInfo;
  palStorage: PalInfo[];
  technologyPoints: number;
  /**
   * `Steam`, `Xbox`, `PS5`, `Mac`, or "" on a save that predates the field.
   *
   * Empty rather than defaulting to Steam — see `docs/CROSSPLAY.md`. Everything
   * here has only ever run against Steam accounts, so this is what makes a
   * console player visible instead of something to infer.
   */
  platform?: string;
  unlockedRecipes: string[];
  /** Progression counters read from the player's own .sav. */
  progress?: Record<string, unknown> & {
    technologyPoints?: number;
    ancientTechnologyPoints?: number;
  };
}

export interface InventoryInfo {
  commonItems: InventorySlot[];
  weapons: InventorySlot[];
  armor: InventorySlot[];
  food: InventorySlot[];
  keyItems: InventorySlot[];
}

export interface InventorySlot {
  slotIndex: number;
  itemId: string;
  /** Resolved at request time, so refreshing game data updates it. */
  itemName: string;
  stackCount: number;
  durability: number;
  maxDurability: number;
  isEmpty: boolean;
  icon?: string;
  /** The game's real stack ceiling; 0 when unknown. */
  maxStack?: number;
}

export interface PalInfo {
  instanceId: string;
  characterId: string;
  /** Species with any BOSS_/PREDATOR_ prefix stripped. */
  speciesId?: string;
  /** In-game species name resolved from bundled game data, e.g. "Lamball". */
  speciesName?: string;
  icon?: string;
  elements?: string[];
  paldeckNumber?: number;
  /** Passive skills as display names, parallel to `passiveSkills`. */
  passiveSkillNames?: string[];
  nickname: string;
  level: number;
  exp: number;
  gender: 'Male' | 'Female';
  hp: number;
  maxHp: number;
  attack: number;
  defense: number;
  workSpeed: number;
  passiveSkills: string[];
  activeSkills: string[];
  isInParty: boolean;
  isInBase: boolean;
  baseId?: string;
}

/** One editable field, as the backend describes it. */
export interface EditField {
  name: string;
  /**
   * `clear` is the odd one and the reason this union is worth reading. It has
   * exactly one legal value — `null` — because a healthy Pal has no
   * `WorkerSick` property at all, so curing is a deletion rather than a write.
   * There is no value to type into a box, which is why the editor renders it as
   * a button.
   */
  kind: 'int' | 'float' | 'string' | 'enum' | 'list' | 'bool' | 'clear' | 'map';
  label: string;
  min: number | null;
  max: number | null;
  choices: string[] | null;
  note: string;
}

export interface EditSchema {
  target: string;
  fields: EditField[];
  readOnly: string[];
  /** `{level: [minExp, maxExp]}` — maxExp is null only past the table. */
  expBands: Record<string, [number, number | null]>;
  maxLevel: number;
}

export interface EditChange {
  field: string;
  label: string;
  before: unknown;
  after: unknown;
}

export interface EditPlan {
  ok: boolean;
  problems: { field: string | null; problem: string }[];
  changes: EditChange[];
  fieldsChanged?: number;
  planHash: string;
  crossFieldChecked?: boolean;
  instanceId?: string;
  uid?: string;
  applied?: boolean;
  /** A player edit can span Level.sav and the player's own .sav. */
  touchesLevelSav?: boolean;
  touchesPlayerSave?: boolean;
}

export interface EditResult {
  ok: boolean;
  applied: boolean;
  instanceId?: string;
  uid?: string;
  filesWritten?: string[];
  fieldsChanged: number;
  changes: EditChange[];
  backupId: string;
  verified: boolean;
}

export interface DiscoveryPoint {
  x: number;
  y: number;
  z?: number;
  /** True when the selected player(s) have already found this one. */
  discovered: boolean;
  /** Fast travel only. */
  name?: string;
  key?: string;
  /** Effigies only — the instance GUID the save keys on. */
  guid?: string;
  kind?: string;
  /**
   * Effigies only — the game's own relic artwork for this kind, served by
   * `gamedata.effigy_kind_icon`. Empty string when it does not resolve, which
   * the map treats as "draw the shape", exactly as all 396 were drawn before.
   */
  icon?: string;
  /**
   * The kind in words — "Lamball Effigy", not `BP_LevelObject_Relic_SheepBall`.
   *
   * Resolved by the backend rather than prettified here, because the suffix is
   * a species *id* and only `gamedata` knows that `SheepBall` means Lamball.
   * The generic class-name tidier produced "Relic Sheep Ball" — de-underscoring
   * is not naming.
   */
  kindName?: string;
  landmass?: string;
}

export interface Discoveries {
  scope: string;
  /** False when the account has no linked character, so nothing reads as found. */
  linkedToPlayer: boolean;
  discoveryVisibility: string;
  /** Whether the server sent the undiscovered half at all. */
  showsUndiscovered: boolean;
  fastTravel: { total: number; found: number; points: DiscoveryPoint[] };
  effigies: { total: number; found: number; points: DiscoveryPoint[] };
}

export interface PalContainer {
  containerId: string;
  capacity: number;
  used: number;
  /**
   * `capacity - used`, not a count of empty entries — there are none. The slot
   * array holds only occupied slots, so a clone appends rather than fills.
   */
  free: number;
}

export interface ClonePlan {
  ok: boolean;
  problems: { field: string | null; problem: string }[];
  instanceId?: string;
  containerId?: string;
  count?: number;
  source?: { speciesName: string; nickname: string; level: number };
  slotIndices?: number[];
  capacity?: number;
  usedBefore?: number;
  freeAfter?: number;
  changes?: EditChange[];
  planHash: string;
  applied?: boolean;
}

/**
 * A field the document carried that the import will not write, and why.
 *
 * Rendered, not swallowed. An export contains a Pal's owner, container and slot;
 * an import cannot set any of them, and someone moving a Pal between servers
 * would reasonably assume ownership came along.
 */
export interface IgnoredField {
  field: string | null;
  problem: string;
  instanceId?: string;
}

export interface PalImportPlan {
  ok: boolean;
  problems: { field?: string | null; instanceId?: string | null; problem: string }[];
  ignored?: IgnoredField[];
  mode?: 'overwrite' | 'create';
  /** overwrite mode: the per-Pal diff, same shape the bulk editor shows. */
  pals?: BulkEditPal[];
  palsChanged?: number;
  palsUnchanged?: number;
  fieldsChanged?: number;
  /** create mode: the same-species Pal whose record will be copied. */
  templateInstanceId?: string;
  speciesId?: string;
  source?: { speciesName: string; nickname: string; level: number };
  slotIndices?: number[];
  containerId?: string;
  planHash: string;
  applied?: boolean;
}

export interface PalImportResult {
  ok: boolean;
  applied: boolean;
  mode: 'overwrite' | 'create';
  ignored?: IgnoredField[];
  backupId: string;
  verified: boolean;
  /** create mode. */
  newInstanceIds?: string[];
  slotIndices?: number[];
  /** overwrite mode. */
  palsChanged?: number;
  fieldsChanged?: number;
}

export interface CloneResult {
  ok: boolean;
  applied: boolean;
  sourceInstanceId: string;
  containerId: string;
  count: number;
  newInstanceIds: string[];
  slotIndices: number[];
  backupId: string;
  verified: boolean;
}

/** One Pal's slice of a batch plan. */
export interface BulkEditPal {
  instanceId: string;
  nickname: string;
  changes: EditChange[];
}

export interface BulkEditPlan {
  ok: boolean;
  problems: { instanceId: string | null; field: string | null; problem: string }[];
  pals: BulkEditPal[];
  palsChanged?: number;
  /** Already at the target values — not a failure, just nothing to do. */
  palsUnchanged?: number;
  fieldsChanged?: number;
  planHash: string;
  autoExp?: boolean;
  applied?: boolean;
}

export interface BulkEditResult {
  ok: boolean;
  applied: boolean;
  palsChanged: number;
  palsUnchanged: number;
  fieldsChanged: number;
  pals: BulkEditPal[];
  backupId: string;
  verified: boolean;
}

/** An empty `itemId` or a zero `stackCount` clears the slot. */
export interface SlotPatch {
  slotIndex: number;
  itemId: string;
  stackCount: number;
}

export interface SlotChange {
  slotIndex: number;
  before: { itemId: string; itemName: string; stackCount: number };
  after: { itemId: string; itemName: string; stackCount: number };
  action: 'add' | 'clear' | 'replace' | 'increase' | 'decrease';
}

export interface SlotEditPlan {
  ok: boolean;
  containerId: string;
  problems: { slotIndex: number | null; problem: string }[];
  changes: SlotChange[];
  slotsChanged?: number;
  itemsBefore?: number;
  itemsAfter?: number;
  planHash: string;
  summary?: string;
}

export interface SlotEditResult {
  ok: boolean;
  applied: boolean;
  containerId: string;
  slotsChanged: number;
  itemsBefore: number;
  itemsAfter: number;
  backupId: string;
  verified: boolean;
}

export interface PalIssue {
  code: string;
  field: string;
  found: unknown;
  detail: string;
  /** False for passive-skill lists — reported, never written. */
  repairable: boolean;
  /**
   * Informational rather than evidence of anything. An id the bundled tables do
   * not list usually means our data is incomplete, not that someone cheated:
   * 13 of the reference world's own characters are ordinary NPCs missing from
   * them. Advisories are never counted in `palsFlagged`.
   */
  advisory: boolean;
  fix: unknown;
}

export interface FlaggedPal {
  instanceId: string;
  speciesId: string;
  speciesName: string;
  nickname: string;
  level: number;
  ownerUid: string;
  ownerName: string;
  issues: PalIssue[];
  repairable: boolean;
}

export interface PalCheckScan {
  palsScanned: number;
  palsFlagged: number;
  palsRepairable: number;
  issueCount: number;
  byCode: Record<string, number>;
  byOwner: Record<string, number>;
  /** Stat violations only — the reliable signal. */
  pals: FlaggedPal[];
  /** Unrecognised ids. Reported, never counted as cheating. */
  advisories: FlaggedPal[];
  palsUnrecognised: number;
  bounds: {
    maxLevel: number;
    maxIv: number;
    rank: [number, number];
    maxPassives: number;
  };
}

export interface PalRepairPlan extends BulkEditPlan {
  palsToRepair?: number;
  /** Flagged Pals keeping a problem this build cannot fix by writing. */
  palsWithUnfixableIssues: number;
  unfixable: {
    instanceId: string;
    nickname: string;
    speciesName: string;
    issues: PalIssue[];
  }[];
}

export interface PalRepairResult extends BulkEditResult {
  palsWithUnfixableIssues: number;
  unfixable: PalRepairPlan['unfixable'];
}

export interface ContainerContents {
  containerId: string;
  slots: InventorySlot[];
  capacity: number;
  usedSlots: number;
}

export interface BackupInfo {
  id: string;
  timestamp: string;
  description: string;
  /** manual | pre-edit | pre-restore | schedule:<frequency> */
  trigger: string;
  createdBy: string | null;
  sizeBytes: number;
  uncompressedBytes: number;
  fileCount: number;
  worldGuid: string;
  /** Backups taken on a live server are best-effort snapshots. */
  serverWasRunning: boolean;
  compressionRatio: number | null;
}

export interface BackupListing {
  backups: BackupInfo[];
  usage: {
    count: number;
    totalBytes: number;
    oldest: string | null;
    newest: string | null;
    directory: string;
  };
  scopes: Record<string, string>;
  retention: Record<string, number>;
}

export interface BackupDetail extends BackupInfo {
  files: { path: string; size: number; sha256: string }[];
}

export interface BackupVerification {
  ok: boolean;
  problems: string[];
  checkedFiles: number;
  expectedFiles?: number;
}

export interface RestorePreview {
  backupId: string;
  scope: string;
  scopeDescription: string;
  timestamp: string;
  serverWasRunning: boolean;
  changes: {
    path: string;
    action: 'replace' | 'create' | 'identical';
    size: number;
    currentSize?: number;
  }[];
  summary: { replace: number; create: number; identical: number };
  /** Files on disk the backup does not contain. A restore leaves these alone. */
  keptUntouched: { path: string; size: number }[];
}

export interface PruneResult {
  kept: number;
  removed: { id: string; timestamp: string; sizeBytes: number }[];
  freedBytes: number;
  rules: Record<string, number>;
  dryRun: boolean;
}

export interface BackupSchedule {
  enabled: boolean;
  frequency: string;
  pruneAfter: boolean;
  lastRun: string | null;
  lastResult: string | null;
  nextRun: string | null;
  frequencies: string[];
}

// ─── Dashboard State Types ──────────────────────────────

export type ServerStatus = 'online' | 'offline' | 'starting' | 'stopping' | 'unknown';

export interface FpsHistoryPoint {
  timestamp: number;
  fps: number;
  frameTime: number;
  playerCount: number;
}

export type DashboardTab =
  | 'overview'
  | 'map'
  | 'players'
  | 'bases'
  | 'items'
  | 'breeding'
  | 'paldeck'
  | 'progress'
  | 'mypals'
  | 'settings'
  | 'access'
  | 'backups'
  | 'users'
  | 'audit'
  | 'account'
  | 'editor';

/**
 * One static world object from the game pak: an ore node, chest, fishing spot or
 * oil field. Positions are fixed per game build — the save supplies state (mined,
 * looted), these supply existence.
 */
export interface StaticWorldObject {
  cls: string;
  category: string;
  x: number;
  y: number;
  z: number;
  landmass: string;
  /** Field bosses only: the `BOSS_…` species the spawner sheet references. */
  species?: string;
  /** That species resolved to what a player reads, e.g. `Univolt`. */
  speciesName?: string;
  /** The Pal's own artwork, resolved at request time. */
  icon?: string;
  /**
   * The boss's level, from `DT_BossSpawnerLoactionData` joined on **position**.
   *
   * Absent on 35 of the 99 placements, and that is the two extractions
   * genuinely covering different spawn points rather than a lookup failure —
   * so a missing level must read as "not recorded here", never as level 0.
   */
  level?: number;
  /** Which boss-table row supplied that level, for tracing the join. */
  levelSpawner?: string;
}

/** A viewport query's answer, which reports what it left out. */
export interface StaticWorldObjects {
  points: StaticWorldObject[];
  /** Everything matching the box, before the cap. */
  inView: number;
  returned: number;
  truncated: boolean;
  limit: number;
}

export interface StaticWorldCategory {
  id: string;
  label: string;
  count: number;
  kinds: { cls: string; count: number }[];
}

export interface StaticWorldSummary {
  /** Only the categories this viewer's policy admits. */
  categories: StaticWorldCategory[];
  /** Total across the *visible* categories, not the world's. */
  objects: number;
  categoryCount: number;
  cellsParsed: number;
  skipped: Record<string, number>;
  cellSize: number;
  maxPoints: number;
  /**
   * Ids withheld by policy. Present so an Owner debugging their own settings can
   * see the dial took effect; the categories themselves carry no counts here.
   */
  restrictedCategories: string[];
}

/**
 * Whether the bundled game data still matches the installed Palworld build.
 *
 * `verdict` is `current`, `stale`, or `unknown` — and `unknown` is a real answer,
 * not an optimistic `current`. Positions are static per build, so a content update
 * can silently invalidate them and nothing in a save file says so.
 */
/**
 * Result of re-reading the bundled data packs from disk.
 *
 * Counts rather than a bare success flag, because "reloaded" and "reloaded
 * something that actually has data in it" are different claims — a truncated or
 * wrongly-compressed file loads to an empty bundle without erroring, which is
 * exactly the failure that shipped once.
 */
export interface WorldPackReload {
  worldObjects: {
    path: string;
    loaded: boolean;
    categories: Record<string, number>;
    total: number;
  };
  gamedata: {
    path: string;
    loaded: boolean;
    items: number;
    pals: number;
    technologies: number;
  };
  effigies: { path: string; count: number };
  build: GameBuildStatus;
}

export interface GameBuildStatus {
  verdict: 'current' | 'stale' | 'unknown';
  buildId: string;
  previousBuildId: string;
  buildChanged: boolean;
  /** `up` on an update, `down` on a deliberate rollback. */
  buildDirection: 'up' | 'down' | 'same' | 'unknown';
  acknowledged: boolean;
  acknowledgedBuild: string;
  reason: string;
  signals: {
    buildId: string;
    buildIdSource: string;
    lastUpdated: string;
    pakStamp: string;
    gameVersion: string;
    installDir: string;
    manifestFound: boolean;
    pakFound: boolean;
  };
  artifacts: {
    artifact: string;
    builtFromBuild: string | null;
    source: string;
    regenerateWith: string;
    note: string;
    state: 'current' | 'stale' | 'unknown';
  }[];
  staleArtifacts: string[];
  unknownArtifacts: string[];
}

/**
 * What a remapped world copy would change.
 *
 * `mode` is the load-bearing field: `rename` moves one player's uid, `swap`
 * exchanges two players' identities because the target uid already has a character
 * in this world.
 */
export interface WorldExportPlan {
  mode: 'rename' | 'swap';
  sourceUid: string;
  targetUid: string;
  sourceInstanceId: string;
  targetInstanceId: string;
  hasDps: boolean;
  references: {
    characterEntries: number;
    targetCharacterEntries: number;
    guildHandles: number;
    guildAdmin: number;
    guildPlayers: number;
    /** Every field in the world holding either uid. */
    total: number;
  };
  warnings: string[];
  planHash: string;
  /** Only when `keepGuilds` was sent. What a prune would remove. */
  prune?: ExportPrunePlan;
}

/** One guild an export can keep or drop. */
export interface ExportGuild {
  guildId: string;
  name: string;
  adminUid: string;
  playerUids: string[];
  memberCount: number;
}

export interface ExportPrunePlan {
  guilds: ExportGuild[];
  keepGuildIds: string[];
  dropGuildIds: string[];
  removes: {
    guilds: number;
    bases: number;
    mapObjects: number;
    containers: number;
    characters: number;
    /**
     * Proves the filter keyed on `group_id` rather than on ownership. A prune
     * removing bases while reporting zero ownerless characters has used the
     * wrong field and would strand every base worker.
     */
    ownerlessCharacters: number;
    playerSaves: number;
  };
  playerUids: string[];
  applyImplemented: boolean;
  note: string;
}

export interface WorldExportResult {
  ok: boolean;
  mode: string;
  destination: string;
  sourceUid: string;
  targetUid: string;
  applied: { total: number } & Record<string, number>;
  sizeBytes: number;
  warnings: string[];
  archive: { path: string; sizeBytes: number; sha256: string };
  /**
   * What the prune actually did. Present whenever `keepGuilds` was sent.
   *
   * **`pruned: false` with a `refused` reason is a SUCCESSFUL export that kept
   * everything** — the design is that a prune which cannot complete cleanly
   * leaves the full copy rather than a half-pruned world. A UI that reports
   * plain success here tells the operator their world was pruned when it was
   * not, which is worse than the refusal it hides.
   */
  prune?: {
    requested: boolean;
    pruned?: boolean;
    refused?: string;
    dropGuildIds?: string[];
    removed?: Record<string, number>;
  };
}

/** One recurring announcement. */
export interface ScheduledAnnouncement {
  id: number;
  message: string;
  interval: string;
  intervalLabel: string;
  enabled: boolean;
  /** Skip the window when nobody is connected, rather than queue it. */
  onlyWhenOnline: boolean;
  lastRun: string | null;
  /** `ok`, `skipped: nobody online`, `failed: …` — shown verbatim. */
  lastResult: string | null;
  nextRun: string | null;
  createdBy: string;
  createdAt: string;
}

export interface AnnouncementList {
  announcements: ScheduledAnnouncement[];
  intervals: { id: string; label: string; seconds: number }[];
  max: number;
}

/** One map-privacy choice, described by the backend rather than the UI. */
export interface PrivacyMode {
  id: string;
  label: string;
  description: string;
}

/** One base this account may hide, and whether it currently is. */
export interface ManageableBase {
  baseId: string;
  name: string;
  guildId: string;
  guildName: string;
  hidden: boolean;
  /** Why you may change it: "guild master", or the member fallback. */
  authority: string;
}

export interface ManageableBases {
  bases: ManageableBase[];
  /** Populated instead of an empty list when nothing is manageable, and why. */
  reason: string;
}

/** This account's own privacy state. Nobody can read or set anyone else's. */
export interface MyPrivacy {
  mode: string;
  modes: PrivacyMode[];
  role: string;
  linkedToPlayer: boolean;
  /** Roles this setting currently conceals from — peers and below. */
  hidesFrom: string[];
}

/** One account, as returned by the users endpoint. */
export interface ManagedUser {
  id: number;
  username: string;
  role: string;
  steamUid: string;
  displayName: string;
  disabled: boolean;
  mustChangePassword: boolean;
  createdAt: string;
  lastLogin: string | null;
}

/** A role preset and what it grants. */
export interface RolePreset {
  id: string;
  label: string;
  rank: number;
  description: string;
  capabilities: string[];
  assignable: boolean;
}

/** One entry in the append-only audit log. */
export interface AuditEntry {
  id: number;
  ts: string;
  username: string | null;
  role: string | null;
  action: string;
  target: string | null;
  detail: string | null;
  ip: string | null;
  result: 'ok' | 'failed' | 'denied';
}

export interface AuditPage {
  entries: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
  retentionDays: number;
  actions: string[];
}

/** The backend's fail-closed verdict on whether the game server is running. */
export interface ServerState {
  running: boolean;
  editable: boolean;
  confidence: 'high' | 'medium' | 'low';
  reason: string;
  readOnlyLock: boolean;
  signals: { name: string; verdict: 'running' | 'stopped' | 'unknown'; detail: string }[];
}

/** Level.sav parse cache state, so the UI can show data age instead of lying. */
export interface CacheStatus {
  enabled: boolean;
  hasData: boolean;
  parsing: boolean;
  parsedAt: number | null;
  ageSeconds: number | null;
  stale: boolean;
  lastError: string | null;
  lastDurationSec: number | null;
  minIntervalSeconds: number;
  /**
   * The on-disk cache was discarded because an upgrade changed the payload
   * shape, and no parse has replaced it yet.
   *
   * Distinct from plain `!hasData`, which also covers "nobody has ever pressed
   * Refresh". The two need different reassurance, and shipping the discard
   * without this left a live server looking empty with nothing saying why.
   */
  schemaStale?: boolean;
  levelSizeMb?: number;
  counts: Record<string, number>;
  /** null when the save carried no clock — NOT a world on day 1. */
  worldClock?: WorldClock | null;
}

/**
 * How old the world is, from `GameTimeSaveData`. Comes out of the save, so it
 * is still true while the server is off.
 */
export interface WorldClock {
  /** Counts from 1. Safe — the offset below can only move a day boundary. */
  day: number;
  hour: number;
  minute: number;
  timeOfDay: string;
  gameTicks: number;
  gameHours: number;
  /**
   * **Always false so far.** `PalWorldTime_GameStartHour` is 5 and it is not
   * established whether the counter is seeded with it, so the clock may be five
   * hours out. Never render a day/night state from this — night is a four-hour
   * window, so a five-hour error could invert it.
   */
  clockOffsetVerified: boolean;
  clockOffsetNote: string;
  /** Server UPTIME, not the world's age. Absent if the save lacked it. */
  serverUptimeHours?: number;
  timeRatio?: number | null;
}

/** A placed world object with coordinates: chest, palbox, farm, bench… */
export interface MapObject {
  id: string;
  kind: string;
  category: string;
  /** Friendly structure name from bundled game data; falls back to `kind`. */
  name?: string;
  x: number;
  y: number;
  z: number;
  baseCampId: string;
  /**
   * True when the object was placed by the world rather than a player. On a real
   * save this splits about 3,600 world-placed to 500 base-placed POIs, and they
   * belong on different map layers.
   */
  worldPlaced?: boolean;
  guildId: string;
  buildPlayerUid: string;
  opened?: boolean | null;
  grade?: string | null;
  /**
   * What this structure contributes to base output, from the game's own
   * `DA_PalBuildObjectCapabilityData` — e.g. `WorkSpeedAdditionalRate` 1.0 on a
   * Blast Furnace against 11.0 on the Ancient one.
   *
   * **Absent on almost everything**: only 48 of the game's ~1,000 build objects
   * carry a capability, so a missing key is the ordinary case rather than data
   * that failed to load.
   *
   * Do NOT multiply this by a Pal's work-rank speed. They are two numbers from
   * two files and no game file states how they compose — the backend ships
   * `composesWithWorkRank: false` for exactly this reason.
   */
  capability?: Record<string, number>;
}

/**
 * A fast-travel statue. These are static level actors, so they appear in no save
 * file — only a player's *unlocked* list does. Positions ship as bundled game
 * data and share the save's world coordinate space.
 */
export interface FastTravelPoint {
  key: string;
  id: string;
  name: string;
  x: number;
  y: number;
  z: number;
  /**
   * `tower` (a boss arena), `watchtower`, or `travel`.
   *
   * Derived from the name in `backend/gamedata.py`. The eight tower bosses were
   * always in this list and drawn identically to the other 166, which is why
   * "there are no towers on the map" was both a fair complaint and not true.
   */
  kind?: 'tower' | 'watchtower' | 'travel';
}

/** One item type, totalled server-wide and resolved against the bundled game data. */
export interface ItemTotalRow {
  itemId: string;
  count: number;
  /** In-game display name, or a humanised fallback when `known` is false. */
  name: string;
  icon: string;
  rarity: number;
  typeA: string;
  typeB: string;
  /** The game's real stack ceiling; 0 when unknown. */
  maxStack: number;
  weight: number;
  description: string;
  /** Whether the ID matched a reference entry rather than falling back. */
  known: boolean;
}

/** Server-wide item totals across every container. */
export interface ItemTotals {
  /**
   * What the figures actually cover: `server`, `own`, or `guild:<id>`.
   *
   * Reported rather than assumed, because the backend may narrow a request it
   * will not refuse — a total labelled server-wide that silently is not would be
   * worse than an error.
   */
  scope?: string;
  items: ItemTotalRow[];
  itemTypes: number;
  totalCount: number;
  containersScanned: number;
  truncated: boolean;
  /** False when the bundled game data is missing, so names are IDs. */
  namesResolved: boolean;
}

/**
 * What happened after a shutdown was issued.
 *
 * `cameBack === false` is the important one: the game process is gone and
 * nothing brought it back, which is what happens when the server container
 * keeps running but its supervisor does not relaunch PalServer.
 */
export interface LifecycleStatus {
  shutdownRequestedAt: number | null;
  shutdownReason: string | null;
  cameBack: boolean | null;
  watching: boolean;
  secondsSinceShutdown: number | null;
  restartSupported: boolean;
  /**
   * Whether the operator configured STOP_COMMAND / START_COMMAND.
   *
   * Both are off by default and need a `docker` binary the runtime image does
   * not ship, so the buttons are hidden rather than shown broken — nothing is
   * lost, because stopping the container by hand works identically and the
   * dashboard detects it either way.
   */
  stopSupported: boolean;
  startSupported: boolean;
  returnWatchSeconds: number;
}

// ─── Settings (PalWorldSettings.ini) ────────────────────

export interface IniOption {
  value: string | number | boolean;
  type: 'bool' | 'int' | 'float' | 'string' | 'enum';
  raw: string;
  /** Masked on read. Submit an empty string to leave it unchanged. */
  secret?: boolean;
  /** Whether a secret is configured at all — safe to show, unlike its value. */
  isSet?: boolean;
  /**
   * The environment variable the common server images regenerate this key from
   * on every container start. If the image sets it, an edit here is reverted on
   * the next restart, so the `.env` file is the real place to change it.
   */
  envManaged?: string;
  /**
   * What this key does. **Absent for 19 of the 119** — Pocketpair does not
   * document them and the game's own settings screen does not name them, so
   * there is nothing to show. Render nothing rather than a heading with no body.
   *
   * Every string travels with its source because the three carry different
   * authority: `official` is Pocketpair's documentation, `game` is a string out
   * of the game's own UI, and `dashboard` is something this project measured.
   * Do not present them identically.
   */
  help?: {
    description?: string;
    descriptionSource?: 'official' | 'game' | 'dashboard';
    label?: string;
    labelSource?: 'official' | 'game' | 'dashboard';
    note?: string;
    noteSource?: 'official' | 'game' | 'dashboard';
    /**
     * The game's own names for this key's values, e.g. `DeathPenalty.All` ->
     * "Drop all items and all Pals on team". Worth more than the key's own
     * description: nothing about `EquipmentAndItemAndRandomPal` is self-evident.
     */
    values?: Record<string, string>;
  };
}

export interface IniSettings {
  path: string;
  writable: boolean;
  options: Record<string, IniOption>;
  count: number;
  presets: SettingsPreset[];
  groups: { label: string; keys: string[] }[];
  /**
   * How much of the file is explained, and by whom. Shown rather than hidden:
   * "Pocketpair does not document these 19" is a fact about their docs, and an
   * operator hunting a missing tooltip should learn that instead of assuming
   * the dashboard is broken.
   */
  helpCoverage?: {
    iniKeys: number | null;
    documented: number | null;
    labelled: number | null;
    undocumented: string[];
    sources: Record<string, { name?: string; url?: string }>;
  };
  serverRunning: boolean;
  restartRequiredForAll: boolean;
  /**
   * Whether this deployment's server image rewrites the INI when it starts.
   *
   * Observed rather than recognised: the dashboard cannot read the game
   * container's environment, so it hashes the file when it writes it and again
   * after a restart. `unknown` is the honest starting state — "not yet
   * observed", not "safe".
   */
  iniWatch?: {
    verdict: 'unknown' | 'preserved' | 'regenerated';
    detail: string;
    observedAt: string | null;
    /** A dashboard write is on record and a restart has not been seen yet. */
    awaitingRestart: boolean;
    lastWriteAt: string | null;
    /** Written and not yet checkable, by name. Empty is not the same as none. */
    pendingKeys?: string[];
    /**
     * Did each key we wrote survive? A narrower question than `verdict`, which
     * is about the deployment — an image can rewrite the file and leave your key
     * alone, or leave the rest alone and revert only yours.
     */
    keyVerification?: {
      checked: number;
      verified: number;
      keys: {
        key: string;
        verdict: 'verified' | 'reverted' | 'missing' | 'unchecked';
        /**
         * **Always empty for a secret**, in both directions. `AdminPassword` and
         * `ServerPassword` are compared against a stored hash and their values
         * never leave the backend — do not render a placeholder here that could
         * be mistaken for one.
         */
        secret: boolean;
        expected: string;
        actual: string;
      }[];
      /** Actionable: a setting you changed is not in effect. */
      warnings: string[];
      /** True but not a failure. Rendered quietly, for the reason above. */
      notes: string[];
    };
  };
}

export interface SettingsPreset {
  id: string;
  label: string;
  description: string;
  changes: Record<string, string | number | boolean>;
  /**
   * `game` for Palworld's own difficulty presets, `official` for a
   * configuration Pocketpair published in their documentation, `dashboard` for
   * the hand-made ones.
   *
   * Shown because an operator choosing between "Hardcore" and "Punishing"
   * deserves to know which one Pocketpair wrote — and because two of the PvP
   * presets **disagree**: `pvp_players_only` sets one of the three parameters
   * the official PvP page says are required to False. The badge is how a reader
   * can tell whose claim they are applying.
   */
  source?: 'game' | 'official' | 'dashboard' | string;
}

// ─── Breeding ───────────────────────────────────────────

/** One World Partition cell a species' spawners occupy, in world space. */
export interface HabitatRegion {
  x: number;
  y: number;
  width: number;
  height: number;
  landmass: 'palpagos' | 'worldtree';
}

/**
 * Where a species spawns.
 *
 * `known: false` is ordinary, not an error — plenty of Pals have no spawner at
 * all (tower bosses, raid-only, breeding-only).
 */
export interface Habitat {
  species: string;
  known: boolean;
  mergedFrom?: string[];
  cells: [number, number][];
  regions: HabitatRegion[];
  spawnerCount: number;
  cellSize: number;
}

/** One row of the Paldeck listing. */
export interface PaldeckEntry {
  id: string;
  species: string;
  name: string;
  icon: string;
  elements: string[];
  rarity: number;
  paldeckNumber: number;
  workSuitabilities: Record<string, number>;
  /** Location variants merged into this entry (e.g. `HadesBird_Oilrig`). */
  speciesIds: string[];
  hasHabitat: boolean;
  habitatCells: number;
  known: boolean;
}

export interface PaldeckListing {
  pals: PaldeckEntry[];
  habitats: {
    species: number;
    spawnersMatched: number;
    spawnersTotal: number;
    cellSize: number;
    available: boolean;
  };
}

export interface PaldeckDetail extends PaldeckEntry {
  habitat: Habitat;
  stats?: Record<string, number>;
  work?: Record<string, number>;
  breedingPower?: number;
  genderOdds?: { MALE: number; FEMALE: number };
  moves?: SpeciesMoves;
  /** Whether breeding can reach this Pal, and by what. */
  obtainability?: BreedingLimitRow;
  /**
   * What it drops, by level BAND — `levelFrom` is 0/10/20…80, never an exact
   * level. 128 species have more than one band and the contents genuinely
   * differ: Anubis at 0 gives Bone and a Large Pal Soul, at 80 it gives World
   * Tree Relics.
   */
  drops?: DropBand[];
  /**
   * What this Pal does for YOU, at every condenser rank.
   *
   * All five travel because the numbers move with the rank — Silvegis reduces
   * shield damage by 65% at one star and 80% at five — and that ladder is the
   * decision somebody is making when they look a Pal up.
   *
   * `filled: false` means the game's text still holds a reference this project
   * does not resolve; the sentence is shown as the game wrote it rather than
   * with a number invented for it.
   */
  partnerSkill?: {
    name?: string | null;
    scales?: boolean;
    byRank?: {
      name?: string | null;
      description?: string;
      filled?: boolean;
      atRank?: number;
    }[];
  };
  /**
   * The ALPHA form's table, which is a separate row rather than a richer
   * version of the ordinary one — `BOSS_Anubis` gives Ancient Civilization
   * Parts where Anubis gives Bone. Absent when it matches.
   */
  alphaDrops?: DropBand[];
}

export interface DropBand {
  levelFrom: number;
  items: {
    itemId: string;
    name: string;
    icon: string;
    /** The game's own per-drop percentage. 100 means always. */
    rate: number;
    min: number;
    max: number;
  }[];
}

export interface SpeciesMove {
  id: string;
  name: string;
  element: string;
  power: number;
  cooldown: number;
  category: string;
  /** Present on level-up moves: the level the species learns it at. */
  level?: number;
  /** Present on egg moves. See `SpeciesMoves.egg`. */
  eggOnly?: boolean;
}

/**
 * What a species *can* have, which is a different question from what one Pal
 * has equipped.
 */
export interface SpeciesMoves {
  levelUp: SpeciesMove[];
  /**
   * **Inheritable by breeding only.** A Pal that already exists cannot be
   * taught one, so this is the answer to "is this breeding target worth
   * chasing" rather than a list of moves to go and buy.
   */
  egg: SpeciesMove[];
  eggCount: number;
}

export interface PalSummary {
  internalName: string;
  name: string;
  /** Path into `public/icons/`, from the bundled game data. "" when there is none. */
  icon?: string;
  dex?: number;
  isVariant?: boolean;
  rarity?: number;
  breedingPower?: number;
  genderOdds?: { MALE: number; FEMALE: number };
  work?: Record<string, number>;
  known: boolean;
}

export interface PalboxSpecies extends PalSummary {
  count: number;
  male: number;
  female: number;
  unknownGender: number;
  canSelfBreed: boolean;
  maxLevel: number;
  bestIvs: Record<string, number>;
  passives: { id: string; name: string; count: number }[];
  /**
   * Where the copies of this species are, e.g. `{ palbox: 3, base: 1 }`.
   *
   * A parent counted here is not necessarily in the palbox: base workers and
   * Pals in a guild's shared store are breedable and are counted for that
   * reason. But a breeding plan is a set of instructions, and "pair your two
   * Lamballs" is a bad one if a Lamball is standing in a base three valleys
   * away. Keyed by the structure's own name where there is one
   * ("Dimensional Pal Storage"), because the word `storage` does not tell
   * anyone where to walk.
   */
  locations?: Record<string, number>;
  individuals: {
    instanceId: string;
    nickname: string;
    gender: string;
    level: number;
    rank: number;
    location?: string;
    storageKind?: string;
    ivs: Record<string, number>;
    passives: { id: string; name: string }[];
  }[];
}

export interface PalboxSummary {
  species: PalboxSpecies[];
  speciesCount: number;
  totalBreedable: number;
  skippedUnbreedable: number;
  /**
   * What "these Pals" actually covered: `own`, `server`, or `player:<uid>`.
   *
   * Reported rather than inferred, because below `allPalsVisibility` the backend
   * pins the request to the caller regardless of what was asked — so a client
   * that assumes its own request was honoured labels the result wrongly.
   */
  scope?: string;
  /** Whether this caller may scope to someone other than themselves at all. */
  mayScopeToOthers?: boolean;
  linkedToPlayer?: boolean;
  /**
   * How many Pals the answer was computed from.
   *
   * Zero alongside `linkedToPlayer: false` is the case people report as the
   * dashboard forgetting their account: the request succeeded, the scope
   * resolved to a character that is not in this world, and an empty planner is
   * otherwise indistinguishable from a broken one.
   */
  pals?: number;
  /**
   * How many of `pals` are the guild's rather than this player's own — base
   * workers and the contents of shared Pal stores. They are legitimately part
   * of the answer (anyone in the guild can take one out and breed it), but a
   * total larger than the palbox needs an explanation attached or it reads as
   * a miscount.
   */
  shared?: number;
}

/** The scope fields every scoped breeding endpoint returns, not just /palbox. */
export interface BreedingScope {
  scope?: string;
  mayScopeToOthers?: boolean;
  linkedToPlayer?: boolean;
  pals?: number;
  /**
   * How many of `pals` are the guild's rather than this player's own — base
   * workers and the contents of shared Pal stores. They are legitimately part
   * of the answer (anyone in the guild can take one out and breed it), but a
   * total larger than the palbox needs an explanation attached or it reads as
   * a miscount.
   */
  shared?: number;
}

/** One breeding step: two parents and what they produce. */
export interface BreedingStep {
  parentA: PalSummary;
  parentB: PalSummary;
  child: PalSummary;
}

/**
 * Pals reachable only via an intermediate, with the shortest route to each.
 *
 * Excludes depth-1 children: those are already the offspring list, and
 * repeating them here would bury the ones that actually need a plan.
 */
export interface ReachableTargets extends BreedingScope {
  maxDepth: number;
  ownedSpecies: number;
  /** Whether the search only used pairs this owner can physically make. */
  genderAware?: boolean;
  targets: (PalSummary & { depth: number; steps: BreedingStep[] })[];
}

/** A route to one target, plus the scope it was computed against. */
export interface BreedingPath extends BreedingScope {
  target?: string;
  reachable: boolean;
  alreadyOwned?: boolean;
  reason?: string;
  /** Whether the search only used pairs this owner can actually make. */
  genderAware?: boolean;
  steps: BreedingStep[];
}

export interface OffspringOption extends PalSummary {
  owned: boolean;
  pairCount: number;
  fromPairs: { a: string; b: string; aId: string; bId: string }[];
}

/** One pairing the game names outright, from `DT_PalCombiUnique`. */
export interface NamedPairing {
  a: string;
  b: string;
  aName: string;
  bName: string;
  /**
   * The variant paired with itself. Real, and **not an answer to "how do I get
   * my first one"** — so it is labelled rather than listed indistinguishably
   * beside the pairing that is.
   */
  breedsTrue?: boolean;
  genderA?: string;
  genderB?: string;
}

/**
 * What breeding cannot reach, and why. Reference data — a fact about Palworld,
 * not about anyone's palbox, which is why it is a separate request from the
 * planner's scoped ones.
 */
export interface BreedingLimits {
  /**
   * `IgnoreCombi`, and no pairing the game names. **Not "cannot be bred"** —
   * every one is a productive parent and most breed true. What no pairing does
   * is *produce* one you have not already got.
   */
  never: BreedingLimitRow[];
  /**
   * An element variant the game names no pairing for, while the table this
   * planner runs on offers one. Three species. The disagreement is reported,
   * not resolved.
   */
  unverified: BreedingLimitRow[];
  /** An element variant: only the pairings the game names produce it. */
  namedPairingOnly: BreedingLimitRow[];
  paldeckEntries: number;
  /** `Combi_BossPalRate` — a bred Pal is an alpha this often. */
  alphaChance?: number;
}

export interface BreedingLimitRow {
  species: string;
  name: string;
  paldeck: number;
  /** `B` marks an element variant — the `B` on Paldeck entry #98B. */
  suffix: string;
  kind: 'never' | 'unverified' | 'named_pairing' | 'standard';
  /**
   * Two of this Pal make another. Reported on `never` rows too, where it is
   * the only breeding fact that applies — 26 of the 28 legendaries breed true.
   */
  breedsTrue?: boolean;
  note?: string;
  pairings?: NamedPairing[];
  mutatedEgg?: {
    quote: string;
    cakeQuote: string;
    cakeItem: string;
    /**
     * The five passives the game flags with `AddMutationPal` — a real column,
     * read from the flag rather than the id prefix because four are named
     * `MutationPal_*` and the fifth (Skymarcher) is not.
     *
     * NOT a drop table and NOT weighted. What the flag means is stated in no
     * game file, which is what `passivesNote` says.
     */
    passives?: {
      id: string;
      name: string;
      /** The game's own prose. May be incomplete — see `descriptionIncomplete`. */
      description: string;
      rank?: number;
      /** Structured effects, which is what to render when the prose is broken. */
      effects?: { type: string; value: number; target?: string }[];
      /**
       * The archive shipped this row with an unsubstituted `{EffectValue1}`.
       * Render the effects instead; do NOT fill the placeholder from
       * `effects[0]`, which skips unused slots and so is not slot 1.
       */
      descriptionIncomplete?: boolean;
      known?: boolean;
    }[];
    passivesNote?: string;
    /**
     * Always false. Mutation chance is a property of the breeding system, not
     * of a species — every constant the game names is `Combi_*`, keyed on the
     * parents' rank and IVs, with values compiled into the server binary.
     */
    perSpecies?: boolean;
    perSpeciesNote?: string;
    /** That no game file says what produces one. The absence is the point. */
    note: string;
  };
}

export interface MapMarker {
  id: string;
  type: 'player' | 'base' | 'fastTravel' | 'boss' | 'dungeon' | 'custom';
  name: string;
  x: number;
  y: number;
  z?: number;
  data?: Record<string, unknown>;
}


/**
 * One row of the game's item catalogue — what Palworld has, not what a world
 * holds. Carries `id` and `name` together because the API speaks ids (`AIcore`)
 * and people speak names ("AI Core"), and either must be searchable.
 */
export interface CatalogueItem {
  id: string;
  name: string;
  icon: string;
  rarity: number;
  typeA: string;
  typeB: string;
  maxStack: number;
  weight: number;
  /** Equipment. Overwriting such a slot would orphan a durability record. */
  hasDurability: boolean;
  /**
   * `DT_ItemDataTable.bLegalInGame`, present only on the 575 that are false.
   *
   * **It does NOT mean unobtainable.** Ten of them are held in the reference
   * world right now, all seven Key Spheres among them. On its own it warrants
   * a neutral note at most — `liveTwin` is the field that carries a claim.
   */
  legalInGame?: false;
  /**
   * The legal item sharing this one's display name, on 95 of the 575.
   *
   * This is the actionable half: `Gunpowder` -> `Gunpowder2`. Two rows read
   * identically in an autocomplete and one of them is dead, so the id is worth
   * showing. Absent when there is no legal namesake (474) or more than one (6),
   * because neither supports a unique answer.
   */
  liveTwin?: string;
}


/** One stat, term by term, so a figure can be explained rather than asserted. */
export interface StatBreakdown {
  base: number;
  condenserMultiplier: number;
  baseWithCondenser: number;
  trust: number;
  awakening: number;
  subtotal: number;
  soulMultiplier: number;
  passiveMultiplier: number;
  final: number;
}

/** How far through the current level, from the game's own Pal EXP table. */
export interface LevelProgress {
  known: boolean;
  maxed?: boolean;
  intoLevel?: number;
  needed?: number;
  remaining?: number;
  percent?: number;
}

export interface PalStats {
  hp: StatBreakdown;
  attack: StatBreakdown;
  defense: StatBreakdown;
  workSpeed: StatBreakdown;
  friendshipRank: number;
  progress: LevelProgress;
  inputs: {
    level: number;
    condenserRank: number;
    /** Rank minus one — rank 1 is *no* stars, which is the easy off-by-one. */
    condenserStars: number;
    soulRanks: Record<string, number>;
    trustPoints: number;
    isAlpha: boolean;
    isLucky: boolean;
  };
  /** Always true. Present so a UI cannot show these as if read from the save. */
  calculated: boolean;
}


// ─── Guild membership ───────────────────────────────────

/** What a guild move would change, before anything is written. */
export interface GuildMovePlan {
  ok: boolean;
  problems: string[];
  warnings: string[];
  playerUid: string;
  playerName: string;
  origin: { id: string; name: string; members: number; bases: number; isAdmin: boolean };
  target: { id: string; name: string; members: number; bases: number };
  /** The player's own character plus every Pal they own. */
  movesCharacters: number;
  /** Only non-zero when the origin guild empties and bases come along. */
  movesBases: number;
  /** Pals deployed at those bases — they belong to the base, not to a person. */
  movesBaseWorkers: number;
  removesOriginGuild: boolean;
  /** Who inherits the origin guild, when it keeps members and loses its leader. */
  newLeaderOfOrigin: string;
  /** Fingerprint of the world this was computed against. Required to apply. */
  planHash: string;
}

export interface GuildMoveResult {
  ok: boolean;
  playerUid: string;
  playerName: string;
  fromGuild: string;
  toGuild: string;
  charactersMoved: number;
  basesMoved: number;
  originGuildRemoved: boolean;
  backupId: string;
  verified: boolean;
}


// ─── Where an item comes from ───────────────────────────

/** An item id with the two things needed to render it. */
export interface ItemRef {
  itemId: string;
  name: string;
  icon: string | null;
}

/**
 * A technology step. `cost` is in technology points — but a boss technology
 * spends **Ancient** Technology Points, a different currency, so the two are
 * never summed and no total is offered.
 */
export interface TechnologyStep {
  technologyId: string;
  name: string;
  cost: number | null;
  isBossTechnology: boolean;
}

export interface TechnologyUnlock extends TechnologyStep {
  icon: string | null;
  levelCap: number | null;
  /** A tower boss that must be beaten first, where the game names one. */
  requiresBoss: string;
  /** Everything to research before it, in order. */
  requires: TechnologyStep[];
}

export interface ItemRecipe {
  recipeId: string;
  count: number;
  workAmount: number;
  materials: (ItemRef & { count: number })[];
  /** A looted schematic, which is not the same thing as a technology. */
  unlockedBySchematic?: ItemRef;
  technologies?: TechnologyUnlock[];
}

/**
 * `levelFrom` is a **band**: the game's column holds only 0, 10, 20 … 80, so a
 * row covers "level 30-39". It is never an exact level.
 */
export interface ItemDropSource {
  speciesId: string;
  name: string;
  isBoss: boolean;
  levelFrom: number;
  rate: number;
  min: number;
  max: number;
}

/**
 * `weight` is relative **within one field's slot** and nothing says how often a
 * field is rolled, so it is not a drop rate. `slotShare` is that weight over its
 * own slot's total, which IS the chance this item fills the slot when the field
 * is rolled.
 */
export interface ItemLootSource {
  field: string;
  slot: number;
  weight: number;
  slotShare: number | null;
  min: number;
  max: number;
  grade: string;
}

export interface ItemShopSource {
  shop: string;
  count: number;
  stock: number;
  type: string;
  price: number | null;
  /** False means the item's own catalogue price, not that it is free. */
  priceIsOverride: boolean;
}

export interface ItemProductionSource {
  structureId: string;
  name: string;
  requiredWork: number;
  autoWorkPerSecond: number;
}

/** `known: false` means there is no such item — not that nothing produces it. */
export interface ItemSources {
  itemId: string;
  known: boolean;
  name?: string;
  icon?: string | null;
  description?: string | null;
  crafting?: ItemRecipe[];
  drops?: { total: number; shown: ItemDropSource[] };
  loot?: ItemLootSource[];
  shops?: ItemShopSource[];
  production?: ItemProductionSource[];
  usedIn?: (ItemRef & { needs: number })[];
  food?: {
    itemId: string;
    durationSeconds: number;
    effects: { type: string; value: number; interval: number }[];
  };
  /** False is a real answer: no bundled table produces this item. */
  hasSource?: boolean;
}

/**
 * One node of a recursive crafting tree.
 *
 * `leaf` with `leafReason: 'raw'` is a material you gather. `'cycle'` and
 * `'depth'` are the guards, and both are visible on purpose — a branch that
 * stopped short must not render like one that finished.
 */
export interface CraftNode extends ItemRef {
  need: number;
  leaf: boolean;
  leafReason?: 'raw' | 'cycle' | 'depth';
  materials: CraftNode[];
  recipeId?: string;
  yields?: number;
  batches?: number;
  made?: number;
  surplus?: number;
  workPerBatch?: number;
  work?: number;
  alternatives?: number;
  /** Present only when there is more than one way to make this — the alternates
   *  described by their materials, so a chooser can offer "from Coal" rather
   *  than a row id. */
  otherRecipes?: CraftRecipeSummary[];
  /** Recipes that convert this item back into what it came from — named, never
   *  expanded, because walking one is the cycle. */
  alsoFrom?: CraftRecipeSummary[];
}

export interface CraftRecipeSummary {
  recipeId: string;
  yields: number;
  from: (ItemRef & { count: number })[];
}

export interface CraftStep extends ItemRef {
  recipeId: string;
  need: number;
  batches: number;
  yields: number;
  made: number;
  surplus: number;
  work: number;
}

export interface CraftTree extends ItemRef {
  known: boolean;
  note?: string;
  count?: number;
  craftable?: boolean;
  tree?: CraftNode;
  /** The shopping list. Not the sum of the tree's leaves by construction — see
   *  `backend/crafting.py` — though the two are measured equal. */
  raw?: (ItemRef & { count: number })[];
  steps?: CraftStep[];
  totalWork?: number;
  maxDepth?: number;
  truncated?: boolean;
  /** Work units, never a duration. What converts them is which Pals are
   *  assigned, which no game file states. */
  workIsUnits?: boolean;
  /** This is catalogue data: nothing here read a world or a chest. */
  checksStock?: boolean;
}

export interface BuildRankRow {
  speciesId: string;
  name: string;
  icon?: string | null;
  elements?: string[];
  rideable?: boolean;
  /**
   * `Fly`, `FlyAndLanding`, `Swim` or `GroundOnly` — from the game's own
   * `EPalMonsterMovementType`, on each species blueprint in the server pak.
   *
   * This was typed `null` with a comment saying the mode is "not in any game
   * file". It is; the search that concluded otherwise looked for `BP_Pal_*`
   * and the game names them `BP_<Species>`.
   *
   * Null only when the bundle is missing — which is distinct from `GroundOnly`
   * and must not be rendered the same way.
   */
  mountMode: 'Fly' | 'FlyAndLanding' | 'Swim' | 'SwimGroundDamage' | 'GroundOnly' | null;
  /**
   * True when the mode was inherited rather than read. `GroundOnly` is an
   * inference — nothing states the native default — so a UI must not give it
   * the same authority as the 52 species the game overrides explicitly.
   */
  mountModeInferred?: boolean;
  stamina?: number | null;
  value: number;
  /** The species column, before any passive multiplier. Movement metrics only. */
  base?: number;
  /** The figure before the element multiplier — always present when one applied,
   *  so nothing is hidden behind the sort. */
  raw?: number;
  passiveBonus?: number;
  /**
   * The share of `passiveBonus` that comes from the species' own partner skill
   * at the chosen condenser rank — i.e. what the stars bought. Separate because
   * a merged figure could not tell a player which part condensing gave them.
   */
  partnerBonus?: number;
  matchRate?: number;
  /** You hitting them. NOT the inverse of `incoming`. */
  matchup?: 'strong' | 'weak' | 'neutral';
  /** Them hitting you. */
  incoming?: 'strong' | 'weak' | 'neutral';
  breakdown?: Record<string, unknown>;
}

export interface BuildRanking {
  metric: string;
  known: boolean;
  note?: string;
  label?: string;
  source?: 'table' | 'calculated';
  ranked?: number;
  rows?: BuildRankRow[];
  build?: Record<string, unknown>;
  passiveEffect?: {
    always: Record<string, number>;
    riding: Record<string, number>;
    conditional: { passiveId: string; type: string; value: number; when?: string[] }[];
  };
  /**
   * False for every movement metric: the stat FORMULA does not touch a speed.
   * Not the same as "a build cannot change one" — see `condenserOnMovement`.
   */
  buildAffectsMetric?: boolean;
  /** The columns carry no build term — a fact about the FILES. */
  movementInFiles?: boolean;
  /**
   * `"viaPartnerSkill"`: condenser rank DOES raise movement, for the 96
   * species whose partner skill scales with it — Direhowl reads +0/10/12/15/20%
   * across the stars. Applied by the ranking and broken out per row as
   * `partnerBonus`.
   */
  condenserOnMovement?: string;
  /**
   * The other half, still open: whether `GenkaiToppa_PerAdd` also multiplies
   * the species speed columns the way it multiplies HP and Attack.
   * `"unverified"`, **never false** — it would be applied at load and invisible
   * in every file, exactly as the work-suitability bonus was.
   */
  condenserOnSpeedColumns?: string;
  /** Movement from the species' own partner skill is counted in `value`. */
  partnerSkillMovementApplied?: boolean;
  mountModeKnown?: boolean;
  speedUnitKnown?: boolean;
  against?: string;
  matchupApplied?: boolean;
  matchRate?: number | null;
  matchRateAppliesBothWays?: boolean;
  stackingKnown?: boolean;
  chartIsHandEntered?: boolean;
  unknownElements?: string[];
}

export interface PaldeckCompletionEntry {
  id: string;
  name: string;
  icon?: string | null;
  elements?: string[];
  paldeckNumber?: number;
  forms?: string[];
  caught: boolean;
  captured?: number;
  habitatCells?: number;
  /** Only on entries you have NOT caught — how to go and get one. */
  route?: {
    catch?: { cells: number };
    breed?: { kind: string; pairings?: unknown[]; breedsTrue?: boolean };
    unknown?: boolean;
  };
}

export interface PaldeckCompletion {
  uid: string;
  name: string;
  entries: PaldeckCompletionEntry[];
  total: number;
  caught: number;
  missing: number;
  /** Always "paldeckEntries" — 204, never the 753 species forms. */
  denominator: string;
  /** False when the account has no linked character: no score, not zero. */
  linked: boolean;
  missingHidden?: boolean;
}

export interface BossCounters {
  bringElements: string[];
  /** Your Pals of these elements take the boss's bonus. NOT the inverse of
   *  `bringElements` — Fire beats Grass and Grass beats Earth. */
  avoidElements: string[];
  matchRate: number;
  matchRateAppliesBothWays: boolean;
}

export interface BossEncounter {
  kind: 'field' | 'raid' | 'tower';
  id: string;
  speciesId: string;
  name: string;
  icon?: string | null;
  elements: string[];
  /** Absent on towers — a tower entrance is a place, not an encounter. */
  level?: number | null;
  /** **Null on a raid boss**, which is summoned rather than placed. */
  position?: { x?: number; y?: number; z?: number } | null;
  summonItemId?: string;
  /** Null on a tower: no species, so no matchup. */
  counters?: BossCounters | null;
}

export interface BossEncounters {
  bosses: BossEncounter[];
  counts: { field: number; raid: number; tower: number };
  kindsAreNotComparable: boolean;
  raidBossesHaveNoPosition: boolean;
  recommendedLevelKnown: boolean;
  partySizeKnown: boolean;
  chartIsHandEntered: boolean;
}

export interface CraftableRecipe extends ItemRef {
  recipeId: string;
  batches: number;
  count: number;
  materials: (ItemRef & { count: number; held: number })[];
}

export interface CraftableReport {
  recipes: CraftableRecipe[];
  basesCounted: number;
  guildChestsCounted: number;
  distinctMaterials: number;
  /** Always false — crafting one thing consumes what another needs. */
  simultaneous: boolean;
  /** Always false — WorkableAttribute is 0 on all 1,414 recipe rows. */
  workstationKnown: boolean;
}


// ─── Progression checklists ─────────────────────────────

/** One entry in a checklist — always named, never a bare id. */
export interface ChecklistEntry {
  id: string;
  name: string;
  /** True when the bundle could not name it and the id was humanised. */
  nameIsInternal?: boolean;
  /** The game withholds two endgame names as `？？？`. Not a decode failure. */
  nameHidden?: boolean;
  kind?: string;
  speciesId?: string;
  level?: number | null;
  levelMax?: number | null;
  x?: number;
  y?: number;
  /** Pal requests only — the game's own area label, e.g. `Area_F1`. */
  area?: string;
  /** Pal requests only — what the NPC hands over. */
  rewards?: { itemId: string; count: number }[];
}

export interface Checklist {
  obtained: number;
  of: number;
  have: ChecklistEntry[];
  /** Absent when the operator's discoveryVisibility hides the unfound half. */
  missing?: ChecklistEntry[];
  missingHidden?: boolean;
  truncated: boolean;
  /** Ids the bundle does not list. Counted as obtained, never absorbed. */
  unlisted: string[];
}

/**
 * Field bosses are two things in one save flag: spawner ids that resolve to a
 * Pal and a level, and `BOSS_`-prefixed NPC ids for the human bosses. Only the
 * first has a trustworthy total, so `humans.of` is deliberately null.
 */
export interface FieldBossProgress {
  pals: Checklist;
  humans: {
    have: { id: string; name: string }[];
    obtained: number;
    of: null;
    totalSource: string;
    missingHidden?: boolean;
  };
}

export interface PlayerProgressDetail {
  uid: string;
  name: string;
  level: number;
  towerBosses: Checklist;
  fieldBosses: FieldBossProgress;
  areasFound: Checklist;
  fastTravel: Checklist;
  effigies: Checklist;
  /**
   * "Show me this Pal" requests, 54 of them, from `DA_PalDisplay`.
   *
   * A real checklist: the save records completion per player in
   * `RecordData.PalDisplayNPCDataTableProgress`, keyed by the same RequestIDs.
   * Its item-request sibling is deliberately NOT here — no save has been seen
   * to record that half, so it is a catalogue rather than progress.
   */
  palDisplay: Checklist;
  /** `available: false` with a reason — no save has ever written the flag. */
  dungeonsCleared: { available: boolean; reason: string };
  achievements: AchievementSummary;
}

/** One milestone tier from the game's own reward NPC. */
export interface AchievementTier {
  id: string;
  requireCount: number;
  expBonusLevel: number;
  rewards: { itemId: string; count: number }[];
  /**
   * `claimed`   — the save names this row; read, never inferred
   * `unclaimed` — earned and still sitting with the NPC
   * `locked`    — not yet reached
   * `unknown`   — no counter is established for this category
   */
  state: 'claimed' | 'unclaimed' | 'locked' | 'unknown';
}

export interface AchievementCategory {
  /** The save counter that drives it, or **null** when none is established. */
  counter: string | null;
  /** The player's figure. **null is not zero** — it means unknowable. */
  value: number | null;
  tiers: AchievementTier[];
  claimed: number;
  unclaimed: number;
  total: number;
  /** False for `BossDefeat`. Do not draw a progress bar without a number. */
  hasProgress: boolean;
}

export interface AchievementSummary {
  categories: Record<string, AchievementCategory>;
  claimed: number;
  unclaimed: number;
  total: number;
  source: string;
  /** Always false. These are the game's own milestones, not Steam's. */
  isSteam: boolean;
}

export interface ProgressDetailReport {
  players: PlayerProgressDetail[];
  /** False when the operator hides undiscovered content from this viewer. */
  showsMissing: boolean;
  available: boolean;
  achievementsAvailable: boolean;
}


// ─── Placed NPCs ────────────────────────────────────────

/**
 * One placed NPC spawner, read from the server pak's world cells.
 *
 * `role` is a **name rule, not a game column** — no table anywhere carries a
 * role — so it fails safe: anything unrecognised is `npc`.
 */
export interface NpcPlacement {
  cls: string;
  uniqueId: string;
  characterId: string;
  /** Resolved: DT_UniqueNPC, then the character tables, then humanised. */
  name: string;
  /** True when the game itself never gave this NPC a display name. */
  nameIsInternal: boolean;
  /** What this spawner spawns at, which can differ from the table's level. */
  level: number;
  respawnSeconds: number;
  role: string;
  x: number;
  y: number;
  z: number;
}

export interface NpcPlacements {
  placements: NpcPlacement[];
  total: number;
  roles: Record<string, string>;
  /** Always true — the role split is derived from ids, not shipped by the game. */
  roleFromName: boolean;
}


// ─── Raid bosses ────────────────────────────────────────

export interface RaidReward {
  itemId: string;
  name: string;
  icon: string | null;
  /** A real per-item chance — these are independent rolls, not slot shares. */
  rate: number;
  min: number;
  max: number;
}

export interface RaidBossForm {
  speciesId: string;
  name: string;
  /** True for the `_2` difficulty variants, which no character table carries. */
  nameIsInternal: boolean;
  level: number;
  canModeChange: boolean;
}

export interface RaidBoss {
  /** The row key IS the summon item — `PalSummon_NightLady` is Bellanoir's Slab. */
  summonItemId: string;
  summonItemName: string;
  summonItemIcon: string | null;
  summonItemKnown: boolean;
  forms: RaidBossForm[];
  rewards: RaidReward[];
  /** One of these, not all — the game's own `SuccessAnyOneItemList`. */
  rewardsAnyOne: RaidReward[];
  /** False: EggPalIDAndWeight is a MapProperty the table reader cannot decode. */
  eggWeightsRead: boolean;
}

export interface RaidBossReport {
  bosses: RaidBoss[];
  total: number;
  /** Always false — altar-summoned, so no game file gives them a position. */
  hasPositions: boolean;
  positionNote: string;
}


// ─── Base raids ─────────────────────────────────────────

export interface InvaderGroup {
  group: string;
  biomes: string[];
  gradeMin: number;
  gradeMax: number;
  attackers: number;
  /** Build-object ids that trigger this raid, where the game names one. */
  conditions: string[];
  rewards: (RaidReward & { name: string; icon: string | null })[];
}

export interface InvaderReport {
  groups: InvaderGroup[];
  total: number;
  visitors: Record<string, unknown>;
  cancelCosts: number[];
  /** False — nothing establishes what a raid "grade" is in save terms. */
  gradeMeaningKnown: boolean;
  /** False — a base's biome is trigger geometry, not a lookup. */
  perBaseForecast: boolean;
  note: string;
}
