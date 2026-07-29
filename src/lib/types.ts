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

export interface SaveEditRequest {
  targetType: 'player' | 'world' | 'pal';
  targetId: string;
  changes: Record<string, unknown>;
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
  /** Whether the operator configured a way to stop the whole container. */
  stopSupported: boolean;
  returnWatchSeconds: number;
}

// ─── Settings (PalWorldSettings.ini) ────────────────────

export interface IniOption {
  value: string | number | boolean;
  type: 'bool' | 'int' | 'float' | 'string' | 'enum';
  raw: string;
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
