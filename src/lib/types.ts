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
  palCount: number;
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
}

export interface GuildMember {
  uid: string;
  name: string;
  level: number;
  isOnline: boolean;
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
  itemName: string;
  stackCount: number;
  durability: number;
  maxDurability: number;
  isEmpty: boolean;
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
  kind: 'int' | 'string' | 'enum' | 'list';
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
  | 'settings'
  | 'access'
  | 'backups'
  | 'users'
  | 'audit'
  | 'editor';

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
  levelSizeMb?: number;
  counts: Record<string, number>;
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
}

export interface IniSettings {
  path: string;
  writable: boolean;
  options: Record<string, IniOption>;
  count: number;
  presets: SettingsPreset[];
  groups: { label: string; keys: string[] }[];
  serverRunning: boolean;
  restartRequiredForAll: boolean;
}

export interface SettingsPreset {
  id: string;
  label: string;
  description: string;
  changes: Record<string, string | number | boolean>;
}

// ─── Breeding ───────────────────────────────────────────

export interface PalSummary {
  internalName: string;
  name: string;
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
  individuals: {
    instanceId: string;
    nickname: string;
    gender: string;
    level: number;
    rank: number;
    ivs: Record<string, number>;
    passives: { id: string; name: string }[];
  }[];
}

export interface PalboxSummary {
  species: PalboxSpecies[];
  speciesCount: number;
  totalBreedable: number;
  skippedUnbreedable: number;
}

export interface OffspringOption extends PalSummary {
  owned: boolean;
  pairCount: number;
  fromPairs: { a: string; b: string; aId: string; bId: string }[];
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
